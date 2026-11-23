"""Display and redaction helpers for serial port values."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from pyxantech.ports import redact_port_url

SERIAL_PROXY_SCHEME = 'esphome-hass'


def port_label(port: str) -> str:
    """Return a short, credential-free name for a port, for titles and logs.

    A serial proxy URL (``esphome-hass://esphome/<entry_id>?port_name=...``)
    is shown as its port name, e.g. ``RS-232 Port 1``.
    """
    parts = urlsplit(port)
    if parts.scheme == SERIAL_PROXY_SCHEME:
        port_names = parse_qs(parts.query).get('port_name')
        if port_names:
            return port_names[0]
    redacted: str = redact_port_url(port)
    return redacted
