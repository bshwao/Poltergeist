"""Let Setup/Uninstall detect a running private companion without killing it."""

import os

INSTALL_MUTEX = "Poltergeist.Private.Companion.Running"
_handles = []


def mark_installation_in_use():
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, INSTALL_MUTEX)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    # Keep it until process termination, as required by Inno's AppMutex contract.
    # Multiple profiles can hold a reference; none claims exclusive ownership.
    _handles.append(handle)
