# Bundled copy of pyxantech/dax88_network.py from the pyxantech fork (commit 492d2ce).
# Keep it identical to the library; replace with `from pyxantech.dax88_network import ...`
# once a pyxantech release ships it. It only uses the Python standard library.
#
# pyxantech is distributed under this licence:
#
# MIT License
#
# Copyright (c) 2017 Egor Tsinko
# Copyright (c) 2020 Ryan Snodgrass
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""Dayton Audio DAX88 control over the network (Linkplay TCP passthrough, port 8899).

The protocol is documented in ``docs/dax88-network.md``. This client speaks it with the same
method names and value ranges as the RS-232 controller, so Home Assistant can use either:

* volume 0-38, treble/bass 0-24 (12 = flat), balance 0-20 (10 = centre), source 1-8
* zone ids 11-18 (as used for the DAX88 over RS-232) or 1-8

DAX88-only extras: zone/input/device names, auto-standby, multi-zone (group) commands, group
volume steps, fine balance, and a guarded factory reset.

Only the amplifier's own ``82 ..`` commands can be sent; nothing here can send Linkplay module
commands (factory reset of the module, firmware updates, Wi-Fi changes).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import contextlib
import logging
import time
from typing import Any

LOG = logging.getLogger(__name__)

DEFAULT_PORT = 8899
HEADER = bytes([0x18, 0x96, 0x18, 0x20])
PREFIX = b'MCU+PAS+'
ZONES = range(1, 9)

CMD_VOLUME = 0x01
CMD_TREBLE = 0x02
CMD_BASS = 0x03
CMD_BALANCE = 0x05
CMD_POWER = 0x08
CMD_SYNC = 0x0A
CMD_STATUS = 0x0C
CMD_SOURCE = 0x0D
CMD_MUTE = 0x0E
CMD_GROUP_VOLUME = 0x10
CMD_FACTORY_RESET = 0x12
CMD_RENAME = 0x13
CMD_NAMES = 0x15
CMD_STANDBY = 0x16

RENAME_ZONE = 0x01
RENAME_INPUT = 0x02
RENAME_DEVICE = 0x03

STANDBY_OFF = 0xA5
STANDBY_STEP_MINUTES = 30
STANDBY_MAX_STEPS = 0x9F  # 79 h 30 min
MAX_NAME_LENGTH = 16  # longer names hang the amp's passthrough until a power cycle
# All 16 names together (device, 8 zones, 7 inputs) must fit the amp's names reply, which
# is 28 bytes plus the name characters. 132 characters (a 160-byte reply) was fine on a real
# DAX88; 138 garbled the reply and then the passthrough until the names were shortened.
MAX_TOTAL_NAME_CHARACTERS = 132

FACTORY_RESET_CONFIRMATION = 'FACTORY RESET'
BACKUP_MAX_AGE_SECONDS = 15 * 60  # a factory reset needs a backup at most this old


class FactoryResetDisabled(PermissionError):
    """Factory reset is switched off for this client (the kill switch)."""


class Dax88NetworkError(Exception):
    """The amplifier did not answer, or the connection failed."""


class FactoryResetUnconfirmed(Dax88NetworkError):
    """The factory reset was sent, but the amp did not confirm it or did not come back.

    Treat the amplifier as reset until it has been checked: its settings may be gone.
    """


# --- framing ------------------------------------------------------------------------------


def encode_frame(body: bytes) -> bytes:
    """Wrap an ``82 ..`` body in the Linkplay header and ``MCU+PAS+`` prefix."""
    payload = PREFIX + body
    return (
        HEADER
        + len(payload).to_bytes(4, 'little')
        + sum(payload).to_bytes(4, 'little')
        + bytes(8)
        + payload
    )


class FrameReader:
    """Split a TCP byte stream into payloads using the header's length field.

    The payload is binary and may contain ``&`` (0x26), so the length field is the only safe
    frame boundary. Bytes that don't start with the header are skipped until it is found.
    """

    def __init__(self) -> None:
        self._buf = b''

    def feed(self, data: bytes) -> list[bytes]:
        self._buf += data
        out: list[bytes] = []
        while True:
            start = self._buf.find(HEADER)
            if start < 0:
                self._buf = self._buf[-3:]  # keep a possible partial header
                return out
            if start:
                self._buf = self._buf[start:]
            if len(self._buf) < 20:
                return out
            length = int.from_bytes(self._buf[4:8], 'little')
            if len(self._buf) < 20 + length:
                return out
            checksum = int.from_bytes(self._buf[8:12], 'little')
            payload = self._buf[20 : 20 + length]
            self._buf = self._buf[20 + length :]
            if sum(payload) != checksum:
                LOG.debug('Dropping frame with bad checksum: %s', payload.hex(' '))
                continue
            out.append(payload)


# --- encoding -----------------------------------------------------------------------------


