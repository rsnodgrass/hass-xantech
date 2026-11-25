"""Setup, Connection page and discovery: cases found by an independent review.

A new network link that only answers on its web interface, a new amplifier on a port
another one uses, a corrected port that waits for the next retry, an address found by
discovery that reloads the entry twice, and an amplifier known by its MAC address.
"""
# ruff: noqa: F811 - the amp fixture is shared with test_amp_settings

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_FALLBACK_PORT,
    CONF_HOST,
    CONF_PORT,
    DOMAIN,
    MODE_NETWORK,
    MODE_SERIAL,
    MODE_SERIAL_FALLBACK_NETWORK,
    MODE_SERIAL_FALLBACK_SERIAL,
)
from custom_components.xantech.dax88_network import Dax88NetworkError

from .test_amp_settings import _loaded, amp  # noqa: F401 - amp is a fixture
from .test_flow_guards import MODULE, _connection_page, _discover, _serial_entry

CHECK = 'custom_components.xantech.config_flow.async_check_linkplay_amp'


async def _save(hass: HomeAssistant, result: dict, module: dict, **fields: str) -> dict:
    with patch(CHECK, AsyncMock(return_value=module)):
        return await hass.config_entries.options.async_configure(
            result['flow_id'], fields
        )


# --- Connection page: a network link that is new here ---


async def test_a_new_network_link_must_answer_on_its_control_connection(
    hass: HomeAssistant, control_port: AsyncMock
) -> None:
    """Its web interface answering is not enough: setup tests the same."""
    entry = _serial_entry(hass, '/dev/ttyUSB0')
    before = dict(entry.data)
    fields = {
        CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_NETWORK,
        CONF_PORT: '/dev/ttyUSB0',
        CONF_HOST: '192.0.2.50',
    }
    control_port.side_effect = Dax88NetworkError('no reply on port 8899')
    result = await _save(hass, await _connection_page(hass, entry), MODULE, **fields)
    assert result['errors'] == {'base': 'no_amp_control'}
    assert dict(entry.data) == before  # nothing saved, the working entry stays

    control_port.side_effect = None
    result = await _save(hass, result, MODULE, **fields)
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_HOST] == '192.0.2.50'
    control_port.assert_awaited_with('192.0.2.50')


async def test_a_network_link_that_is_already_in_use_is_not_tested_again(
    hass: HomeAssistant, control_port: AsyncMock
) -> None:
    """Changing something else on the page must not depend on the link answering."""
    entry = _serial_entry(
        hass,
        '/dev/ttyUSB0',
        **{
            CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_NETWORK,
            CONF_HOST: '192.0.2.50',
            CONF_DEVICE_ID: 'TESTUUID',
        },
    )
    control_port.side_effect = Dax88NetworkError('busy')
    result = await _save(
        hass,
        await _connection_page(hass, entry),
        MODULE,
        **{
            CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_NETWORK,
            CONF_PORT: '/dev/ttyUSB1',
            CONF_HOST: '192.0.2.50',
        },
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_PORT] == '/dev/ttyUSB1'
    control_port.assert_not_awaited()


async def test_an_amplifier_known_by_its_mac_address_is_recognised(
    hass: HomeAssistant,
) -> None:
    """Setup stores the MAC address when the module has no uuid; this page asked
    for the uuid only and took any amplifier without one for the same amplifier.
    """
    entry = _serial_entry(
        hass,
        '/dev/ttyUSB0',
        **{
            CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_NETWORK,
            CONF_HOST: '192.0.2.50',
            CONF_DEVICE_ID: '00:22:6C:00:00:01',
        },
    )
    fields = {
        CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_NETWORK,
        CONF_PORT: '/dev/ttyUSB0',
        CONF_HOST: '192.0.2.51',
    }
    other = {'MAC': '00:22:6C:00:00:02', 'project': 'DAX88', 'probed': True}
    result = await _save(hass, await _connection_page(hass, entry), other, **fields)
    assert result['errors'] == {'base': 'different_amp'}
    assert entry.data[CONF_HOST] == '192.0.2.50'

    same = {'MAC': '00:22:6C:00:00:01', 'project': 'DAX88', 'probed': True}
    result = await _save(hass, result, same, **fields)
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_HOST] == '192.0.2.51'
    assert entry.data[CONF_DEVICE_ID] == '00:22:6C:00:00:01'


