"""Tests for connection modes: failover, stable ids, options and LAN discovery."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xantech.connection import link_is_down
from custom_components.xantech.const import (
    CONF_AMP_NAME,
    CONF_AMP_TYPE,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PORT,
    CONF_SOURCES,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.xantech.failover import FailoverAmp
from custom_components.xantech.network import NotADax88Error

STATUS = {'power': True, 'volume': 10, 'mute': False, 'source': 1}


class FakeClient:
    """A client that can be told to fail, and records calls and overlap."""

    def __init__(self, name: str, tracker: dict[str, int]) -> None:
        self.name = name
        self.fail = False
        self.calls: list[str] = []
        self.tracker = tracker
        self.closed = False

    async def _run(self, method: str) -> Any:
        self.tracker['in_flight'] += 1
        self.tracker['max'] = max(self.tracker['max'], self.tracker['in_flight'])
        try:
            await asyncio.sleep(0.01)
            self.calls.append(method)
            if self.fail:
                raise TimeoutError(f'{self.name} down')
            return dict(STATUS, via=self.name) if method == 'zone_status' else None
        finally:
            self.tracker['in_flight'] -= 1

    async def zone_status(self, zone: int) -> Any:
        return await self._run('zone_status')

    async def set_volume(self, zone: int, volume: int) -> None:
        if not 0 <= volume <= 38:
            raise ValueError('volume must be 0-38')
        await self._run('set_volume')

    async def names(self) -> dict[str, Any]:
        await self._run('names')
        return {'device': self.name}


def _pair(retry: float = 300.0) -> tuple[FailoverAmp, FakeClient, FakeClient, dict]:
    tracker = {'in_flight': 0, 'max': 0}
    serial, network = FakeClient('serial', tracker), FakeClient('network', tracker)
    amp = FailoverAmp(
        ('serial', serial),
        ('network', network),
        retry_preferred_seconds=retry,
        close_client=lambda c: setattr(c, 'closed', True),
    )
    return amp, serial, network, tracker


async def test_uses_the_preferred_link() -> None:
    amp, serial, network, _ = _pair()
    assert (await amp.zone_status(11))['via'] == 'serial'
    assert network.calls == []


async def test_missed_reading_answered_by_other_link_then_switch() -> None:
    amp, serial, network, _ = _pair()
    serial.fail = True
    # first miss: answered from the network link, but no switch yet
    assert (await amp.zone_status(11))['via'] == 'network'
    assert amp.active_transport == 'serial'
    # second miss in a row: switch
    assert (await amp.zone_status(12))['via'] == 'network'
    assert amp.active_transport == 'network'


async def test_one_dropped_reply_does_not_switch() -> None:
    amp, serial, network, _ = _pair()
    serial.fail = True
    await amp.zone_status(11)
    serial.fail = False
    assert (await amp.zone_status(12))['via'] == 'serial'
    serial.fail = True
    await amp.zone_status(13)  # a miss again, but not two in a row
    assert amp.active_transport == 'serial'


async def test_failed_command_switches_at_once_and_is_retried() -> None:
    amp, serial, network, _ = _pair()
    serial.fail = True
    await amp.set_volume(11, 5)
    assert amp.active_transport == 'network'
    assert network.calls == ['set_volume']


async def test_goes_back_to_preferred_when_it_answers_again() -> None:
    amp, serial, network, _ = _pair(retry=0.05)
    serial.fail = True
    await amp.set_volume(11, 5)
    assert amp.active_transport == 'network'
    serial.fail = False
    await asyncio.sleep(0.06)
    assert (await amp.zone_status(11))['via'] == 'serial'
    assert amp.active_transport == 'serial'


async def test_both_down_raises() -> None:
    amp, serial, network, _ = _pair()
    serial.fail = network.fail = True
    with pytest.raises(TimeoutError):
        await amp.set_volume(11, 5)


async def test_never_uses_both_links_at_once() -> None:
    amp, serial, network, tracker = _pair()
    serial.fail = True
    await asyncio.gather(
        *(amp.zone_status(z) for z in range(11, 17)),
        *(amp.set_volume(z, 3) for z in range(11, 17)),
        return_exceptions=True,
    )
    assert tracker['max'] == 1


async def test_network_features_share_the_lock() -> None:
    """Names, backup and reset use the network client, never beside the other link."""
    amp, serial, network, tracker = _pair()
    locked = amp.locked('network')
    assert amp.active_transport == 'serial'
    results = await asyncio.gather(
        locked.names(), amp.zone_status(11), amp.set_volume(12, 3), locked.names()
    )
    assert results[0] == {'device': 'network'}
    assert tracker['max'] == 1
    assert amp.locked('serial_backup') is None  # no such link in this pair


async def test_a_bad_value_is_not_a_link_failure() -> None:
    amp, serial, network, _ = _pair()
    with pytest.raises(ValueError):
        await amp.set_volume(11, 99)
    assert amp.active_transport == 'serial'
    assert network.calls == []


async def test_commands_never_probe_the_preferred_link() -> None:
    """Only status reads try the preferred link again, so commands stay quick."""
    amp, serial, network, _ = _pair(retry=0.05)
    serial.fail = True
    await amp.set_volume(11, 5)
    assert amp.active_transport == 'network'
    await asyncio.sleep(0.06)
    tried = len(serial.calls)
    await amp.set_volume(11, 6)
    assert len(serial.calls) == tried
    assert amp.active_transport == 'network'


def _reopening_pair() -> tuple[FailoverAmp, list[FakeClient], FakeClient]:
    tracker = {'in_flight': 0, 'max': 0}
    serials = [FakeClient('serial', tracker)]
    network = FakeClient('network', tracker)

    async def reopen() -> FakeClient:
        serials.append(FakeClient(f'serial{len(serials) + 1}', tracker))
        return serials[-1]

    amp = FailoverAmp(
        ('serial', serials[0]),
        ('network', network),
        retry_preferred_seconds=0.05,
        reopen_seconds=0.0,
        close_client=lambda c: setattr(c, 'closed', True),
        factories={'serial': reopen},
    )
    return amp, serials, network


async def test_a_dead_serial_port_is_reopened_when_retrying() -> None:
    """An unplugged adapter never answers again on the old connection."""
    amp, serials, network = _reopening_pair()
    serials[0].fail = True  # stays dead for good
    await amp.set_volume(11, 5)
    assert amp.active_transport == 'network'
    await asyncio.sleep(0.06)
    status = await amp.zone_status(11)
    assert status['via'] == 'serial2'  # a new connection, and it answers
    assert amp.active_transport == 'serial'
    assert serials[0].closed


async def test_a_link_missing_at_startup_is_opened_later() -> None:
    tracker = {'in_flight': 0, 'max': 0}
    network = FakeClient('network', tracker)
    opened: list[FakeClient] = []

    async def open_serial() -> FakeClient:
        opened.append(FakeClient('serial', tracker))
        return opened[-1]

    amp = FailoverAmp(
        ('serial', None),
        ('network', network),
        retry_preferred_seconds=0.05,
        reopen_seconds=0.0,
        factories={'serial': open_serial},
    )
    assert amp.active_transport == 'network'
    assert (await amp.zone_status(11))['via'] == 'network'
    await asyncio.sleep(0.06)
    assert (await amp.zone_status(11))['via'] == 'serial'
    assert amp.active_transport == 'serial'


async def test_both_links_down_does_not_flap() -> None:
    """The amplifier is switched off: nothing answers, so the active link stays put."""
    amp, serial, network, _ = _pair()
    serial.fail = network.fail = True
    for _ in range(6):
        with pytest.raises(TimeoutError):
            await amp.zone_status(11)
        assert amp.active_transport == 'serial'
    with pytest.raises(TimeoutError):
        await amp.set_volume(11, 5)
    assert amp.active_transport == 'serial'
    # the fallback comes back first: now there is a link that answers to move to
    network.fail = False
    assert (await amp.zone_status(11))['via'] == 'network'
    assert amp.active_transport == 'network'


async def test_a_fallback_port_that_died_while_idle_is_reopened() -> None:
    """The spare port's adapter was re-plugged long ago; it is needed only now."""
    tracker = {'in_flight': 0, 'max': 0}
    network = FakeClient('network', tracker)
    serials = [FakeClient('serial', tracker)]

    async def reopen() -> FakeClient:
        serials.append(FakeClient(f'serial{len(serials) + 1}', tracker))
        return serials[-1]

    amp = FailoverAmp(
        ('network', network),
        ('serial', serials[0]),
        reopen_seconds=0.0,
        close_client=lambda c: setattr(c, 'closed', True),
        factories={'serial': reopen},
    )
    serials[0].fail = True  # dead for good, and nobody has noticed yet
    network.fail = True
    assert (await amp.zone_status(11))['via'] == 'serial2'
    assert serials[0].closed
    await amp.set_volume(11, 5)  # a command moves to the link that works
    assert amp.active_transport == 'serial'
    assert serials[1].calls == ['zone_status', 'set_volume']


