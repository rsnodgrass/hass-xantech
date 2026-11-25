"""Replacing the released integration in place: what Home Assistant stores is kept.

The entry, device and entities are written the way upstream v0.4.0 leaves them (and
disabled, as they are while a copy is being swapped), then the entry is enabled on
this code. Every entity id, every user setting and the device must come back.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xantech.const import DOMAIN

PORT = '/dev/ttyUSB0'
AMP = 'dax88__dev_ttyusb0'
ZONES = {'11': 'Living Room', '12': 'Kitchen', '13': 'Patio'}
SOURCES = {'1': {'name': 'Streamer'}, '5': {'name': 'TV'}}
STATUS = {
    'power': True,
    'volume': 10,
    'mute': False,
    'source': 1,
    'bass': 7,
    'treble': 7,
    'balance': 10,
}
CONNECTION_ID = 'sensor.hi_fi_connection'


def _stored_rows() -> list[dict[str, Any]]:
    """Entity registry rows as the released integration created them."""
    rows: list[dict[str, Any]] = []
    for zone, name in ZONES.items():
        slug = name.lower().replace(' ', '_')
        rows.append(
            {
                'entity_id': f'media_player.xantech_dax88_dev_ttyusb0_{slug}',
                'unique_id': f'{DOMAIN}_{AMP}_zone_{zone}',
                'original_name': name,
            }
        )
        rows.extend(
            {
                'entity_id': f'number.xantech_dax88_dev_ttyusb0_{slug}_{control}',
                'unique_id': f'{DOMAIN}_{AMP}_zone_{zone}_{control}',
                'original_name': f'{name} {control.title()}',
            }
            for control in ('bass', 'treble', 'balance')
        )
    return rows


def _client() -> MagicMock:
    client = MagicMock()
    client.zone_status = AsyncMock(return_value=dict(STATUS))
    for method in (
        'set_power',
        'set_volume',
        'set_mute',
        'set_source',
        'set_bass',
        'set_treble',
        'set_balance',
    ):
        setattr(client, method, AsyncMock())
    client.close = MagicMock()
    return client


def _ids(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, str]:
    registry = er.async_get(hass)
    return {
        row.unique_id: row.entity_id
        for row in er.async_entries_for_config_entry(registry, entry.entry_id)
    }


async def test_a_disabled_entry_of_the_released_version_comes_back_whole(
    hass: HomeAssistant,
) -> None:
    """Enable the stored entry on this code, then add the network fallback."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Hi-Fi',
        version=1,
        minor_version=1,
        unique_id=f'{DOMAIN}_{PORT}',
        data={
            'amp_type': 'dax88',
            'enable_audio_controls': True,
            'port': PORT,
            'sources': SOURCES,
            'zones': {zone: {'name': name} for zone, name in ZONES.items()},
        },
        options={},
        disabled_by=ConfigEntryDisabler.USER,
    )
    entry.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, 'dax88__dev_ttyUSB0')},
        name='Xantech dax88__dev_ttyUSB0',
        manufacturer='Dayton Audio',
        model='Multi-Zone Amplifier',
    )
    devices.async_update_device(
        device.id,
        name_by_user='Hi-Fi',
        disabled_by=dr.DeviceEntryDisabler.CONFIG_ENTRY,
    )
    stored = _stored_rows()
    for row in stored:
        domain, object_id = row['entity_id'].split('.')
        made = entities.async_get_or_create(
            domain,
            DOMAIN,
            row['unique_id'],
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=object_id,
            original_name=row['original_name'],
            disabled_by=er.RegistryEntryDisabler.CONFIG_ENTRY,
        )
        if made.entity_id != row['entity_id']:
            entities.async_update_entity(made.entity_id, new_entity_id=row['entity_id'])
    # a name and an icon the user gave one zone
    renamed = stored[0]['entity_id']
    entities.async_update_entity(renamed, name='Lounge', icon='mdi:sofa')
    before = _ids(hass, entry)
    assert before == {row['unique_id']: row['entity_id'] for row in stored}

    serial, network = _client(), _client()
    network.sync = AsyncMock()
    network.version = AsyncMock(return_value='V6.5')
    with (
        patch(
            'custom_components.xantech.async_get_amp_controller',
            AsyncMock(return_value=serial),
        ),
        patch('custom_components.xantech.Dax88NetworkAmp', return_value=network),
    ):
        # what the Enable button does
        await hass.config_entries.async_set_disabled_by(entry.entry_id, None)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert entry.title == 'Hi-Fi'

        after = _ids(hass, entry)
        assert {uid: after.get(uid) for uid in before} == before
        # one new entity; it is named after the device as the user called it
        assert set(after.values()) - set(before.values()) == {CONNECTION_ID}
        assert not [entity_id for entity_id in after.values() if entity_id[-2:] == '_2']
        for entity_id in after.values():
            row = entities.async_get(entity_id)
            assert row is not None and row.disabled_by is None, entity_id
            state = hass.states.get(entity_id)
            assert state is not None and state.state != 'unavailable', entity_id
        assert hass.states.get(CONNECTION_ID).state == 'serial'
        kept = entities.async_get(renamed)
        assert (kept.name, kept.icon) == ('Lounge', 'mdi:sofa')
        player = hass.states.get(renamed)
        assert player.attributes['source_list'] == ['Streamer', 'TV']

        found = dr.async_entries_for_config_entry(devices, entry.entry_id)
        assert [found_device.id for found_device in found] == [device.id]
        assert found[0].disabled_by is None
        assert found[0].name_by_user == 'Hi-Fi'

        # add the network module as the fallback link, then leave by Done
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result['flow_id'], {'next_step_id': 'connection'}
        )
        with patch(
            'custom_components.xantech.config_flow.async_check_linkplay_amp',
            AsyncMock(
                return_value={'uuid': 'TESTUUID', 'project': 'DAYTONAUDIO_DAX88'}
            ),
        ):
            result = await hass.config_entries.options.async_configure(
                result['flow_id'],
                {
                    'connection_mode': 'serial_fallback_network',
                    'port': PORT,
                    'host': '192.0.2.50',
                },
            )
        assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')
        result = await hass.config_entries.options.async_configure(
            result['flow_id'], {'next_step_id': 'done'}
        )
        assert result['type'] == FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert entry.data['connection_mode'] == 'serial_fallback_network'
        assert entry.data['zones'] == {z: {'name': n} for z, n in ZONES.items()}
        assert entry.data['sources'] == SOURCES
        assert _ids(hass, entry) == after

        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
