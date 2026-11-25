"""Setup and Connection pages: one amplifier, one port, one entry; clear errors."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.xantech.connection import async_socket_reachable
from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_FALLBACK_PORT,
    CONF_HOST,
    CONF_PORT,
    CONF_SOURCES,
    CONF_TRANSPORT,
    CONF_ZONES,
    DOMAIN,
    TRANSPORT_NETWORK,
)
from custom_components.xantech.network import async_check_dax88

MODULE = {'uuid': 'TESTUUID', 'project': 'DAYTONAUDIO_DAX88'}


def _serial_entry(hass: HomeAssistant, port: str, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f'Xantech Multi-Zone Audio ({port})',
        data={
            CONF_PORT: port,
            CONF_AMP_TYPE: 'dax88',
            CONF_ZONES: {11: {'name': 'Room 1'}},
            CONF_SOURCES: {1: {'name': 'Input1'}},
            **data,
        },
        unique_id=f'{DOMAIN}_{port}',
    )
    entry.add_to_hass(hass)
    return entry


async def _connection_page(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result['flow_id'], {'next_step_id': 'connection'}
    )


async def _save(hass: HomeAssistant, result: dict, **fields: str) -> dict:
    with patch(
        'custom_components.xantech.config_flow.async_check_linkplay_amp',
        AsyncMock(return_value=MODULE),
    ):
        return await hass.config_entries.options.async_configure(
            result['flow_id'], fields
        )


# --- Connection page ---


async def test_a_port_another_amplifier_uses_is_refused(hass: HomeAssistant) -> None:
    _serial_entry(hass, '/dev/ttyUSB0')
    second = _serial_entry(hass, '/dev/ttyUSB1')
    result = await _connection_page(hass, second)
    result = await _save(
        hass, result, **{CONF_CONNECTION_MODE: 'serial', CONF_PORT: '/dev/ttyUSB0'}
    )
    assert result['errors'] == {CONF_PORT: 'port_in_use'}
    assert second.data[CONF_PORT] == '/dev/ttyUSB1'


async def test_another_amplifiers_fallback_port_counts_as_used(
    hass: HomeAssistant,
) -> None:
    _serial_entry(
        hass,
        '/dev/ttyUSB0',
        **{
            CONF_CONNECTION_MODE: 'serial_fallback_serial',
            CONF_FALLBACK_PORT: 'socket://192.0.2.60:4999',
        },
    )
    second = _serial_entry(hass, '/dev/ttyUSB1')
    result = await _connection_page(hass, second)
    result = await _save(
        hass,
        result,
        **{
            CONF_CONNECTION_MODE: 'serial_fallback_serial',
            CONF_PORT: '/dev/ttyUSB1',
            CONF_FALLBACK_PORT: 'socket://192.0.2.60:4999',
        },
    )
    assert result['errors'] == {CONF_FALLBACK_PORT: 'port_in_use'}


async def test_a_port_only_a_disabled_or_network_entry_names_is_free(
    hass: HomeAssistant,
) -> None:
    disabled = _serial_entry(hass, '/dev/ttyUSB0')
    await hass.config_entries.async_set_disabled_by(
        disabled.entry_id, config_entries.ConfigEntryDisabler.USER
    )
    # a network-only amplifier that still remembers the port it once used
    network_only = _serial_entry(
        hass,
        '/dev/ttyUSB2',
        **{
            CONF_CONNECTION_MODE: 'network',
            CONF_TRANSPORT: TRANSPORT_NETWORK,
            CONF_HOST: '192.0.2.51',
            CONF_DEVICE_ID: 'OTHERUUID',
        },
    )
    assert network_only.data[CONF_PORT] == '/dev/ttyUSB2'
    third = _serial_entry(hass, '/dev/ttyUSB1')
    for port in ('/dev/ttyUSB0', '/dev/ttyUSB2'):
        result = await _connection_page(hass, third)
        with patch('custom_components.xantech.async_setup_entry', return_value=True):
            result = await _save(
                hass, result, **{CONF_CONNECTION_MODE: 'serial', CONF_PORT: port}
            )
            await hass.async_block_till_done()
        assert (result['type'], result['step_id']) == (
            FlowResultType.MENU,
            'init',
        ), result
        assert third.data[CONF_PORT] == port


async def test_the_second_rs232_link_must_be_another_port(hass: HomeAssistant) -> None:
    entry = _serial_entry(hass, '/dev/ttyUSB0')
    result = await _connection_page(hass, entry)
    result = await _save(
        hass,
        result,
        **{
            CONF_CONNECTION_MODE: 'serial_fallback_serial',
            CONF_PORT: '/dev/ttyUSB0',
            CONF_FALLBACK_PORT: '/dev/ttyUSB0',
        },
    )
    assert result['errors'] == {CONF_FALLBACK_PORT: 'same_port'}


async def test_a_network_module_another_amplifier_uses_is_refused(
    hass: HomeAssistant,
) -> None:
    """The same amplifier must not end up as two entries with two links to it."""
    _serial_entry(
        hass,
        '/dev/ttyUSB7',
        **{
            CONF_CONNECTION_MODE: 'network',
            CONF_TRANSPORT: TRANSPORT_NETWORK,
            CONF_HOST: '192.0.2.50',
            CONF_DEVICE_ID: 'TESTUUID',
        },
    )
    serial = _serial_entry(hass, '/dev/ttyUSB0')
    result = await _connection_page(hass, serial)
    result = await _save(
        hass,
        result,
        **{
            CONF_CONNECTION_MODE: 'serial_fallback_network',
            CONF_PORT: '/dev/ttyUSB0',
            CONF_HOST: '192.0.2.50',
        },
    )
    assert result['errors'] == {'base': 'amp_in_other_entry'}
    assert CONF_DEVICE_ID not in serial.data


# --- an amplifier found on the network that is already set up over RS-232 ---


async def _discover(hass: HomeAssistant, host: str = '192.0.2.77') -> dict:
    with patch(
        'custom_components.xantech.config_flow.async_check_dax88',
        AsyncMock(return_value=MODULE),
    ):
        return await hass.config_entries.flow.async_init(
            DOMAIN,
            context={'source': config_entries.SOURCE_ZEROCONF},
            data=ZeroconfServiceInfo(
                ip_address=ip_address(host),
                ip_addresses=[ip_address(host)],
                hostname='linkplay.local.',
                name='DAX 88_0000._linkplay._tcp.local.',
                port=49152,
                type='_linkplay._tcp.local.',
                properties={},
            ),
        )


async def test_adding_the_module_to_an_rs232_entry_closes_its_discovery(
    hass: HomeAssistant,
) -> None:
    """Seen on a real system: the "Discovered" card stayed, offering a second entry."""
    entry = _serial_entry(hass, '/dev/ttyUSB0')
    found = await _discover(hass)
    assert found['step_id'] == 'zeroconf_confirm'
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1
    result = await _connection_page(hass, entry)
    with patch('custom_components.xantech.async_setup_entry', return_value=True):
        result = await _save(
            hass,
            result,
            **{
                CONF_CONNECTION_MODE: 'serial_fallback_network',
                CONF_PORT: '/dev/ttyUSB0',
                CONF_HOST: '192.0.2.77',
            },
        )
        await hass.async_block_till_done()
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init'), result
    assert entry.data[CONF_DEVICE_ID] == 'TESTUUID'
    assert hass.config_entries.flow.async_progress_by_handler(DOMAIN) == []


async def test_another_amplifiers_discovery_stays(hass: HomeAssistant) -> None:
    entry = _serial_entry(hass, '/dev/ttyUSB0')
    await _discover(hass)
    other = {'uuid': 'OTHERUUID', 'project': 'DAYTONAUDIO_DAX88'}
    result = await _connection_page(hass, entry)
    with (
        patch('custom_components.xantech.async_setup_entry', return_value=True),
        patch(
            'custom_components.xantech.config_flow.async_check_linkplay_amp',
            AsyncMock(return_value=other),
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result['flow_id'],
            {
                CONF_CONNECTION_MODE: 'serial_fallback_network',
                CONF_PORT: '/dev/ttyUSB0',
                CONF_HOST: '192.0.2.78',
            },
        )
        await hass.async_block_till_done()
    assert entry.data[CONF_DEVICE_ID] == 'OTHERUUID'
    assert len(hass.config_entries.flow.async_progress_by_handler(DOMAIN)) == 1


async def test_a_discovery_confirmed_late_does_not_make_a_second_entry(
    hass: HomeAssistant,
) -> None:
    """The card was open while the module became part of the RS-232 entry."""
    entry = _serial_entry(hass, '/dev/ttyUSB0')
    found = await _discover(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CONNECTION_MODE: 'serial_fallback_network',
            CONF_HOST: '192.0.2.50',
            CONF_DEVICE_ID: 'TESTUUID',
        },
    )
    with (
        patch(
            'custom_components.xantech.config_flow.async_check_linkplay_amp',
            AsyncMock(return_value=MODULE),
        ),
        patch.object(hass.config_entries, 'async_schedule_reload') as reload,
    ):
        result = await hass.config_entries.flow.async_configure(found['flow_id'], {})
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'already_configured',
    )
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    # and the entry learns the address the amplifier was found at
    assert entry.data[CONF_HOST] == '192.0.2.77'
    reload.assert_called_once_with(entry.entry_id)


async def test_typing_the_address_of_an_amp_that_is_set_up_does_not_duplicate(
    hass: HomeAssistant,
) -> None:
    _serial_entry(
        hass,
        '/dev/ttyUSB0',
        **{
            CONF_CONNECTION_MODE: 'serial_fallback_network',
            CONF_HOST: '192.0.2.50',
            CONF_DEVICE_ID: 'TESTUUID',
        },
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'next_step_id': 'network'}
    )
    with patch(
        'custom_components.xantech.config_flow.async_check_linkplay_amp',
        AsyncMock(return_value=MODULE),
    ):
        result = await hass.config_entries.flow.async_configure(
            result['flow_id'], {CONF_HOST: '192.0.2.50'}
        )
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'already_configured',
    )
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


# --- setup flow ---


async def test_a_serial_port_that_is_already_set_up_says_so(
    hass: HomeAssistant, mock_async_get_amp_controller: AsyncMock
) -> None:
    _serial_entry(hass, '/dev/ttyUSB0')
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={'source': config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {'next_step_id': 'serial'}
    )
    result = await hass.config_entries.flow.async_configure(
        result['flow_id'], {CONF_PORT: '/dev/ttyUSB0', CONF_AMP_TYPE: 'dax88'}
    )
    # "already set up", not "an unexpected error occurred"
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'already_configured',
    )


@pytest.mark.parametrize('status', [404, 500])
async def test_a_device_that_answers_with_an_http_error_is_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int
) -> None:
    aioclient_mock.get(
        'http://192.0.2.9/httpapi.asp?command=getStatusEx', status=status, text='no'
    )
    with pytest.raises(OSError):
        await async_check_dax88(hass, '192.0.2.9')


async def test_a_device_that_answers_something_else_is_not_an_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        'http://192.0.2.9/httpapi.asp?command=getStatusEx', text='["not", "a status"]'
    )
    with pytest.raises(ValueError):
        await async_check_dax88(hass, '192.0.2.9')


# --- RS-232 over IP: check without blocking before a reopen ---


async def test_only_socket_links_are_checked() -> None:
    assert await async_socket_reachable('/dev/ttyUSB0')
    assert await async_socket_reachable('rfc2217://192.0.2.1:1')
    # malformed: left for the serial library to report
    assert await async_socket_reachable('socket://')


async def test_socket_link_reachable_or_not(socket_enabled: None) -> None:
    server = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    try:
        assert await async_socket_reachable(f'socket://127.0.0.1:{port}')
    finally:
        server.close()
        await server.wait_closed()
    assert not await async_socket_reachable(f'socket://127.0.0.1:{port}', timeout=1.0)