class AnyVolumeClient(FakeClient):
    """A link that accepts values the other link refuses."""

    async def set_volume(self, zone: int, volume: int) -> None:
        await self._run('set_volume')


async def test_a_value_the_other_link_refuses_is_not_a_dead_port() -> None:
    tracker = {'in_flight': 0, 'max': 0}
    network = AnyVolumeClient('network', tracker)
    serials = [FakeClient('serial', tracker)]

    async def reopen() -> FakeClient:
        serials.append(FakeClient(f'serial{len(serials) + 1}', tracker))
        return serials[-1]

    amp = FailoverAmp(
        ('network', network),
        ('serial', serials[0]),
        reopen_seconds=0.0,
        factories={'serial': reopen},
    )
    network.fail = True
    with pytest.raises(ValueError):
        await amp.set_volume(11, 99)
    assert len(serials) == 1  # not reopened for a value it will never accept
    # and no move to a link that did not answer
    assert amp.active_transport == 'network'


class RestartingClient(FakeClient):
    """A network client whose module is restarting after a factory reset."""

    def __init__(self, name: str, tracker: dict[str, int]) -> None:
        super().__init__(name, tracker)
        self.polling = asyncio.Event()
        self.back = asyncio.Event()

    async def sync(self) -> None:
        await self._run('sync')

    async def wait_for_restart(self, *, sync: Any = None) -> None:
        while not self.back.is_set():
            await (sync or self.sync)()
            self.polling.set()
            await asyncio.sleep(0.02)


