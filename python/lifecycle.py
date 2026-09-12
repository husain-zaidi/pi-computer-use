"""Owned-process cleanup. This is lifecycle management, not a security sandbox."""
import ctypes
import os
import signal
import sys
import threading
import time


def kill_windows_descendants(roots):
    """Snapshot parent IDs, then terminate descendants leaf-first (including orphaned children).

    Root IDs come only from our launched subprocesses. Keep the actual interpreter PID
    as well as the venv launcher PID: Windows Store Python uses both.
    """
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("exe", wintypes.WCHAR * 260)]

    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    entry = ProcessEntry()
    entry.size = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.pid] = entry.parent
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    known = set(roots)
    descendants = []
    while True:
        found = [pid for pid, parent in parents.items() if parent in known and pid not in known]
        if not found:
            break
        descendants.extend(found)
        known.update(found)
    errors = []
    for pid in reversed(descendants):
        if pid == os.getpid():
            continue
        handle = kernel.OpenProcess(1, False, pid)
        if not handle:
            # Already exited is harmless; access denied is not.
            if ctypes.get_last_error() == 5:
                errors.append(f"access denied releasing child {pid}")
            continue
        try:
            if not kernel.TerminateProcess(handle, 1) and ctypes.get_last_error() != 5:
                errors.append(f"could not terminate child {pid}")
        finally:
            kernel.CloseHandle(handle)
    if errors:
        raise RuntimeError("; ".join(errors))


def install_parent_guard():
    parent = int(os.environ.get("PI_CUA_PARENT_PID", "0"))
    if not parent:
        return
    if sys.platform == "win32":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        parent_handle = kernel.OpenProcess(0x00100000, False, parent)
        if not parent_handle:
            raise ctypes.WinError(ctypes.get_last_error())

        def watch():
            kernel.WaitForSingleObject(parent_handle, 0xFFFFFFFF)
            try:
                kill_windows_descendants([os.getpid()])
                if "--desktop" in sys.argv:
                    from release_inputs import main as release
                    release()
            finally:
                os._exit(1)
    else:
        def watch():
            while os.getppid() == parent:
                time.sleep(0.5)
            os.killpg(os.getpgrp(), signal.SIGKILL)

    threading.Thread(target=watch, name="pi-cua-parent-guard", daemon=True).start()