def zone_number(zone: int) -> int:
    """Accept RS-232 style zone ids 11-18 or plain 1-8; return 1-8."""
    if 11 <= zone <= 18:
        return zone - 10
    if 1 <= zone <= 8:
        return zone
    raise ValueError(f'DAX88 zone must be 1-8 or 11-18, got {zone}')


def zone_mask(zones: Iterable[int]) -> bytes:
    selected = {zone_number(z) for z in zones}
    if not selected:
        raise ValueError('at least one zone is required')
    return bytes(1 if z in selected else 2 for z in ZONES)


def zone_command(cmd: int, value: int, zones: Iterable[int]) -> bytes:
    if not 0 <= value <= 0xFF:
        raise ValueError(f'value out of range: {value}')
    return bytes([0x82, cmd, value]) + zone_mask(zones) + b'\xff\xcc&'


def _check(name: str, value: int, low: int, high: int) -> int:
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f'{name} must be {low}-{high}, got {value}')
    return value


def _whole(name: str, value: Any) -> int:
    """A whole number as JSON holds it: an int, not 30.0, '30' or true."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f'{name} must be a whole number, got {value!r}')
    return value


def encode_volume(volume: int) -> int:
    return _check('volume', volume, 0, 38) + 1


def encode_tone(level: int) -> int:
    return _check('treble/bass', level, 0, 24) + 1


def encode_balance(balance: int) -> int:
    return 3 * _check('balance', balance, 0, 20) + 1


def check_name(name: str) -> bytes:
    raw = name.encode('ascii', errors='strict') if name.isascii() else b''
    if not 1 <= len(raw) <= MAX_NAME_LENGTH or not all(32 <= c < 127 for c in raw):
        raise ValueError(
            f'name must be 1-{MAX_NAME_LENGTH} printable ASCII characters, got {name!r}'
        )
    return raw


def rename_command(kind: int, index: int, name: str) -> bytes:
    raw = check_name(name)
    return bytes([0x82, CMD_RENAME, kind, index, len(raw)]) + raw + b'\xcc&'


def standby_steps(minutes: int | None) -> int:
    """Minutes (multiple of 30, 30-4770) or None for off -> the amp's value."""
    if minutes is None:
        return STANDBY_OFF
    _whole('auto-standby minutes', minutes)
    if minutes % STANDBY_STEP_MINUTES:
        raise ValueError('auto-standby must be a multiple of 30 minutes')
    steps = minutes // STANDBY_STEP_MINUTES
    _check('auto-standby steps', steps, 1, STANDBY_MAX_STEPS)
    return steps


SETTINGS_FORMAT = 'dax88-network-settings/1'


def _absent(group: dict[Any, Any], low: int, high: int) -> list[int]:
    """The numbers low-high that are not among the keys of ``group``."""
    have: set[int] = set()
    for key in group:
        with contextlib.suppress(TypeError, ValueError):
            have.add(int(key))
    return [n for n in range(low, high + 1) if n not in have]


def check_settings(settings: Any) -> None:
    """Raise ValueError unless ``import_settings`` could put all of ``settings`` back.

    Nothing is sent. This runs before a restore, so a bad file changes nothing, and before
    a factory reset: a backup that cannot be restored is not a backup.
    """
    if not isinstance(settings, dict) or settings.get('format') != SETTINGS_FORMAT:
        raise ValueError(f'not a {SETTINGS_FORMAT} export')
    problems: list[str] = []

    def attempt(what: str, check: Callable[[], Any]) -> None:
        try:
            check()
        except ValueError as err:
            problems.append(f'{what}: {err}')
        except (TypeError, KeyError, AttributeError):
            problems.append(f'{what}: missing or not readable')

    def zone_values(values: dict[str, Any]) -> None:
        for key in ('source', 'volume', 'treble', 'bass', 'balance_fine'):
            _whole(key, values[key])
        _check('source', values['source'], 1, 8)
        encode_volume(values['volume'])
        encode_tone(values['treble'])
        encode_tone(values['bass'])
        _check('fine balance', values['balance_fine'], 1, 61)
        for key in ('mute', 'power'):
            if not isinstance(values[key], bool):
                raise ValueError(f'{key} must be true or false')

    names: list[Any] = [settings.get('device_name')]
    attempt('device name', lambda: check_name(settings['device_name']))
    for kind, key, low, high in (
        ('zone', 'zone_names', 1, 8),
        ('input', 'input_names', 1, 7),
    ):
        group = settings.get(key)
        if not isinstance(group, dict):
            problems.append(f'{key}: missing')
            continue
        for index, name in group.items():
            attempt(
                f'{kind} {index} name',
                lambda i=index, n=name, k=kind, lo=low, hi=high: (
                    _check(k, i, lo, hi),
                    check_name(n),
                ),
            )
            names.append(name)
        # every name: one that is not in the file could not be put back
        problems.extend(
            f'{kind} {index} name: missing' for index in _absent(group, low, high)
        )
    total = sum(len(n) for n in names if isinstance(n, str))
    if total > MAX_TOTAL_NAME_CHARACTERS:
        problems.append(
            f'all names together are {total} characters; the amp holds at most '
            f'{MAX_TOTAL_NAME_CHARACTERS}'
        )
    attempt('auto-standby', lambda: standby_steps(settings['auto_standby_minutes']))
    zones = settings.get('zones')
    if not isinstance(zones, dict):
        problems.append('zones: missing')
    else:
        for zone, values in zones.items():
            attempt(
                f'zone {zone}',
                lambda z=zone, v=values: (_check('zone', z, 1, 8), zone_values(v)),
            )
        problems.extend(f'zone {zone}: missing' for zone in _absent(zones, 1, 8))
    if problems:
        raise ValueError('; '.join(problems))


