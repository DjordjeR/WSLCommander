"""Win32 DWM helpers for frameless popup windows.

qfluentwidgets' ``RoundMenu`` windows are frameless and translucent and paint
their own rounded background plus shadow margin. On Windows 11 the Desktop
Window Manager still draws its own rounded border on every top-level window,
which shows up as a stray translucent outline around the tray menus. These
helpers turn DWM's non-client rendering off for a menu's HWND.
"""

import ctypes
import ctypes.wintypes
import sys

_IS_WINDOWS = sys.platform == "win32"

# dwmapi.h constants
_DWMWA_NCRENDERING_POLICY = 2
_DWMNCRP_DISABLED = 1
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1
_DWMWA_BORDER_COLOR = 34
_DWMWA_COLOR_NONE = 0xFFFFFFFE


def disable_window_border(hwnd: int) -> None:
    """Remove DWM's border and rounded corners from the window *hwnd*.

    Safe to call on any window handle and on non-Windows platforms (no-op);
    failures are ignored since the attribute is purely cosmetic.
    """
    if not _IS_WINDOWS or not hwnd:
        return
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [
            ctypes.wintypes.HWND,
            ctypes.wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
        ]
        dwm.DwmSetWindowAttribute.restype = ctypes.HRESULT
        policy = ctypes.c_int(_DWMNCRP_DISABLED)
        dwm.DwmSetWindowAttribute(
            hwnd, _DWMWA_NCRENDERING_POLICY, ctypes.byref(policy), ctypes.sizeof(policy)
        )
        corner = ctypes.c_int(_DWMWCP_DONOTROUND)
        dwm.DwmSetWindowAttribute(
            hwnd,
            _DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(corner),
            ctypes.sizeof(corner),
        )
        border = ctypes.c_uint32(_DWMWA_COLOR_NONE)
        dwm.DwmSetWindowAttribute(
            hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(border), ctypes.sizeof(border)
        )
    except Exception:
        pass
