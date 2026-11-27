"""Helpers for cleaning up leftover ``usbipd`` child processes.

When WSL Commander is killed (or crashes), ``usbipd attach --auto-attach``
children that were *not* placed in a kill-on-close job (persistent entries)
keep running.  Before re-spawning an auto-attach worker for a bus ID we must
terminate any leftover process from a previous session, otherwise two
``usbipd`` instances would fight over the same device.
"""

import ctypes
import sys
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_TERMINATE = 0x0001
_EXIT_CODE_STILL_ACTIVE = 259


def is_process_running(pid: int) -> bool:
    """Return *True* if *pid* refers to a live process (best effort)."""
    if pid <= 0:
        return False
    if not _IS_WINDOWS:  # pragma: no cover - non-Windows
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # Cannot open: either exited or access denied.  Access-denied
            # processes are alive, but our own leftovers are always ours.
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(
                ctypes.wintypes.HANDLE(handle), ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == _EXIT_CODE_STILL_ACTIVE
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
    except (OSError, AttributeError):
        return False


def terminate_process(pid: int) -> bool:
    """Terminate the process *pid*.  Returns *True* if termination succeeded."""
    if pid <= 0 or not _IS_WINDOWS:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(wintypes.HANDLE(handle), 0))
        finally:
            kernel32.CloseHandle(wintypes.HANDLE(handle))
    except (OSError, AttributeError):
        return False
