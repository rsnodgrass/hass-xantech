"""Tests for Xantech coordinator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.core import HomeAssistant
import pytest

from custom_components.xantech.coordinator import XantechCoordinator


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_amp: MagicMock) -> XantechCoordinator:
    """Create a coordinator for testing."""
    return XantechCoordinator(
        hass=hass,
        amp=mock_amp,
        amp_name='test_amp',
        zone_ids=[11, 12, 13],
        scan_interval=30,
    )


async def test_coordinator_update_success(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test successful coordinator data update."""
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data is not None
    assert 11 in coordinator.data
    assert coordinator.data[11]['power'] is True
    assert coordinator.data[11]['volume'] == 20


async def test_coordinator_update_partial_failure(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test coordinator handles partial zone failures gracefully."""
    # make zone 12 fail
    call_count = 0

    async def zone_status_with_failure(zone_id: int) -> dict | None:
        nonlocal call_count
        call_count += 1
        if zone_id == 12:
            raise Exception('Zone 12 failed')
        return {
            'power': True,
            'volume': 20,
            'mute': False,
            'source': 1,
        }

    mock_amp.zone_status = AsyncMock(side_effect=zone_status_with_failure)

    await coordinator.async_refresh()

    # should still succeed overall, just missing zone 12
    assert coordinator.last_update_success
    assert 11 in coordinator.data
    assert 13 in coordinator.data
    assert 12 not in coordinator.data


async def test_coordinator_set_zone_power(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone power through coordinator."""
    await coordinator.async_set_zone_power(11, True)

    mock_amp.set_power.assert_called_once_with(11, True)


async def test_coordinator_set_zone_volume(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone volume through coordinator."""
    await coordinator.async_set_zone_volume(11, 25)

    mock_amp.set_volume.assert_called_once_with(11, 25)


async def test_coordinator_set_zone_mute(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone mute through coordinator."""
    await coordinator.async_set_zone_mute(11, True)

    mock_amp.set_mute.assert_called_once_with(11, True)


async def test_coordinator_set_zone_source(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test setting zone source through coordinator."""
    await coordinator.async_set_zone_source(11, 2)

    mock_amp.set_source.assert_called_once_with(11, 2)


async def test_coordinator_snapshot_zone(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test getting zone snapshot."""
    snapshot = await coordinator.async_get_zone_snapshot(11)

    mock_amp.zone_status.assert_called_with(11)
    assert snapshot is not None
    assert snapshot['power'] is True


async def test_coordinator_restore_zone(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test restoring zone from snapshot."""
    snapshot = {'power': True, 'volume': 30, 'mute': False, 'source': 2}
    await coordinator.async_restore_zone(snapshot)

    mock_amp.restore_zone.assert_called_once_with(snapshot)


async def test_coordinator_set_power_error_handling(
    coordinator: XantechCoordinator,
    mock_amp: MagicMock,
) -> None:
    """Test error handling when setting zone power fails."""
    mock_amp.set_power.side_effect = Exception('Connection lost')

    with pytest.raises(Exception, match='Connection lost'):
        await coordinator.async_set_zone_power(11, True)
