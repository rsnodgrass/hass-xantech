"""Helpers for connection modes and amps with a built-in Linkplay Wi-Fi/LAN module."""

from __future__ import annotations

import time
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CONNECTION_MODE,
    CONF_FACTORY_RESET_ENABLED_UNTIL,
    CONF_FALLBACK_PORT,
    CONF_PORT,
    CONF_TRANSPORT,
    DEFAULT_NETWORK_PORT,
    KNOWN_LINKPLAY_PROJECTS,
    MODE_NETWORK,
    MODE_NETWORK_FALLBACK_SERIAL,
    MODE_SERIAL,
    MODE_SERIAL_FALLBACK_NETWORK,
    MODE_SERIAL_FALLBACK_SERIAL,
    TRANSPORT_NETWORK,
)
from .dax88_network import Dax88NetworkAmp, Dax88NetworkError


class NotADax88Error(Exception):
    """The device answered, but doesn't speak the amplifier control protocol."""


async def async_get_module_status(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """Read the Linkplay module's status (read-only HTTP request)."""
    session = async_get_clientsession(hass)
    url = f'http://{host}/httpapi.asp?command=getStatusEx'
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            resp.raise_for_status()
            # the module answers JSON with a text/html content type
            status = await resp.json(content_type=None)
    except aiohttp.ClientError as err:
        # e.g. HTTP 404 from a device that is not a Linkplay module: callers treat
        # OSError as "cannot connect"
        raise ConnectionError(f'{host}: {err}') from err
    if not isinstance(status, dict):
        raise ValueError(f'{host} did not answer with a module status')
    return status


async def async_check_dax88(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """Known amplifier modules only (used by discovery: never probes other devices)."""
    status = await async_get_module_status(hass, host)
    if status.get('project') not in KNOWN_LINKPLAY_PROJECTS:
        raise NotADax88Error(status.get('project'))
    return status


async def async_check_linkplay_amp(hass: HomeAssistant, host: str) -> dict[str, Any]:
    """Accept any amplifier whose Linkplay module answers the DAX88 control protocol.

    The model name doesn't matter: rebadged hardware works the same. A known module is
    accepted straight away; otherwise one read-only status request (the same one the
    Matrio app sends first) must be answered with an amplifier status.
    """
    status = await async_get_module_status(hass, host)
    if status.get('project') in KNOWN_LINKPLAY_PROJECTS:
        return status
    probe = Dax88NetworkAmp(host, DEFAULT_NETWORK_PORT, timeout=3.0, retries=2)
    try:
        await probe.sync()
    except (Dax88NetworkError, ValueError) as err:
        raise NotADax88Error(status.get('project')) from err
    finally:
        probe.close()
    return {**status, 'probed': True}


async def async_check_control_port(host: str) -> None:
    """One read-only status request on the amplifier's control connection.

    The module's web interface can answer while the connection the amplifier is
    controlled over does not. Raises Dax88NetworkError (or ValueError for a reply
    that is not an amplifier status) when it does not answer.
    """
    probe = Dax88NetworkAmp(host, DEFAULT_NETWORK_PORT, timeout=3.0, retries=2)
    try:
        await probe.sync()
    finally:
        probe.close()


def ports_in_use(entries: list[Any]) -> set[str]:
    """The serial ports (first and second link) that these entries are set to use."""
    taken: set[str] = set()
    for entry in entries:
        mode = connection_mode(dict(entry.data))
        if uses_serial(mode):
            taken.add(entry.data.get(CONF_PORT) or '')
        if uses_fallback_port(mode):
            taken.add(entry.data.get(CONF_FALLBACK_PORT) or '')
    taken.discard('')
    return taken


def factory_reset_enabled(options: dict[str, Any]) -> bool:
    """True while the factory reset kill switch is on and hasn't expired."""
    return float(options.get(CONF_FACTORY_RESET_ENABLED_UNTIL, 0)) > time.time()


def connection_mode(data: dict[str, Any]) -> str:
    """The entry's connection mode (older entries: derived from transport)."""
    if mode := data.get(CONF_CONNECTION_MODE):
        return mode
    return (
        MODE_NETWORK if data.get(CONF_TRANSPORT) == TRANSPORT_NETWORK else MODE_SERIAL
    )


def uses_network(mode: str) -> bool:
    """Uses the amplifier's built-in Linkplay Wi-Fi/LAN module."""
    return mode in (
        MODE_NETWORK,
        MODE_SERIAL_FALLBACK_NETWORK,
        MODE_NETWORK_FALLBACK_SERIAL,
    )


def uses_serial(mode: str) -> bool:
    """Uses an RS-232 link: a serial port, or RS-232 over IP (socket://host:port)."""
    return mode != MODE_NETWORK


def uses_fallback_port(mode: str) -> bool:
    """Uses a second RS-232 link as the fallback."""
    return mode == MODE_SERIAL_FALLBACK_SERIAL


def is_fallback(mode: str) -> bool:
    return mode in (
        MODE_SERIAL_FALLBACK_NETWORK,
        MODE_NETWORK_FALLBACK_SERIAL,
        MODE_SERIAL_FALLBACK_SERIAL,
    )
