"""Restore and reset: the right amplifier, the right moment, a true report.

Found by an independent review: another amplifier at the stored address, a restore
started while a reset is still waiting for the amplifier's restart, a restore picker
that offers another amplifier's backup, file names with a folder, half-written backups,
and result texts that said more than was true.
"""
# ruff: noqa: F811 - the amp fixture is shared with test_amp_settings

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xantech import settings
from custom_components.xantech.const import CONF_DEVICE_ID, DOMAIN
from custom_components.xantech.dax88_network import Dax88NetworkError

from .conftest import full_export
from .test_amp_settings import (  # noqa: F401 - amp is a fixture
    _after_progress,
    _all_names,
    _default,
    _entry,
    _loaded,
    _step,
    _submit,
    amp,
)
from .test_reset_safety import _reset_action, _switch_on, _until


async def _import_action(
    hass: HomeAssistant, entry: MockConfigEntry, name: str, **data: Any
) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        'import_settings',
        {'config_entry_id': entry.entry_id, 'backup_file': name, **data},
        blocking=True,
        return_response=True,
    )


def _backup(tmp_path: Any, name: str = 'saved.json', **changes: Any) -> str:
    folder = tmp_path / 'xantech_backups'
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(json.dumps(full_export(**changes)))
    return name


def _another_amplifier(module_identity: AsyncMock) -> None:
    """The address now belongs to a different amplifier."""
    module_identity.side_effect = AsyncMock(
        return_value={'uuid': 'SOMEONE-ELSE', 'project': 'DAX88'}
    )


# --- the amplifier at the address is still this one ---


