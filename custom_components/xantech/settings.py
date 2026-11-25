"""Amplifier settings kept on the amp: backups, restore and the guarded factory reset.

These need the amplifier's network (Linkplay) module; RS-232 has no commands for them.
The actions (services) and the options flow both go through this module, so the same
checks apply whichever way the user gets here.
"""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Callable, Coroutine
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .const import (
    BACKUP_DIR,
    CONF_DEVICE_ID,
    CONF_FACTORY_RESET_ENABLED_UNTIL,
    CONF_HOST,
    DOMAIN,
    FACTORY_RESET_ENABLE_MINUTES,
    FACTORY_RESET_PHRASE,
)
from .dax88_network import (
    Dax88NetworkError,
    FactoryResetUnconfirmed,
    check_settings,
)
from .network import async_get_module_status, factory_reset_enabled

if TYPE_CHECKING:
    from . import XantechConfigEntry

LOG = logging.getLogger(__name__)

# entries with a factory reset in progress: from the backup to the end of the
# wait for the amplifier's restart
_RESETTING: set[str] = set()


class AmpSettingsError(ServiceValidationError):
    """A settings action was refused or failed.

    ``key`` names the reason for forms; ``placeholders`` fill that reason's text
    (``detail`` is always the message itself).
    """

    def __init__(
        self,
        message: str,
        key: str = 'amp_no_answer',
        placeholders: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.key = key
        self.placeholders = {'detail': message, **(placeholders or {})}


def network_amp(hass: HomeAssistant, entry_id: str) -> tuple[XantechConfigEntry, Any]:
    """The loaded entry and its network client, or a clear refusal."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if (
        entry is None
        or entry.domain != DOMAIN
        or entry.state is not ConfigEntryState.LOADED
    ):
        raise AmpSettingsError(
            'Choose a loaded amplifier from this integration.', 'not_loaded'
        )
    client = entry.runtime_data.network_amp
    if client is None:
        raise AmpSettingsError(
            "This needs the amplifier's network (Wi-Fi/LAN) module. Add it under "
            'Configure → Connection; RS-232 has no command for it.',
            'needs_network',
        )
    return entry, client


async def _async_finish[T](
    hass: HomeAssistant,
    job: Coroutine[Any, Any, T],
    unwatched: Callable[[T], None],
) -> T:
    """Run ``job`` to its end even if the caller goes away.

    Home Assistant cancels a dialog's job when the dialog is closed. A restore or a
    reset that stops half-way is worse than one nobody is watching, so the job carries
    on, and ``unwatched`` gets its result to tell the user. ``job`` must not raise.
    """
    task = hass.async_create_task(job)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():

            def report(done: asyncio.Task[T]) -> None:
                if not done.cancelled() and done.exception() is None:
                    unwatched(done.result())

            task.add_done_callback(report)
        raise


# --- backup files ---


def backup_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(BACKUP_DIR))


def backup_path(hass: HomeAssistant, name: str) -> Path:
    """Only .json files directly inside <config>/xantech_backups may be read.

    ``name`` is a file name, not a path: one with a folder in it is refused, not cut
    down to its last part. Touches the file system, so not for the event loop.
    """
    if not name or name != Path(name).name or '\\' in name or '/' in name:
        raise AmpSettingsError(
            f'Give the name of a file in {BACKUP_DIR}/, without a folder.',
            'bad_backup_file',
        )
    folder = backup_dir(hass).resolve()
    path = (folder / name).resolve()
    if path.parent != folder or path.suffix != '.json':
        raise AmpSettingsError(
            f'Backups are .json files in {BACKUP_DIR}/.', 'bad_backup_file'
        )
    return path


def amplifier_tag(entry: XantechConfigEntry) -> str | None:
    """A short mark of which amplifier a backup came from, or None if not known.

    A hash, so the backup file does not hold the module's identifier itself.
    """
    device_id = entry.data.get(CONF_DEVICE_ID)
    if not device_id:
        return None
    return hashlib.sha256(str(device_id).encode()).hexdigest()[:16]


async def async_list_backups(
    hass: HomeAssistant, entry: XantechConfigEntry | None = None
) -> list[tuple[str, bool]]:
    """Backup files as (name, from another amplifier), the one to offer first.

    Newest first. With ``entry``, the backups of that amplifier and those that do not
    say where they came from are listed before the ones of another amplifier, so the
    first one is never another amplifier's while this one has a backup of its own.
    """
    tag = amplifier_tag(entry) if entry is not None else None

    def _list() -> list[tuple[str, bool]]:
        folder = backup_dir(hass)
        if not folder.is_dir():
            return []
        found: list[tuple[bool, float, str]] = []
        for file in folder.glob('*.json'):
            other = False
            if tag is not None:
                try:
                    origin = json.loads(file.read_text(encoding='utf-8')).get(
                        'amplifier'
                    )
                except (OSError, ValueError, AttributeError):
                    origin = None
                other = origin is not None and origin != tag
            found.append((other, -file.stat().st_mtime, file.name))
        return [(name, other) for other, _, name in sorted(found)]

    return await hass.async_add_executor_job(_list)


async def async_read_backup(hass: HomeAssistant, name: str) -> dict[str, Any]:
    def _read() -> dict[str, Any]:
        return json.loads(backup_path(hass, name).read_text(encoding='utf-8'))

    try:
        return await hass.async_add_executor_job(_read)
    except (OSError, ValueError) as err:
        raise AmpSettingsError(
            f'Could not read backup {name}: {err}', 'bad_backup_file'
        ) from err


async def async_check_same_amplifier(
    hass: HomeAssistant, entry: XantechConfigEntry
) -> None:
    """Refuse unless the module at the entry's address is still this amplifier.

    The client talks to an address. After a change of addresses on the network another
    amplifier can be there, and a restore or a factory reset would hit that one. The
    module's own identifier is asked for (a read-only request) and compared with the
    one stored when the amplifier was added. An entry without a stored identifier
    cannot be checked and is let through.
    """
    known = entry.data.get(CONF_DEVICE_ID)
    host = entry.data.get(CONF_HOST)
    if not known or not host or known == host:
        return
    try:
        module = await async_get_module_status(hass, host)
    except (OSError, TimeoutError, ValueError) as err:
        raise AmpSettingsError(
            'Could not check that the device at the amplifier\'s network address is '
            f'still this amplifier ({err}). Nothing was changed.'
        ) from err
    if known not in (module.get('uuid'), module.get('MAC')):
        raise AmpSettingsError(
            f'The device at the network address of {entry.title} is a different '
            'amplifier now. Nothing was changed. Enter this amplifier\'s current '
            'address under Configure → Connection.',
            'different_amp',
        )


async def async_export(hass: HomeAssistant, entry: XantechConfigEntry) -> str:
    """Save every name, zone setting and auto-standby to a new backup file."""
    _, client = network_amp(hass, entry.entry_id)
    try:
        settings = await client.export_settings()
    except Dax88NetworkError as err:
        raise AmpSettingsError(f'Could not read the amplifier: {err}') from err
    tag = amplifier_tag(entry)
    if tag is not None:
        # which amplifier this is a backup of (see async_list_backups)
        settings = {**settings, 'amplifier': tag}
    slug = re.sub(r'[\W_]+', '-', entry.title.lower()).strip('-') or 'amplifier'
    stem = f'{slug}-{time.strftime("%Y%m%d-%H%M%S")}'
    text = json.dumps(settings, indent=2)

    def _write() -> str:
        folder = backup_dir(hass)
        folder.mkdir(exist_ok=True)
        for copy in range(1, 100):
            path = folder / (f'{stem}.json' if copy == 1 else f'{stem}-{copy}.json')
            try:
                # 'x': a backup never replaces an earlier one
                with path.open('x', encoding='utf-8') as file:
                    file.write(text)
                    file.flush()
                    os.fsync(file.fileno())
            except FileExistsError:
                continue
            except BaseException:
                # half a file would be offered for a restore: it is ours, remove it
                with contextlib.suppress(OSError):
                    path.unlink()
                raise
            return path.name
        raise OSError(f'too many backups named {stem}')

    try:
        name = await hass.async_add_executor_job(_write)
    except OSError as err:
        raise AmpSettingsError(
            f'Could not save the backup: {err}', 'backup_not_saved'
        ) from err
    LOG.info('Saved amplifier settings to %s', backup_dir(hass) / name)
    return name


async def async_import(
    hass: HomeAssistant,
    entry: XantechConfigEntry,
    backup_file: str,
    *,
    include_power: bool = False,
) -> list[int]:
    """Put names, zone settings and auto-standby back from a backup file.

    Returns the zones (1-8) whose volume and mute were left as they are: zones that
    are on now and off in the backup, when power is not restored (see the client's
    ``import_settings``).
    """
    _, client = network_amp(hass, entry.entry_id)
    if entry.entry_id in _RESETTING:
        # the amplifier's module restarts a minute after a reset; what is sent before
        # that is lost, and the restore would stop half-way
        raise AmpSettingsError(
            'A factory reset of this amplifier is still running. Restore the backup '
            'when it has finished.',
            'reset_running',
        )
    backup = await async_read_backup(hass, backup_file)
    try:
        check_settings(backup)  # before anything is sent
    except ValueError as err:
        raise AmpSettingsError(
            f'{backup_file} cannot be restored: {err}', 'bad_backup_file'
        ) from err
    await async_check_same_amplifier(hass, entry)

    async def restore() -> AmpSettingsError | list[int]:
        try:
            left_alone = await client.import_settings(
                backup, include_power=include_power
            )
        except (ValueError, Dax88NetworkError) as err:
            return AmpSettingsError(
                f'The restore of {backup_file} did not finish: {err}',
                'restore_incomplete',
            )
        except Exception as err:  # noqa: BLE001 - reported like any other failure
            LOG.exception('Restore of %s failed unexpectedly', backup_file)
            return AmpSettingsError(
                f'The restore of {backup_file} did not finish: {err!r}',
                'restore_incomplete',
            )
        return sorted(left_alone or [])

    def unwatched(result: AmpSettingsError | list[int]) -> None:
        failed = isinstance(result, AmpSettingsError)
        persistent_notification.async_create(
            hass,
            f'{result} Some settings may already be back; run the restore again '
            '(it only sends what still differs).'
            if failed
            else restore_summary(entry.title, backup_file, include_power, result),
            title='Amplifier restore did not finish'
            if failed
            else 'Amplifier restore finished',
            notification_id=f'{DOMAIN}_restore_{entry.entry_id}',
        )

    result = await _async_finish(hass, restore(), unwatched)
    if isinstance(result, AmpSettingsError):
        raise result
    if entry.state is ConfigEntryState.LOADED:
        await entry.runtime_data.coordinator.async_command_sent()
    return result


def zone_list(zones: list[int]) -> str:
    """Zones named in a sentence: zone 1; zones 1 and 4; zones 1, 4 and 6."""
    numbers = [str(zone) for zone in zones]
    if len(numbers) == 1:
        return f'zone {numbers[0]}'
    return f'zones {", ".join(numbers[:-1])} and {numbers[-1]}'


def restore_summary(
    title: str, backup_file: str, include_power: bool, left_alone: list[int]
) -> str:
    """What a finished restore did, for the notification."""
    text = (
        f'Names, zone settings and auto-standby from `{backup_file}` are back on '
        f'**{title}**.'
    )
    if not include_power:
        text += ' Zones were not switched on or off.'
    if left_alone:
        text += (
            f' Not changed: the volume and mute of {zone_list(left_alone)}, on now '
            'and off in the backup. Raising the volume or unmuting there would have '
            'been heard.'
        )
    return text


# --- factory reset: kill switch, typed phrase, backup first ---


def _switch_notice_id(entry: XantechConfigEntry) -> str:
    return f'{DOMAIN}_factory_reset_{entry.entry_id}'


def enable_factory_reset_options(
    options: dict[str, Any], enabled: bool
) -> dict[str, Any]:
    """Options with the kill switch on (for a limited time) or off."""
    new = {k: v for k, v in options.items() if k != CONF_FACTORY_RESET_ENABLED_UNTIL}
    if enabled:
        new[CONF_FACTORY_RESET_ENABLED_UNTIL] = (
            time.time() + FACTORY_RESET_ENABLE_MINUTES * 60
        )
    return new


def async_disable_factory_reset(hass: HomeAssistant, entry: XantechConfigEntry) -> None:
    """Turn the kill switch off and remove its notice."""
    if CONF_FACTORY_RESET_ENABLED_UNTIL in entry.options:
        hass.config_entries.async_update_entry(
            entry, options=enable_factory_reset_options(dict(entry.options), False)
        )
    persistent_notification.async_dismiss(hass, _switch_notice_id(entry))


def async_show_switch_notice(
    hass: HomeAssistant, entry: XantechConfigEntry, remaining: float
) -> None:
    persistent_notification.async_create(
        hass,
        f'Factory reset is enabled for **{entry.title}** for the next '
        f'{max(1, round(remaining / 60))} minutes. It turns itself off after that, or '
        "turn it off now under the integration's Configure → Factory reset. A factory "
        "reset erases every zone and input name and every zone's input, volume, bass, "
        'treble and balance on the amplifier.',
        title='Amplifier factory reset is enabled',
        notification_id=_switch_notice_id(entry),
    )


def reset_running(entry: XantechConfigEntry) -> bool:
    """True from the backup until the amplifier is back after a factory reset."""
    return entry.entry_id in _RESETTING


def _check_reset_allowed(
    hass: HomeAssistant, entry: XantechConfigEntry, client: Any, confirm: str
) -> None:
    """The guards that need no amplifier: same loaded entry, switch on, exact phrase."""
    current = hass.config_entries.async_get_entry(entry.entry_id)
    if (
        current is None
        or current.state is not ConfigEntryState.LOADED
        or current.runtime_data.network_amp is not client
    ):
        raise AmpSettingsError(
            'The amplifier was reloaded or removed meanwhile. Nothing was reset.',
            'not_loaded',
        )
    if not factory_reset_enabled(dict(current.options)):
        raise AmpSettingsError(
            'Factory reset is switched off. Turn on "Allow factory reset" under this '
            "integration's Configure → Factory reset first; it switches itself off "
            f'again after {FACTORY_RESET_ENABLE_MINUTES} minutes.',
            'switch_off',
        )
    if confirm != FACTORY_RESET_PHRASE:
        raise AmpSettingsError(
            f'Type {FACTORY_RESET_PHRASE} in the confirm field to reset the amplifier.',
            'confirm_mismatch',
        )


async def async_factory_reset(
    hass: HomeAssistant, entry: XantechConfigEntry, *, confirm: str
) -> str:
    """Reset the amplifier to factory settings. Returns the backup file to restore from.

    Refused unless all three hold: the kill switch is on, ``confirm`` is the exact
    phrase, and a backup of this amplifier was saved just now, read back from its file
    and found restorable. Once the reset has been sent it is always reported: a
    notification names the backup, and the kill switch goes off.
    """
    _, client = network_amp(hass, entry.entry_id)
    _check_reset_allowed(hass, entry, client, confirm)
    if entry.entry_id in _RESETTING:
        raise AmpSettingsError(
            'A factory reset of this amplifier is already running.', 'reset_running'
        )
    _RESETTING.add(entry.entry_id)
    job = None
    try:
        # before the backup: another amplifier's settings must not be saved under
        # this one's name either
        await async_check_same_amplifier(hass, entry)
        backup_name = await async_export(hass, entry)
        backup = await async_read_backup(hass, backup_name)
        try:
            check_settings(backup)
        except ValueError as err:
            raise AmpSettingsError(f'{err}', 'backup_unusable') from err
        # the backup took a moment: the switch may have been turned off or run out, or
        # the entry reloaded, while it was made
        _check_reset_allowed(hass, entry, client, confirm)
        job = _async_send_reset(hass, entry, client, backup, backup_name)
    finally:
        if job is None:
            _RESETTING.discard(entry.entry_id)
    # from here the job runs to its end, watched or not, and reports by itself
    failure = await _async_finish(hass, job, lambda _failure: None)
    if failure is not None:
        raise failure
    return backup_name


async def _async_send_reset(
    hass: HomeAssistant,
    entry: XantechConfigEntry,
    client: Any,
    backup: dict[str, Any],
    backup_name: str,
) -> AmpSettingsError | None:
    """Send the reset, wait for the amplifier and report. Returns what went wrong."""
    failure: AmpSettingsError | None = None
    try:
        try:
            await client.factory_reset(FACTORY_RESET_PHRASE, backup, wait=False)
            await client.wait_for_restart()
        except PermissionError as err:  # the client's own kill switch: nothing sent
            return AmpSettingsError(f'Factory reset not done: {err}', 'switch_off')
        except ValueError as err:  # the client refused the backup: nothing sent
            return AmpSettingsError(f'{err}', 'backup_unusable')
        except FactoryResetUnconfirmed as err:
            failure = AmpSettingsError(
                f'Factory reset of {entry.title}: {err}. Treat the amplifier as '
                f'reset; its settings from before are in {backup_name}.',
                'reset_unconfirmed',
                {'backup_file': backup_name},
            )
        except Dax88NetworkError as err:  # it never reached the amplifier
            return AmpSettingsError(f'Factory reset not sent: {err}')

        # the reset has gone out: always leave a trace, and close the switch
        LOG.warning(
            'Factory reset %s (%s); settings saved in %s',
            entry.title,
            'not confirmed by the amplifier' if failure else 'done',
            backup_name,
        )
        restore_hint = (
            "open the integration's Configure → Backup and restore and choose "
            f'`{backup_name}`.'
        )
        if failure is None:
            message = (
                f'**{entry.title}** was reset to factory settings. To put your names '
                f'and zone settings back, {restore_hint}'
            )
            title = 'Amplifier factory reset done'
        else:
            message = (
                f'A factory reset was sent to **{entry.title}**, but the amplifier did '
                'not confirm it or has not come back on the network yet. Treat it as '
                'reset. Once it is back, to put your names and zone settings back, '
                f'{restore_hint}'
            )
            title = 'Amplifier factory reset not confirmed'
        persistent_notification.async_create(
            hass,
            message,
            title=title,
            notification_id=f'{DOMAIN}_factory_reset_done_{entry.entry_id}',
        )
        if hass.config_entries.async_get_entry(entry.entry_id) is not None:
            async_disable_factory_reset(hass, entry)  # one reset per opt-in
        return failure
    except Exception:
        LOG.exception('Factory reset of %s failed unexpectedly', entry.title)
        return failure or AmpSettingsError(
            'Something unexpected went wrong during the factory reset.', 'unknown'
        )
    finally:
        _RESETTING.discard(entry.entry_id)
