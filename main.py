import os
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

from app.main_window import MainWindow
from app.utils.single_instance import SingleInstanceGuard
from app.utils.startup import TRAY_FLAG
from app.utils.wslc_locator import ensure_wslc_on_process_path


def main():
    # Make the WSL container CLI (wslc.exe) resolvable for this process only,
    # without modifying the user's persistent/system PATH.
    ensure_wslc_on_process_path()

    # Set an explicit AppUserModelID so Windows groups the app under its own
    # taskbar entry and uses our window icon instead of the generic python.exe
    # icon when running from source. Harmless on non-Windows platforms.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "hpconcept.WSLCommander"
            )
        except Exception:
            pass

    # Enable High-DPI support
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("WSL Commander")
    app.setApplicationDisplayName("WSL Commander")
    app.setQuitOnLastWindowClosed(False)  # tray-resident app

    # Set application icon – prefer .ico (multi-resolution) over .png
    icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon", "icon.ico")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(
            os.path.dirname(__file__), "assets", "icon", "icon.png"
        )
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    setTheme(Theme.AUTO)

    # Single instance: a second launch (e.g. logon start + manual start)
    # raises the running window instead of double-spawning auto-attach.
    guard = SingleInstanceGuard()
    if not guard.try_acquire():
        guard.notify_running_instance()
        return

    start_hidden = TRAY_FLAG in sys.argv[1:]

    window = MainWindow(start_hidden=start_hidden)
    guard.activateRequested.connect(window.show_and_raise)
    guard.start_listening()

    # Also set the window icon directly
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))

    if not start_hidden:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
