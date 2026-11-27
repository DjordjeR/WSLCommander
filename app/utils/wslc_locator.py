"""Locate and enable the WSL Container CLI (`wslc.exe`).

`wslc.exe` ships with WSL 2.9.3+ but is installed under the WSL program
directory (e.g. ``C:\\Program Files\\WSL``) and is **not** placed on the
system PATH.  This module resolves its absolute location so the rest of the
app can invoke it reliably, and offers a process-local PATH convenience that
never mutates the user's persistent/system PATH.
"""

import functools
import os
import shutil

# Well-known install locations for the WSL package (newest layout first).
_KNOWN_WSL_DIRS = [
    os.path.join(os.environ.get("PROGRAMFILES", r"C:\Program Files"), "WSL"),
    os.path.join(os.environ.get("PROGRAMW6432", r"C:\Program Files"), "WSL"),
]

_EXE_NAME = "wslc.exe"


@functools.lru_cache(maxsize=1)
def _resolve() -> str:
    """Resolve ``wslc.exe`` once, returning "" when it cannot be found."""
    for directory in _KNOWN_WSL_DIRS:
        candidate = os.path.join(directory, _EXE_NAME)
        if os.path.isfile(candidate):
            return candidate
    return shutil.which(_EXE_NAME) or ""


def resolve_wslc_path():
    """Return the absolute path to ``wslc.exe`` or ``None`` if unavailable.

    Resolution order:
      1. Known WSL install directories.
      2. Anything already on PATH (``shutil.which``).
    The result is cached for the lifetime of the process.
    """
    return _resolve() or None


def is_container_support_available():
    """Return True if the WSL container CLI is present on this machine."""
    return resolve_wslc_path() is not None


@functools.lru_cache(maxsize=1)
def ensure_wslc_on_process_path():
    """Prepend the resolved WSL directory to *this process's* PATH.

    This is a convenience so bare ``wslc`` invocations resolve; it only
    affects ``os.environ`` for the running process and is never written back
    to the user's persistent or system PATH.  Cached, so it is safe to call
    multiple times.
    """
    wslc_path = resolve_wslc_path()
    if not wslc_path:
        return None
    wsl_dir = os.path.dirname(wslc_path)
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if wsl_dir not in parts:
        os.environ["PATH"] = os.pathsep.join([wsl_dir, *parts]) if parts else wsl_dir
    return None
