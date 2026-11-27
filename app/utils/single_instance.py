"""Single-instance enforcement for WSL Commander.

Uses a named ``QLocalServer`` as an advisory lock plus a tiny
``QLocalSocket`` handshake: the first instance owns the server name; any
subsequent launch connects to it, sends ``ACTIVATE`` and exits.  The running
instance reacts by showing/raising its main window.

This prevents double-spawned ``usbipd`` auto-attach children when the app is
started both at logon and manually.
"""

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

_SERVER_NAME = "WSLCommander-SingleInstance"
_ACTIVATE_MSG = b"ACTIVATE"


class SingleInstanceGuard(QObject):
    """Holds the single-instance lock; emits :attr:`activateRequested` for
    secondary launches.

    Usage::

        guard = SingleInstanceGuard()
        if not guard.try_acquire():
            guard.notify_running_instance()
            return  # exit early
        ...
        guard.activateRequested.connect(window.show_and_raise)
        guard.start_listening()
    """

    activateRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server: QLocalServer | None = None

    def try_acquire(self) -> bool:
        """Try to become the primary instance.

        Returns *True* if this process owns the lock (or if locking is not
        possible — better to run twice than to refuse to start).
        """
        # Probe: is another instance already listening?
        probe = QLocalSocket()
        probe.connectToServer(_SERVER_NAME)
        connected = probe.waitForConnected(300)
        probe.disconnectFromServer()
        if connected:
            return False

        # Stale socket from a crashed instance — remove and take over.
        QLocalServer.removeServer(_SERVER_NAME)

        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(_SERVER_NAME):
            # Cannot listen (e.g. permissions): allow running anyway.
            self._server = None
        return True

    def notify_running_instance(self) -> bool:
        """Tell the primary instance to raise its window.  Returns success."""
        sock = QLocalSocket()
        sock.connectToServer(_SERVER_NAME)
        if not sock.waitForConnected(500):
            return False
        sock.write(_ACTIVATE_MSG)
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True

    def start_listening(self) -> None:
        """Begin accepting activate messages (call after the window exists).

        ``try_acquire`` already opened the listener; this re-arms the handler
        so connections are only processed once the window can react.
        """
        if self._server is not None:
            self._server.newConnection.disconnect(self._on_connection)
            self._server.newConnection.connect(self._on_connection)

    # ── Internal ─────────────────────────────────────────────────────────

    def _on_connection(self) -> None:
        if self._server is None:
            return
        conn = self._server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: self._read(conn))
        conn.disconnected.connect(conn.deleteLater)

    def _read(self, conn: QLocalSocket) -> None:
        data = conn.readAll().data()
        if _ACTIVATE_MSG in data:
            self.activateRequested.emit()
        conn.disconnectFromServer()
