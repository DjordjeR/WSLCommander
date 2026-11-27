"""Notification-area (system tray) icon for WSL Commander.

The tray provides:

* A live badge on the app icon while any auto-attach session is active, plus
  a device-count tooltip — the "icon next to the clock" status indicator.
* A context menu to open the app, start/stop individual auto-attach sessions
  (including persisted-but-currently-inactive ones), quick-attach to the most
  recently used devices, toggle close-to-tray and start-at-login, and quit.
* Native balloon notifications for auto-attach events *and* live
  attach/detach transitions reported by the :class:`DeviceWatcher`, which —
  unlike the in-window InfoBars — are visible when the window is hidden.
"""

import os
import sys
import time

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
from qfluentwidgets import SystemTrayMenu

from app.services.auto_attach_manager import AutoAttachManager
from app.services.device_watcher import DeviceWatcher
from app.utils import startup
from app.utils.human_time import relative_time as _relative_time
from app.utils.settings import MAX_RECENT_DEVICES, settings_store
from app.utils.win32_dwm import disable_window_border


def _app_icon_path() -> str | None:
    """Locate the app icon (works from source and PyInstaller bundle)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        root = sys._MEIPASS
    else:
        # app/services/tray_service.py -> project root
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    for name in ("icon.ico", "icon.png"):
        candidate = os.path.join(root, "assets", "icon", name)
        if os.path.isfile(candidate):
            return candidate
    return None


def _make_badged_icon(base_path: str, count: int) -> QIcon:
    """Return the app icon with a green count badge when *count* > 0.

    Badged pixmaps are rendered at each standard tray size so the shell can
    pick a native one; scaling a single large pixmap down produces a faint
    translucent halo around the icon in the notification area.
    """
    if count <= 0:
        return QIcon(base_path)
    base = QIcon(base_path)
    icon = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64):
        pixmap = base.pixmap(size, size)
        if pixmap.isNull():
            continue
        pixmap = pixmap.copy()  # detach from the icon's internal cache
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        diameter = max(8, round(size * 0.42))
        x = y = size - diameter - max(1, size // 32)
        painter.setBrush(QColor("#107C10"))
        painter.setPen(QPen(QColor("#FFFFFF"), max(1.0, size / 32.0)))
        painter.drawEllipse(x, y, diameter, diameter)
        font = painter.font()
        font.setPixelSize(max(7, int(diameter * 0.68)))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        text = "9+" if count > 9 else str(count)
        painter.drawText(
            x,
            y - max(1, size // 48),
            diameter,
            diameter,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter),
            text,
        )
        painter.end()
        icon.addPixmap(pixmap)
    return icon if not icon.isNull() else QIcon(base_path)


class _BorderlessTrayMenu(SystemTrayMenu):
    """Tray menu without the Windows 11 DWM border.

    The menu window is translucent and paints its own rounded background, so
    DWM's own border/corners show up as a stray translucent outline around it.
    """

    def showEvent(self, event) -> None:
        super().showEvent(event)
        disable_window_border(int(self.winId()))


class TrayService(QObject):
    """Owns the :class:`QSystemTrayIcon` and its menu."""

    openRequested = pyqtSignal()
    quitRequested = pyqtSignal()

    def __init__(
        self,
        manager: AutoAttachManager,
        watcher: DeviceWatcher,
        parent=None,
    ):
        super().__init__(parent)
        self._manager = manager
        self._watcher = watcher
        self._icon_path = _app_icon_path()
        self._tray = QSystemTrayIcon(self)
        self._menu = _BorderlessTrayMenu()
        self._tray.setContextMenu(self._menu)
        # Bus IDs whose auto-attach we just stopped; the matching watcher
        # "detached" event is redundant and gets suppressed briefly.
        self._recently_stopped: dict[str, float] = {}
        # Same idea for the "attached" event that follows our own start.
        self._recently_started: dict[str, float] = {}
        # Rate limiting for identical balloons: (title, message) -> last shown.
        self._last_notified: dict[tuple[str, str], float] = {}

        self._tray.activated.connect(self._on_activated)
        self._menu.aboutToShow.connect(self._rebuild_menu)

        manager.countChanged.connect(self._update_icon)
        manager.entryChanged.connect(
            lambda _b: self._update_icon(manager.active_count())
        )
        manager.started.connect(self._on_started)
        manager.stopped.connect(self._on_stopped)
        manager.error.connect(self._on_error)

        watcher.attached.connect(self._on_device_attached)
        watcher.detached.connect(self._on_device_detached)

        self._update_icon(manager.active_count())
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.show()

    # ── Public API ───────────────────────────────────────────────────────

    def hide(self) -> None:
        """Remove the icon from the notification area (on app quit)."""
        self._tray.hide()

    def notify(
        self,
        title: str,
        message: str,
        kind: QSystemTrayIcon.MessageIcon = QSystemTrayIcon.MessageIcon.Information,
        ms: int = 4000,
        category: str | None = None,
        cooldown_s: float = 120.0,
        once_per_session: bool = False,
    ) -> None:
        """Show a balloon, unless *category* is a notification the user
        disabled in settings (``None`` = always show, e.g. app lifecycle).

        Identical notifications (same title and message) are rate-limited:
        repeats within *cooldown_s* are dropped so a flapping device or a
        retrying auto-attach worker cannot spam the notification area.
        With *once_per_session*, a given notification is shown at most once
        per app run.
        """
        if category is not None and not getattr(
            settings_store.data.notifications, category, True
        ):
            return
        key = (title, message)
        now = time.monotonic()
        last = self._last_notified.get(key)
        if last is not None and (once_per_session or now - last < cooldown_s):
            return
        self._last_notified[key] = now
        if self._tray.isVisible():
            self._tray.showMessage(title, message, kind, ms)

    # ── Icon / tooltip ───────────────────────────────────────────────────

    def _update_icon(self, count: int) -> None:
        if self._icon_path is None:
            icon = QApplication.windowIcon()
        else:
            icon = _make_badged_icon(self._icon_path, count)
        self._tray.setIcon(icon)
        if count > 0:
            plural = "device" if count == 1 else "devices"
            self._tray.setToolTip(f"WSL Commander — auto-attaching {count} {plural}")
        else:
            self._tray.setToolTip("WSL Commander")

    # ── Menu ─────────────────────────────────────────────────────────────

    def _rebuild_menu(self) -> None:
        # NOTE: RoundMenu.clear() removes actions but NOT separators (they are
        # plain list items, not QActions), so separators must be purged from
        # the underlying view explicitly or they accumulate on every rebuild.
        # It also only *unlinks* QActions/submenus; since they are parented to
        # the menu they would leak on every open, so delete them explicitly.
        stale_actions = list(self._menu._actions)
        stale_menus = list(self._menu._subMenus)
        self._menu.clear()
        view = self._menu.view
        while view.count():
            view.takeItem(0)
        self._menu.adjustSize()
        for action in stale_actions:
            action.deleteLater()
        for submenu in stale_menus:
            submenu.deleteLater()

        open_action = QAction("Open WSL Commander", self._menu)
        open_action.triggered.connect(self.openRequested.emit)
        self._menu.addAction(open_action)
        self._menu.addSeparator()

        active = self._manager.active_keys()
        persisted = [b for b in self._manager.keep_alive_keys() if b not in active]

        if active:
            header = QAction("Auto-attach running", self._menu)
            header.setEnabled(False)
            self._menu.addAction(header)
            for key in active:
                entry = settings_store.data.entry(key)
                shown = entry.busid if entry else key
                label = f"Stop: {shown}"
                if entry and entry.description:
                    label = f"Stop: {shown} — {entry.description}"
                action = QAction(label, self._menu)
                action.triggered.connect(lambda _=False, k=key: self._manager.stop(k))
                self._menu.addAction(action)
            self._menu.addSeparator()

        if persisted:
            header = QAction("Saved (not running)", self._menu)
            header.setEnabled(False)
            self._menu.addAction(header)
            for key in persisted:
                entry = settings_store.data.entry(key)
                shown = entry.busid if entry else key
                label = f"Start: {shown}"
                if entry and entry.description:
                    label = f"Start: {shown} — {entry.description}"
                action = QAction(label, self._menu)
                action.triggered.connect(
                    lambda _=False, e=entry: self._restart_entry(e)
                )
                self._menu.addAction(action)
            self._menu.addSeparator()

        if not active and not persisted:
            empty = QAction("No auto-attach sessions", self._menu)
            empty.setEnabled(False)
            self._menu.addAction(empty)
        self._menu.addSeparator()

        self._add_recent_devices_menu()

        close_action = QAction("Close to tray", self._menu)
        close_action.setCheckable(True)
        close_action.setChecked(settings_store.data.close_to_tray)
        close_action.toggled.connect(self._on_close_to_tray_toggled)
        self._menu.addAction(close_action)

        login_action = QAction("Start at login", self._menu)
        login_action.setCheckable(True)
        login_action.setChecked(settings_store.data.start_at_login)
        login_action.toggled.connect(self._on_start_at_login_toggled)
        self._menu.addAction(login_action)

        self._menu.addSeparator()
        quit_action = QAction("Quit", self._menu)
        quit_action.triggered.connect(self.quitRequested.emit)
        self._menu.addAction(quit_action)

    def _add_recent_devices_menu(self) -> None:
        """Build the "Recent devices" submenu with per-device quick actions.

        Every USB device is tracked (not just shared ones), ordered by the
        last time it was plugged in — the timestamp next to each entry is
        what identifies *your* device among several with the same name.
        """
        recents = settings_store.data.recent_devices[:MAX_RECENT_DEVICES]
        if not recents:
            empty = QAction("Recent devices (none yet)", self._menu)
            empty.setEnabled(False)
            self._menu.addAction(empty)
            self._menu.addSeparator()
            return

        submenu = _BorderlessTrayMenu("Recent devices", self._menu)
        for device in recents:
            key = device.key
            attached = self._watcher.is_device_attached(key)
            label = device.description or device.busid
            if len(label) > 22:
                label = label[:21].rstrip() + "…"
            marker = "● " if attached else "○ "
            when = _relative_time(device.last_seen)
            header_text = f"{marker}{label} ({device.busid})"
            if when:
                header_text += f" · {when}"

            header = QAction(header_text, submenu)
            header.setEnabled(False)
            submenu.addAction(header)

            if self._manager.is_active(key):
                stop_action = QAction("Stop", submenu)
                stop_action.triggered.connect(
                    lambda _=False, k=key: self._manager.stop(k)
                )
                submenu.addAction(stop_action)
            else:
                attach_action = QAction("Auto attach", submenu)
                attach_action.triggered.connect(
                    lambda _=False, d=device: self._start_recent(d, keep_alive=False)
                )
                submenu.addAction(attach_action)

                keep_action = QAction("Keep after restart", submenu)
                keep_action.triggered.connect(
                    lambda _=False, d=device: self._start_recent(d, keep_alive=True)
                )
                submenu.addAction(keep_action)

        submenu.adjustSize()

        self._menu.addMenu(submenu)
        self._menu.addSeparator()

    def _start_recent(self, device, keep_alive: bool) -> None:
        """Start auto-attach for a recent device remembered from the tray."""
        entry = settings_store.data.entry(device.key)
        self._manager.start(
            device.key,
            busid=device.busid,
            distro=entry.distro if entry else "",
            unplugged=entry.unplugged if entry else False,
            description=device.description,
            keep_alive=keep_alive,
        )

    def _restart_entry(self, entry) -> None:
        if entry is None:
            return
        self._manager.start(
            entry.key,
            busid=entry.busid,
            distro=entry.distro,
            unplugged=entry.unplugged,
            description=entry.description,
            keep_alive=True,
        )

    # ── Slots ────────────────────────────────────────────────────────────

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.openRequested.emit()

    def _on_close_to_tray_toggled(self, checked: bool) -> None:
        settings_store.data.close_to_tray = checked
        settings_store.save()

    def _on_start_at_login_toggled(self, checked: bool) -> None:
        ok = startup.register() if checked else startup.unregister()
        if ok:
            settings_store.data.start_at_login = checked
        else:
            # Revert the checkbox on failure without re-triggering this slot.
            sender = self.sender()
            if sender is not None:
                sender.blockSignals(True)
                sender.setChecked(not checked)
                sender.blockSignals(False)
            self.notify(
                "Start at login",
                "Could not modify the Windows Run registry key.",
                QSystemTrayIcon.MessageIcon.Warning,
            )
        settings_store.save()

    def _on_started(self, busid: str) -> None:
        # The watcher will also see the device become Attached momentarily;
        # that first event is redundant with this notification.
        self._recently_started[busid] = time.monotonic()
        # User-initiated starts always confirm; reconcile-driven re-spawns
        # use the normal cooldown so a flapping device cannot spam.
        user = self._manager.consume_started_by_user(busid)
        self.notify(
            "Auto-attach started",
            f"Auto-attach is running for device {settings_store.data.display(busid)}.",
            QSystemTrayIcon.MessageIcon.Information,
            category="started",
            cooldown_s=0 if user else 120,
        )

    def _on_stopped(self, busid: str) -> None:
        # Suppress the redundant "detached" balloon the watcher would emit.
        self._recently_stopped[busid] = time.monotonic()
        user = self._manager.consume_stopped_by_user(busid)
        self.notify(
            "Auto-attach stopped",
            f"Auto-attach stopped for device {settings_store.data.display(busid)}.",
            QSystemTrayIcon.MessageIcon.Information,
            category="stopped",
            cooldown_s=0 if user else 120,
        )

    def _on_device_attached(self, busid: str, description: str) -> None:
        # Right after starting our own session, _on_started already notified.
        started_at = self._recently_started.pop(busid, None)
        if started_at is not None and time.monotonic() - started_at < 30:
            return
        name = description or busid
        self.notify(
            "Device attached",
            f"{name} ({settings_store.data.display(busid)}) is now attached to WSL.",
            QSystemTrayIcon.MessageIcon.Information,
            category="attached",
        )

    def _on_device_detached(self, busid: str, description: str) -> None:
        # If *we* stopped the auto-attach, _on_stopped already notified.
        stopped_at = self._recently_stopped.pop(busid, None)
        if stopped_at is not None and time.monotonic() - stopped_at < 30:
            return
        name = description or busid
        self.notify(
            "Device detached",
            f"{name} ({settings_store.data.display(busid)}) was detached from WSL.",
            QSystemTrayIcon.MessageIcon.Information,
            category="detached",
        )

    def _on_error(self, busid: str, message: str) -> None:
        # Key the cooldown on the device only: a retrying worker repeats
        # nearly the same error, and the exact text may vary per attempt.
        key = ("Auto-attach error", busid)
        now = time.monotonic()
        last = self._last_notified.get(key)
        if last is not None and now - last < 300:
            return
        self._last_notified[key] = now
        self.notify(
            "Auto-attach error",
            f"{settings_store.data.display(busid)}: {message}",
            QSystemTrayIcon.MessageIcon.Critical,
            ms=8000,
            category="error",
            cooldown_s=0,
        )