SYNC_REQUEST = bytes([0x82, CMD_SYNC, 0xFF, 0xFF, 0xFF, 0x89, 0x26])
STANDBY_READ = bytes([0x82, CMD_STANDBY, 0x01, 0x01, 0xFF, 0xCC, 0x26])
FACTORY_RESET = bytes([0x82, CMD_FACTORY_RESET, 0xFF, 0xFF, 0xFF, 0xCC, 0x26])


# --- decoding -----------------------------------------------------------------------------


def parse_status(body: bytes) -> dict[str, Any]:
    """Decode an ``82 0c`` reply into RS-232-scale zone statuses keyed 1-8."""
    data = body[2:]
    if len(data) < 59:
        raise ValueError(f'status reply too short: {len(data)} bytes')
    zones: dict[int, dict[str, Any]] = {}
    for i in range(8):
        raw_balance = data[32 + i]
        if not (
            1 <= data[i] <= 8
            and 1 <= data[8 + i] <= 39
            and 1 <= data[16 + i] <= 25
            and 1 <= data[24 + i] <= 25
            and 1 <= raw_balance <= 61
            and data[42 + i] in (1, 2)
            and data[50 + i] in (1, 2)
        ):
            # Not a value this amp can hold (section 4 of the protocol notes): a zone
            # this unit does not have, or another firmware. It gets no status rather
            # than a made-up one, such as a volume of -1.
            LOG.debug('Zone %d: values outside the known ranges, left out', i + 1)
            continue
        zones[i + 1] = {
            'zone': i + 1,
            'source': data[i],
            'volume': data[8 + i] - 1,
            'treble': data[16 + i] - 1,
            'bass': data[24 + i] - 1,
            'balance': round((raw_balance - 1) / 3),
            'balance_fine': raw_balance,
            'power': data[42 + i] == 2,
            'mute': data[50 + i] == 2,
        }
    if not zones:
        raise ValueError('status reply has no zone with values in the known ranges')
    fw = data[58]
    return {'zones': zones, 'firmware': f'{fw // 10}.{fw % 10}'}


def parse_names(body: bytes) -> dict[str, Any]:
    """Decode an ``82 15`` reply: device name, 8 zone names, 7 input names."""
    data, names, i = body[2:], [], 0
    while i < len(data) and data[i] != 0xCC:
        n = data[i]
        names.append(data[i + 1 : i + 1 + n].decode('ascii', errors='replace'))
        i += 1 + n
    if len(names) < 16:
        raise ValueError(f'names reply has {len(names)} names, expected 16')
    return {
        'device': names[0],
        'zones': {z: names[z] for z in ZONES},
        'inputs': {s: names[8 + s] for s in range(1, 8)},
    }


def parse_standby(body: bytes) -> int | None:
    """Decode an ``82 16 01 <v>`` reply to minutes, or None when off."""
    value = body[3]
    return None if value == STANDBY_OFF else value * STANDBY_STEP_MINUTES


# --- client -------------------------------------------------------------------------------


