"""Bass / treble / balance ranges per amplifier type.

The amps use unsigned raw values on the wire (e.g. DAX88 bass ``00``-``24``
with ``12`` = flat). Where the neutral point is known, Home Assistant shows a
signed value instead (DAX88 bass -12..+12 dB), so 0 always means flat/centre.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from pyxantech import get_device_config

from .const import (
    AMP_TYPE_DAX88,
    AMP_TYPE_SONANCE6,
    DEFAULT_MAX_BALANCE,
    DEFAULT_MAX_BASS,
    DEFAULT_MAX_TREBLE,
    SONANCE6_MAX_BALANCE,
    SONANCE6_MAX_BASS,
    SONANCE6_MAX_TREBLE,
    SONANCE6_MIN_BALANCE,
    SONANCE6_MIN_BASS,
    SONANCE6_MIN_TREBLE,
)

LOG = logging.getLogger(__name__)

CONTROL_BASS: Final = 'bass'
CONTROL_TREBLE: Final = 'treble'
CONTROL_BALANCE: Final = 'balance'


@dataclass(frozen=True)
class ControlRange:
    """Raw range of one control and how it maps to the value shown in HA."""

    raw_min: int
    raw_max: int
    offset: int = 0  # HA value = raw value - offset
    unit: str | None = None

    @property
    def native_min(self) -> int:
        """Smallest value shown in Home Assistant."""
        return self.raw_min - self.offset

    @property
    def native_max(self) -> int:
        """Largest value shown in Home Assistant."""
        return self.raw_max - self.offset

    def to_raw(self, value: float) -> int:
        """Convert a Home Assistant value to the raw amp value (clamped)."""
        raw = round(value) + self.offset
        return max(self.raw_min, min(raw, self.raw_max))

    def from_raw(self, raw: int) -> int:
        """Convert a raw amp value to the value shown in Home Assistant."""
        return raw - self.offset


# Known ranges. DAX88 per its manual (RS232 section): bass/treble 00-24 with
# 12 = 0 dB (+/-12 dB shelf), balance 00-20 with 10 = centre.
_KNOWN_RANGES: Final[dict[str, dict[str, ControlRange]]] = {
    AMP_TYPE_DAX88: {
        CONTROL_BASS: ControlRange(0, 24, offset=12, unit='dB'),
        CONTROL_TREBLE: ControlRange(0, 24, offset=12, unit='dB'),
        CONTROL_BALANCE: ControlRange(0, 20, offset=10),
    },
    # Sonance takes signed values directly on the wire
    AMP_TYPE_SONANCE6: {
        CONTROL_BASS: ControlRange(SONANCE6_MIN_BASS, SONANCE6_MAX_BASS),
        CONTROL_TREBLE: ControlRange(SONANCE6_MIN_TREBLE, SONANCE6_MAX_TREBLE),
        CONTROL_BALANCE: ControlRange(SONANCE6_MIN_BALANCE, SONANCE6_MAX_BALANCE),
    },
}

_DEFAULT_MAX: Final = {
    CONTROL_BASS: DEFAULT_MAX_BASS,
    CONTROL_TREBLE: DEFAULT_MAX_TREBLE,
    CONTROL_BALANCE: DEFAULT_MAX_BALANCE,
}


def get_control_range(amp_type: str, control: str) -> ControlRange:
    """Return the range for a control on the given amp type."""
    known = _KNOWN_RANGES.get(amp_type, {}).get(control)
    if known is not None:
        return known
    raw_max = (
        get_device_config(amp_type, f'max_{control}', log_missing=False)
        or _DEFAULT_MAX[control]
    )
    return ControlRange(0, int(raw_max))


SNAPSHOT_SETTINGS: Final = frozenset(
    {'source', 'volume', CONTROL_BASS, CONTROL_TREBLE, CONTROL_BALANCE, 'mute'}
)


def restorable_settings(amp_type: str) -> frozenset[str]:
    """The settings a zone snapshot of this amp type really holds and can put back.

    pyxantech fills in a default (0 / off) for every setting an amp's status reply
    does not contain, so a snapshot cannot tell "volume 0" from "not reported". The
    field names of the reply say what is real. Tone controls the series has no
    command for are left out as well.

    Some amplifiers (Sonance) have no single status reply. The library then asks one
    question per setting (``zone_status_queries``) and puts the answers together, so
    the fields of all those replies count, not only those of ``zone_status``.
    """
    settings = set(SNAPSHOT_SETTINGS)
    try:
        from pyxantech.config import (  # noqa: PLC0415
            PROTOCOL_CONFIG,
            RS232_RESPONSE_PATTERNS,
        )

        protocol = get_device_config(amp_type, 'protocol', log_missing=False)
        patterns = RS232_RESPONSE_PATTERNS.get(protocol, {})
        queries = (PROTOCOL_CONFIG.get(protocol) or {}).get('zone_status_queries')
        names = queries or ['zone_status']
        replies = [patterns[name] for name in names if name in patterns]
        if replies:
            settings &= {field for reply in replies for field in reply.groupindex}
        for control in (CONTROL_BASS, CONTROL_TREBLE, CONTROL_BALANCE):
            key = f'supports_{control}'
            if get_device_config(amp_type, key, log_missing=False) is False:
                settings.discard(control)
    except Exception:  # noqa: BLE001 - unknown type or library layout: cut nothing
        LOG.debug('Could not read what %s reports', amp_type, exc_info=True)
        return SNAPSHOT_SETTINGS
    return frozenset(settings)


def apply_library_limit_overrides(amp_type: str) -> None:
    """Make older pyxantech releases accept the full range for known amps.

    pyxantech <= 0.10.7 clamps DAX88 bass/treble to 14 before sending, so
    values above +2 dB never reach the amp. Raise the library's limits to
    match ``_KNOWN_RANGES``. Harmless once the library ships the fix.
    """
    known = _KNOWN_RANGES.get(amp_type)
    if not known or amp_type == AMP_TYPE_SONANCE6:
        return
    try:
        from pyxantech.config import DEVICE_CONFIG  # noqa: PLC0415
    except ImportError:  # pragma: no cover - layout changed
        return
    device_config = DEVICE_CONFIG.get(amp_type)
    if device_config is None:
        return
    for control, control_range in known.items():
        key = f'max_{control}'
        current = device_config.get(key)
        if current is None or current < control_range.raw_max:
            LOG.debug(
                'Raising pyxantech %s %s from %s to %s',
                amp_type,
                key,
                current,
                control_range.raw_max,
            )
            device_config[key] = control_range.raw_max
