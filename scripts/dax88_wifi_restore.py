"""Restore DAX88 settings over Wi-Fi/LAN (Linkplay TCP 8899) from a backup JSON.

Usage:
    python scripts/dax88_wifi_restore.py <amp-ip> <backup.json> [--use-pre-test] [--dry-run]

Restores the device name, zone names 1-8, input names 1-7, and for each zone the input,
volume, treble, bass and balance, plus auto-standby. Power and mute are left alone.
Each command must be echoed by the amp. Afterwards everything is read back and compared.
--use-pre-test takes the device name and standby from the backup's "before_todays_tests".
--dry-run prints the frames without connecting.
See pyxantech/docs/dax88-network.md for the protocol.
"""

from __future__ import annotations

import json
import socket
import sys
import time

PACE = 0.3  # Linkplay asks for >= 200 ms between commands


def frame(body: bytes) -> bytes:
    payload = b'MCU+PAS+' + body
    return (
        bytes([0x18, 0x96, 0x18, 0x20])
        + len(payload).to_bytes(4, 'little')
        + sum(payload).to_bytes(4, 'little')
        + bytes(8)
        + payload
    )


def zone_mask(zone: int) -> bytes:
    return bytes(1 if z == zone else 2 for z in range(1, 9))


def name_body(kind: int, index: int, name: str) -> bytes:
    raw = name.encode('ascii')
    if not 1 <= len(raw) <= 16 or not all(32 <= c < 127 for c in raw):
        sys.exit(f'refusing name {name!r}: must be 1-16 printable ASCII characters')
    return bytes([0x82, 0x13, kind, index, len(raw)]) + raw + b'\xcc&'


def zone_body(cmd: int, value: int, zone: int) -> bytes:
    return bytes([0x82, cmd, value]) + zone_mask(zone) + b'\xff\xcc&'


def plan(backup: dict, pre_test: bool) -> list[tuple[str, bytes]]:
    pre = backup.get('before_todays_tests', {}) if pre_test else {}
    device = pre.get('device_name', backup['device_name'])
    standby = int(pre.get('auto_standby_raw', backup['auto_standby_raw']), 16)
    if not (1 <= standby <= 0x9F or standby == 0xA5):
        sys.exit(f'refusing auto-standby {standby:02x}: must be 01-9f or a5')
    steps = [(f'device name {device!r}', name_body(3, 2, device))]
    for z in backup['zones']:
        steps.append(
            (f'zone {z["zone"]} name {z["name"]!r}', name_body(1, z['zone'], z['name']))
        )
    for idx, nm in sorted(backup['input_names'].items(), key=lambda kv: int(kv[0])):
        steps.append((f'input {idx} name {nm!r}', name_body(2, int(idx), nm)))
    for z in backup['zones']:
        n = z['zone']
        assert 1 <= z['source'] <= 8 and 0 <= z['volume'] <= 38
        assert -12 <= z['treble_db'] <= 12 and -12 <= z['bass_db'] <= 12
        assert 1 <= z['balance_raw_network'] <= 61
        steps += [
            (f'zone {n} input {z["source"]}', zone_body(0x0D, z['source'], n)),
            (f'zone {n} volume {z["volume"]}', zone_body(0x01, z['volume'] + 1, n)),
            (
                f'zone {n} treble {z["treble_db"]:+d} dB',
                zone_body(0x02, z['treble_db'] + 13, n),
            ),
            (
                f'zone {n} bass {z["bass_db"]:+d} dB',
                zone_body(0x03, z['bass_db'] + 13, n),
            ),
            (
                f'zone {n} balance raw {z["balance_raw_network"]}',
                zone_body(0x05, z['balance_raw_network'], n),
            ),
        ]
    steps.append(
        (
            f'auto-standby {standby:02x}',
            bytes([0x82, 0x16, 0x02, standby, 0xFF, 0xCC, 0x26]),
        )
    )
    return steps


