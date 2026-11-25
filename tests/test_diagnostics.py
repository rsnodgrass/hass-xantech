"""Tests for Xantech diagnostics."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.xantech.const import (
    DOMAIN,
)
from custom_components.xantech.coordinator import XantechCoordinator
from custom_components.xantech.diagnostics import (
    _redact_port,
    async_get_config_entry_diagnostics,
)


@pytest.fixture
def mock_runtime_data(mock_amp: MagicMock, hass: HomeAssistant) -> MagicMock:
    """Create mock runtime data."""
    coordinator = XantechCoordinator(
        hass=hass,
        amp=mock_amp,
        amp_name='test_amp',
        zone_ids=[11, 12],
        scan_interval=30,
    )
    coordinator.data = {
        11: {'power': True, 'volume': 20, 'mute': False, 'source': 1},
        12: {'power': False, 'volume': 0, 'mute': True, 'source': 2},
    }

    runtime_data = MagicMock()
    runtime_data.coordinator = coordinator
    runtime_data.amp = mock_amp
    runtime_data.sources = {1: 'Sonos', 2: 'Turntable'}
    return runtime_data


@pytest.fixture
def mock_entry_for_diagnostics(
    mock_runtime_data: MagicMock,
    config_entry_data: dict[str, Any],
) -> MagicMock:
    """Create mock config entry for diagnostics."""
    entry = MagicMock()
    entry.entry_id = 'test_entry_id'
    entry.version = 1
    entry.domain = DOMAIN
    entry.title = 'Xantech Multi-Zone Audio (/dev/ttyUSB0)'
    entry.data = config_entry_data
    entry.options = {'scan_interval': 30}
    entry.runtime_data = mock_runtime_data
    return entry


async def test_diagnostics_output(
    hass: HomeAssistant,
    mock_entry_for_diagnostics: MagicMock,
) -> None:
    """Test diagnostics output structure."""
    result = await async_get_config_entry_diagnostics(hass, mock_entry_for_diagnostics)

    assert 'config_entry' in result
    assert 'coordinator' in result
    assert 'zone_names' in result
    assert 'source_names' in result
    assert 'zone_statuses' in result

    # check config entry data
    assert result['config_entry']['entry_id'] == 'test_entry_id'
    assert result['config_entry']['domain'] == DOMAIN

    # check coordinator info
    assert result['coordinator']['zone_ids'] == [11, 12]
    assert result['coordinator']['update_interval_seconds'] == 30

    # check zone statuses
    assert '11' in result['zone_statuses']
    assert result['zone_statuses']['11']['power'] is True


async def test_diagnostics_hold_no_names(
    hass: HomeAssistant,
    mock_entry_for_diagnostics: MagicMock,
) -> None:
    """The file is made to be passed on: no room, input or device name in it."""
    entry = mock_entry_for_diagnostics
    entry.title = 'Xantech Multi-Zone Audio (DAX 88_AB12)'
    entry.data = {
        **entry.data,
        'zones': {11: {'name': 'Nursery'}, 12: {'name': 'Study'}},
        'sources': {1: {'name': 'Turntable'}},
    }
    result = await async_get_config_entry_diagnostics(hass, entry)
    text = str(result)
    for private in ('Nursery', 'Study', 'Turntable', 'AB12'):
        assert private not in text
    # which zones and inputs are set up is still there
    assert set(result['zone_names']) == {'11', '12'}
    assert set(result['source_names']) == {'1'}


def test_redact_port_serial() -> None:
    """Test port redaction preserves serial device paths."""
    assert _redact_port('/dev/ttyUSB0') == '/dev/ttyUSB0'
    assert (
        _redact_port('/dev/serial/by-id/usb-device') == '/dev/serial/by-id/usb-device'
    )


def test_redact_port_socket() -> None:
    """Test port redaction hides IP addresses."""
    assert _redact_port('socket://192.168.1.100:888/') == 'socket://[REDACTED]:[PORT]/'
    assert _redact_port('socket://192.0.2.50:8888/') == 'socket://[REDACTED]:[PORT]/'
