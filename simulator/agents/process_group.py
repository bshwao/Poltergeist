"""Own only the subprocess tree started for one local agent worker."""

import os
import signal


class AgentProcessGroup:
    def __init__(self, process):
        self.process = process
        self._job = None
        self._resumed = False
        if os.name == "nt":
            self._assign_windows_job()

    def _assign_windows_job(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD),
            ]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        handle = None
        try:
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            handle = kernel.OpenProcess(0x0100 | 0x0001, False, self.process.pid)
            if not handle or not kernel.AssignProcessToJobObject(job, handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._job = job
            self._kernel = kernel
        except BaseException:
            kernel.CloseHandle(job)
            raise
        finally:
            if handle:
                kernel.CloseHandle(handle)

    def resume(self):
        """Resume a Windows CREATE_SUSPENDED launch after job assignment.

        Python's Popen closes the primary thread handle. The documented Tool
        Help APIs recover that one thread without resuming an unowned process.
        Suspending creation closes the launcher-to-job race, including Windows
        virtual-environment redirectors that start a second Python process.
        POSIX launches already run in their dedicated session.
        """
        if self._resumed:
            raise RuntimeError("Agent process has already been resumed")
        if os.name != "nt":
            self._resumed = True
            return
        if self._job is None:
            raise RuntimeError("Cannot resume an agent process without its Windows job")

        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
                ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
            ]

        kernel = self._kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32First.restype = wintypes.BOOL
        kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32Next.restype = wintypes.BOOL
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD

        snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if not snapshot or snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        thread_handle = None
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            matching_threads = []
            more = kernel.Thread32First(snapshot, ctypes.byref(entry))
            if not more:
                raise ctypes.WinError(ctypes.get_last_error())
            while more:
                if entry.th32OwnerProcessID == self.process.pid:
                    matching_threads.append(entry.th32ThreadID)
                entry.dwSize = ctypes.sizeof(entry)
                more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            last_error = ctypes.get_last_error()
            if last_error != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(last_error)
            if len(matching_threads) != 1:
                raise RuntimeError("Suspended agent launch must have exactly one primary thread")
            thread_handle = kernel.OpenThread(0x0002, False, matching_threads[0])  # THREAD_SUSPEND_RESUME
            if not thread_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            previous_count = kernel.ResumeThread(thread_handle)
            if previous_count == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            if previous_count != 1:
                raise RuntimeError("Agent primary thread did not have the expected suspended creation state")
            self._resumed = True
        finally:
            if thread_handle:
                kernel.CloseHandle(thread_handle)
            kernel.CloseHandle(snapshot)

    def terminate(self):
        if os.name == "nt":
            job, self._job = self._job, None
            if job:
                self._kernel.CloseHandle(job)
        else:
            # Popen starts a dedicated session; never target the parent's group.
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
