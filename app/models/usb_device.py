from dataclasses import dataclass


@dataclass
class UsbDevice:
    busid: str
    description: str
    state: str  # "Not shared", "Shared", "Attached", etc.
    connected: bool = True  # listed under "Connected:" (vs "Persisted:")
    # Stable hardware identity "VID:PID:serial" (or "vp:VID:PID:description"
    # for devices without a usable serial number); "" when unknown.
    hwid: str = ""

    @property
    def key(self) -> str:
        """Stable identity for this device: hwid when known, else busid.

        Bus IDs are per-port and change when a device re-enumerates on a
        different port, so they are only ever used as the ephemeral handle
        passed to usbipd — never as the stored identity of a device.
        """
        return self.hwid or self.busid
