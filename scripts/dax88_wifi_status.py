"""Read-only: ask a DAX88 for its status and names over Wi-Fi/LAN (Linkplay TCP 8899).

Usage: python scripts/dax88_wifi_status.py <amp-ip>
Sends the same sync request as the Matrio app (82 0a) and prints each reply payload in hex.
It changes nothing on the amp. See IMPROVEMENTS.md P4-4 for the frame layout.
"""
import socket, sys, time
host = sys.argv[1]
payload = b'MCU+PAS+' + bytes([0x82, 0x0A, 0xFF, 0xFF, 0xFF, 0x89, 0x26])
frame = (bytes([0x18, 0x96, 0x18, 0x20]) + len(payload).to_bytes(4, 'little')
         + sum(payload).to_bytes(4, 'little') + bytes(8) + payload)
s = socket.create_connection((host, 8899), timeout=3)
s.sendall(frame)
buf = b''; end = time.time() + 2.5
s.settimeout(0.5)
while time.time() < end:
    try:
        d = s.recv(4096)
        if not d: break
        buf += d
    except socket.timeout:
        pass
s.close()
while len(buf) >= 20:
    n = int.from_bytes(buf[4:8], 'little'); pl = buf[20:20+n]; buf = buf[20+n:]
    print(pl[8:].hex(' '))
