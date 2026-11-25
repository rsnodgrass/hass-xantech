"""Compare an RS-232 snapshot (HA diagnostics zone_statuses) with a live DAX88 Wi-Fi status.

Predict the DAX88 Wi-Fi status bytes from an RS-232 snapshot and compare with a live read.

Usage: python scripts/dax88_wifi_vs_rs232.py scripts/dax88_wifi_status.py <amp-ip> serial.json  (serial.json = HA diagnostics zone_statuses)
"""
import json, subprocess, sys
probe, host, snap = sys.argv[1:4]
serial = json.load(open(snap))
out = subprocess.check_output([sys.executable, '-I', probe, host], text=True)
line = next(l for l in out.splitlines() if l.startswith('82 0c'))
b = bytes.fromhex(line.replace(' ', ''))[2:]
fields = [  # name, offset, predict(rs232 status)
    ('source', 0, lambda s: s['source']),
    ('volume', 8, lambda s: s['volume'] + 1),
    ('treble', 16, lambda s: s['treble'] + 1),
    ('bass', 24, lambda s: s['bass'] + 1),
    ('balance', 32, lambda s: 3 * s['balance'] + 1),
    ('power', 42, lambda s: 2 if s['power'] else 1),
    ('mute', 50, lambda s: 2 if s['mute'] else 1),
]
checks = fails = 0
for zid, s in sorted(serial.items()):
    i = int(zid) - 11
    for name, off, f in fields:
        want, got = f(s), b[off + i]
        checks += 1
        if want != got:
            fails += 1
            print(f'MISMATCH zone {i+1} {name}: rs232 -> expect {want}, wifi {got}')
print(f'{checks - fails}/{checks} match; unknown bytes 40-41={b[40:42].hex(" ")} 58-61={b[58:62].hex(" ")}')