async def test_restore_is_refused_when_another_amplifier_has_the_address(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, module_identity: AsyncMock
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    name = _backup(tmp_path)
    _another_amplifier(module_identity)

    with pytest.raises(ServiceValidationError, match='different amplifier'):
        await _import_action(hass, entry, name)
    result = await _step(hass, entry, 'backup', 'restore_backup')
    result = await _after_progress(
        hass, await _submit(hass, result, {'backup_file': name})
    )
    assert (result['type'], result['reason']) == (FlowResultType.ABORT, 'different_amp')
    amp.import_settings.assert_not_called()


async def test_reset_is_refused_when_another_amplifier_has_the_address(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, module_identity: AsyncMock
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())
    _another_amplifier(module_identity)

    with pytest.raises(ServiceValidationError, match='different amplifier'):
        await _reset_action(hass, entry)
    amp.factory_reset.assert_not_called()
    # and its settings were not saved under this amplifier's name either
    amp.export_settings.assert_not_called()
    assert not (tmp_path / 'xantech_backups').exists()
    assert not settings.reset_running(entry)


async def test_reset_is_refused_when_the_module_cannot_say_who_it_is(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, module_identity: AsyncMock
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())
    module_identity.side_effect = ConnectionError('192.0.2.50: timed out')

    with pytest.raises(ServiceValidationError, match='Could not check'):
        await _reset_action(hass, entry)
    amp.factory_reset.assert_not_called()
    amp.export_settings.assert_not_called()


async def test_the_identifier_stored_as_a_mac_address_is_accepted(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, module_identity: AsyncMock
) -> None:
    """Setup stores the uuid, or the MAC address when the module reports no uuid."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    module_identity.side_effect = AsyncMock(return_value={'MAC': 'TESTUUID'})
    await _import_action(hass, entry, _backup(tmp_path))
    amp.import_settings.assert_awaited_once()


async def test_an_entry_without_a_stored_identifier_is_not_checked(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any, module_identity: AsyncMock
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != CONF_DEVICE_ID}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    module_identity.side_effect = ConnectionError('never asked')

    await _import_action(hass, entry, _backup(tmp_path))
    amp.import_settings.assert_awaited_once()
    module_identity.assert_not_called()


# --- a restore does not start while a reset is waiting for the restart ---


async def test_restore_is_refused_while_a_reset_waits_for_the_restart(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass, _switch_on())
    back = asyncio.Event()
    amp.wait_for_restart.side_effect = back.wait
    reset = hass.async_create_task(_reset_action(hass, entry))
    try:
        await _until(lambda: amp.wait_for_restart.await_count)
        (saved,) = (tmp_path / 'xantech_backups').iterdir()
        with pytest.raises(ServiceValidationError, match='still running'):
            await _import_action(hass, entry, saved.name, include_power=True)
        amp.import_settings.assert_not_called()
    finally:
        back.set()
    await reset
    await hass.async_block_till_done()  # the switch going off reloads the entry
    # once the amplifier is back the same restore goes through
    await _import_action(hass, entry, saved.name, include_power=True)
    amp.import_settings.assert_awaited_once()


# --- backup files ---


async def test_a_backup_says_which_amplifier_it_is_of(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'backup', 'backup_now')
    name = result['description_placeholders']['backup_file']
    text = (tmp_path / 'xantech_backups' / name).read_text()
    assert json.loads(text)['amplifier'] == settings.amplifier_tag(entry)
    assert 'TESTUUID' not in text  # a mark, not the module's identifier itself


async def test_restore_offers_this_amplifiers_own_backup_first(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    folder = tmp_path / 'xantech_backups'
    _backup(tmp_path, 'unmarked-old.json')
    _backup(tmp_path, 'own.json', amplifier=settings.amplifier_tag(entry))
    _backup(tmp_path, 'other-newest.json', amplifier='0123456789abcdef')
    for age, name in enumerate(('other-newest.json', 'own.json', 'unmarked-old.json')):
        os.utime(folder / name, (1_800_000_000 - age * 60,) * 2)

    result = await _step(hass, entry, 'backup', 'restore_backup')
    assert _default(result, 'backup_file') == 'own.json'
    selector = result['data_schema'].schema['backup_file']
    assert [(o['value'], o['label']) for o in selector.config['options']] == [
        ('own.json', 'own.json'),
        ('unmarked-old.json', 'unmarked-old.json'),
        ('other-newest.json', 'other-newest.json (another amplifier)'),
    ]


async def test_a_backup_file_name_with_a_folder_is_refused(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    """Not cut down to its last part: that would open another file than asked for."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    _backup(tmp_path, 'saved.json')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    (elsewhere / 'saved.json').write_text(json.dumps(full_export()))

    for name in (
        str(elsewhere / 'saved.json'),
        '../elsewhere/saved.json',
        'xantech_backups/saved.json',
        '..\\elsewhere\\saved.json',
        '',
    ):
        with pytest.raises(ServiceValidationError, match='without a folder'):
            await _import_action(hass, entry, name)
    amp.import_settings.assert_not_called()
    await _import_action(hass, entry, 'saved.json')
    amp.import_settings.assert_awaited_once()


async def test_a_backup_that_could_not_be_written_leaves_no_file(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    """Half a file would be the first one offered for the next restore."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    with patch(
        'custom_components.xantech.settings.os.fsync',
        side_effect=OSError(28, 'No space left on device'),
    ):
        result = await _step(hass, entry, 'backup', 'backup_now')
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'backup_not_saved',
    )
    assert list((tmp_path / 'xantech_backups').iterdir()) == []


# --- what the user is told ---


@pytest.mark.parametrize(
    ('include_power', 'left_alone', 'page', 'zones'),
    [
        (True, [], 'restore_done', ''),
        (False, [], 'restore_done_no_power', ''),
        (False, [4, 1], 'restore_done_left_alone', 'zones 1 and 4'),
        (False, [6], 'restore_done_left_alone', 'zone 6'),
    ],
)
async def test_the_restore_result_says_what_was_and_was_not_restored(
    hass: HomeAssistant,
    amp: MagicMock,
    tmp_path: Any,
    include_power: bool,
    left_alone: list[int],
    page: str,
    zones: str,
) -> None:
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    name = _backup(tmp_path)
    amp.import_settings.return_value = left_alone

    result = await _step(hass, entry, 'backup', 'restore_backup')
    result = await _after_progress(
        hass,
        await _submit(
            hass, result, {'backup_file': name, 'include_power': include_power}
        ),
    )
    assert (result['type'], result['step_id']) == (FlowResultType.FORM, page)
    assert result['description_placeholders'] == {'backup_file': name, 'zones': zones}
    result = await _submit(hass, result, {})  # "Next" works from every result page
    assert (result['type'], result['step_id']) == (FlowResultType.MENU, 'init')

    response = await _import_action(hass, entry, name, include_power=include_power)
    assert response == {'zones_left_alone': sorted(left_alone)}


def test_the_restore_notification_says_the_same() -> None:
    text = settings.restore_summary('Amp', 'a.json', True, [])
    assert 'a.json' in text and 'not switched' not in text and 'Not changed' not in text
    text = settings.restore_summary('Amp', 'a.json', False, [])
    assert 'not switched on or off' in text and 'Not changed' not in text
    text = settings.restore_summary('Amp', 'a.json', False, [2, 3, 5])
    assert 'volume and mute of zones 2, 3 and 5' in text


async def test_an_unexpected_error_in_a_restore_is_reported_as_unfinished(
    hass: HomeAssistant, amp: MagicMock, tmp_path: Any
) -> None:
    """Whatever stops a restore, part of the backup may already be on the amplifier."""
    hass.config.config_dir = str(tmp_path)
    entry = await _loaded(hass)
    name = _backup(tmp_path)
    amp.import_settings.side_effect = TypeError("'float' object is not an integer")

    result = await _step(hass, entry, 'backup', 'restore_backup')
    result = await _after_progress(
        hass, await _submit(hass, result, {'backup_file': name})
    )
    assert (result['type'], result['reason']) == (
        FlowResultType.ABORT,
        'restore_incomplete',
    )
    assert 'float' in result['description_placeholders']['detail']


async def test_names_that_may_be_half_written_are_read_again(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'amp_names')
    reads = amp.names.await_count
    amp.set_names.side_effect = Dax88NetworkError('no reply to the second rename')
    result = await _submit(hass, result, _all_names(amp_zone_1='Living Room'))
    # not "Nothing was changed": the first of two renames may have gone through
    assert result['errors'] == {'base': 'amp_stopped_answering'}

    amp.set_names.side_effect = None
    result = await _submit(hass, result, _all_names(amp_zone_1='Living Room'))
    assert amp.names.await_count == reads + 1  # compared with what the amp holds now
    assert result['type'] == FlowResultType.MENU


async def test_standby_that_may_have_been_stored_is_not_called_unchanged(
    hass: HomeAssistant, amp: MagicMock
) -> None:
    entry = await _loaded(hass)
    result = await _step(hass, entry, 'standby')
    amp.set_auto_standby.side_effect = Dax88NetworkError('no echo')
    result = await _submit(
        hass,
        result,
        {'standby_enabled': True, 'standby_hours': 4, 'standby_minutes': '0'},
    )
    assert result['errors'] == {'base': 'amp_stopped_answering'}

