"""Use a preferred connection to the amplifier and fall back to another one.

``FailoverAmp`` holds two clients (see ``const.CONNECTION_MODES``) and sends every
request through exactly one of them, under one lock, so the two links never act on the
amplifier at the same time.

- A status read that fails is answered from the other link straight away; two failed
  reads in a row move the active link.
- A command that fails is sent on the other link at once, and that link becomes active.
  A serial port that is closed counts as a failure: the serial library does not raise
  for it, it returns without having sent the command.
- The active link only ever moves to a link that has just answered. With both links
  down (the amplifier is switched off) nothing moves, so the links cannot flap.
- A link with a factory (a serial port) that fails as the second choice is opened
  again once: an idle port whose adapter was re-plugged only works as a new connection.
- While on the fallback, a status read tries the preferred link again every
  ``retry_preferred_seconds``. A link with a factory (a serial port) is reopened for
  that try, because a port that was unplugged only works again as a new connection.
- Mistakes in a call (a value out of range) are raised as they are. They are not link
  failures and never cause a switch.
"""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Awaitable, Callable
import inspect
import time
from typing import Any

from .connection import link_is_down

LOG = logging.getLogger(__name__)

AsyncFactory = Callable[[], Awaitable[Any]]

# raised for a bad argument, not because the link is down
CALLER_ERRORS = (ValueError, TypeError, KeyError)


class LockedClient:
    """One client, called under the failover lock so the links never overlap."""

    def __init__(self, client: Any, lock: asyncio.Lock) -> None:
        self._client = client
        self._lock = lock

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._client, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        async def locked(*args: Any, **kwargs: Any) -> Any:
            async with self._lock:
                return await attr(*args, **kwargs)

        return locked

    async def wait_for_restart(self) -> None:
        """Wait for the module's restart after a factory reset.

        This takes minutes. The lock is held for each poll, not for the whole wait, so
        the other link keeps working meanwhile.
        """
        await self._client.wait_for_restart(sync=self.sync)


