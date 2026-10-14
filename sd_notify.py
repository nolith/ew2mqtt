"""Minimal sd_notify(3) client built on the standard library.

Every call is a no-op when NOTIFY_SOCKET is unset, so the bridge runs
unchanged outside systemd.
"""

import logging
import os
import socket

log = logging.getLogger()


def notify(*fields):
    """Send fields such as "READY=1" to systemd; return True if sent."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        return False
    if address.startswith("@"):
        address = "\0" + address[1:]  # abstract namespace socket
    elif not address.startswith("/"):
        log.warning("Unsupported NOTIFY_SOCKET address: %s", address)
        return False

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(address)
            sock.sendall("\n".join(fields).encode())
    except OSError as e:
        log.warning("sd_notify failed: %s", e)
        return False
    return True


def watchdog_timeout():
    """Return the systemd watchdog timeout in seconds, or None if disabled."""
    pid = os.environ.get("WATCHDOG_PID")
    if pid and pid != str(os.getpid()):
        return None
    try:
        usec = int(os.environ.get("WATCHDOG_USEC", "0"))
    except ValueError:
        return None
    return usec / 1_000_000 if usec > 0 else None
