"""Win32 job-object support for owning child processes.

A *kill-on-close* job object is the canonical Windows way to guarantee that
child processes never outlive their parent: when the last handle to the job
is closed (which happens automatically when the parent process exits, even
after a hard crash), every process in the job is terminated by the OS.

WSL Commander uses this for **non-persistent** ``usbipd attach --auto-attach``
children so they can never linger as orphans after the app dies.  Persistent
(keep-alive) children are deliberately *not* assigned to the job so they can
survive an app restart.
"""

import contextlib
import ctypes
import sys
from ctypes import wintypes

_IS_WINDOWS = sys.platform == "win32"

# ── Win32 constants ──────────────────────────────────────────────────────────

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JobObjectExtendedLimitInformation = 9


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class KillOnCloseJob:
    """A kill-on-close job object; terminates all members when closed.

    Usage::

        job = KillOnCloseJob.create()
        if job:
            job.assign(process_handle_or_pid)
        ...
        job.close()   # optional; OS kills members on process exit anyway
    """

    def __init__(self, handle: int):
        self._handle = handle

    @classmethod
    def create(cls) -> "KillOnCloseJob | None":
        """Create the job, or return *None* on non-Windows / on failure."""
        if not _IS_WINDOWS:  # pragma: no cover - non-Windows
            return None
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.CreateJobObjectW(None, None)
            if not handle:
                return None

            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            ok = kernel32.SetInformationJobObject(
                wintypes.HANDLE(handle),
                ctypes.c_int(_JobObjectExtendedLimitInformation),
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not ok:
                kernel32.CloseHandle(wintypes.HANDLE(handle))
                return None
            return cls(handle)
        except (OSError, AttributeError):
            return None

    def assign(self, pid: int) -> bool:
        """Open the process *pid* and assign it to this job.

        Returns *True* on success.  Failure is non-fatal — the child simply
        keeps running without the kill-on-close guarantee.
        """
        if not self._handle or not _IS_WINDOWS:
            return False
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        try:
            kernel32 = ctypes.windll.kernel32
            process = kernel32.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid
            )
            if not process:
                return False
            try:
                return bool(
                    kernel32.AssignProcessToJobObject(
                        wintypes.HANDLE(self._handle), wintypes.HANDLE(process)
                    )
                )
            finally:
                kernel32.CloseHandle(wintypes.HANDLE(process))
        except (OSError, AttributeError):
            return False

    def close(self) -> None:
        """Close the job handle, terminating any members still in the job."""
        if self._handle:
            with contextlib.suppress(OSError, AttributeError):
                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = 0