class Amp:
    def __init__(self, host: str) -> None:
        self.sock = socket.create_connection((host, 8899), timeout=3)
        self.sock.settimeout(0.3)
        self.buf = b''

    def payloads(self, secs: float) -> list[bytes]:
        out, end = [], time.time() + secs
        while time.time() < end:
            try:
                d = self.sock.recv(4096)
                if not d:
                    break
                self.buf += d
            except TimeoutError:
                pass
            while len(self.buf) >= 20:
                n = int.from_bytes(self.buf[4:8], 'little')
                if len(self.buf) < 20 + n:
                    break
                out.append(self.buf[20 : 20 + n])
                self.buf = self.buf[20 + n :]
        return out

    def send(self, body: bytes, tries: int = 3) -> bool:
        for _ in range(tries):
            self.sock.sendall(frame(body))
            for p in self.payloads(1.5):
                if p == b'MCU+PAS+' + body:
                    return True
            time.sleep(2)
        return False

    def read_all(self) -> tuple[bytes, list[str], int]:
        for _ in range(4):
            self.sock.sendall(frame(bytes([0x82, 0x0A, 0xFF, 0xFF, 0xFF, 0x89, 0x26])))
            got = {
                p[9]: p[8:] for p in self.payloads(2.5) if p.startswith(b'MCU+PAS+\x82')
            }
            time.sleep(PACE)
            self.sock.sendall(frame(bytes([0x82, 0x16, 0x01, 0x01, 0xFF, 0xCC, 0x26])))
            got.update(
                {
                    p[9]: p[8:]
                    for p in self.payloads(1.5)
                    if p.startswith(b'MCU+PAS+\x82')
                }
            )
            if all(k in got for k in (0x0C, 0x15, 0x16)):
                nm, names, i = got[0x15][2:], [], 0
                while i < len(nm) and nm[i] != 0xCC:
                    names.append(nm[i + 1 : i + 1 + nm[i]].decode('latin1'))
                    i += 1 + nm[i]
                return got[0x0C][2:], names, got[0x16][3]
            time.sleep(5)
        sys.exit('could not read the amp back')


def verify(
    backup: dict, pre_test: bool, st: bytes, names: list[str], standby: int
) -> int:
    pre = backup.get('before_todays_tests', {}) if pre_test else {}
    want_dev = pre.get('device_name', backup['device_name'])
    want_sb = int(pre.get('auto_standby_raw', backup['auto_standby_raw']), 16)
    bad = 0

    def check(label: str, want: object, got: object) -> None:
        nonlocal bad
        if want != got:
            bad += 1
            print(f'  MISMATCH {label}: want {want!r}, amp has {got!r}')

    check('device name', want_dev, names[0])
    for z in backup['zones']:
        i = z['zone'] - 1
        check(f'zone {z["zone"]} name', z['name'], names[1 + i])
        check(f'zone {z["zone"]} input', z['source'], st[i])
        check(f'zone {z["zone"]} volume', z['volume'] + 1, st[8 + i])
        check(f'zone {z["zone"]} treble', z['treble_db'] + 13, st[16 + i])
        check(f'zone {z["zone"]} bass', z['bass_db'] + 13, st[24 + i])
        check(f'zone {z["zone"]} balance', z['balance_raw_network'], st[32 + i])
    for idx, nm in backup['input_names'].items():
        check(f'input {idx} name', nm, names[8 + int(idx)])
    check('auto-standby', want_sb, standby)
    return bad


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    pre_test, dry = '--use-pre-test' in sys.argv, '--dry-run' in sys.argv
    if len(args) != 2:
        sys.exit(__doc__)
    host, path = args
    with open(path, encoding='utf-8') as fh:
        backup = json.load(fh)
    steps = plan(backup, pre_test)
    if dry:
        for label, body in steps:
            print(f'{label:40} {frame(body).hex(" ")}')
        print(f'{len(steps)} commands (dry run, nothing sent)')
        return
    amp = Amp(host)
    failed = []
    for label, body in steps:
        ok = amp.send(body)
        print(f'{"ok  " if ok else "FAIL"} {label}')
        if not ok:
            failed.append(label)
        time.sleep(PACE)
    print('reading back...')
    bad = verify(backup, pre_test, *amp.read_all())
    print(
        f'{len(steps) - len(failed)}/{len(steps)} commands acknowledged; '
        f'{"all settings match the backup" if not bad else f"{bad} settings differ"}'
    )
    sys.exit(1 if failed or bad else 0)


if __name__ == '__main__':
    main()