class Dax88NetworkAmp:
    """Async DAX88 controller over the Linkplay TCP passthrough.

    One request is in flight at a time, commands are paced (Linkplay asks for >= 200 ms), every
    set command must be echoed by the amp, unanswered requests are retried, and a dropped
    connection is reopened on the next request.
    """

    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        *,
        timeout: float = 2.0,
        retries: int = 3,
        pace: float = 0.25,
        cache_seconds: float = 1.0,
        settle: float = 2.0,
        on_unsolicited: Callable[[bytes], None] | None = None,
        allow_factory_reset: bool = False,
        restart_window: float = 120.0,
        restart_timeout: float = 300.0,
        restart_poll: float = 5.0,
        fail_fast: float = 5.0,
    ) -> None:
        self.host, self.port = host, port
        self._timeout, self._retries, self._pace = timeout, retries, pace
        self._cache_seconds = cache_seconds
        # Renames, standby and factory reset are saved by the amp. A command sent within
        # ~0.25 s of a rename was garbled or lost on a real DAX88, so wait after those.
        self._settle = settle
        self._settle_until = 0.0
        self._on_unsolicited = on_unsolicited
        # kill switch: factory reset is refused unless this was turned on deliberately
        self._allow_factory_reset = allow_factory_reset
        # after a factory reset, wait for the Linkplay module's restart (see factory_reset)
        self._restart_window = restart_window
        self._restart_timeout = restart_timeout
        self._restart_poll = restart_poll
        # after a status read fails, further status reads fail at once for this long, so
        # a poll of several zones costs one timeout, not one per zone
        self._fail_fast = fail_fast
        self._failed_at: float | None = None
        self._timeouts = 0  # consecutive unanswered requests on this connection
        self._names_ok = False
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        # one set_names at a time: it reads all names, checks the total, then writes
        self._names_lock = asyncio.Lock()
        self._inbox: asyncio.Queue[bytes] = asyncio.Queue()
        # True while a request is collecting its replies. Frames that arrive at any other
        # time are the amp's own pushes and are handled as they come, not queued.
        self._waiting = False
        self._last_send = 0.0
        self._writes = 0  # frames handed to the connection (see factory_reset)
        self._closed = False
        self._status: dict[str, Any] | None = None
        self._names: dict[str, Any] | None = None
        self._status_time = 0.0

    # connection ---------------------------------------------------------------------------

    async def connect(self) -> None:
        if self._closed:
            # close() is final: a client that was put away must never talk to the amp
            # again (it may hold an out-of-date factory-reset permission)
            raise Dax88NetworkError(f'the connection to {self.host} was closed')
        if self.connected:
            return
        self._drop()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self._timeout
            )
        except (OSError, TimeoutError) as err:
            raise Dax88NetworkError(
                f'cannot connect to {self.host}:{self.port}: {err}'
            ) from err
        if self._closed:
            # close() ran while the connection was being opened: it must not be kept
            writer.close()
            raise Dax88NetworkError(f'the connection to {self.host} was closed')
        self._writer = writer
        self._timeouts = 0
        self._reader_task = asyncio.get_running_loop().create_task(
            self._read_loop(reader, writer)
        )

    async def _read_loop(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        frames = FrameReader()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                for payload in frames.feed(data):
                    if payload.startswith(PREFIX + b'\x82'):
                        body = payload[len(PREFIX) :]
                        if self._waiting:
                            self._inbox.put_nowait(body)
                        else:
                            self._unsolicited(body)
                    else:
                        # the module's own AXX+... text (e.g. after a device rename)
                        LOG.debug('Ignoring non-amp payload: %r', payload)
        except (OSError, asyncio.IncompleteReadError) as err:
            LOG.debug('Connection to %s lost: %s', self.host, err)
        finally:
            # only drop the connection this loop was reading: a newer one may exist
            if self._writer is writer:
                self._drop()
            else:
                writer.close()

    def _drop(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()

    def close(self) -> None:
        """Close the connection for good (also stops the reader task).

        A closed client refuses every further request; make a new one to reconnect.
        """
        self._closed = True
        task, self._reader_task = self._reader_task, None
        if task is not None:
            task.cancel()
        self._drop()

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    # requests -----------------------------------------------------------------------------

    def _dispatch_stale(self) -> None:
        while not self._inbox.empty():
            body = self._inbox.get_nowait()
            self._unsolicited(body)

    def _unsolicited(self, body: bytes) -> None:
        if body[1:2] == bytes([CMD_STATUS]):
            with contextlib.suppress(ValueError):
                self._status = parse_status(body)
                self._status_time = time.monotonic()
        if self._on_unsolicited is not None:
            try:
                self._on_unsolicited(body)
            except Exception:  # noqa: BLE001 - a listener must not stop the reader
                LOG.exception('on_unsolicited callback failed')

    async def _request(
        self,
        body: bytes,
        want: Callable[[dict[int, bytes]], bool],
        retries: int | None = None,
        partial: Callable[[dict[int, bytes]], bool] | None = None,
    ) -> dict[int, bytes]:
        """Send one request and collect its replies.

        ``want`` says when the reply is complete. ``partial``, if given, says whether an
        incomplete reply is still usable once the timeout has passed.
        """
        async with self._lock:
            try:
                return await self._exchange(body, want, retries, partial)
            finally:
                # Also after a cancel: never leave frames queueing. What is left over
                # (a reply's trailing frames) has only just arrived, so it is handled now
                # rather than at the next request, when it would be old.
                self._waiting = False
                self._dispatch_stale()

    async def _exchange(
        self,
        body: bytes,
        want: Callable[[dict[int, bytes]], bool],
        retries: int | None,
        partial: Callable[[dict[int, bytes]], bool] | None,
    ) -> dict[int, bytes]:
        last_err: Exception | None = None
        for attempt in range(1, (retries or self._retries) + 1):
            self._waiting = False
            try:
                await self.connect()
                wait = max(
                    self._pace - (time.monotonic() - self._last_send),
                    self._settle_until - time.monotonic(),
                )
                if wait > 0:
                    await asyncio.sleep(wait)
                writer = self._writer
                if writer is None:  # dropped while we waited
                    raise ConnectionError('connection closed')
                # whatever is still queued arrived before this request: not its reply
                self._dispatch_stale()
                self._waiting = True
                self._writes += 1
                writer.write(encode_frame(body))
                await writer.drain()
            except Dax88NetworkError as err:  # could not connect
                if self._closed:
                    raise
                last_err = err
                await asyncio.sleep(self._pace * attempt)
                continue
            except OSError as err:  # the connection died under us: reconnect
                last_err = Dax88NetworkError(f'connection to {self.host} lost: {err}')
                self._drop()
                continue
            self._last_send = time.monotonic()
            got: dict[int, bytes] = {}
            deadline = self._last_send + self._timeout
            while not want(got):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    reply = await asyncio.wait_for(self._inbox.get(), remaining)
                except TimeoutError:
                    break
                if reply == body:
                    got[-1] = reply  # exact echo = acknowledgement
                if len(reply) > 1:
                    # also keyed by command: a standby read of value 01 looks like its echo
                    got[reply[1]] = reply
            self._waiting = False
            if want(got) or (partial is not None and partial(got)):
                self._timeouts = 0
                return got
            self._timeouts += 1
            if self._timeouts >= 2:
                # twice in a row on this connection: it may be half-dead (the module
                # restarted), so the next try gets a fresh one
                self._drop()
            last_err = Dax88NetworkError(
                f'no reply from {self.host} to {body[:2].hex(" ")} (attempt {attempt})'
            )
            LOG.debug('%s', last_err)
        raise last_err or Dax88NetworkError('request failed')

    async def _command(
        self, body: bytes, retries: int | None = None, saves: bool = False
    ) -> None:
        try:
            await self._request(body, lambda got: -1 in got, retries)
        finally:
            if saves:
                self._settle_until = time.monotonic() + self._settle
        self._status_time = 0.0  # next read must come from the amp

    # reading ------------------------------------------------------------------------------

    async def sync(self) -> dict[str, Any]:
        """Read every zone, the names and the firmware in one request.

        The status reply is required. The names reply is optional here: if it is missing
        or unreadable (which happens when the amp's names exceed its 132-character budget),
        the zone status is still returned, and ``names()`` reports the problem.
        """
        got = await self._request(
            SYNC_REQUEST,
            lambda g: CMD_STATUS in g and CMD_NAMES in g,
            partial=lambda g: CMD_STATUS in g,
        )
        try:
            self._status = parse_status(got[CMD_STATUS])
        except ValueError as err:
            raise Dax88NetworkError(
                f'bad status reply from {self.host}: {err}'
            ) from err
        self._status_time = time.monotonic()
        self._failed_at = None
        self._names_ok = False
        if CMD_NAMES in got:
            try:
                self._names = parse_names(got[CMD_NAMES])
                self._names_ok = True
            except ValueError as err:
                LOG.debug('Unreadable names reply from %s: %s', self.host, err)
        return {**self._status, 'names': self._names}

    async def _fresh_status(self) -> dict[str, Any]:
        if (
            self._status is None
            or time.monotonic() - self._status_time > self._cache_seconds
        ):
            if (
                self._failed_at is not None
                and time.monotonic() - self._failed_at < self._fail_fast
            ):
                raise Dax88NetworkError(f'{self.host} did not answer a moment ago')
            try:
                await self.sync()
            except Dax88NetworkError:
                self._failed_at = time.monotonic()
                raise
        assert self._status is not None
        return self._status

    async def zone_status(self, zone: int) -> dict[str, Any] | None:
        """Status of one zone in RS-232 units (one network read serves all zones)."""
        status = await self._fresh_status()
        values = status['zones'].get(zone_number(zone))
        if values is None:  # the amp reported nothing usable for this zone
            return None
        result = dict(values)
        result['zone'] = zone_number(zone)
        return result

    async def all_zone_statuses(self) -> dict[int, dict[str, Any]]:
        return dict((await self._fresh_status())['zones'])

    async def names(self) -> dict[str, Any]:
        """The device, zone and input names stored on the amp (always read fresh)."""
        await self.sync()
        if not self._names_ok or self._names is None:
            raise Dax88NetworkError(
                f'{self.host} did not send a readable names reply (names over the '
                f'{MAX_TOTAL_NAME_CHARACTERS}-character budget garble it)'
            )
        return self._names

    async def firmware(self) -> str:
        return (await self._fresh_status())['firmware']

    async def get_auto_standby(self) -> int | None:
        """Auto-standby timeout in minutes, or None when off."""
        got = await self._request(STANDBY_READ, lambda g: CMD_STANDBY in g)
        return parse_standby(got[CMD_STANDBY])

    # single-zone setters (RS-232 compatible) ---------------------------------------------

    async def set_power(self, zone: int, power: bool) -> None:
        await self.set_power_zones([zone], power)

    async def set_mute(self, zone: int, mute: bool) -> None:
        await self.set_mute_zones([zone], mute)

    async def set_volume(self, zone: int, volume: int) -> None:
        await self.set_volume_zones([zone], volume)

    async def set_treble(self, zone: int, treble: int) -> None:
        await self.set_treble_zones([zone], treble)

    async def set_bass(self, zone: int, bass: int) -> None:
        await self.set_bass_zones([zone], bass)

    async def set_balance(self, zone: int, balance: int) -> None:
        await self.set_balance_zones([zone], balance)

    async def set_source(self, zone: int, source: int) -> None:
        await self.set_source_zones([zone], source)

    async def set_balance_fine(self, zone: int, value: int) -> None:
        """Balance on the network scale 1-61 (31 = centre), finer than RS-232."""
        await self._command(
            zone_command(CMD_BALANCE, _check('fine balance', value, 1, 61), [zone])
        )

    async def all_off(self) -> None:
        await self.set_power_zones(ZONES, False)

    async def restore_zone(self, status: dict[str, Any]) -> None:
        zone = status['zone']
        # off first and on last, so nothing is heard while the zone is being changed
        if status.get('power') is False:
            await self.set_power(zone, False)
        for key, setter in (
            ('source', self.set_source),
            ('volume', self.set_volume),
            ('mute', self.set_mute),
            ('bass', self.set_bass),
            ('treble', self.set_treble),
            ('balance', self.set_balance),
        ):
            if key in status:
                await setter(zone, status[key])
        if status.get('power') is True:
            await self.set_power(zone, True)

    # group setters --------------------------------------------------------------------------

    async def set_power_zones(self, zones: Iterable[int], power: bool) -> None:
        await self._command(zone_command(CMD_POWER, 2 if power else 1, zones))

    async def set_mute_zones(self, zones: Iterable[int], mute: bool) -> None:
        await self._command(zone_command(CMD_MUTE, 2 if mute else 1, zones))

    async def set_volume_zones(self, zones: Iterable[int], volume: int) -> None:
        await self._command(zone_command(CMD_VOLUME, encode_volume(volume), zones))

    async def set_treble_zones(self, zones: Iterable[int], treble: int) -> None:
        await self._command(zone_command(CMD_TREBLE, encode_tone(treble), zones))

    async def set_bass_zones(self, zones: Iterable[int], bass: int) -> None:
        await self._command(zone_command(CMD_BASS, encode_tone(bass), zones))

    async def set_balance_zones(self, zones: Iterable[int], balance: int) -> None:
        await self._command(zone_command(CMD_BALANCE, encode_balance(balance), zones))

    async def set_source_zones(self, zones: Iterable[int], source: int) -> None:
        await self._command(
            zone_command(CMD_SOURCE, _check('source', source, 1, 8), zones)
        )

    async def volume_up(self, zones: Iterable[int]) -> None:
        """One volume step up for each selected zone (the app's group +)."""
        body = (
            bytes([0x82, CMD_GROUP_VOLUME, 0x01, 0xFF])
            + zone_mask(zones)
            + b'\xff\xcc&'
        )
        await self._command(body, retries=1)  # a retry could step twice

    async def volume_down(self, zones: Iterable[int]) -> None:
        """One volume step down for each selected zone (the app's group -)."""
        body = (
            bytes([0x82, CMD_GROUP_VOLUME, 0x02, 0xFF])
            + zone_mask(zones)
            + b'\xff\xcc&'
        )
        await self._command(body, retries=1)  # a retry could step twice

    async def sync_volume(self, zones: Iterable[int], volume: int) -> None:
        """Set every selected zone to one volume (the app's Sync Volume)."""
        body = (
            bytes([0x82, CMD_GROUP_VOLUME, 0x03, encode_volume(volume)])
            + zone_mask(zones)
            + b'\xff\xcc&'
        )
        await self._command(body)

    # names and amp-wide settings --------------------------------------------------------------

    async def set_names(
        self,
        *,
        device: str | None = None,
        zones: dict[int, str] | None = None,
        inputs: dict[int, str] | None = None,
    ) -> int:
        """Change any of the names stored on the amp; returns how many were written.

        Every name is checked first (1-16 printable ASCII characters, and all 16 names
        together within the amp's 132-character budget). Names that get shorter are written
        first, so the budget is never exceeded part-way. Unchanged names are not written.
        The result is read back and compared.
        """
        wanted: dict[tuple[str, int], str] = {}
        if device is not None:
            wanted[('device', 0)] = device
        for zone, name in (zones or {}).items():
            wanted[('zone', zone_number(int(zone)))] = name
        for source, name in (inputs or {}).items():
            wanted[('input', _check('input', int(source), 1, 7))] = name
        for name in wanted.values():
            check_name(name)
        # Two edits at once would each read the names, find their own total within
        # the budget, and together exceed it.
        async with self._names_lock:
            return await self._write_names(wanted)

    async def _write_names(self, wanted: dict[tuple[str, int], str]) -> int:
        def keyed(names: dict[str, Any]) -> dict[tuple[str, int], str]:
            return {
                ('device', 0): names['device'],
                **{('zone', z): n for z, n in names['zones'].items()},
                **{('input', i): n for i, n in names['inputs'].items()},
            }

        current = keyed(await self.names())
        total = sum(len(n) for n in {**current, **wanted}.values())
        if total > MAX_TOTAL_NAME_CHARACTERS:
            raise ValueError(
                f'all names together would be {total} characters; the amp holds at most '
                f'{MAX_TOTAL_NAME_CHARACTERS} (shorten another name first)'
            )
        kinds = {'device': RENAME_DEVICE, 'zone': RENAME_ZONE, 'input': RENAME_INPUT}
        changed = 0
        for (kind, index), name in sorted(
            wanted.items(), key=lambda kv: len(kv[1]) - len(current[kv[0]])
        ):
            if current[(kind, index)] == name:
                continue
            body = rename_command(
                kinds[kind], 0x02 if kind == 'device' else index, name
            )
            await self._command(body, saves=True)
            changed += 1
        if changed:
            stored = keyed(await self.names())
            wrong = [k for k, name in wanted.items() if stored[k] != name]
            if wrong:
                raise Dax88NetworkError(
                    f'{self.host} did not store {len(wrong)} of the new names'
                )
        return changed

    async def set_zone_name(self, zone: int, name: str) -> None:
        await self.set_names(zones={zone: name})

    async def set_input_name(self, source: int, name: str) -> None:
        await self.set_names(inputs={source: name})

    async def set_device_name(self, name: str) -> None:
        """Rename the amp; the Linkplay module takes the same name (network name)."""
        await self.set_names(device=name)

    async def set_auto_standby(self, minutes: int | None) -> None:
        """Set the auto-standby timeout (multiple of 30 min, 30-4770) or None for off."""
        steps = standby_steps(minutes)
        await self._command(
            bytes([0x82, CMD_STANDBY, 0x02, steps, 0xFF, 0xCC, 0x26]), saves=True
        )

    async def export_settings(self) -> dict[str, Any]:
        """Everything a factory reset clears, plus auto-standby, as a JSON-safe dict."""
        names = await self.names()
        status = await self._fresh_status()
        return {
            'format': SETTINGS_FORMAT,
            'exported_at': time.time(),
            'device_name': names['device'],
            'zone_names': {str(z): n for z, n in names['zones'].items()},
            'input_names': {str(s): n for s, n in names['inputs'].items()},
            'auto_standby_minutes': await self.get_auto_standby(),
            'zones': {
                str(z): {
                    k: v[k]
                    for k in (
                        'source',
                        'volume',
                        'treble',
                        'bass',
                        'balance_fine',
                        'mute',
                        'power',
                    )
                }
                for z, v in status['zones'].items()
            },
        }

    async def import_settings(
        self, settings: dict[str, Any], *, include_power: bool = False
    ) -> list[int]:
        """Put back settings from ``export_settings``.

        Names go through ``set_names`` (checked, shrinking ones first, read back). Only
        values that differ from the amp's current ones are sent. Power is left alone unless
        ``include_power``.

        Nothing is heard louder on the way than it will be at the end: a zone that is
        to be off is switched off first, one that is to be on is switched on last, and a
        zone is muted before and unmuted after its other settings.

        Without ``include_power``, a zone that is on now and off in the backup stays on,
        so its volume is not raised and it is not unmuted: that would be heard, and the
        backup says the zone should be silent. Returns those zones (1-8); everything
        else about them is put back.
        """
        check_settings(settings)  # a bad file changes nothing
        await self.set_names(
            device=settings['device_name'],
            zones={int(z): n for z, n in settings['zone_names'].items()},
            inputs={int(i): n for i, n in settings['input_names'].items()},
        )
        if await self.get_auto_standby() != settings['auto_standby_minutes']:
            await self.set_auto_standby(settings['auto_standby_minutes'])
        # only send what differs: quicker, and nothing is rewritten needlessly
        now = await self.all_zone_statuses()
        left_alone: list[int] = []
        for z, want in sorted(settings['zones'].items(), key=lambda kv: int(kv[0])):
            zone = int(z)
            have = now.get(zone)
            if have is None:
                raise Dax88NetworkError(
                    f'{self.host} reports no usable status for zone {zone}'
                )
            power_differs = have['power'] != want['power']
            if include_power and power_differs and not want['power']:
                await self.set_power(zone, False)
            # on now, off in the backup, and staying on: nothing may get louder
            stays_on = power_differs and have['power'] and not include_power
            louder = want['volume'] > have['volume']
            unmute = have['mute'] and not want['mute']
            if stays_on and (louder or unmute):
                left_alone.append(zone)
            if want['mute'] and not have['mute']:
                await self.set_mute(zone, True)
            if have['source'] != want['source']:
                await self.set_source(zone, want['source'])
            if have['volume'] != want['volume'] and not (stays_on and louder):
                await self.set_volume(zone, want['volume'])
            if have['treble'] != want['treble']:
                await self.set_treble(zone, want['treble'])
            if have['bass'] != want['bass']:
                await self.set_bass(zone, want['bass'])
            if have['balance_fine'] != want['balance_fine']:
                await self.set_balance_fine(zone, want['balance_fine'])
            if unmute and not stays_on:
                await self.set_mute(zone, False)
            if include_power and power_differs and want['power']:
                await self.set_power(zone, True)
        return left_alone

    @property
    def factory_reset_allowed(self) -> bool:
        return self._allow_factory_reset

    async def factory_reset(
        self, confirmation: str, backup: dict[str, Any], *, wait: bool = True
    ) -> None:
        """Reset the amplifier to factory defaults. Guarded three ways.

        A factory reset clears every zone and input name and every zone's input, volume, tone
        and balance; the device name returns to ``DAX 88_<mac>``. Auto-standby and the network
        settings are kept. It is only sent when all of these hold:

        1. the kill switch is on: the client was created with ``allow_factory_reset=True``
           (otherwise ``FactoryResetDisabled``);
        2. ``confirmation`` is exactly ``FACTORY_RESET_CONFIRMATION``;
        3. ``backup`` is a result of ``export_settings()`` taken in the last
           ``BACKUP_MAX_AGE_SECONDS`` that ``import_settings`` could put back in full
           (``check_settings``).

        If a guard fails, or the amp cannot be reached, nothing is sent. Once the command
        has gone out, any failure is a ``FactoryResetUnconfirmed``: the amp may be reset.
        ``wait=False`` returns as soon as the amp has acknowledged; call
        ``wait_for_restart()`` afterwards.
        """
        if not self._allow_factory_reset:
            raise FactoryResetDisabled(
                'factory reset is switched off; create the client with '
                'allow_factory_reset=True to allow it'
            )
        if confirmation != FACTORY_RESET_CONFIRMATION:
            raise ValueError(
                f'factory reset needs confirmation={FACTORY_RESET_CONFIRMATION!r}'
            )
        if not isinstance(backup, dict) or backup.get('format') != SETTINGS_FORMAT:
            raise ValueError(
                'factory reset needs a backup: call export_settings() first and keep the result'
            )
        try:
            age = time.time() - float(backup.get('exported_at', 0))
        except (TypeError, ValueError):
            age = -1.0
        if not 0 <= age <= BACKUP_MAX_AGE_SECONDS:
            raise ValueError(
                'the backup is older than 15 minutes; call export_settings() again first'
            )
        try:
            check_settings(backup)
        except ValueError as err:
            raise ValueError(
                f'the backup could not be put back afterwards ({err}); fix that on the '
                'amp first, then take a new backup'
            ) from err
        writes = self._writes
        try:
            # One attempt only. A reset whose echo is lost must not be sent again: the
            # amp may be saving or restarting, and what a second reset does then is
            # not known.
            await self._command(FACTORY_RESET, retries=1, saves=True)
        except Dax88NetworkError as err:
            if self._writes == writes:
                raise  # never reached the connection: nothing has changed
            raise FactoryResetUnconfirmed(
                f'the factory reset was sent to {self.host}, but the amp did not '
                'acknowledge it'
            ) from err
        self._status = self._names = None
        if wait:
            await self.wait_for_restart()

    async def wait_for_restart(self, *, sync: Callable[[], Any] | None = None) -> None:
        """Wait for the Linkplay module's restart after a factory reset.

        On a real DAX88 the module dropped off the network ~1 minute after the reset and
        came back ~1 minute later; commands sent in between were lost. Poll until the
        restart has been seen and the amp answers again, or until ``restart_window``
        passes with no restart. One unanswered poll is not a restart (the amp is busy
        right after a reset); two in a row are.

        ``sync`` replaces ``self.sync`` for callers that serialise requests themselves
        (so their lock is held per poll, not for the whole wait).
        """
        poll = sync or self.sync
        start = time.monotonic()
        misses, saw_drop = 0, False
        while True:
            elapsed = time.monotonic() - start
            if not saw_drop and elapsed >= self._restart_window:
                return
            if elapsed >= self._restart_timeout:
                raise FactoryResetUnconfirmed(
                    f'{self.host} did not come back after the factory reset'
                )
            if self._closed:
                raise FactoryResetUnconfirmed(
                    f'the connection to {self.host} was closed while waiting for the '
                    'restart after the factory reset'
                )
            try:
                await poll()
            except Dax88NetworkError:
                misses += 1
                if misses >= 2 and not saw_drop:
                    LOG.debug('Linkplay module is restarting after the factory reset')
                    saw_drop = True
                self._drop()
            else:
                if saw_drop:
                    return
                misses = 0
            await asyncio.sleep(self._restart_poll)


async def async_get_dax88_network_amp(
    host: str, port: int = DEFAULT_PORT, **kwargs: Any
) -> Dax88NetworkAmp:
    """Connect to a DAX88 over the network and check it answers a status read."""
    amp = Dax88NetworkAmp(host, port, **kwargs)
    try:
        await amp.sync()
    except BaseException:  # also a cancel: never leave the connection open
        amp.close()
        raise
    return amp