async def test_waiting_for_the_restart_does_not_block_the_other_link() -> None:
    """The wait takes minutes; RS-232 must keep working meanwhile."""
    tracker = {'in_flight': 0, 'max': 0}
    serial, network = FakeClient('serial', tracker), RestartingClient('net', tracker)
    amp = FailoverAmp(('serial', serial), ('network', network))
    waiting = asyncio.ensure_future(amp.locked('network').wait_for_restart())
    await asyncio.wait_for(network.polling.wait(), 1)
    for _ in range(3):
        status = await asyncio.wait_for(amp.zone_status(11), 0.5)
        assert status['via'] == 'serial'
    network.back.set()
    await asyncio.wait_for(waiting, 1)
    assert 'sync' in network.calls
    assert tracker['max'] == 1  # and the two links still never overlapped


async def test_close_closes_both() -> None:
    amp, serial, network, _ = _pair()
    amp.close()
    assert serial.closed and network.closed


# --- in Home Assistant ---

MODULE = {
    'uuid': 'TESTUUID',
    'project': 'DAYTONAUDIO_DAX88',
    'DeviceName': 'DAX 88_0000',
}


def _dax88_serial_entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Xantech Multi-Zone Audio (/dev/ttyUSB0)',
        data={
            CONF_PORT: '/dev/ttyUSB0',
            CONF_AMP_TYPE: 'dax88',
            CONF_ZONES: {11: {'name': 'Room 1'}},
            CONF_SOURCES: {1: {'name': 'Input1'}},
            **data,
        },
        unique_id=f'{DOMAIN}_/dev/ttyUSB0',
    )
    entry.add_to_hass(hass)
    return entry


def _clients(serial_ok: bool = True) -> tuple[MagicMock, MagicMock]:
    serial = MagicMock()
    serial.zone_status = AsyncMock(return_value=STATUS if serial_ok else None)
    serial.close = MagicMock()
    network = MagicMock()
    network.zone_status = AsyncMock(return_value=STATUS)
    network.close = MagicMock()
    return serial, network


