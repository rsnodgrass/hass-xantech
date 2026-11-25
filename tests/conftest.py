"""Fixtures for Xantech integration tests."""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
import re
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.xantech.const import (
    CONF_AMP_TYPE,
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PORT,
    CONF_SOURCES,
    CONF_ZONES,
    DOMAIN,
)


def full_export(**changes: Any) -> dict[str, Any]:
    """A complete export, as the network client's export_settings() returns it."""
    export: dict[str, Any] = {
        'format': 'dax88-network-settings/1',
        'exported_at': time.time(),
        'device_name': 'DAX 88_0000',
        'zone_names': {str(z): f'Room {z}' for z in range(1, 9)},
        'input_names': {str(i): f'Input{i}' for i in range(1, 8)},
        'auto_standby_minutes': 120,
        'zones': {
            str(z): {
                'source': 1,
                'volume': 4,
                'treble': 12,
                'bass': 12,
                'balance_fine': 31,
                'mute': False,
                'power': False,
            }
            for z in range(1, 9)
        },
    }
    export.update(changes)
    return export


@pytest.fixture(autouse=True)
def module_identity() -> Generator[AsyncMock]:
    """The network module at an entry's address says it is that entry's amplifier.

    A restore and a factory reset ask the module who it is first. A test puts
    another amplifier at the address by changing ``side_effect``.
    """

    async def status(hass: HomeAssistant, host: str) -> dict[str, Any]:
        for entry in hass.config_entries.async_entries(DOMAIN):
            if entry.data.get(CONF_HOST) == host:
                return {'uuid': entry.data.get(CONF_DEVICE_ID), 'project': 'DAX88'}
        raise ConnectionError(f'{host}: no such amplifier in this test')

    with patch(
        'custom_components.xantech.settings.async_get_module_status',
        side_effect=status,
    ) as module:
        yield module


STRINGS = json.loads(
    (
        Path(__file__).parents[1] / 'custom_components' / 'xantech' / 'strings.json'
    ).read_text(encoding='utf-8')
)


def _placeholders(text: str | None) -> set[str]:
    return set(re.findall(r'{(\w+)}', text or ''))


def missing_texts(section: str, result: dict[str, Any]) -> list[str]:
    """What a screen of the setup (``config``) or settings (``options``) flow lacks.

    Home Assistant shows the bare key for a text that is missing, and leaves a
    ``{name}`` in the text when the flow does not hand over that value.
    """
    texts = STRINGS[section]
    given = set(result.get('description_placeholders') or {})
    kind = result['type']
    problems: list[str] = []

    def needs(where: str, text: str | None) -> None:
        if lacking := _placeholders(text) - given:
            problems.append(f'{where}: no value for {sorted(lacking)}')

    if kind in (FlowResultType.FORM, FlowResultType.MENU):
        step = result['step_id']
        text = texts['step'].get(step)
        if text is None:
            return [f'step {step}: no text']
        needs(f'step {step}', text.get('title'))
        needs(f'step {step}', text.get('description'))
        if kind is FlowResultType.MENU:
            problems += [
                f'step {step}: menu choice {choice} has no label'
                for choice in result['menu_options']
                if choice not in text.get('menu_options', {})
            ]
            return problems
        schema = result.get('data_schema')
        for field in schema.schema if schema is not None else ():
            if str(field) not in text.get('data', {}):
                problems.append(f'step {step}: field {field} has no label')
        for error in (result.get('errors') or {}).values():
            if error not in texts['error']:
                problems.append(f'step {step}: error {error} has no text')
            needs(f'error {error}', texts['error'].get(error))
    elif kind is FlowResultType.ABORT:
        reason = result['reason']
        if reason not in texts['abort']:
            return [f'abort {reason}: no text']
        needs(f'abort {reason}', texts['abort'][reason])
    elif kind is FlowResultType.SHOW_PROGRESS:
        action = result['progress_action']
        if action not in texts.get('progress', {}):
            problems.append(f'progress {action}: no text')
    return problems


@pytest.fixture(autouse=True)
def flow_texts() -> Generator[None]:
    """Every screen a flow of this integration shows in a test has its text."""

    def section_of(manager: Any, result: dict[str, Any]) -> str:
        handler = result.get('handler')
        if isinstance(manager, config_entries.OptionsFlowManager):
            entry = manager.hass.config_entries.async_get_entry(handler)
            return 'options' if entry is not None and entry.domain == DOMAIN else ''
        return 'config' if handler == DOMAIN else ''

    def checked(method: Any) -> Any:
        async def call(manager: Any, *args: Any, **kwargs: Any) -> Any:
            result = await method(manager, *args, **kwargs)
            section = section_of(manager, result) if isinstance(result, dict) else ''
            problems = missing_texts(section, result) if section else []
            assert not problems, f'{section} flow: {problems}'
            return result

        return call

    manager = data_entry_flow.FlowManager
    with (
        patch.object(manager, 'async_init', checked(manager.async_init)),
        patch.object(manager, 'async_configure', checked(manager.async_configure)),
    ):
        yield


@pytest.fixture(autouse=True)
def control_port() -> Generator[AsyncMock]:
    """The amplifier's control connection answers when the Connection page tries it.

    A test makes it fail by setting ``side_effect``.
    """
    with patch(
        'custom_components.xantech.config_flow.async_check_control_port',
        AsyncMock(),
    ) as check:
        yield check


@pytest.fixture
def mock_amp() -> Generator[MagicMock]:
    """Create a mock amplifier controller."""
    amp = MagicMock()
    amp.zone_status = AsyncMock(
        return_value={
            'power': True,
            'volume': 20,
            'mute': False,
            'source': 1,
        }
    )
    amp.set_power = AsyncMock()
    amp.set_volume = AsyncMock()
    amp.set_mute = AsyncMock()
    amp.set_source = AsyncMock()
    amp.set_bass = AsyncMock()
    amp.set_treble = AsyncMock()
    amp.set_balance = AsyncMock()
    amp.restore_zone = AsyncMock()
    yield amp


@pytest.fixture
def mock_async_get_amp_controller(
    mock_amp: MagicMock,
) -> Generator[AsyncMock]:
    """Mock the async_get_amp_controller function."""
    with patch(
        'custom_components.xantech.config_flow.async_get_amp_controller',
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = mock_amp
        yield mock_get


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Mock setting up a config entry."""
    with patch(
        'custom_components.xantech.async_setup_entry',
        return_value=True,
    ) as mock_setup:
        yield mock_setup


@pytest.fixture
def config_entry_data() -> dict[str, Any]:
    """Return standard config entry data."""
    return {
        CONF_PORT: '/dev/ttyUSB0',
        CONF_AMP_TYPE: 'xantech8',
        CONF_ZONES: {
            11: {'name': 'Living Room'},
            12: {'name': 'Kitchen'},
            13: {'name': 'Bedroom'},
        },
        CONF_SOURCES: {
            1: {'name': 'Sonos'},
            2: {'name': 'Turntable'},
            3: {'name': 'TV'},
        },
    }


@pytest.fixture
def config_entry(
    hass: HomeAssistant,
    config_entry_data: dict[str, Any],
) -> Any:
    """Create a mock config entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        title='Xantech Multi-Zone Audio (/dev/ttyUSB0)',
        data=config_entry_data,
        options={},
        unique_id=f'{DOMAIN}_/dev/ttyUSB0',
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the xantech custom integration."""
