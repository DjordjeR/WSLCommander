"""Settings page: general behaviour and notification preferences."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    InfoBar,
    InfoBarPosition,
    ScrollArea,
    SwitchButton,
    TitleLabel,
)

from app.utils import startup
from app.utils.settings import settings_store


def _setting_row(title: str, description: str, switch: SwitchButton) -> CardWidget:
    card = CardWidget()
    layout = QHBoxLayout(card)
    layout.setContentsMargins(16, 12, 16, 12)
    text = QVBoxLayout()
    text.setSpacing(2)
    text.addWidget(BodyLabel(title))
    text.addWidget(CaptionLabel(description))
    layout.addLayout(text)
    layout.addStretch()
    layout.addWidget(switch)
    return card


class SettingsPage(ScrollArea):
    """User-facing settings: tray/startup behaviour and notification toggles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsPage")

        self._content = QWidget()
        self._content.setObjectName("settingsContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(30, 20, 30, 20)
        self._layout.setSpacing(14)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setWidget(self._content)
        self.setWidgetResizable(True)

        self._build_ui()

    def _build_ui(self) -> None:
        header = QHBoxLayout()
        header.addWidget(TitleLabel("Settings"))
        header.addStretch()
        self._layout.addLayout(header)

        # ── General ──────────────────────────────────────────────────
        self._layout.addSpacing(6)

        self._close_tray_switch = SwitchButton()
        self._close_tray_switch.setChecked(settings_store.data.close_to_tray)
        self._close_tray_switch.checkedChanged.connect(self._on_close_to_tray)
        self._layout.addWidget(
            _setting_row(
                "Minimize to tray",
                "Closing the window keeps WSL Commander running in the "
                "notification area.",
                self._close_tray_switch,
            )
        )

        self._login_switch = SwitchButton()
        self._login_switch.setChecked(settings_store.data.start_at_login)
        self._login_switch.checkedChanged.connect(self._on_start_at_login)
        self._layout.addWidget(
            _setting_row(
                "Start at login",
                "Launch WSL Commander automatically when you sign in to Windows.",
                self._login_switch,
            )
        )

        # ── Notifications ────────────────────────────────────────────
        self._layout.addSpacing(12)
        self._layout.addLayout(
            self._section_header(
                "Notifications",
                "Which balloon notifications appear in the notification area.",
            )
        )

        notif = settings_store.data.notifications
        self._notif_switches: dict[str, SwitchButton] = {}
        rows = [
            (
                "started",
                "Auto-attach started",
                "A device was added to the auto-attach list.",
            ),
            (
                "stopped",
                "Auto-attach stopped",
                "A device was removed from the auto-attach list.",
            ),
            (
                "error",
                "Auto-attach errors",
                "An auto-attach attempt failed (for example the device is not shared).",
            ),
            ("attached", "Device attached", "A device became attached to WSL."),
            (
                "detached",
                "Device detached",
                "A device detached from WSL (unplugged or released).",
            ),
        ]
        for key, title, description in rows:
            switch = SwitchButton()
            switch.setChecked(getattr(notif, key))
            switch.checkedChanged.connect(
                lambda checked, k=key: self._on_notification(k, checked)
            )
            self._notif_switches[key] = switch
            self._layout.addWidget(_setting_row(title, description, switch))

    def _section_header(self, title: str, description: str) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(2)
        box.addWidget(BodyLabel(title))
        box.addWidget(CaptionLabel(description))
        return box

    # ── Handlers ─────────────────────────────────────────────────────────

    def _on_close_to_tray(self, checked: bool) -> None:
        settings_store.data.close_to_tray = checked
        settings_store.save()

    def _on_start_at_login(self, checked: bool) -> None:
        ok = startup.register() if checked else startup.unregister()
        if ok:
            settings_store.data.start_at_login = checked
        else:
            # Revert the switch without re-triggering this handler.
            self._login_switch.blockSignals(True)
            self._login_switch.setChecked(not checked)
            self._login_switch.blockSignals(False)
            InfoBar.error(
                "Start at login",
                "Could not modify the Windows Run registry key.",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=5000,
            )
        settings_store.save()

    def _on_notification(self, key: str, checked: bool) -> None:
        setattr(settings_store.data.notifications, key, checked)
        settings_store.save()