async def test_existing_entry_keeps_its_entity_ids_when_switching_mode(
    hass: HomeAssistant,
) -> None:
    entry = _dax88_serial_entry(hass)
    serial, network = _clients()
    with (
        patch(
            'custom_components.xantech.async_get_amp_controller',
            AsyncMock(return_value=serial),
        ),
        patch('custom_components.xantech.Dax88NetworkAmp', return_value=network),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # the legacy name is frozen into the entry
        assert entry.data[CONF_AMP_NAME] == 'dax88__dev_ttyUSB0'
        registry = er.async_get(hass)
        before = {
            e.unique_id
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        }

        # switch to network-with-serial-fallback; entities must not change
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_CONNECTION_MODE: 'network_fallback_serial',
                CONF_HOST: '192.0.2.50',
                CONF_DEVICE_ID: 'TESTUUID',
            },
        )
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        after = {
            e.unique_id
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        }
    assert after == before
    assert entry.runtime_data.amp.active_transport == 'network'


async def test_fallback_mode_starts_on_the_other_link(hass: HomeAssistant) -> None:
    entry = _dax88_serial_entry(
        hass,
        **{CONF_CONNECTION_MODE: 'serial_fallback_network', CONF_HOST: '192.0.2.50'},
    )
    _, network = _clients()
    with (
        patch(
            'custom_components.xantech.async_get_amp_controller',
            AsyncMock(side_effect=OSError('no such port')),
        ),
        patch('custom_components.xantech.Dax88NetworkAmp', return_value=network),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.amp.active_transport == 'network'
    # the settings features get the network client wrapped in the failover lock
    assert entry.runtime_data.network_amp._client is network


async def _options_connection(hass: HomeAssistant, entry: MockConfigEntry) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result['flow_id'], {'next_step_id': 'connection'}
    )


async def test_options_connection_offers_every_mode_for_dax88(
    hass: HomeAssistant,
) -> None:
    entry = _dax88_serial_entry(hass)
    result = await _options_connection(hass, entry)
    assert result['step_id'] == 'connection'
    with patch(
        'custom_components.xantech.config_flow.async_check_linkplay_amp',
        AsyncMock(return_value=MODULE),
    ):
        result = await hass.config_entries.options.async_configure(
            result['flow_id'],
            {
                CONF_CONNECTION_MODE: 'serial_fallback_network',
                CONF_PORT: '/dev/ttyUSB0',
                CONF_HOST: '192.0.2.50',
            },
        )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_CONNECTION_MODE] == 'serial_fallback_network'
    assert entry.data[CONF_DEVICE_ID] == 'TESTUUID'


async def test_options_connection_checks_what_each_mode_needs(
    hass: HomeAssistant,
) -> None:
    entry = _dax88_serial_entry(hass)
    result = await _options_connection(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'],
        {CONF_CONNECTION_MODE: 'network_fallback_serial', CONF_PORT: '', CONF_HOST: ''},
    )
    assert result['errors'] == {CONF_PORT: 'port_required', CONF_HOST: 'host_required'}


async def test_every_mode_is_offered_for_every_amp_type(hass: HomeAssistant) -> None:
    entry = _dax88_serial_entry(hass, **{CONF_AMP_TYPE: 'xantech8'})
    result = await _options_connection(hass, entry)
    mode_field = next(
        v for k, v in result['data_schema'].schema.items() if k == CONF_CONNECTION_MODE
    )
    assert mode_field.config['options'] == [
        'serial',
        'network',
        'serial_fallback_network',
        'network_fallback_serial',
        'serial_fallback_serial',
    ]


async def test_network_link_accepted_when_the_module_answers(
    hass: HomeAssistant,
) -> None:
    """A rebadged amp (any type) is accepted because its module answers the protocol."""
    entry = _dax88_serial_entry(hass, **{CONF_AMP_TYPE: 'xantech8'})
    result = await _options_connection(hass, entry)
    with patch(
        'custom_components.xantech.config_flow.async_check_linkplay_amp',
        AsyncMock(
            return_value={'uuid': 'OTHERUUID', 'project': 'REBADGE', 'probed': True}
        ),
    ):
        result = await hass.config_entries.options.async_configure(
            result['flow_id'],
            {
                CONF_CONNECTION_MODE: 'network_fallback_serial',
                CONF_PORT: '/dev/ttyUSB0',
                CONF_HOST: '192.0.2.51',
            },
        )
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
    assert entry.data[CONF_DEVICE_ID] == 'OTHERUUID'