async def test_an_entry_that_only_knew_the_address_learns_the_identifier(
    hass: HomeAssistant,
) -> None:
    entry = _serial_entry(
        hass,
        '/dev/ttyUSB0',
        **{
            CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_NETWORK,
            CONF_HOST: '192.0.2.50',
            CONF_DEVICE_ID: '192.0.2.50',  # setup found neither uuid nor MAC
        },
    )
    result = await _save(
        hass,
        await _connection_page(hass, entry),
        {**MODULE, 'probed': True},
        **{
            CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_NETWORK,
            CONF_PORT: '/dev/ttyUSB0',
            CONF_HOST: '192.0.2.60',
        },
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_DEVICE_ID] == 'TESTUUID'


# --- a new amplifier on a port another one uses ---


async def test_a_new_amplifier_cannot_take_a_port_another_one_uses_now(
    hass: HomeAssistant, mock_async_get_amp_controller: AsyncMock
) -> None:
    """The first entry keeps the unique id of the port it was added with. Its
    port was changed since, and it has a second link: neither shows in that id.
    """
    first = _serial_entry(hass, '/dev/ttyUSB0')
    hass.config_entries.async_update_entry(
        first,
        data={
            **first.data,
            CONF_CONNECTION_MODE: MODE_SERIAL_FALLBACK_SERIAL,
            CONF_PORT: '/dev/ttyUSB1',
            CONF_FALLBACK_PORT: '/dev/ttyUSB2',
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'next_step_id': 'serial'}
    )
    for port in ('/dev/ttyUSB1', '/dev/ttyUSB2'):
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {CONF_PORT: port, CONF_AMP_TYPE: 'dax88'}
        )
        assert (result['step_id'], result['errors']) == (
            'serial',
            {CONF_PORT: 'port_in_use'},
        )
    mock_async_get_amp_controller.assert_not_awaited()  # the port was not opened

    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {CONF_PORT: '/dev/ttyUSB3', CONF_AMP_TYPE: 'dax88'}
    )
    assert result['step_id'] == 'zones'


# --- a corrected port while setup is waiting to retry ---


async def test_a_corrected_port_is_used_at_once_while_setup_waits_to_retry(
    hass: HomeAssistant, mock_amp: MagicMock
) -> None:
    entry = _serial_entry(hass, '/dev/ttyUSB0')

    async def controller(_amp_type: str, port: str, *_args: Any, **_kwargs: Any) -> Any:
        if port != '/dev/ttyUSB1':
            raise OSError(f'no such port {port}')
        return mock_amp

    with patch(
        'custom_components.xantech.async_get_amp_controller', side_effect=controller
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY

        result = await _save(
            hass,
            await _connection_page(hass, entry),
            MODULE,
            **{CONF_CONNECTION_MODE: MODE_SERIAL, CONF_PORT: '/dev/ttyUSB1'},
        )
        assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
        await hass.async_block_till_done()
        # not at Home Assistant's next retry: now
        assert entry.state is ConfigEntryState.LOADED
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


# --- discovery finds a loaded amplifier at a new address ---


async def test_a_new_address_from_discovery_reloads_a_loaded_entry_once(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    assert entry.data[CONF_HOST] == '192.0.2.50'
    with patch.object(
        hass.config_entries, 'async_reload', wraps=hass.config_entries.async_reload
    ) as reload:
        result = await _discover(hass, '192.0.2.77')
        await hass.async_block_till_done()
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'already_configured',
    )
    assert entry.data[CONF_HOST] == '192.0.2.77'
    assert entry.state is ConfigEntryState.LOADED
    assert reload.await_count == 1  # by the entry's own listener, not a second time


async def test_network_only_mode_needs_no_serial_port(
    hass: HomeAssistant, control_port: AsyncMock
) -> None:
    entry = _serial_entry(hass, '/dev/ttyUSB0')
    result = await _save(
        hass,
        await _connection_page(hass, entry),
        MODULE,
        **{CONF_CONNECTION_MODE: MODE_NETWORK, CONF_HOST: '192.0.2.50'},
    )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    control_port.assert_awaited_once_with('192.0.2.50')
