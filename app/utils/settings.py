"""Persistent application settings stored as JSON.

The file lives in ``%APPDATA%\\WSLCommander\\settings.json`` (per-user, no
admin rights needed, survives app upgrades).  Writes are atomic — content is
written to a temporary file in the same directory and then moved over the
target with ``os.replace`` — so a crash mid-write can never leave a
half-written settings file behind.

A corrupt or unreadable settings file is never fatal: it is replaced with
fresh defaults so the app always starts.
"""

import contextlib
import dataclasses
import json
import os
import tempfile
from datetime import datetime

from PyQt6.QtCore import QObject, pyqtSignal

APP_DIR_NAME = "WSLCommander"
SETTINGS_FILE_NAME = "settings.json"


def settings_dir() -> str:
    """Return (and create) the per-user settings directory."""
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(settings_dir(), SETTINGS_FILE_NAME)


@dataclasses.dataclass
class AutoAttachEntry:
    """A persisted auto-attach request.

    Entries are keyed by their stable identity: ``hwid`` when known, else the
    (ephemeral) ``busid``.  ``busid`` is refreshed from each device snapshot
    and is only ever the handle passed to usbipd, so an entry survives the
    device re-enumerating on a different port.

    ``keep_alive`` entries are re-spawned at app start and survive a graceful
    app quit; the ``usbipd`` child process of a running entry is recorded in
    ``child_pid`` so a leftover process from a previous session can be cleaned
    up before a fresh worker is spawned.
    """

    busid: str
    distro: str = ""
    unplugged: bool = False
    keep_alive: bool = False
    description: str = ""
    child_pid: int = 0
    hwid: str = ""

    @property
    def key(self) -> str:
        return self.hwid or self.busid

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class RecentDevice:
    """A device that was recently connected (tray quick-attach list)."""

    busid: str
    description: str = ""
    last_seen: str = ""  # ISO timestamp of the last connection event
    hwid: str = ""

    @property
    def key(self) -> str:
        return self.hwid or self.busid

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


MAX_RECENT_DEVICES = 4


@dataclasses.dataclass
class Notifications:
    """Which tray balloon notifications the user wants to receive."""

    started: bool = True  # an auto-attach session was started
    stopped: bool = True  # an auto-attach session was stopped
    error: bool = True  # an auto-attach attempt failed
    attached: bool = True  # a device became attached to WSL
    detached: bool = True  # a device detached from WSL


@dataclasses.dataclass
class Settings:
    close_to_tray: bool = False
    start_at_login: bool = False
    auto_attach: dict[str, AutoAttachEntry] = dataclasses.field(default_factory=dict)
    recent_devices: list[RecentDevice] = dataclasses.field(default_factory=list)
    notifications: Notifications = dataclasses.field(default_factory=Notifications)

    def entry(self, key: str) -> AutoAttachEntry | None:
        return self.auto_attach.get(key)

    def set_entry(self, entry: AutoAttachEntry) -> None:
        self.auto_attach[entry.key] = entry

    def remove_entry(self, key: str) -> None:
        self.auto_attach.pop(key, None)

    def display(self, key: str) -> str:
        """Human-friendly label for a device *key* (its bus ID, not raw hwid).

        Falls back to the key itself when no current handle is known.  Shared
        by the tray and the USB page so both show the same handle for a device.
        """
        entry = self.entry(key)
        if entry is not None and entry.busid:
            return entry.busid
        for device in self.recent_devices:
            if device.key == key:
                return device.busid
        return key

    def update_recent_handle(self, key: str, busid: str, hwid: str = "") -> bool:
        """Refresh the stored bus ID of recent entry *key* without touching
        its timestamp or order.  Returns True if anything changed."""
        for device in self.recent_devices:
            if device.key == key:
                changed = False
                if device.busid != busid:
                    device.busid = busid
                    changed = True
                if hwid and device.hwid != hwid:
                    device.hwid = hwid
                    changed = True
                return changed
        return False

    def record_recent(
        self,
        busid: str,
        description: str,
        last_seen: str | None = None,
        hwid: str = "",
    ) -> bool:
        """Mark a device as most-recently connected. Returns True if changed.

        *last_seen* overrides the connection timestamp (``""`` = connected
        before the app started watching, so the time is unknown).  Devices are
        matched by stable identity (hwid when known, else busid), so the same
        device re-enumerated on a new port updates its entry instead of
        creating a duplicate.
        """
        key = hwid or busid
        now = (
            datetime.now().isoformat(timespec="seconds")
            if last_seen is None
            else last_seen
        )
        for device in self.recent_devices:
            if device.key == key:
                changed = (
                    device.description != description
                    or device.last_seen != now
                    or device.busid != busid
                )
                device.description = description or device.description
                device.last_seen = now
                device.busid = busid
                if hwid:
                    device.hwid = hwid
                self.recent_devices.remove(device)
                self.recent_devices.insert(0, device)
                self.recent_devices = self.recent_devices[:MAX_RECENT_DEVICES]
                return changed
        self.recent_devices.insert(
            0,
            RecentDevice(
                busid=busid, description=description, last_seen=now, hwid=hwid
            ),
        )
        self.recent_devices = self.recent_devices[:MAX_RECENT_DEVICES]
        return True


