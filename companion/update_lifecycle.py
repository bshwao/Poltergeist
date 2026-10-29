"""An update helper outlives the closed desktop and its installation mutex."""

import os
import subprocess


def launch_update_helper(arguments):
    return subprocess.Popen(arguments, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def wait_for_process_exit(pid, timeout=30):
    if type(pid) is not int or pid <= 0 or pid == os.getpid():
        raise ValueError("Invalid closing Companion process")
    if os.name != "nt":
        import time
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(.05)
        raise ValueError("The Companion process did not exit; update was not started")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # Process already exited before the helper opened it.
            return
        raise ctypes.WinError(error)
    try:
        if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
            raise ValueError("The Companion process did not exit; update was not started")
    finally:
        kernel.CloseHandle(handle)
