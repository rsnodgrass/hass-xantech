"""Tests for recognising an amplifier's Linkplay module by what it answers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.xantech.dax88_network import Dax88NetworkError
from custom_components.xantech.network import (
    NotADax88Error,
    async_check_dax88,
    async_check_linkplay_amp,
)


async def test_known_module_needs_no_probe(hass: HomeAssistant) -> None:
    with (
        patch(
            'custom_components.xantech.network.async_get_module_status',
            AsyncMock(return_value={'project': 'DAYTONAUDIO_DAX88', 'uuid': 'U'}),
        ),
        patch('custom_components.xantech.network.Dax88NetworkAmp') as amp,
    ):
        assert (await async_check_linkplay_amp(hass, '192.0.2.1'))['uuid'] == 'U'
    amp.assert_not_called()


async def test_unknown_module_accepted_if_it_answers(hass: HomeAssistant) -> None:
    probe = MagicMock()
    probe.sync = AsyncMock(return_value={})
    with (
        patch(
            'custom_components.xantech.network.async_get_module_status',
            AsyncMock(return_value={'project': 'SOME_REBADGE', 'uuid': 'U2'}),
        ),
        patch('custom_components.xantech.network.Dax88NetworkAmp', return_value=probe),
    ):
        result = await async_check_linkplay_amp(hass, '192.0.2.2')
    assert result['probed'] is True
    probe.close.assert_called_once()


async def test_unknown_module_refused_if_it_doesnt_answer(hass: HomeAssistant) -> None:
    probe = MagicMock()
    probe.sync = AsyncMock(side_effect=Dax88NetworkError('no reply'))
    with (
        patch(
            'custom_components.xantech.network.async_get_module_status',
            AsyncMock(return_value={'project': 'A_SPEAKER'}),
        ),
        patch('custom_components.xantech.network.Dax88NetworkAmp', return_value=probe),
        pytest.raises(NotADax88Error),
    ):
        await async_check_linkplay_amp(hass, '192.0.2.3')
    probe.close.assert_called_once()


async def test_discovery_check_never_probes(hass: HomeAssistant) -> None:
    with (
        patch(
            'custom_components.xantech.network.async_get_module_status',
            AsyncMock(return_value={'project': 'A_SPEAKER'}),
        ),
        patch('custom_components.xantech.network.Dax88NetworkAmp') as amp,
        pytest.raises(NotADax88Error),
    ):
        await async_check_dax88(hass, '192.0.2.4')
    amp.assert_not_called()
