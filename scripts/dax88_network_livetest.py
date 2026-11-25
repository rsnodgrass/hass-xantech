"""Live test of pyxantech's DAX88 network client, one phase at a time.

Usage (PYTHONPATH must include the pyxantech checkout):
    python scripts/dax88_network_livetest.py <amp-ip> snapshot <file.json>
    python scripts/dax88_network_livetest.py <amp-ip> phase1|phase2|phase3
    python scripts/dax88_network_livetest.py <amp-ip> restore <file.json>
    python scripts/dax88_network_livetest.py <amp-ip> factory-reset <backup.json>

Each phase changes the amp through the library, reads it back over the network, and prints
the RS-232 values Home Assistant should now show for zones 1-6 (as JSON, keyed 11-16), so
they can be compared with HA's RS-232 reading. The phases only touch zones that are off
(4-6) and the preamp outputs (7-8); zone 4 is muted before it is powered on.
"""

from __future__ import annotations

import asyncio
import json
import sys

from pyxantech.dax88_network import Dax88NetworkAmp

FIELDS = ('source', 'volume', 'treble', 'bass', 'balance', 'power', 'mute')
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = '') -> None:
    results.append((label, ok, detail))
    print(f'{"PASS" if ok else "FAIL"}  {label}{"  " + detail if detail else ""}')


async def expect(amp: Dax88NetworkAmp, zone: int, **want: object) -> None:
    got = await amp.zone_status(zone)
    assert got is not None
    bad = {k: (v, got[k]) for k, v in want.items() if got[k] != v}
    check(
        f'zone {zone} reads back {want}', not bad, f'(want, got) {bad}' if bad else ''
    )


async def rs232_view(amp: Dax88NetworkAmp) -> dict[str, dict[str, object]]:
    zones = await amp.all_zone_statuses()
    return {str(10 + z): {k: zones[z][k] for k in FIELDS} for z in range(1, 7)}


async def phase1(amp: Dax88NetworkAmp) -> None:
    status = await amp.sync()
    check(
        'sync: status + names',
        len(status['zones']) == 8,
        f'firmware {status["firmware"]}',
    )
    standby = await amp.get_auto_standby()
    check('auto-standby read', standby is None or standby % 30 == 0, f'{standby} min')
    await amp.set_volume(14, 0)
    await amp.set_treble(14, 24)
    await amp.set_bass(14, 0)
    await amp.set_balance(14, 0)
    await amp.set_source(14, 1)
    await amp.set_volume(15, 20)
    await amp.set_treble(15, 6)
    await amp.set_bass(15, 18)
    await amp.set_balance(15, 20)
    await amp.set_source(15, 8)
    await amp.set_balance_fine(16, 32)  # between RS-232 steps 10 and 11
    await amp.set_source(16, 3)
    await expect(amp, 4, volume=0, treble=24, bass=0, balance=0, source=1)
    await expect(amp, 5, volume=20, treble=6, bass=18, balance=20, source=8)
    z6 = await amp.zone_status(6)
    check(
        'zone 6 fine balance 32 accepted',
        z6['balance_fine'] == 32,
        f'got {z6["balance_fine"]}',
    )


async def phase2(amp: Dax88NetworkAmp) -> None:
    await amp.set_mute(14, True)  # muted first, so powering on is silent
    await amp.set_power(14, True)
    await expect(amp, 4, power=True, mute=True)
    await amp.sync_volume([15, 16], 15)
    await expect(amp, 5, volume=15)
    await expect(amp, 6, volume=15)
    await amp.volume_up([15])
    await amp.volume_down([16])
    await expect(amp, 5, volume=16)
    await expect(amp, 6, volume=14)
    await amp.set_mute_zones([15, 16], True)
    await expect(amp, 5, mute=True)
    await expect(amp, 6, mute=True)


async def phase3(amp: Dax88NetworkAmp) -> None:
    await amp.set_power(14, False)
    await amp.set_mute_zones([14, 15, 16], False)
    await expect(amp, 4, power=False, mute=False)
    await amp.set_mute_zones([17, 18], True)
    await amp.set_power_zones([17, 18], True)
    await expect(amp, 7, power=True, mute=True)
    await expect(amp, 8, power=True, mute=True)
    await amp.set_power_zones([17, 18], False)
    await amp.set_mute_zones([17, 18], False)
    await expect(amp, 7, power=False, mute=False)
    await amp.all_off()
    zones = await amp.all_zone_statuses()
    check('all_off: every zone off', not any(z['power'] for z in zones.values()))
    names = await amp.names()
    # stay within the amp's 132-character budget for all names together
    await amp.set_zone_name(7, 'Z7')
    await amp.set_input_name(6, 'In6')
    await amp.set_device_name('DAX 88_TEST')
    new = await amp.names()
    check('zone rename', new['zones'][7] == 'Z7', new['zones'][7])
    check('input rename', new['inputs'][6] == 'In6', new['inputs'][6])
    check('device rename', new['device'] == 'DAX 88_TEST', new['device'])
    await amp.set_zone_name(7, names['zones'][7])
    await amp.set_input_name(6, names['inputs'][6])
    await amp.set_device_name(names['device'])
    back = await amp.names()
    check('names put back', back == names)
    before = await amp.get_auto_standby()
    await amp.set_auto_standby(60)
    check('auto-standby set 60 min', await amp.get_auto_standby() == 60)
    await amp.set_auto_standby(None)
    check('auto-standby off', await amp.get_auto_standby() is None)
    await amp.set_auto_standby(before)
    check(
        'auto-standby put back', await amp.get_auto_standby() == before, f'{before} min'
    )
    for bad, call in (
        ('17-character name refused', amp.set_zone_name(7, 'A' * 17)),
        ('rename over the 132-character total refused', amp.set_zone_name(8, 'Z' * 16)),
        ('45-minute standby refused', amp.set_auto_standby(45)),
        ('volume 39 refused', amp.set_volume(17, 39)),
        (
            'factory reset refused while the kill switch is off',
            amp.factory_reset('FACTORY RESET', {}),
        ),
    ):
        try:
            await call
            check(bad, False, 'was sent!')
        except (ValueError, PermissionError):
            check(bad, True)