class FailoverAmp:
    """Send through the preferred client; fall back to the other one when it fails."""

    def __init__(
        self,
        preferred: tuple[str, Any],
        fallback: tuple[str, Any],
        *,
        status_failures_before_switch: int = 2,
        retry_preferred_seconds: float = 300.0,
        reopen_seconds: float = 60.0,
        close_client: Callable[[Any], None] | None = None,
        factories: dict[str, AsyncFactory] | None = None,
    ) -> None:
        self._names = (preferred[0], fallback[0])
        self._clients = {preferred[0]: preferred[1], fallback[0]: fallback[1]}
        self.active_transport = preferred[0] if preferred[1] else fallback[0]
        self._threshold = status_failures_before_switch
        self._retry_after = retry_preferred_seconds
        self._reopen_every = reopen_seconds
        self._close_client = close_client
        self._factories = factories or {}
        self._reopened_at: dict[str, float] = {}
        self._status_failures = 0
        self._switched_at = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def preferred_transport(self) -> str:
        return self._names[0]

    def locked(self, name: str) -> LockedClient | None:
        """A client for direct use (e.g. network-only features), under the same lock."""
        client = self._clients.get(name)
        return LockedClient(client, self._lock) if client is not None else None

    def _other(self) -> str:
        return (
            self._names[1]
            if self.active_transport == self._names[0]
            else self._names[0]
        )

    async def _link(self, name: str, *, reopen: bool = False) -> Any:
        """The client for a link; opens or reopens it when it has a factory."""
        client = self._clients.get(name)
        factory = self._factories.get(name)
        if factory is None or not (client is None or reopen):
            return client
        now = time.monotonic()
        if now - self._reopened_at.get(name, float('-inf')) < self._reopen_every:
            return client
        self._reopened_at[name] = now
        if client is not None and self._close_client is not None:
            self._close_client(client)
            await asyncio.sleep(0.2)  # let the old port close before opening it again
        try:
            client = await factory()
        except Exception:  # noqa: BLE001 - the link is simply still down
            LOG.debug('Could not open the %s link', name, exc_info=True)
            client = None
        self._clients[name] = client
        return client

    def _switch(self, reason: str) -> None:
        other = self._other()
        LOG.warning(
            'Amplifier %s connection failed (%s); switching to %s',
            self.active_transport,
            reason,
            other,
        )
        self.active_transport = other
        self._status_failures = 0
        self._switched_at = time.monotonic()

    async def _try_preferred_again(self, args: tuple[Any, ...]) -> Any:
        """While on the fallback, now and then give the preferred link a status read."""
        preferred = self._names[0]
        if (
            self.active_transport == preferred
            or time.monotonic() - self._switched_at < self._retry_after
        ):
            return _NOT_TRIED
        self._switched_at = time.monotonic()
        client = self._clients.get(preferred)
        for reopened in (False, True):
            if reopened:
                if preferred not in self._factories:
                    break
                client = await self._link(preferred, reopen=True)
            if client is None:
                continue
            try:
                result = await client.zone_status(*args)
            except Exception:  # noqa: BLE001 - any failure just means "not back yet"
                continue
            if result is not None:
                LOG.warning(
                    'Amplifier %s connection is back; using it again', preferred
                )
                self.active_transport = preferred
                self._status_failures = 0
                return result
        LOG.debug('Preferred %s connection still unavailable', preferred)
        return _NOT_TRIED

    async def _call(self, method: str, *args: Any) -> Any:
        is_status = method == 'zone_status'
        async with self._lock:
            if is_status:
                result = await self._try_preferred_again(args)
                if result is not _NOT_TRIED:
                    return result

            result, failure = None, None
            client = await self._link(self.active_transport)
            if client is None:
                failure = ConnectionError(f'{self.active_transport} link is not open')
            else:
                try:
                    result = await self._send(
                        self.active_transport, client, method, args
                    )
                except CALLER_ERRORS:
                    raise
                except Exception as err:  # noqa: BLE001 - a link failure, handled below
                    failure = err
            if failure is None and not (is_status and result is None):
                if is_status:
                    self._status_failures = 0
                return result

            reason = f'{method}: {failure}' if failure else 'no status reply'
            other_name = self._other()
            other = await self._link(other_name)
            if other is None:
                if failure is not None:
                    raise failure
                return None
            if is_status:
                self._status_failures += 1
            # both links tried: whatever happens now is reported, never retried again
            result = await self._call_other(other_name, other, method, args)
            # The other link answered (a failure would have been raised above), so it
            # may become the active one. A missed reading only moves the link after
            # repeated misses, so one dropped reply cannot flap; a failed command
            # moves it at once.
            if not is_status or (
                result is not None and self._status_failures >= self._threshold
            ):
                self._switch(reason)
            return result

    async def _call_other(
        self, name: str, client: Any, method: str, args: tuple[Any, ...]
    ) -> Any:
        """Call the link that is not active; reopen it once if it has died unnoticed."""
        try:
            return await self._send(name, client, method, args)
        except CALLER_ERRORS:
            raise
        except Exception:
            reopened = await self._link(name, reopen=True)
            if reopened is None or reopened is client:
                raise
            return await self._send(name, reopened, method, args)

    @staticmethod
    async def _send(name: str, client: Any, method: str, args: tuple[Any, ...]) -> Any:
        """One call on one link. A command that was not sent is an error.

        The serial library returns from a command as if all were well when its port
        is closed (see ``link_is_down``), before and after it has waited for the
        port. A status read needs no such check: it comes back empty.
        """
        is_command = method != 'zone_status'
        if is_command and link_is_down(client):
            raise ConnectionError(f'the {name} port is closed')
        result = await getattr(client, method)(*args)
        if is_command and link_is_down(client):
            raise ConnectionError(f'the {name} port closed, the command was not sent')
        return result

    # the methods the integration calls (same as the RS-232 and network clients)

    async def zone_status(self, zone: int) -> dict[str, Any] | None:
        return await self._call('zone_status', zone)

    async def set_power(self, zone: int, power: bool) -> None:
        await self._call('set_power', zone, power)

    async def set_mute(self, zone: int, mute: bool) -> None:
        await self._call('set_mute', zone, mute)

    async def set_volume(self, zone: int, volume: int) -> None:
        await self._call('set_volume', zone, volume)

    async def set_treble(self, zone: int, treble: int) -> None:
        await self._call('set_treble', zone, treble)

    async def set_bass(self, zone: int, bass: int) -> None:
        await self._call('set_bass', zone, bass)

    async def set_balance(self, zone: int, balance: int) -> None:
        await self._call('set_balance', zone, balance)

    async def set_source(self, zone: int, source: int) -> None:
        await self._call('set_source', zone, source)

    async def all_off(self) -> None:
        await self._call('all_off')

    def close(self) -> None:
        for client in self._clients.values():
            if client is not None and self._close_client is not None:
                self._close_client(client)


class _NotTried:
    pass


_NOT_TRIED: Any = _NotTried()


def describe(amp: Any) -> dict[str, Any]:
    """For diagnostics: which link is in use."""
    return {
        'active_transport': getattr(amp, 'active_transport', None),
        'preferred_transport': getattr(amp, 'preferred_transport', None),
    }
