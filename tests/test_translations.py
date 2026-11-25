"""The UI text must cover every step, field, error and message the flows can show.

Home Assistant shows the raw key when a translation is missing, so a typo here would
only be noticed by a user. These checks read the flow source and compare it with
strings.json.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).parents[1] / 'custom_components'
PACKAGE = next(p for p in ROOT.iterdir() if (p / 'config_flow.py').exists())
STRINGS = json.loads((PACKAGE / 'strings.json').read_text(encoding='utf-8'))
FLOW = (PACKAGE / 'config_flow.py').read_text(encoding='utf-8')
SETTINGS = (PACKAGE / 'settings.py').read_text(encoding='utf-8')
HELPERS, CONFIG, OPTIONS = re.split(
    r'^class Xantech(?:Config|Options)Flow', FLOW, flags=re.MULTILINE
)
SECTIONS = {'config': CONFIG, 'options': OPTIONS}
# steps that only run a job behind a progress screen, or only pass on to another step
NO_SCREEN = {'restore_run', 'factory_reset_run', 'action_failed', 'backup_now'}


def _steps(source: str) -> set[str]:
    return set(re.findall(r"step_id='(\w+)'", source)) - NO_SCREEN


@pytest.mark.parametrize('section', ['config', 'options'])
def test_every_step_has_a_title(section: str) -> None:
    texts = STRINGS[section]['step']
    for step in _steps(SECTIONS[section]):
        assert step in texts, f'{section} step {step} has no text'
        assert texts[step].get('title'), f'{section} step {step} has no title'


@pytest.mark.parametrize('section', ['config', 'options'])
def test_every_menu_choice_is_labelled_and_exists(section: str) -> None:
    source = SECTIONS[section]
    for step, text in STRINGS[section]['step'].items():
        for choice in text.get('menu_options', {}):
            assert f'async def async_step_{choice}(' in source, (step, choice)
    # every step name the code can put in a menu has a label somewhere
    listed = re.findall(r'(?:menu_options=|options (?:\+)?= )\[([^\]]*)\]', source)
    listed += re.findall(r"options\.append\(('\w+')\)", source)
    choices = {c for group in listed for c in re.findall(r"'(\w+)'", group)}
    labels = {
        c
        for text in STRINGS[section]['step'].values()
        for c in text.get('menu_options', {})
    }
    assert choices <= labels, choices - labels


@pytest.mark.parametrize('section', ['config', 'options'])
def test_every_error_has_a_message(section: str) -> None:
    used = set(re.findall(r"errors\[[^\]]+\] = '(\w+)'", SECTIONS[section]))
    assert used <= set(STRINGS[section]['error']), used - set(STRINGS[section]['error'])


def _settings_error_keys() -> set[str]:
    """The reason keys settings.py can raise (second argument of AmpSettingsError)."""
    keys = {'amp_no_answer'}  # the default
    for node in ast.walk(ast.parse(SETTINGS)):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, 'id', None) == 'AmpSettingsError'
            and len(node.args) > 1
            and isinstance(node.args[1], ast.Constant)
        ):
            keys.add(node.args[1].value)
    return keys


def test_every_reason_a_flow_can_stop_for_has_a_message() -> None:
    config = set(re.findall(r"async_abort\(reason='(\w+)'\)", CONFIG))
    assert config <= set(STRINGS['config']['abort']), config
    options = set(re.findall(r"async_abort\(reason='(\w+)'\)", OPTIONS))
    options |= _settings_error_keys() | {'unknown'}
    assert options <= set(STRINGS['options']['abort']), options - set(
        STRINGS['options']['abort']
    )


def test_every_field_is_labelled() -> None:
    def labels(section: str, step: str) -> set[str]:
        return set(STRINGS[section]['step'][step].get('data', {}))

    zone_fields = {f'zone_{z}' for z in range(1, 39)}
    source_fields = {f'source_{s}' for s in range(1, 9)}
    for section in ('config', 'options'):
        assert zone_fields <= labels(section, 'zone_names')
        assert source_fields <= labels(section, 'sources')
        assert 'zone_ids' in labels(section, 'zones')
    amp_fields = (
        {'device_name', 'use_in_ha'}
        | {f'amp_zone_{z}' for z in range(1, 9)}
        | {f'amp_input_{i}' for i in range(1, 8)}
    )
    assert amp_fields <= labels('options', 'amp_names')
    # fields written as plain strings in the flow
    for section, source in SECTIONS.items():
        literal = set(re.findall(r"vol\.(?:Required|Optional)\(\s*'(\w+)'", source))
        all_labels = {
            f
            for text in STRINGS[section]['step'].values()
            for f in text.get('data', {})
        }
        assert literal <= all_labels, (section, literal - all_labels)


def test_fields_named_by_a_constant_are_labelled_too() -> None:
    """``vol.Required(CONF_SCAN_INTERVAL, ...)``: the check above sees quoted names."""
    const = (PACKAGE / 'const.py').read_text(encoding='utf-8')
    pattern = r"^(CONF_\w+): Final = '(\w+)'"
    names = dict(re.findall(pattern, const, flags=re.MULTILINE))
    for section, source in SECTIONS.items():
        used = set(re.findall(r'vol\.(?:Required|Optional)\(\s*(CONF_\w+)', source))
        # the helpers above the two flows build fields for both of them
        used |= set(re.findall(r'vol\.(?:Required|Optional)\(\s*(CONF_\w+)', HELPERS))
        assert used, section
        all_labels = {
            f
            for text in STRINGS[section]['step'].values()
            for f in text.get('data', {})
        }
        missing = {names[c] for c in used} - all_labels
        assert not missing, (section, missing)


def test_every_action_and_its_fields_have_text() -> None:
    """The actions are described in services.yaml and again, for the UI, here."""
    actions: dict[str, set[str]] = {}
    current = ''
    yaml = (PACKAGE / 'services.yaml').read_text(encoding='utf-8')
    part = ''
    for line in yaml.splitlines():
        if top := re.match(r'(\w+):\s*$', line):
            current, part = top.group(1), ''
            actions[current] = set()
        elif below := re.match(r'  (\w+):', line):
            part = below.group(1)
        elif part == 'fields' and (field := re.match(r'    (\w+):\s*$', line)):
            actions[current].add(field.group(1))
    assert {'export_settings', 'import_settings', 'factory_reset'} <= set(actions)
    texts = STRINGS['services']
    for action, fields in actions.items():
        assert action in texts, f'action {action} has no text'
        assert texts[action].get('name') and texts[action].get('description'), action
        for name in fields:
            text = texts[action].get('fields', {}).get(name, {})
            assert text.get('name') and text.get('description'), (action, name)


def test_progress_screens_and_selectors_have_text() -> None:
    actions = {
        a for _, a in re.findall(r"self\._progress\(\s*'(\w+)',\s*'(\w+)'", FLOW)
    }
    assert actions and actions <= set(STRINGS['options']['progress'])
    selectors = set(re.findall(r"translation_key='(\w+)'", FLOW))
    assert selectors <= set(STRINGS['selector'])


def test_connection_sensor_states_are_named() -> None:
    sensor = (PACKAGE / 'sensor.py').read_text(encoding='utf-8')
    links = re.search(r'LINKS = \[([^\]]*)\]', sensor)
    assert links is not None
    states = set(re.findall(r"'(\w+)'", links.group(1)))
    assert states == set(STRINGS['entity']['sensor']['connection']['state'])


def test_english_translation_matches_strings() -> None:
    english = json.loads(
        (PACKAGE / 'translations' / 'en.json').read_text(encoding='utf-8')
    )
    assert english == STRINGS
