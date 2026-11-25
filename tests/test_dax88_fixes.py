"""Tests for the DAX88 fixes: tone range, closing the port, availability."""

from __future__ import annotations

import logging
import asyncio
from collections.abc import AsyncGenerator
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.xantech.audio_controls import (
    CONTROL_BALANCE,
    CONTROL_BASS,
    CONTROL_TREBLE,
    apply_library_limit_overrides,
    get_control_range,
)
from custom_components.xantech.connection import async_close_amp
from custom_components.xantech.coordinator import XantechCoordinator

# --- tone range -------------------------------------------------------------


def test_dax88_tone_range_is_signed_db() -> None:
    """DAX88 bass/treble show -12..+12 dB and map to raw 0..24 (12 = flat)."""
    for control in (CONTROL_BASS, CONTROL_TREBLE):
        rng = get_control_range('dax88', control)
        assert (rng.native_min, rng.native_max) == (-12, 12)
        assert rng.unit == 'dB'
        assert rng.to_raw(0) == 12
        assert rng.to_raw(12) == 24
        assert rng.to_raw(-12) == 0
        assert rng.to_raw(99) == 24  # clamped
        assert rng.from_raw(12) == 0
        assert rng.from_raw(24) == 12


def test_dax88_balance_is_centred() -> None:
    """DAX88 balance shows -10..+10 and maps to raw 0..20 (10 = centre)."""
    rng = get_control_range('dax88', CONTROL_BALANCE)
    assert (rng.native_min, rng.native_max) == (-10, 10)
    assert rng.to_raw(0) == 10
    assert rng.from_raw(20) == 10


def test_other_amps_keep_raw_range() -> None:
    """Amps without a known neutral point keep the unsigned library range."""
    rng = get_control_range('xantech8', CONTROL_BASS)
    assert rng.offset == 0
    assert rng.native_min == 0


def test_library_limits_raised_for_dax88() -> None:
    """Older pyxantech clamps DAX88 bass at 14; the override lifts it to 24."""
    from pyxantech import _set_bass_cmd
    from pyxantech.config import DEVICE_CONFIG

    original = dict(DEVICE_CONFIG['dax88'])
    try:
        DEVICE_CONFIG['dax88']['max_bass'] = 14
        DEVICE_CONFIG['dax88']['max_treble'] = 14
        apply_library_limit_overrides('dax88')
        assert DEVICE_CONFIG['dax88']['max_bass'] == 24
        assert DEVICE_CONFIG['dax88']['max_treble'] == 24
        assert _set_bass_cmd('dax88', 11, 24) == b'<11BS24\r'
    finally:
        DEVICE_CONFIG['dax88'].clear()
        DEVICE_CONFIG['dax88'].update(original)


# --- closing the port ----------------------------------------------------------


def test_close_uses_library_close_when_available() -> None:
    """A library with close() is closed through it."""
    amp = MagicMock()
    async_close_amp(amp)
    amp.close.assert_called_once()


def test_close_falls_back_to_transport() -> None:
    """Older libraries: close the transport and disable auto-reconnect."""
    transport = MagicMock()
    protocol = MagicMock(spec=['_transport', '_create_connection'])
    protocol._transport = transport
    protocol._create_connection = AsyncMock()
    amp = MagicMock(spec=['_protocol'])
    amp._protocol = protocol

    async_close_amp(amp)

    transport.close.assert_called_once()
    assert protocol._create_connection is None


def test_close_none_is_noop() -> None:
    """Closing when no amp was created does nothing."""
    async_close_amp(None)


async def test_close_waits_for_late_transport() -> None:
    """Older libraries: a close before connection_made still closes the port.

    A serial library may deliver the transport a loop tick after opening the
    port (pyserial-asyncio did); the config flow closes its test connection
    before that tick.
    """
    protocol = MagicMock(spec=['_transport', '_create_connection'])
    protocol._transport = None
    amp = MagicMock(spec=['_protocol'])
    amp._protocol = protocol

    async_close_amp(amp)
    transport = MagicMock()
    protocol._transport = transport  # connection_made runs on the next tick
    await asyncio.sleep(0)

    transport.close.assert_called_once()


class _WarningProtocol:
    """Like the library's protocol: connection_lost warns, whatever the reason."""

    def __init__(self) -> None:
        self._transport: Any = MagicMock()
        self._create_connection: Any = object()
        self.lost: list[Exception | None] = []

    def connection_lost(self, exc: Exception | None) -> None:
        logging.getLogger(type(self).__module__).warning('Port closed: exc=%s', exc)
        self.lost.append(exc)


def test_a_deliberate_close_is_not_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Seen on a real system: every reload logged the library's "Port closed"."""
    protocol = _WarningProtocol()
    amp = MagicMock(spec=['_protocol'])
    amp._protocol = protocol
    logger = logging.getLogger(_WarningProtocol.__module__)
    level = logger.level

    async_close_amp(amp)
    async_close_amp(amp)  # twice must not wrap twice
    protocol._transport.close.assert_called()
    with caplog.at_level(logging.DEBUG):
        protocol.connection_lost(None)  # what the transport does once it has closed
    assert protocol.lost == [None]  # the library's own handling still ran
    assert 'Port closed' not in caplog.text
    assert logger.level == level

    # a close with an error is still reported
    with caplog.at_level(logging.DEBUG):
        protocol.connection_lost(OSError('device removed'))
    assert 'Port closed: exc=device removed' in caplog.text


@pytest.mark.skipif(not hasattr(os, 'openpty'), reason='needs a pseudo-terminal')
async def test_flow_style_open_close_releases_real_port() -> None:
    """Open a real (pseudo) serial port and close at once, as the flow does.

    serialx locks serial ports exclusively, so a leaked handle makes the entry's
    own setup fail because the port is still held.
    """
    from pyxantech import async_get_amp_controller
    import serialx

    main_fd, sub_fd = os.openpty()
    path = os.ttyname(sub_fd)
    try:
        amp = await async_get_amp_controller(
            'dax88', path, asyncio.get_running_loop()
        )
        async_close_amp(amp)
        for _ in range(3):
            await asyncio.sleep(0)

        port = serialx.serial_for_url(path, baudrate=9600, exclusive=True)
        port.open()  # raises if the first connection still holds the port
        port.close()
    finally:
        os.close(main_fd)
        os.close(sub_fd)


# --- availability -------------------------------------------------------------


@pytest.fixture
async def coordinator(
    hass: HomeAssistant, mock_amp: MagicMock
) -> AsyncGenerator[XantechCoordinator]:
    """Create a coordinator for testing."""
    coord = XantechCoordinator(
        hass=hass,
        amp=mock_amp,
        amp_name='test_amp',
        zone_ids=[11, 12],
        scan_interval=30,
    )
    yield coord
    await coord.async_shutdown()


async def test_no_zone_answers_marks_update_failed(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """If no zone answers (port down), the update fails -> entities unavailable."""
    mock_amp.zone_status = AsyncMock(return_value=None)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success


async def test_all_zones_raising_marks_update_failed(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Timeouts on every zone also fail the update."""
    mock_amp.zone_status = AsyncMock(side_effect=TimeoutError)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success


async def test_recovers_when_amp_answers_again(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """After a failed poll, a good poll makes the coordinator healthy again."""
    good = mock_amp.zone_status
    mock_amp.zone_status = AsyncMock(return_value=None)
    await coordinator.async_refresh()
    assert not coordinator.last_update_success

    mock_amp.zone_status = good
    await coordinator.async_refresh()
    assert coordinator.last_update_success
    assert 11 in coordinator.data
