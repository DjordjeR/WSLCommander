from PyQt6.QtWidgets import QApplication
from qfluentwidgets import FluentIcon, FluentWindow

from app.pages.distros_page import DistrosPage
from app.pages.install_page import InstallPage
from app.pages.settings_page import SettingsPage
from app.pages.usb_page import UsbPage
from app.services.auto_attach_manager import AutoAttachManager
from app.services.device_watcher import DeviceWatcher
from app.services.tray_service import TrayService
from app.utils.settings import settings_store


class MainWindow(FluentWindow):
    def __init__(self, start_hidden: bool = False):
        super().__init__()
        self._quitting = False
        self._init_window()
        self._init_services()
        self._init_navigation()
        if start_hidden:
            self.hide()
            self.tray.notify(
                "WSL Commander is running",
                "The app stays in the notification area. "
                "Double-click the icon to open it.",
                once_per_session=True,
            )

    def _init_window(self):
        self.setWindowTitle("WSL Commander")
        self.setMinimumSize(900, 650)
        self.resize(1100, 720)

        # Centre on the screen that contains the cursor (handles multi-monitor)
        screen = QApplication.screenAt(QApplication.screens()[0].geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.move(
            geo.center().x() - self.width() // 2,
            geo.center().y() - self.height() // 2,
        )

    def _init_services(self):
        """Create app-level services (auto-attach manager + tray icon)."""
        self.auto_attach_manager = AutoAttachManager(self)
        self.device_watcher = DeviceWatcher(self)
        self.tray = TrayService(self.auto_attach_manager, self.device_watcher, self)
        self.tray.openRequested.connect(self.show_and_raise)
        self.tray.quitRequested.connect(self.quit_application)

        # Re-spawn persisted keep-alive auto-attach sessions.
        self.auto_attach_manager.restore_persisted()
        # Live attach/detach tracking for tray notifications + recent devices.
        # Each fresh device snapshot also reconciles the auto-attach desired
        # state, so a device plugged in later is attached automatically.
        self.device_watcher.devicesUpdated.connect(self.auto_attach_manager.reconcile)
        self.device_watcher.start()

    def _init_navigation(self):
        self.distros_page = DistrosPage(self)
        self.usb_page = UsbPage(self.auto_attach_manager, self.device_watcher, self)
        self.install_page = InstallPage(self)

        self.addSubInterface(
            interface=self.distros_page,
            icon=FluentIcon.HOME,
            text="Distributions",
        )
        self.addSubInterface(
            interface=self.usb_page,
            icon=FluentIcon.CONNECT,
            text="USB Sharing",
        )
        self.addSubInterface(
            interface=self.install_page,
            icon=FluentIcon.DOWNLOAD,
            text="Install",
        )
        self.settings_page = SettingsPage(self)
        self.addSubInterface(
            interface=self.settings_page,
            icon=FluentIcon.SETTING,
            text="Settings",
        )

        # Auto-refresh distributions page after an install/import completes
        # Import (.tar) is synchronous – do a direct refresh
        self.install_page.distro_installed.connect(self.distros_page.refresh)
        # Catalogue install launches an external terminal – start polling until
        # the new distro appears
        self.install_page.install_launched.connect(
            self.distros_page.start_install_polling
        )

        self.navigationInterface.setCurrentItem(self.distros_page.objectName())

    # ── Window lifecycle ─────────────────────────────────────────────────

    def show_and_raise(self):
        """Restore from tray click or single-instance activation."""
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.activateWindow()
        self.raise_()

    def quit_application(self):
        """Exit the app; keep-alive auto-attach children keep running."""
        if self._quitting:
            return
        self._quitting = True
        self.device_watcher.stop()
        self.auto_attach_manager.shutdown_for_quit()
        self.tray.hide()
        QApplication.quit()

    def closeEvent(self, event):
        """Close to tray when enabled; otherwise perform a real quit."""
        if self._quitting:
            super().closeEvent(event)
            return
        if settings_store.data.close_to_tray:
            event.ignore()
            self.hide()
            self.tray.notify(
                "Still running",
                "WSL Commander was minimized to the notification area. "
                "Use the tray icon's Quit to exit.",
                once_per_session=True,
            )
            return
        # Tray disabled: closing the window quits the app.
        self.quit_application()
        event.accept()
