"""Register WSL Commander to start at Windows login.

Uses the per-user ``HKCU\\...\\CurrentVersion\\Run`` key — the standard,
admin-free mechanism for launch-at-logon.  The app is launched with
``--tray`` so it starts minimized to the notification area and restores
persisted auto-attach sessions without user interaction.
"""

import os
import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "WSLCommander"

TRAY_FLAG = "--tray"


def _launch_command() -> str:
    """Build the command line used at logon.

    For a frozen (PyInstaller) build this is the exe path; when running from
    source it is ``pythonw.exe main.py`` so no console window pops up.
    ``pythonw`` lives next to ``python`` in every venv/installation.
    """
    if getattr(sys, "frozen", False):
        base = f'"{sys.executable}"'
    else:
        python_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(python_dir, "pythonw.exe")
        script = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "main.py",
        )
        if os.path.isfile(pythonw):
            base = f'"{pythonw}" "{script}"'
        else:
            base = f'"{sys.executable}" "{script}"'
    return f"{base} {TRAY_FLAG}"


def is_registered() -> bool:
    """Return *True* if the app is registered in the current user's Run key."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _VALUE_NAME)
        return isinstance(value, str) and bool(value)
    except OSError:
        return False


def register() -> bool:
    """Add (or refresh) the Run-key entry.  Returns success."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
        return True
    except OSError:
        return False


def unregister() -> bool:
    """Remove the Run-key entry.  Returns success (True if it was absent)."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False
