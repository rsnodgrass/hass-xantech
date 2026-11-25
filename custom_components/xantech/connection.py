"""Helpers for the amplifier connection."""

from __future__ import annotations

import logging
import asyncio
import contextlib
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from pyxantech import AmpControlBase

LOG = logging.getLogger(__name__)


def async_close_amp(amp: AmpControlBase | None) -> None:
    """Close the amp connection and stop any library auto-reconnect.

    Uses ``amp.close()`` when the library has it; older pyxantech releases
    have no close(), so fall back to closing the transport directly.
    """
    if amp is None:
        return
    close = getattr(amp, 'close', None)
    if callable(close):
        try:
            close()
        except Exception:
            LOG.debug('Error closing amplifier connection', exc_info=True)
        return

    protocol = getattr(amp, '_protocol', None)
    if protocol is None:
        return
    if hasattr(protocol, '_closing'):
        protocol._closing = True
    if hasattr(protocol, '_create_connection'):
        # stop the library's auto-reconnect from reopening the port
        protocol._create_connection = None
    _expect_close(protocol)
    if getattr(protocol, '_transport', None) is not None:
        _close_transport(protocol)
        return
    # pyserial-asyncio opens the port, then delivers the transport to the
    # protocol with call_soon. A caller that closes straight away (the config
    # flow's connection test) gets here first, so close once it has arrived.
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.call_soon(_close_transport, protocol)


def link_is_down(amp: object) -> bool:
    """True when a serial client's port is known to be closed.

    pyxantech (0.11.0 and earlier) does not raise when a command finds its port
    closed: it waits a moment for the reconnect and then returns without having sent
    anything. A caller that only looks for an exception takes that for success. The
    library keeps the state of the port in an event; any client without one (the
    network client, a test double) is never reported as down.
    """
    connected = getattr(getattr(amp, '_protocol', None), '_connected', None)
    return isinstance(connected, asyncio.Event) and not connected.is_set()


def _expect_close(protocol: object) -> None:
    """Keep the library from warning about a port that is closed on purpose.

    Its connection_lost logs "Port closed" as a warning. That is right for a cable
    that was pulled, and alarming after every reload or restart of Home Assistant.
    The library's own handling still runs; only that one message is held back, and
    only when the port closed without an error.
    """
    original = getattr(protocol, 'connection_lost', None)
    if not callable(original) or getattr(protocol, '_expected_close', False):
        return

    def connection_lost(exc: Exception | None) -> None:
        if exc is not None:
            original(exc)
            return
        logger = logging.getLogger(type(protocol).__module__)
        level = logger.level
        logger.setLevel(logging.ERROR)
        try:
            original(exc)
        finally:
            logger.setLevel(level)

    with contextlib.suppress(AttributeError):
        protocol.connection_lost = connection_lost  # type: ignore[attr-defined]
        protocol._expected_close = True  # type: ignore[attr-defined]


async def async_socket_reachable(url: str, timeout: float = 3.0) -> bool:
    """False when ``url`` is a socket:// link whose host accepts no connection.

    Opening a socket:// serial link blocks Home Assistant for up to five seconds when
    the bridge is down. Links that are reopened while running are therefore checked
    first, without blocking. Every other kind of port counts as reachable.
    """
    if not url.lower().startswith('socket://'):
        return True
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError:
        return True  # let the serial library report a malformed address
    if not host or not port:
        return True
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError, TimeoutError):
        # closed before the real connection is opened: some bridges take one client
        await asyncio.wait_for(writer.wait_closed(), 1.0)
    return True


def _close_transport(protocol: object) -> None:
    transport = getattr(protocol, '_transport', None)
    if transport is None:
        LOG.debug('Amplifier transport never arrived; nothing to close')
        return
    try:
        transport.close()
    except Exception:
        LOG.debug('Error closing amplifier transport', exc_info=True)
