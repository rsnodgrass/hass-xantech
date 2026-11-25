"""Rename a DAX88 zone (kind 1) or input (kind 2) over Wi-Fi/LAN, then print the names reply.

Usage: python scripts/dax88_wifi_rename.py <amp-ip> <kind 1=zone|2=input> <index 1-8> "<name>"
"""
import socket, sys, time
host, kind, index, name = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4].encode('ascii')
if not 1 <= len(name) <= 16 or not all(32 <= c < 127 for c in name):
    sys.exit('name must be 1-16 printable ASCII characters (longer names hang the amp passthrough)')

def frame(body):
    payload = b'MCU+PAS+' + body
    return (bytes([0x18, 0x96, 0x18, 0x20]) + len(payload).to_bytes(4, 'little')
            + sum(payload).to_bytes(4, 'little') + bytes(8) + payload)

def read(s, secs):
    buf = b''; end = time.time() + secs
    while time.time() < end:
        try:
            d = s.recv(4096)
            if not d: break
            buf += d
        except socket.timeout:
            pass
    out = []
    while len(buf) >= 20:
        n = int.from_bytes(buf[4:8], 'little'); out.append(buf[28:20 + n]); buf = buf[20 + n:]
    return out

s = socket.create_connection((host, 8899), timeout=3); s.settimeout(0.3)
s.sendall(frame(bytes([0x82, 0x13, kind, index, len(name)]) + name + b'\xcc&'))
for p in read(s, 1.0): print('ack ', p.hex(' '))
time.sleep(0.3)
s.sendall(frame(bytes([0x82, 0x0A, 0xFF, 0xFF, 0xFF, 0x89, 0x26])))
for p in read(s, 2.0):
    if p[1] == 0x15:
        i = 2; names = []
        while i < len(p) and p[i] != 0xCC:
            n = p[i]; names.append(p[i + 1:i + 1 + n].decode('latin1')); i += 1 + n
        print('zones ', names[1:9]); print('inputs', names[9:])
s.close()