async def test_network_link_refused_when_the_module_doesnt_answer(
    hass: HomeAssistant,
) -> None:
    entry = _dax88_serial_entry(hass, **{CONF_AMP_TYPE: 'xantech8'})
    result = await _options_connection(hass, entry)
    with patch(
        'custom_components.xantech.config_flow.async_check_linkplay_amp',
        AsyncMock(side_effect=NotADax88Error('SPEAKER')),
    ):
        result = await hass.config_entries.options.async_configure(
            result['flow_id'],
            {CONF_CONNECTION_MODE: 'network', CONF_PORT: '', CONF_HOST: '192.0.2.52'},
        )
    assert result['errors'] == {'base': 'no_linkplay_control'}


async def test_second_rs232_link_needs_an_address(hass: HomeAssistant) -> None:
    entry = _dax88_serial_entry(hass, **{CONF_AMP_TYPE: 'xantech8'})
    result = await _options_connection(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result['flow_id'],
        {CONF_CONNECTION_MODE: 'serial_fallback_serial', CONF_PORT: '/dev/ttyUSB0'},
    )
    assert result['errors'] == {'fallback_port': 'fallback_port_required'}


async def test_cable_with_ip_bridge_fallback(hass: HomeAssistant) -> None:
    """RS-232 cable first, RS-232 over IP (e.g. Xantech MRIP) as the fallback."""
    entry = _dax88_serial_entry(
        hass,
        **{
            CONF_AMP_TYPE: 'xantech8',
            CONF_CONNECTION_MODE: 'serial_fallback_serial',
            'fallback_port': 'socket://192.0.2.60:4999',
        },
    )
    cable, bridge = MagicMock(), MagicMock()
    cable.zone_status = AsyncMock(return_value=None)  # the cable stops answering
    bridge.zone_status = AsyncMock(return_value=STATUS)
    opened: list[str] = []

    async def controller(amp_type: str, url: str, loop: Any) -> MagicMock:
        opened.append(url)
        return cable if url == '/dev/ttyUSB0' else bridge

    with patch('custom_components.xantech.async_get_amp_controller', controller):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert opened == ['/dev/ttyUSB0', 'socket://192.0.2.60:4999']
        coordinator = entry.runtime_data.coordinator
        # the first missed reading was answered over the IP bridge
        assert coordinator.data[11] == STATUS
        assert entry.runtime_data.amp.active_transport == 'serial'
        # a second miss in a row moves to the bridge
        await coordinator.async_refresh()
        assert entry.runtime_data.amp.active_transport == 'serial_backup'
        assert entry.runtime_data.network_amp is None


# --- LAN discovery ---


def _zeroconf(host: str = '192.0.2.77') -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=__import__('ipaddress').ip_address(host),
        ip_addresses=[__import__('ipaddress').ip_address(host)],
        hostname='linkplay.local.',
        name='DAX 88_0000._linkplay._tcp.local.',
        port=49152,
        type='_linkplay._tcp.local.',
        properties={},
    )


async def test_discovery_ignores_other_linkplay_devices(hass: HomeAssistant) -> None:
    with patch(
        'custom_components.xantech.config_flow.async_check_dax88',
        AsyncMock(side_effect=NotADax88Error('SOME_SPEAKER')),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_ZEROCONF}, data=_zeroconf()
        )
    assert result['type'] == FlowResultType.ABORT
    assert result['reason'] == 'not_dax88'


async def test_discovery_offers_a_new_dax88(hass: HomeAssistant) -> None:
    with patch(
        'custom_components.xantech.config_flow.async_check_dax88',
        AsyncMock(return_value=MODULE),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_ZEROCONF}, data=_zeroconf()
        )
    assert result['type'] == FlowResultType.FORM
    assert result['step_id'] == 'zeroconf_confirm'
    assert result['description_placeholders'] == {
        'host': '192.0.2.77',
        'name': 'DAX 88_0000',
    }


