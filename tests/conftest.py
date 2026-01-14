"""Fixtures for Xantech integration tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_PORT,
    CONF_SOURCES,
    CONF_ZONES,
    DOMAIN,
)


@pytest.fixture
def mock_amp() -> Generator[MagicMock]:
    """Create a mock amplifier controller."""
    amp = MagicMock()
    amp.zone_status = AsyncMock(
        return_value={
            'power': True,
            'volume': 20,
            'mute': False,
            'source': 1,
        }
    )
    amp.set_power = AsyncMock()
    amp.set_volume = AsyncMock()
    amp.set_mute = AsyncMock()
    amp.set_source = AsyncMock()
    amp.restore_zone = AsyncMock()
    yield amp


@pytest.fixture
def mock_async_get_amp_controller(
    mock_amp: MagicMock,
) -> Generator[AsyncMock]:
    """Mock the async_get_amp_controller function."""
    with patch(
        'custom_components.xantech.config_flow.async_get_amp_controller',
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = mock_amp
        yield mock_get


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Mock setting up a config entry."""
    with patch(
        'custom_components.xantech.async_setup_entry',
        return_value=True,
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def config_entry_data() -> dict[str, Any]:
    """Return standard config entry data."""
    return {
        CONF_PORT: '/dev/ttyUSB0',
        CONF_AMP_TYPE: 'xantech8',
        CONF_ZONES: {
            11: {'name': 'Living Room'},
            12: {'name': 'Kitchen'},
            13: {'name': 'Bedroom'},
        },
        CONF_SOURCES: {
            1: {'name': 'Sonos'},
            2: {'name': 'Turntable'},
            3: {'name': 'TV'},
        },
    }


@pytest.fixture
def config_entry(
    hass: HomeAssistant,
    config_entry_data: dict[str, Any],
) -> Any:
    """Create a mock config entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Xantech Multi-Zone Audio (/dev/ttyUSB0)',
        data=config_entry_data,
        options={},
        unique_id=f'{DOMAIN}_/dev/ttyUSB0',
    )
    entry.add_to_hass(hass)
    return entry
