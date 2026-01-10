"""Tests for Xantech media player entities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.components.media_player import MediaPlayerState
from homeassistant.core import HomeAssistant
import pytest

from custom_components.xantech.const import MAX_VOLUME
from custom_components.xantech.coordinator import XantechCoordinator
from custom_components.xantech.media_player import ZoneMediaPlayer


@pytest.fixture
def coordinator(hass: HomeAssistant, mock_amp: MagicMock) -> XantechCoordinator:
    """Create a coordinator for testing."""
    coord = XantechCoordinator(
        hass=hass,
        amp=mock_amp,
        amp_name='test_amp',
        zone_ids=[11, 12],
        scan_interval=30,
    )
    # simulate successful data fetch
    coord.data = {
        11: {'power': True, 'volume': 20, 'mute': False, 'source': 1},
        12: {'power': False, 'volume': 0, 'mute': True, 'source': 2},
    }
    return coord


@pytest.fixture
def mock_config_entry() -> MagicMock:
    """Create a mock config entry."""
    entry = MagicMock()
    entry.entry_id = 'test_entry'
    return entry


@pytest.fixture
def sources() -> dict[int, str]:
    """Return test sources mapping."""
    return {1: 'Sonos', 2: 'Turntable', 3: 'TV'}


@pytest.fixture
def zone_player(
    coordinator: XantechCoordinator,
    mock_config_entry: MagicMock,
    sources: dict[int, str],
) -> ZoneMediaPlayer:
    """Create a zone media player for testing."""
    return ZoneMediaPlayer(
        coordinator=coordinator,
        entry=mock_config_entry,
        zone_id=11,
        zone_name='Living Room',
        sources=sources,
    )


async def test_zone_player_state_on(zone_player: ZoneMediaPlayer) -> None:
    """Test zone player reports ON state correctly."""
    assert zone_player.state == MediaPlayerState.ON


async def test_zone_player_state_off(
    coordinator: XantechCoordinator,
    mock_config_entry: MagicMock,
    sources: dict[int, str],
) -> None:
    """Test zone player reports OFF state correctly."""
    player = ZoneMediaPlayer(
        coordinator=coordinator,
        entry=mock_config_entry,
        zone_id=12,
        zone_name='Kitchen',
        sources=sources,
    )
    assert player.state == MediaPlayerState.OFF


async def test_zone_player_volume_level(zone_player: ZoneMediaPlayer) -> None:
    """Test zone player volume level calculation."""
    # volume is 20 out of MAX_VOLUME (38)
    expected = 20 / MAX_VOLUME
    assert zone_player.volume_level == pytest.approx(expected)


async def test_zone_player_mute_status(zone_player: ZoneMediaPlayer) -> None:
    """Test zone player mute status."""
    assert zone_player.is_volume_muted is False


async def test_zone_player_source(zone_player: ZoneMediaPlayer) -> None:
    """Test zone player current source."""
    assert zone_player.source == 'Sonos'


async def test_zone_player_source_list(zone_player: ZoneMediaPlayer) -> None:
    """Test zone player source list."""
    # sources should be sorted by id
    assert zone_player.source_list == ['Sonos', 'Turntable', 'TV']


async def test_zone_player_unique_id(zone_player: ZoneMediaPlayer) -> None:
    """Test zone player unique ID format."""
    assert zone_player.unique_id == 'xantech_test_amp_zone_11'


async def test_zone_player_icon_on(zone_player: ZoneMediaPlayer) -> None:
    """Test zone player icon when on."""
    assert zone_player.icon == 'mdi:speaker'


async def test_zone_player_icon_off(
    coordinator: XantechCoordinator,
    mock_config_entry: MagicMock,
    sources: dict[int, str],
) -> None:
    """Test zone player icon when off."""
    player = ZoneMediaPlayer(
        coordinator=coordinator,
        entry=mock_config_entry,
        zone_id=12,
        zone_name='Kitchen',
        sources=sources,
    )
    assert player.icon == 'mdi:speaker-off'


async def test_zone_player_turn_on(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test turning zone on."""
    with patch.object(
        coordinator, 'async_set_zone_power', new_callable=AsyncMock
    ) as mock_power:
        await zone_player.async_turn_on()
        mock_power.assert_called_once_with(11, True)


async def test_zone_player_turn_off(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test turning zone off."""
    with patch.object(
        coordinator, 'async_set_zone_power', new_callable=AsyncMock
    ) as mock_power:
        await zone_player.async_turn_off()
        mock_power.assert_called_once_with(11, False)


async def test_zone_player_set_volume(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test setting zone volume."""
    with patch.object(
        coordinator, 'async_set_zone_volume', new_callable=AsyncMock
    ) as mock_volume:
        await zone_player.async_set_volume_level(0.5)
        # 0.5 * 38 = 19
        mock_volume.assert_called_once_with(11, 19)


async def test_zone_player_mute(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test muting zone."""
    with patch.object(
        coordinator, 'async_set_zone_mute', new_callable=AsyncMock
    ) as mock_mute:
        await zone_player.async_mute_volume(True)
        mock_mute.assert_called_once_with(11, True)


async def test_zone_player_select_source(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test selecting source."""
    with patch.object(
        coordinator, 'async_set_zone_source', new_callable=AsyncMock
    ) as mock_source:
        await zone_player.async_select_source('TV')
        mock_source.assert_called_once_with(11, 3)


async def test_zone_player_select_invalid_source(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test selecting invalid source does nothing."""
    with patch.object(
        coordinator, 'async_set_zone_source', new_callable=AsyncMock
    ) as mock_source:
        await zone_player.async_select_source('NonExistent')
        mock_source.assert_not_called()


async def test_zone_player_volume_up(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test volume up."""
    with patch.object(
        coordinator, 'async_set_zone_volume', new_callable=AsyncMock
    ) as mock_volume:
        await zone_player.async_volume_up()
        # current volume is 20, so should set to 21
        mock_volume.assert_called_once_with(11, 21)


async def test_zone_player_volume_down(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test volume down."""
    with patch.object(
        coordinator, 'async_set_zone_volume', new_callable=AsyncMock
    ) as mock_volume:
        await zone_player.async_volume_down()
        # current volume is 20, so should set to 19
        mock_volume.assert_called_once_with(11, 19)


async def test_zone_player_snapshot_and_restore(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test snapshot and restore functionality."""
    snapshot_data = {'power': True, 'volume': 25, 'mute': False, 'source': 1}

    with patch.object(
        coordinator,
        'async_get_zone_snapshot',
        new_callable=AsyncMock,
        return_value=snapshot_data,
    ) as mock_snapshot:
        await zone_player.async_snapshot()
        mock_snapshot.assert_called_once_with(11)

    with patch.object(
        coordinator, 'async_restore_zone', new_callable=AsyncMock
    ) as mock_restore:
        await zone_player.async_restore()
        mock_restore.assert_called_once_with(snapshot_data)


async def test_zone_player_restore_without_snapshot(
    zone_player: ZoneMediaPlayer,
    coordinator: XantechCoordinator,
) -> None:
    """Test restore when no snapshot exists."""
    with patch.object(
        coordinator, 'async_restore_zone', new_callable=AsyncMock
    ) as mock_restore:
        # no snapshot saved
        zone_player._status_snapshot = None
        await zone_player.async_restore()
        mock_restore.assert_not_called()