async def test_discovery_updates_the_address_of_a_known_amp(
    hass: HomeAssistant,
) -> None:
    entry = _dax88_serial_entry(
        hass,
        **{
            CONF_CONNECTION_MODE: 'serial_fallback_network',
            CONF_HOST: '192.0.2.50',
            CONF_DEVICE_ID: 'TESTUUID',
        },
    )
    with (
        patch(
            'custom_components.xantech.config_flow.async_check_dax88',
            AsyncMock(return_value=MODULE),
        ),
        patch.object(hass.config_entries, 'async_schedule_reload') as reload,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={'source': config_entries.SOURCE_ZEROCONF}, data=_zeroconf()
        )
    assert result['type'] == FlowResultType.ABORT
    assert result['reason'] == 'already_configured'
    assert entry.data[CONF_HOST] == '192.0.2.77'
    reload.assert_called_once_with(entry.entry_id)


# --- a serial port that is closed: the library does not raise, it returns ---


async def _serial_controller(connected: bool) -> tuple[Any, Any]:
    """A controller from the released library on a port that is open or closed."""
    from pyxantech import async_get_amp_controller
    from pyxantech.protocol import RS232ControlProtocol

    loop = asyncio.get_running_loop()
    protocol = RS232ControlProtocol('synthetic', {'timeout': 0.001}, {}, {}, loop)
    if connected:
        transport = MagicMock()
        protocol.connection_made(transport)
    with patch('pyxantech.async_get_rs232_protocol', AsyncMock(return_value=protocol)):
        controller = await async_get_amp_controller('dax88', 'synthetic', loop)
    return controller, protocol


async def test_the_library_swallows_a_command_when_its_port_is_closed() -> None:
    """What the fallback has to live with: no exception, nothing sent."""
    serial, protocol = await _serial_controller(connected=False)
    await serial.set_volume(11, 8)  # returns as if all were well
    assert protocol._transport is None
    assert link_is_down(serial)
    assert not link_is_down(AsyncMock())  # a client without that state is never "down"
    assert not link_is_down((await _serial_controller(connected=True))[0])


async def test_a_command_for_a_closed_serial_port_goes_to_the_fallback() -> None:
    serial, _ = await _serial_controller(connected=False)
    network = AsyncMock()
    amp = FailoverAmp(('serial', serial), ('network', network))

    await amp.set_volume(11, 8)

    network.set_volume.assert_awaited_once_with(11, 8)
    assert amp.active_transport == 'network'


async def test_a_port_that_closes_while_the_command_waits_counts_as_a_failure() -> None:
    serial, protocol = await _serial_controller(connected=True)
    network = AsyncMock()
    amp = FailoverAmp(('serial', serial), ('network', network))

    async def port_drops(*_args: Any) -> None:
        protocol.connection_lost(None)

    with patch.object(serial, 'set_mute', side_effect=port_drops):
        await amp.set_mute(11, True)

    network.set_mute.assert_awaited_once_with(11, True)
    assert amp.active_transport == 'network'


async def test_a_closed_serial_fallback_is_not_taken_for_a_working_one() -> None:
    """The network link fails, the RS-232 fallback is unplugged: an error, no switch."""
    serial, _ = await _serial_controller(connected=False)
    network = AsyncMock()
    network.set_volume.side_effect = TimeoutError('no echo')
    amp = FailoverAmp(('network', network), ('serial', serial))

    with pytest.raises(ConnectionError, match='serial port is closed'):
        await amp.set_volume(11, 8)
    assert amp.active_transport == 'network'


async def test_an_open_serial_port_is_used_as_before() -> None:
    serial, _ = await _serial_controller(connected=True)
    network = AsyncMock()
    amp = FailoverAmp(('serial', serial), ('network', network))
    with patch.object(serial, 'set_volume', AsyncMock()) as sent:
        await amp.set_volume(11, 8)
    sent.assert_awaited_once_with(11, 8)
    network.set_volume.assert_not_awaited()
    assert amp.active_transport == 'serial'
