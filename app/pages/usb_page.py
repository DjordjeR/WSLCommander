from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    ComboBox,
    FluentIcon,
    IndeterminateProgressBar,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    TableWidget,
    TitleLabel,
    ToolButton,
)

from app.models.usb_device import UsbDevice
from app.services.device_watcher import DeviceWatcher
from app.utils.human_time import relative_time
from app.utils.settings import settings_store
from app.workers.usb_worker import (
    AttachUsbWorker,
    BindUsbWorker,
    DetachUsbWorker,
    UnbindUsbWorker,
)
from app.workers.wsl_worker import ListInstalledWorker


class UsbPage(ScrollArea):
    """Page for USB device sharing with WSL distributions."""

    def __init__(self, auto_attach_manager, watcher: DeviceWatcher, parent=None):
        super().__init__(parent)
        self.setObjectName("UsbPage")
        self._manager = auto_attach_manager
        self._watcher = watcher
        self._workers = []
        self._devices: dict[str, UsbDevice] = {}  # busid -> UsbDevice
        self._distros = []
        self._last_selected_busid: str | None = (
            None  # remember selection across refreshes
        )

        # The DeviceWatcher owns the only usbipd polling loop; the table is a
        # pure view of its snapshots (same data the tray and reconciler use).
        watcher.devicesUpdated.connect(self._on_usb_loaded)
        watcher.error.connect(self._on_usb_error)

        # Keep the table/button state in sync with manager-driven changes
        # (tray actions, startup restoration, worker exits).
        self._manager.started.connect(self._on_auto_attach_started)
        self._manager.stopped.connect(self._on_auto_attach_stopped)
        self._manager.error.connect(self._on_auto_attach_error)

        self._content = QWidget()
        self._content.setObjectName("usbContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(30, 20, 30, 20)
        self._layout.setSpacing(14)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.setWidget(self._content)
        self.setWidgetResizable(True)

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        # Header
        header = QHBoxLayout()
        title = TitleLabel("USB Device Sharing")
        header.addWidget(title)
        header.addStretch()
        refresh_btn = ToolButton(FluentIcon.SYNC)
        refresh_btn.setToolTip("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(refresh_btn)
        self._layout.addLayout(header)

        hint = BodyLabel(
            "Manage USB devices shared with WSL using usbipd-win. "
            "Binding may require administrator privileges."
        )
        hint.setWordWrap(True)
        self._layout.addWidget(hint)

        self._progress = IndeterminateProgressBar(self)
        self._progress.setVisible(False)
        self._layout.addWidget(self._progress)

        # Distro selector card
        sel_card = CardWidget()
        sel_card.setFixedHeight(70)
        sel_layout = QHBoxLayout(sel_card)
        sel_layout.setContentsMargins(16, 10, 16, 10)
        sel_layout.addWidget(CaptionLabel("Target WSL Distribution:"))
        self._distro_combo = ComboBox()
        self._distro_combo.setPlaceholderText("(default)")
        self._distro_combo.setMinimumWidth(220)
        sel_layout.addWidget(self._distro_combo)

        self._force_check = CheckBox("Force bind")
        self._force_check.setToolTip("Pass --force flag to usbipd bind")
        sel_layout.addWidget(self._force_check)

        self._unplugged_check = CheckBox("Unplugged")
        self._unplugged_check.setToolTip(
            "Allow auto-attach for devices not currently plugged in"
        )
        sel_layout.addWidget(self._unplugged_check)

        self._keep_alive_check = CheckBox("Keep after restart")
        self._keep_alive_check.setToolTip(
            "Keep this auto-attach session alive over app restarts and Windows\n"
            "logoffs. Enable 'Start at login' in the tray menu to restore it\n"
            "automatically after rebooting."
        )
        self._keep_alive_check.toggled.connect(self._on_keep_alive_toggled)
        sel_layout.addWidget(self._keep_alive_check)
        sel_layout.addStretch()
        self._layout.addWidget(sel_card)

        # Table
        self._table = TableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(
            ["Bus ID", "Device", "State", "Last connected", "Actions"]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        for col in (2, 3, 4):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setMinimumHeight(320)
        self._table.setSortingEnabled(True)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._layout.addWidget(self._table)

        # Action toolbar
        action_bar = QHBoxLayout()
        self._bind_btn = PrimaryPushButton(FluentIcon.LINK, "Bind")
        self._bind_btn.setToolTip("Bind selected device (share it with WSL)")
        self._bind_btn.clicked.connect(self._bind_selected)
        action_bar.addWidget(self._bind_btn)

        self._attach_btn = PushButton(FluentIcon.SHARE, "Attach to WSL")
        self._attach_btn.setToolTip(
            "Attach the bound device to the selected WSL distribution"
        )
        self._attach_btn.clicked.connect(self._attach_selected)
        action_bar.addWidget(self._attach_btn)

        self._auto_attach_btn = PushButton(FluentIcon.PIN, "Auto-Attach")
        self._auto_attach_btn.setToolTip(
            "Automatically re-attach device to WSL whenever it is disconnected or re-plugged"
        )
        self._auto_attach_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._auto_attach_btn.clicked.connect(self._toggle_auto_attach)
        action_bar.addWidget(self._auto_attach_btn)

        self._detach_btn = PushButton(FluentIcon.REMOVE, "Detach")
        self._detach_btn.setToolTip("Detach device from WSL")
        self._detach_btn.clicked.connect(self._detach_selected)
        action_bar.addWidget(self._detach_btn)

        self._unbind_btn = PushButton(FluentIcon.UNPIN, "Unbind")
        self._unbind_btn.setToolTip("Unbind selected device (stop sharing with WSL)")
        self._unbind_btn.clicked.connect(self._unbind_selected)
        action_bar.addWidget(self._unbind_btn)

        action_bar.addStretch()
        self._layout.addLayout(action_bar)

    def refresh(self):
        """Redraw from the latest watcher snapshot and ask for a fresh one."""
        self._on_usb_loaded(self._watcher.devices())
        self._watcher.poll_now()

        # Load WSL distros for combo
        self._progress.setVisible(True)
        wsl_w = ListInstalledWorker(self)
        wsl_w.result.connect(self._on_distros_loaded)
        wsl_w.finished.connect(lambda: self._progress.setVisible(False))
        self._workers.append(wsl_w)
        wsl_w.start()

    def _on_distros_loaded(self, distros):
        self._distros = distros
        self._distro_combo.clear()
        self._distro_combo.addItem("(default)")
        for d in distros:
            self._distro_combo.addItem(d.name)

    def _on_usb_loaded(self, devices):
        self._devices = {dev.busid: dev for dev in devices}
        recent_seen = {d.key: d.last_seen for d in settings_store.data.recent_devices}
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        for row, dev in enumerate(devices):
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(dev.busid))
            self._table.setItem(row, 1, QTableWidgetItem(dev.description))
            state_item = QTableWidgetItem(dev.state)
            # Colour-code state
            state_lower = dev.state.lower()
            display_state = dev.state
            if self._manager.is_active(dev.key):
                if "attached" in state_lower:
                    display_state += " (auto)"
                else:
                    display_state += " (auto-waiting)"
            state_item.setText(display_state)
            if "attached" in state_lower:
                state_item.setForeground(QColor("#107C10"))
            elif "not shared" in state_lower:
                state_item.setForeground(QColor("#CA5010"))
            elif "shared" in state_lower or "bound" in state_lower:
                state_item.setForeground(QColor("#0078D4"))
            self._table.setItem(row, 2, state_item)
            # When this device was last plugged in — identifies a freshly
            # attached device among several with the same name.
            seen = recent_seen.get(dev.key) or recent_seen.get(dev.busid, "")
            when_item = QTableWidgetItem(relative_time(seen))
            when_item.setData(Qt.ItemDataRole.UserRole, seen)
            if seen:
                try:
                    fresh = (
                        datetime.now() - datetime.fromisoformat(seen)
                    ).total_seconds() < 120
                except ValueError:
                    fresh = False
                if fresh:
                    when_item.setForeground(QColor("#B8860B"))
            self._table.setItem(row, 3, when_item)
            # Empty actions column (handled via toolbar)
            self._table.setItem(row, 4, QTableWidgetItem(""))
        self._table.setSortingEnabled(True)
        self._table.resizeRowsToContents()

        # Restore the previously selected device (if it still exists after refresh)
        if self._last_selected_busid:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item and item.text() == self._last_selected_busid:
                    self._table.selectRow(row)
                    break

        self._on_selection_changed()

    def _on_usb_error(self, msg):
        InfoBar.warning(
            title="USB Warning",
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=6000,
            parent=self,
        )

    def _selected_device(self):
        rows = self._table.selectedItems()
        if not rows:
            InfoBar.warning(
                title="No selection",
                content="Please select a USB device from the table first.",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return None
        row = self._table.currentRow()
        busid_item = self._table.item(row, 0)
        if busid_item:
            busid = busid_item.text()
            dev = self._devices.get(busid)
            if dev:
                self._last_selected_busid = dev.busid
                return dev
        return None

    def _selected_distro(self) -> str:
        text = self._distro_combo.currentText()
        if text == "(default)" or not text:
            return ""
        return text

    def _bind_selected(self):
        dev = self._selected_device()
        if not dev:
            return
        force = self._force_check.isChecked()
        worker = BindUsbWorker(dev.busid, force=force, parent=self)
        worker.done.connect(self._on_action_done)
        self._workers.append(worker)
        worker.start()

    def _attach_selected(self):
        dev = self._selected_device()
        if not dev:
            return
        distro = self._selected_distro()
        worker = AttachUsbWorker(dev.busid, distro=distro, parent=self)
        worker.done.connect(self._on_action_done)
        self._workers.append(worker)
        worker.start()

    def _detach_selected(self):
        dev = self._selected_device()
        if not dev:
            return
        # Stop auto-attach first — otherwise it will immediately re-attach.
        if self._manager.is_active(dev.key):
            self._manager.stop(dev.key)
        worker = DetachUsbWorker(dev.busid, parent=self)
        # After detach the device re-enumerates at USB level; delay the refresh
        # so usbipd has time to update its device list before we query it.
        worker.done.connect(
            lambda ok, msg: self._on_action_done(ok, msg, refresh_delay_ms=3000)
        )
        self._workers.append(worker)
        worker.start()

    def _unbind_selected(self):
        dev = self._selected_device()
        if not dev:
            return
        worker = UnbindUsbWorker(dev.busid, parent=self)
        worker.done.connect(self._on_action_done)
        self._workers.append(worker)
        worker.start()

    def _toggle_auto_attach(self):
        dev = self._selected_device()
        if not dev:
            return
        # Stop if already running
        if self._manager.is_active(dev.key):
            self._manager.stop(dev.key)
            return
        # Start auto-attach
        distro = self._selected_distro()
        unplugged = self._unplugged_check.isChecked()
        keep_alive = self._keep_alive_check.isChecked()
        self._manager.start(
            dev.key,
            busid=dev.busid,
            distro=distro,
            unplugged=unplugged,
            description=dev.description,
            keep_alive=keep_alive,
        )

    def _on_auto_attach_started(self, busid: str):
        InfoBar.success(
            title="Auto-Attach",
            content=f"Auto-attach started for device {settings_store.data.display(busid)}.",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )
        # Give the first attach attempt time to change the device state.
        QTimer.singleShot(1500, self._watcher.poll_now)

    def _on_auto_attach_stopped(self, busid: str):
        self._watcher.poll_now()
        InfoBar.info(
            title="Auto-Attach",
            content=f"Auto-attach stopped for device {settings_store.data.display(busid)}.",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_auto_attach_error(self, busid: str, msg: str):
        InfoBar.error(
            title="Auto-Attach Error",
            content=msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=6000,
            parent=self,
        )
        self._watcher.poll_now()

    def _on_selection_changed(self):
        row = self._table.currentRow()
        busid_item = self._table.item(row, 0) if row >= 0 else None
        if not busid_item:
            self._auto_attach_btn.setText("Auto-Attach")
            self._auto_attach_btn.setEnabled(False)
            return
        busid = busid_item.text()
        self._last_selected_busid = busid
        dev = self._devices.get(busid)
        if not dev:
            self._auto_attach_btn.setText("Auto-Attach")
            self._auto_attach_btn.setEnabled(False)
            return
        state_lower = dev.state.lower()
        if self._manager.is_active(dev.key):
            self._auto_attach_btn.setText("Stop Auto-Attach")
            self._auto_attach_btn.setEnabled(True)
        elif "not shared" in state_lower:
            self._auto_attach_btn.setText("Auto-Attach")
            self._auto_attach_btn.setEnabled(False)
        else:
            self._auto_attach_btn.setText("Auto-Attach")
            self._auto_attach_btn.setEnabled(True)

        # Reflect the persisted keep-alive flag without firing the toggle slot
        self._keep_alive_check.blockSignals(True)
        self._keep_alive_check.setChecked(self._manager.is_keep_alive(dev.key))
        self._keep_alive_check.blockSignals(False)

    def _on_keep_alive_toggled(self, checked: bool):
        """Apply a keep-alive change immediately for the selected device."""
        rows = self._table.selectedItems()
        if not rows:
            return
        item = self._table.item(self._table.currentRow(), 0)
        if item is None:
            return
        busid = item.text()
        dev = self._devices.get(busid)
        key = dev.key if dev else busid
        # Only act when the flag actually differs from the persisted state.
        if self._manager.is_keep_alive(key) == checked:
            return
        self._manager.set_keep_alive(
            key,
            checked,
            description=dev.description if dev else "",
            busid=dev.busid if dev else "",
        )
        if self._manager.is_active(key):
            what = "will now survive" if checked else "will no longer survive"
            InfoBar.info(
                title="Auto-Attach",
                content=f"Auto-attach for {busid} {what} app restarts.",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _on_action_done(self, success: bool, msg: str, refresh_delay_ms: int = 0):
        if success:
            InfoBar.success(
                title="Success",
                content=msg,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
        else:
            InfoBar.error(
                title="Error",
                content=msg,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=6000,
                parent=self,
            )
        poll = self._watcher.poll_now
        if refresh_delay_ms > 0:
            QTimer.singleShot(refresh_delay_ms, poll)
        else:
            poll()