async def snapshot(amp: Dax88NetworkAmp, path: str) -> None:
    data = await amp.sync()
    data['auto_standby'] = await amp.get_auto_standby()
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, default=str)
    print(f'saved {path}')


async def restore(amp: Dax88NetworkAmp, path: str) -> None:
    with open(path, encoding='utf-8') as fh:
        snap = json.load(fh)
    for key, z in snap['zones'].items():
        n = int(key)
        await amp.set_source(n, z['source'])
        await amp.set_volume(n, z['volume'])
        await amp.set_treble(n, z['treble'])
        await amp.set_bass(n, z['bass'])
        await amp.set_balance_fine(n, z['balance_fine'])
        await amp.set_mute(n, z['mute'])
        await amp.set_power(n, z['power'])
    await amp.set_auto_standby(snap['auto_standby'])
    now = await amp.sync()
    for key, z in snap['zones'].items():
        got = now['zones'][int(key)]
        bad = {k: (v, got[k]) for k, v in z.items() if got[k] != v}
        check(f'zone {key} restored', not bad, f'(want, got) {bad}' if bad else '')
    check('names unchanged', json.loads(json.dumps(now['names'])) == snap['names'])
    check('auto-standby restored', await amp.get_auto_standby() == snap['auto_standby'])


async def factory_reset_test(host: str, path: str) -> None:
    """Kill switch on, fresh backup, reset, then put everything back (power too)."""
    from pyxantech.dax88_network import FACTORY_RESET_CONFIRMATION, FactoryResetDisabled

    locked = Dax88NetworkAmp(host, timeout=2.5, retries=4)
    try:
        await locked.factory_reset(
            FACTORY_RESET_CONFIRMATION, await locked.export_settings()
        )
        check('kill switch off blocks the reset', False, 'reset was sent!')
    except FactoryResetDisabled:
        check('kill switch off blocks the reset', True)
    finally:
        locked.close()

    amp = Dax88NetworkAmp(host, timeout=2.5, retries=4, allow_factory_reset=True)
    try:
        backup = await amp.export_settings()
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(backup, fh, indent=2)
        print(f'backup saved to {path}')
        try:
            await amp.factory_reset('yes', backup)
            check('wrong phrase blocks the reset', False, 'reset was sent!')
        except ValueError:
            check('wrong phrase blocks the reset', True)
        await amp.factory_reset(FACTORY_RESET_CONFIRMATION, backup)
        await asyncio.sleep(5)
        names = await amp.names()
        zones = await amp.all_zone_statuses()
        check(
            'reset: names back to factory',
            names['zones'][1] == 'ZONE1' and names['inputs'][1] == 'Input1',
            f'{names["zones"][1]!r} {names["inputs"][1]!r}',
        )
        check(
            'reset: zone n on input n, volume 4',
            all(z['source'] == n and z['volume'] == 4 for n, z in zones.items()),
        )
        await amp.import_settings(backup, include_power=True)
        after = await amp.export_settings()
        same = {k: v for k, v in after.items() if k != 'exported_at'} == {
            k: v for k, v in backup.items() if k != 'exported_at'
        }
        check('restore: every name and setting matches the backup', same)
    finally:
        amp.close()


async def main() -> None:
    host, phase, *rest = sys.argv[1:]
    amp = Dax88NetworkAmp(host, timeout=2.5, retries=4)
    try:
        if phase == 'factory-reset':
            amp.close()
            await factory_reset_test(host, rest[0])
            amp = Dax88NetworkAmp(host, timeout=2.5, retries=4)
        elif phase == 'snapshot':
            await snapshot(amp, rest[0])
        elif phase == 'restore':
            await restore(amp, rest[0])
        else:
            await {'phase1': phase1, 'phase2': phase2, 'phase3': phase3}[phase](amp)
        print('RS232_EXPECT ' + json.dumps(await rs232_view(amp)))
    finally:
        amp.close()
    failed = [r for r in results if not r[1]]
    print(f'{len(results) - len(failed)}/{len(results)} checks passed')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    asyncio.run(main())
