"""Stable hardware identity (hwid) for USB devices.

usbipd bus IDs (``3-4``) name a *port*, not a device: the same stick
re-enumerated on a different port gets a new bus ID, and a different device
can take the old one.  Auto-attach entries and recent-device history need an
identity that survives re-plugging, so the app keys everything on an *hwid*
derived from the Windows device instance path that ``usbipd state`` reports:

    USB\\VID_046D&PID_0836\\0947FA90   -> "046d:0836:0947FA90" (real serial)
    USB\\VID_413C&PID_B06E\\6&306E0096&3&5
                                       -> "vp:413c:b06e"  (location path)

Serials that are really PCI-style location paths (``8&136C542B&0&1``) change
with the port, so they are not usable; those devices fall back to a
vendor+product identity, which is stable but not unique between two identical
devices (Windows itself cannot tell those apart either).
"""

import re

_VIDPID_RE = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")
# "8&136C542B&0&1" / "6&306E0096&3&5": PCI-style location instance paths.
_LOCATION_SERIAL_RE = re.compile(r"^[0-9a-fA-F]+&[0-9a-zA-Z]+(&[0-9a-fA-F]+){1,2}$")


def hwid_from_instance_id(instance_id: str) -> str:
    """Return the stable hwid for a Windows device instance path, or ""."""
    if not instance_id:
        return ""
    match = _VIDPID_RE.search(instance_id)
    if not match:
        return ""
    vid, pid = match.group(1).lower(), match.group(2).lower()
    serial = instance_id[match.end() :].lstrip("\\").strip()
    if not serial or _LOCATION_SERIAL_RE.match(serial):
        return f"vp:{vid}:{pid}"
    return f"{vid}:{pid}:{serial}"