class SettingsStore(QObject):
    """Loads/saves :class:`Settings` and notifies listeners on change."""

    changed = pyqtSignal()

    def __init__(self, path: str | None = None):
        super().__init__()
        self._path = path or settings_path()
        self._data = self._load()

    @property
    def data(self) -> Settings:
        return self._data

    # ── Persistence ──────────────────────────────────────────────────────

    def _load(self) -> Settings:
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
            return Settings()
        if not isinstance(raw, dict):
            # Valid JSON but not an object (e.g. "[]" from a bad edit):
            # treat like a corrupt file and start from defaults.
            return Settings()

        settings = Settings(
            close_to_tray=bool(raw.get("close_to_tray", False)),
            start_at_login=bool(raw.get("start_at_login", False)),
        )
        entries = raw.get("auto_attach")
        if isinstance(entries, dict):
            for busid, value in entries.items():
                if not isinstance(value, dict):
                    continue
                known = {f.name for f in dataclasses.fields(AutoAttachEntry)}
                kwargs = {k: v for k, v in value.items() if k in known}
                kwargs["busid"] = str(busid)
                # Coerce scalar fields so a hand-edited file cannot crash
                # later consumers with TypeError.
                for field in ("distro", "description", "hwid"):
                    if field in kwargs:
                        kwargs[field] = str(kwargs[field])
                for field in ("unplugged", "keep_alive"):
                    if field in kwargs:
                        kwargs[field] = bool(kwargs[field])
                if "child_pid" in kwargs:
                    try:
                        kwargs["child_pid"] = int(kwargs["child_pid"])
                    except (TypeError, ValueError):
                        kwargs["child_pid"] = 0
                try:
                    entry = AutoAttachEntry(**kwargs)
                except (TypeError, ValueError):
                    continue
                settings.set_entry(entry)
        recents = raw.get("recent_devices")
        if isinstance(recents, list):
            known = {f.name for f in dataclasses.fields(RecentDevice)}
            for value in recents:
                if not isinstance(value, dict) or "busid" not in value:
                    continue
                kwargs = {k: v for k, v in value.items() if k in known}
                kwargs["busid"] = str(kwargs["busid"])
                if "last_seen" in kwargs:
                    kwargs["last_seen"] = str(kwargs["last_seen"])
                if "description" in kwargs:
                    kwargs["description"] = str(kwargs["description"])
                if "hwid" in kwargs:
                    kwargs["hwid"] = str(kwargs["hwid"])
                try:
                    settings.recent_devices.append(RecentDevice(**kwargs))
                except TypeError:
                    continue
            settings.recent_devices = settings.recent_devices[:MAX_RECENT_DEVICES]
        notif = raw.get("notifications")
        if isinstance(notif, dict):
            known = {f.name for f in dataclasses.fields(Notifications)}
            kwargs = {k: bool(v) for k, v in notif.items() if k in known}
            settings.notifications = Notifications(**kwargs)
        return settings

    def save(self) -> None:
        """Atomically persist the current settings and emit :attr:`changed`."""
        payload = {
            "close_to_tray": self._data.close_to_tray,
            "start_at_login": self._data.start_at_login,
            "auto_attach": {
                busid: entry.to_dict()
                for busid, entry in self._data.auto_attach.items()
            },
            "recent_devices": [d.to_dict() for d in self._data.recent_devices],
            "notifications": dataclasses.asdict(self._data.notifications),
        }
        directory = os.path.dirname(self._path)
        fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix=".settings-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self._path)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        self.changed.emit()


# Process-wide singleton used across the app.
settings_store = SettingsStore()
