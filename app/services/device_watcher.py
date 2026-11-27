"""Background watcher that tracks live USB attach state.

Polls ``usbipd list`` on a timer and turns the raw device table into
high-level events the rest of the app cares about:

* a device became **Attached** (auto-attach fired, or it was attached by hand)
* a device **detached** (unplugged or released)
* a device was **plugged in** (newly appears in the list) and should appear
  at the top of the tray "recent devices" list with a fresh timestamp

The first successful poll only *primes* the state so that devices already
present when the app starts do not produce a flood of notifications, and are
remembered with an unknown connection time.

Polling reuses :class:`ListUsbWorker` so the heavy ``usbipd`` call always runs
off the GUI thread.  A failed poll does *not* stop the loop (a transient
``usbipd`` failure must not permanently kill the reconcile/tray feed); the
error is reported once per failure streak instead.
"""

from datetime import datetime

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from app.models.usb_device import UsbDevice
from app.utils.settings import settings_store
from app.workers.usb_worker import ListUsbWorker

# How often to refresh attach state.  Long enough to be unobtrusive, short
# enough that attach/detach notifications feel immediate.
POLL_INTERVAL_MS = 6000

# Threads that refused to finish by app shutdown; kept referenced so Qt never
# destroys a running QThread during teardown (see DeviceWatcher.stop()).
_abandoned_workers: list = []


def is_attached(device: UsbDevice) -> bool:
    return device.state.strip().lower() == "attached"


class DeviceWatcher(QObject):
    """Emits attach/detach events by polling ``usbipd list``."""

    attached = pyqtSignal(str, str)  # key, description
    detached = pyqtSignal(str, str)  # key, description
    devicesUpdated = pyqtSignal(list)  # full fresh list[UsbDevice] each poll
    error = pyqtSignal(str)  # usbipd listing failed

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last: dict[str, UsbDevice] = {}
        self._primed = False
        self._failing = False
        self._worker: ListUsbWorker | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # ── Control ──────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        # Kick an immediate first poll so the tray is fresh on open.
        self._poll()

    def stop(self) -> None:
        self._timer.stop()
        # Wait for any in-flight poll; destroying a running QThread at app
        # teardown crashes Qt.
        worker = self._worker
        if worker is not None:
            self._worker = None
            worker.wait(3000)
            if worker.isRunning():
                # usbipd hung longer than our wait: keep the thread object
                # alive (re-parented, unreferenced by Qt) instead of letting
                # Qt destroy a running QThread at teardown.  The process is
                # exiting anyway, so the leak is harmless.
                worker.setParent(None)
                _abandoned_workers.append(worker)

    # ── Queries (latest poll snapshot) ───────────────────────────────────

    def devices(self) -> list[UsbDevice]:
        """Latest poll snapshot (empty until the first poll completes)."""
        return list(self._last.values())

    def poll_now(self) -> None:
        """Request an immediate poll (e.g. right after a usbipd action)."""
        self._poll()

    def is_device_attached(self, key: str) -> bool:
        return self._last.get(key) is not None and is_attached(self._last[key])

    # ── Polling ──────────────────────────────────────────────────────────

    def _poll(self) -> None:
        # Skip if a poll is still in flight (slow machine / hung usbipd).
        if self._worker is not None and self._worker.isRunning():
            return
        worker = ListUsbWorker(parent=self)
        worker.result.connect(self._on_result)
        worker.error.connect(self._on_error)
        worker.finished.connect(self._on_finished)
        self._worker = worker
        worker.start()

    def _on_finished(self) -> None:
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        # The worker is parented to the watcher, so it would otherwise leak
        # one QThread object per poll for the whole app lifetime.
        if isinstance(worker, ListUsbWorker):
            worker.deleteLater()

    def _on_error(self, message: str) -> None:
        # Keep the timer running: the poll interval is itself the backoff, and
        # stopping it would permanently kill auto-attach reconciliation and
        # tray notifications for a transient usbipd failure.  Report the
        # failure once per streak so a persistently broken usbipd does not
        # spam the USB page.
        if not self._failing:
            self._failing = True
            self.error.emit(message)

    def _on_result(self, devices: list[UsbDevice]) -> None:
        self._failing = False
        current = {d.key: d for d in devices}
        recents_changed = False

        for key, device in current.items():
            prev = self._last.get(key)
            now_attached = is_attached(device)
            was_attached = prev is not None and is_attached(prev)

            if now_attached and not was_attached and self._primed:
                self.attached.emit(key, device.description)
            elif was_attached and not now_attached and self._primed:
                self.detached.emit(key, device.description)

            # Recent list = *any* USB device, tracked by connection time and
            # keyed by stable identity: a device never seen before was just
            # plugged in (fresh timestamp), and a changed description on a
            # known identity means a different device took its port (re-plug).
            # Devices already present when the app started are remembered
            # with an unknown time.
            if prev is None:
                if self._primed:
                    stamp = datetime.now().isoformat(timespec="seconds")
                else:
                    existing = next(
                        (
                            d
                            for d in settings_store.data.recent_devices
                            if d.key == key or d.busid == device.busid
                        ),
                        None,
                    )
                    if existing is not None:
                        # Known from a previous session: keep its timestamp,
                        # refresh the name, current bus ID, and hwid.
                        changed = False
                        if (
                            device.description
                            and existing.description != device.description
                        ):
                            existing.description = device.description
                            changed = True
                        if existing.busid != device.busid:
                            existing.busid = device.busid
                            changed = True
                        if device.hwid and existing.hwid != device.hwid:
                            existing.hwid = device.hwid
                            changed = True
                        if changed:
                            recents_changed = True
                        continue
                    stamp = ""  # connected before we started watching
                if settings_store.data.record_recent(
                    device.busid,
                    device.description,
                    last_seen=stamp,
                    hwid=device.hwid,
                ):
                    recents_changed = True
            elif prev.description != device.description:
                stamp = datetime.now().isoformat(timespec="seconds")
                if settings_store.data.record_recent(
                    device.busid,
                    device.description,
                    last_seen=stamp,
                    hwid=device.hwid,
                ):
                    recents_changed = True
            elif prev.busid != device.busid:
                # Same identity, new port: keep the timestamp but refresh the
                # handle stored in the recent list.
                if settings_store.data.update_recent_handle(
                    key, device.busid, device.hwid
                ):
                    recents_changed = True

        # A device that was attached and vanished from the list entirely
        # (unplugged) counts as detached too.
        if self._primed:
            for key, prev in self._last.items():
                if key not in current and is_attached(prev):
                    self.detached.emit(key, prev.description)

        if recents_changed:
            settings_store.save()

        self._last = current
        self._primed = True
        # Let the auto-attach manager reconcile desired state with reality
        # (e.g. respawn a worker for a device that was just plugged in).
        self.devicesUpdated.emit(list(devices))
