"""Application-level service that owns all auto-attach sessions.

This is the single source of truth for auto-attach state — the USB page, the
tray icon and startup restoration all talk to this manager instead of
tracking workers themselves.  Responsibilities:

* Spawn/stop :class:`AutoAttachWorker` instances (one per bus ID).
* Persist entries to settings so *keep-alive* sessions survive app restarts.
* On startup, clean up leftover ``usbipd`` children from a previous session
  and re-spawn workers for persisted entries.
* Place **non-persistent** children in a kill-on-close job object so they can
  never orphan; persistent children intentionally outlive the app.
"""

import time

from PyQt6.QtCore import QObject, pyqtSignal

from app.utils.job_object import KillOnCloseJob
from app.utils.process_cleanup import is_process_running, terminate_process
from app.utils.settings import AutoAttachEntry, settings_store
from app.workers.usb_worker import AutoAttachWorker

# Minimum seconds between auto-attach spawn attempts for the same device while
# it stays present but keeps failing, so reconcile never rapid-fires.
RETRY_BACKOFF_S = 15.0


class AutoAttachManager(QObject):
    """Owns every running auto-attach worker.

    Workers and persisted entries are keyed by *device key*: the stable hwid
    when known, else the bus ID (see :attr:`UsbDevice.key`).  Signals carry
    the same key (and message where relevant) so views can react without
    owning any process state themselves.
    """

    started = pyqtSignal(str)  # key
    stopped = pyqtSignal(str)  # key
    error = pyqtSignal(str, str)  # key, message
    entryChanged = pyqtSignal(str)  # key — persisted data updated
    countChanged = pyqtSignal(int)  # number of active workers

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: dict[str, AutoAttachWorker] = {}
        # Worker currently being stopped (its exit signal is still expected).
        self._stopping: dict[str, AutoAttachWorker] = {}
        # Workers replaced by set_keep_alive; awaited at shutdown.
        self._retiring: list[AutoAttachWorker] = []
        # Keys the user explicitly stopped (entry should be removed).
        self._user_stopped: set[str] = set()
        # Keys whose current spawn/stop was a direct user command; the
        # tray consumes these flags to decide whether a balloon must bypass
        # the anti-spam cooldown.
        self._user_started_pending: set[str] = set()
        self._user_stop_flags: set[str] = set()
        # Last spawn attempt per key, for retry backoff during reconcile.
        self._last_attempt: dict[str, float] = {}
        # Job that guarantees non-persistent children die with the app.
        self._job = KillOnCloseJob.create()
        self._quitting = False

    # ── Queries ──────────────────────────────────────────────────────────

    def is_active(self, key: str) -> bool:
        return key in self._workers

    def active_keys(self) -> list[str]:
        """Active device keys (hwid when known, else bus ID)."""
        return sorted(self._workers)

    def active_count(self) -> int:
        return len(self._workers)

    def is_keep_alive(self, key: str) -> bool:
        entry = settings_store.data.entry(key)
        return bool(entry and entry.keep_alive)

    def consume_started_by_user(self, key: str) -> bool:
        """True once per user-initiated start; reconcile re-spawns return False."""
        if key in self._user_started_pending:
            self._user_started_pending.discard(key)
            return True
        return False

    def consume_stopped_by_user(self, key: str) -> bool:
        """True once per user-initiated stop; natural exits return False."""
        if key in self._user_stop_flags:
            self._user_stop_flags.discard(key)
            return True
        return False

    def keep_alive_keys(self) -> list[str]:
        """Persisted (keep-alive) device keys."""
        return sorted(
            key for key, e in settings_store.data.auto_attach.items() if e.keep_alive
        )

    # ── Commands ─────────────────────────────────────────────────────────

    def start(
        self,
        key: str,
        busid: str = "",
        distro: str = "",
        unplugged: bool = False,
        description: str = "",
        keep_alive: bool = False,
    ) -> bool:
        """Start auto-attach for the device identified by *key*.

        *key* is the stable identity (hwid when known, else bus ID); *busid*
        is the current usbipd handle (defaults to *key* for bus-ID-keyed
        devices).  Returns *False* if already running.
        """
        if key in self._workers:
            return False
        busid = busid or key

        self._user_stopped.discard(key)
        # start() is only reached from user commands (USB page, tray);
        # reconcile/restore spawn via _spawn_worker directly.
        self._user_started_pending.add(key)
        self._user_stop_flags.discard(key)
        # A worker still shutting down from a previous stop() is superseded;
        # its exit signal becomes stale and must not touch the new worker.
        # Keep a reference so shutdown still waits for its thread to finish.
        superseded = self._stopping.pop(key, None)
        if superseded is not None:
            self._retiring.append(superseded)
        entry = settings_store.data.entry(key)
        if entry is None:
            entry = AutoAttachEntry(
                busid=busid,
                distro=distro,
                unplugged=unplugged,
                keep_alive=keep_alive,
                description=description,
                hwid=key if key != busid else "",
            )
            settings_store.data.set_entry(entry)
        else:
            # Re-starting an existing (possibly failed) entry refreshes options.
            entry.busid = busid
            entry.distro = distro
            entry.unplugged = unplugged
            entry.keep_alive = keep_alive
            if description:
                entry.description = description
        settings_store.save()

        self._spawn_worker(entry)
        return True

    def _spawn_worker(self, entry: AutoAttachEntry) -> None:
        """Create and start a worker for *entry* (assumes none is running)."""
        key = entry.key
        # A leftover usbipd child from a previous session would fight the new
        # one over the same device — remove it first.
        self._cleanup_leftover(key)

        worker = AutoAttachWorker(
            entry.busid,
            key=key,
            distro=entry.distro,
            unplugged=entry.unplugged,
            job=None if entry.keep_alive else self._job,
            parent=self,
        )
        worker.started.connect(self._on_worker_started)
        worker.stopped.connect(self._on_worker_stopped)
        worker.error.connect(self._on_worker_error)
        self._workers[key] = worker
        self._last_attempt[key] = time.monotonic()
        worker.start()

    # ── Reconciliation ───────────────────────────────────────────────────

    def reconcile(self, devices: list) -> None:
        """Keep desired state and reality in sync after a fresh snapshot.

        Called whenever a fresh ``usbipd list`` snapshot is available:

        * Legacy entries keyed only by bus ID are **re-keyed** to the device's
          stable hwid as soon as the device is visible.
        * When a device re-enumerates on a different port (bus ID changed but
          hwid the same), the entry's bus ID is refreshed and a running worker
          is re-spawned — its old child process waits on a dead bus ID and
          would never fire again.
        * A persisted entry whose worker died (device was unplugged, attach
          failed, or the child exited) is re-spawned as soon as the device is
          visible again — the auto-attach list is a *desired state*, not a
          one-shot action.

        *unplugged* entries are spawned regardless of visibility: usbipd
        itself waits for the device to appear.
        """
        if self._quitting:
            return
        by_key = {d.key: d for d in devices}
        # A device can only be attached while it is Shared or already Attached;
        # "Not shared" attempts would just fail (and notify) on every pass.
        attachable = {d.key for d in devices if d.state in ("Shared", "Attached")}
        now = time.monotonic()
        resaved = False

        for entry_key, entry in list(settings_store.data.auto_attach.items()):
            # `key` may be re-assigned below when a legacy entry is re-keyed;
            # the rest of the iteration then follows the new identity.
            key = entry_key
            device = by_key.get(key)

            # Migration: entry keyed by a legacy bus ID, but the device is now
            # known by its stable hwid.  Re-key the entry (and any bookkeeping
            # and running worker registered under the old key) in place.
            if device is None:
                candidate = self._legacy_match(key, entry, devices)
                if candidate is not None:
                    settings_store.data.auto_attach.pop(key, None)
                    entry.hwid = candidate.hwid
                    entry.busid = candidate.busid
                    settings_store.data.set_entry(entry)
                    self._rekey_bookkeeping(key, candidate.key)
                    key = candidate.key
                    device = candidate
                    resaved = True

            if device is not None and entry.busid != device.busid:
                # Device re-enumerated on a new port: refresh the handle.
                entry.busid = device.busid
                resaved = True
                worker = self._workers.get(key)
                if worker is not None:
                    if worker.busid != device.busid:
                        # The child waits on a dead bus ID; replace it.
                        self._respawn_worker(key, entry, worker)
                    continue
                # No worker running: fall through to the spawn checks below.

            if key in self._workers or key in self._stopping:
                continue
            if key in self._user_stopped:
                continue
            if not entry.unplugged and key not in attachable:
                continue
            last = self._last_attempt.get(key)
            if last is not None and now - last < RETRY_BACKOFF_S:
                continue
            self._spawn_worker(entry)

        if resaved:
            settings_store.save()

    def _legacy_match(self, key: str, entry, devices: list):
        """Device that identifies the legacy bus-ID-keyed *entry* by hwid.

        Returns the live device whose bus ID matches the entry's stored one
        but whose stable key differs (and is not claimed by another entry or
        worker), or *None* when no migration applies.
        """
        for candidate in devices:
            if (
                candidate.hwid
                and candidate.busid == entry.busid
                and candidate.key != key
                and candidate.key not in settings_store.data.auto_attach
                and candidate.key not in self._workers
            ):
                return candidate
        return None

    def _rekey_bookkeeping(self, old_key: str, new_key: str) -> None:
        """Move all per-device state from a legacy bus-ID key to the hwid key."""
        for flags in (
            self._user_stopped,
            self._user_started_pending,
            self._user_stop_flags,
        ):
            if old_key in flags:
                flags.discard(old_key)
                flags.add(new_key)
        if old_key in self._last_attempt:
            self._last_attempt[new_key] = self._last_attempt.pop(old_key)
        worker = self._workers.pop(old_key, None)
        if worker is not None:
            worker.key = new_key
            self._workers[new_key] = worker
        stopping = self._stopping.pop(old_key, None)
        if stopping is not None:
            stopping.key = new_key
            self._stopping[new_key] = stopping

    def _respawn_worker(
        self, key: str, entry: AutoAttachEntry, worker: AutoAttachWorker
    ) -> None:
        """Replace a running worker whose device moved to a new bus ID."""
        self._workers.pop(key, None)
        worker.started.disconnect()
        worker.stopped.disconnect()
        worker.error.disconnect()
        worker.stop()
        self._retiring.append(worker)
        self._spawn_worker(entry)

    def stop(self, key: str) -> None:
        """Stop the worker for *key* and forget its desired-state entry.

        This is always a deliberate user action, so the entry is removed and
        :meth:`reconcile` will not re-spawn it.
        """
        self._user_stopped.add(key)
        self._user_stop_flags.add(key)
        self._last_attempt.pop(key, None)
        worker = self._workers.pop(key, None)
        if worker is None:
            settings_store.data.remove_entry(key)
            settings_store.save()
            self._user_stopped.discard(key)
            self.stopped.emit(key)
            self.entryChanged.emit(key)
            return
        # Remember it: its exit signal is expected and must still run the
        # bookkeeping even though it is no longer in _workers.
        self._stopping[key] = worker
        worker.stop()
        # _on_worker_stopped / _on_worker_error finish the bookkeeping.

    def set_keep_alive(
        self, key: str, keep_alive: bool, description: str = "", busid: str = ""
    ) -> None:
        """Toggle persistence for a running (or persisted) session.

        Changing persistence of a *running* worker requires restarting its
        child process: the child must either be inside, or outside, the
        kill-on-close job.  *busid* is the device's current usbipd handle,
        needed only when this creates a brand-new entry for an hwid-keyed
        device (the handle is not derivable from *key*).
        """
        entry = settings_store.data.entry(key)
        if entry is None:
            entry = AutoAttachEntry(
                busid=busid or key,
                hwid="" if busid in ("", key) else key,
                keep_alive=keep_alive,
                description=description,
            )
            settings_store.data.set_entry(entry)
        else:
            entry.keep_alive = keep_alive
            if description and not entry.description:
                entry.description = description
        if not keep_alive and key not in self._workers:
            # Nothing running and not persisted — no reason to keep the entry.
            settings_store.data.remove_entry(key)
        settings_store.save()
        self.entryChanged.emit(key)

        worker = self._workers.pop(key, None)
        if worker is not None and not self._quitting:
            distro, unplugged = worker.distro, worker.unplugged
            # Disconnect first: the old child's stopped/error signals must not
            # touch the bookkeeping of the replacement worker.
            worker.started.disconnect()
            worker.stopped.disconnect()
            worker.error.disconnect()
            worker.stop()
            # Keep a reference so shutdown_for_quit can wait for the monitor
            # loop to finish before Qt teardown.
            self._retiring.append(worker)
            # Restart the child under/without the job, preserving options.
            # start() -> _cleanup_leftover() terminates the old child process
            # synchronously before the new one is spawned.
            self.start(
                key,
                busid=entry.busid,
                distro=distro,
                unplugged=unplugged,
                description=entry.description,
                keep_alive=keep_alive,
            )

    # ── Lifecycle ────────────────────────────────────────────────────────

    def restore_persisted(self) -> None:
        """Re-spawn workers for all persisted keep-alive entries."""
        for key, entry in list(settings_store.data.auto_attach.items()):
            if not entry.keep_alive or key in self._workers:
                continue
            self._spawn_worker(entry)

    def shutdown_for_quit(self) -> None:
        """Stop everything that must not survive app exit.

        Keep-alive workers are *detached* — their child processes keep
        running, which is their whole purpose.  Non-persistent workers are
        stopped; the job object is the safety net for any that refuse to die.
        """
        self._quitting = True
        workers = list(self._workers.values())
        self._workers.clear()
        # Workers already being stopped (user clicked Stop shortly before
        # Quit) are still alive; they must be waited on too or Qt aborts at
        # teardown with "QThread: Destroyed while thread is still running".
        stopping = list(self._stopping.values())
        self._stopping.clear()
        retiring = self._retiring
        self._retiring = []
        for worker in workers:
            keep = self.is_keep_alive(worker.key)
            worker.started.disconnect()
            worker.stopped.disconnect()
            worker.error.disconnect()
            if keep:
                # Detach: release the child to keep running after we exit.
                worker.release()
            else:
                worker.stop()
        for worker in stopping:
            try:
                worker.started.disconnect()
                worker.stopped.disconnect()
                worker.error.disconnect()
            except (RuntimeError, TypeError):
                pass  # already disconnected by set_keep_alive / earlier stop
            worker.stop()
        # Wait for the monitor loops to exit; destroying a running QThread
        # during teardown crashes Qt.  Polls are fast (<= POLL_INTERVAL_MS).
        for worker in workers + stopping + retiring:
            worker.wait(3000)
        for key, entry in list(settings_store.data.auto_attach.items()):
            if not entry.keep_alive:
                settings_store.data.remove_entry(key)
        settings_store.save()

    def close(self) -> None:
        """Release the job object (kills any members still in the job)."""
        if self._job:
            self._job.close()
            self._job = None

    # ── Internal ─────────────────────────────────────────────────────────

    def _cleanup_leftover(self, key: str) -> None:
        entry = settings_store.data.entry(key)
        if entry and entry.child_pid:
            if is_process_running(entry.child_pid):
                terminate_process(entry.child_pid)
            entry.child_pid = 0

    def _is_current_signal(self, key: str) -> bool:
        """True if the calling worker is still registered for *key*, or is
        the worker that :meth:`stop` just removed (its ``stopped`` signal is
        expected and must still run the bookkeeping).

        Guards against a stale (already-replaced) worker mutating state for
        the same device.
        """
        sender = self.sender()
        registered = self._workers.get(key)
        if registered is not None:
            # A worker is registered: only its signals count.  (A worker that
            # stop() moved to _stopping may still emit while a replacement
            # spawned by start() is already running — that signal is stale.)
            return registered is sender
        return sender is self._stopping.get(key)

    def _on_worker_started(self, key: str, pid: int) -> None:
        entry = settings_store.data.entry(key)
        if entry is not None:
            entry.child_pid = pid
            settings_store.save()
        self.started.emit(key)
        self.countChanged.emit(self.active_count())

    def _on_worker_stopped(self, key: str) -> None:
        if not self._is_current_signal(key):
            return  # stale signal from a replaced worker
        self._stopping.pop(key, None)
        self._workers.pop(key, None)
        user_stopped = key in self._user_stopped
        self._user_stopped.discard(key)
        self._last_attempt.pop(key, None)
        if user_stopped:
            # Deliberate stop: forget the desired state entirely.
            settings_store.data.remove_entry(key)
            settings_store.save()
        # Otherwise the child exited on its own (e.g. the device was unplugged
        # while it ran).  Keep the entry so reconcile re-attaches on re-plug.
        self.stopped.emit(key)
        self.entryChanged.emit(key)
        self.countChanged.emit(self.active_count())

    def _on_worker_error(self, key: str, msg: str) -> None:
        if not self._is_current_signal(key):
            return  # stale signal from a replaced worker
        self._stopping.pop(key, None)
        self._workers.pop(key, None)
        # No started/stopped balloon will follow this error; drop any pending
        # user-action flags so they don't leak onto a later auto re-spawn.
        self._user_started_pending.discard(key)
        self._user_stop_flags.discard(key)
        if key in self._user_stopped:
            self._user_stopped.discard(key)
            self._last_attempt.pop(key, None)
            settings_store.data.remove_entry(key)
            settings_store.save()
        # Otherwise KEEP the entry: the auto-attach list is a desired state.
        # The device may simply be unplugged right now; reconcile re-spawns
        # this worker (with backoff) as soon as the device reappears.
        self.error.emit(key, msg)
        self.entryChanged.emit(key)
        self.countChanged.emit(self.active_count())
