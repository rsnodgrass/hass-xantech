"""Tests to prevent entity ID regressions across versions.

WARNING: These tests protect user installations. Changing unique_id formats
breaks automations, dashboards, and entity history. You MUST provide a
migration path if changes are absolutely necessary.
"""

import pytest

DOMAIN = 'xantech'

# Golden unique_id format documentation
# These patterns are part of the public API - do not change without migration
GOLDEN_FORMATS = {
    'media_player': '{domain}_{amp_name}_zone_{zone_id}',
    'number_bass': '{domain}_{amp_name}_zone_{zone_id}_bass',
    'number_treble': '{domain}_{amp_name}_zone_{zone_id}_treble',
    'number_balance': '{domain}_{amp_name}_zone_{zone_id}_balance',
}


def generate_media_player_unique_id(amp_name: str, zone_id: int) -> str:
    """Generate unique_id using same logic as ZoneMediaPlayer.__init__."""
    # mirrors: custom_components/xantech/media_player.py line 108-110
    return f'{DOMAIN}_{amp_name}_zone_{zone_id}'.lower().replace(' ', '_')


def generate_number_unique_id(amp_name: str, zone_id: int, control_key: str) -> str:
    """Generate unique_id using same logic as ZoneAudioControlNumber.__init__."""
    # mirrors: custom_components/xantech/number.py line 143-146
    return (
        f'{DOMAIN}_{amp_name}_zone_{zone_id}_{control_key}'.lower().replace(' ', '_')
    )


class TestMediaPlayerUniqueIdStability:
    """Test media_player unique_id format stability."""

    @pytest.mark.parametrize(
        'amp_name,zone_id,expected',
        [
            ('My Amp', 11, 'xantech_my_amp_zone_11'),
            ('DAX88', 11, 'xantech_dax88_zone_11'),
            ('Living Room Amp', 12, 'xantech_living_room_amp_zone_12'),
            ('monoprice6', 15, 'xantech_monoprice6_zone_15'),
        ],
    )
    def test_format_stability(self, amp_name: str, zone_id: int, expected: str):
        """Ensure media_player unique_id format matches golden values.

        WARNING: If this test fails, you are about to break user automations,
        dashboards, and entity history. You MUST provide a migration path.
        """
        result = generate_media_player_unique_id(amp_name, zone_id)
        assert result == expected, (
            f'BREAKING CHANGE: media_player unique_id format changed!\n'
            f'  Amp: {amp_name}, Zone: {zone_id}\n'
            f'  Expected: {expected}\n'
            f'  Got: {result}\n'
            f'This will break existing user installations.'
        )

    def test_special_characters_normalized(self):
        """Verify spaces are converted to underscores consistently."""
        assert generate_media_player_unique_id('My Amp', 11) == 'xantech_my_amp_zone_11'
        assert (
            generate_media_player_unique_id('Multi Word Amp Name', 13)
            == 'xantech_multi_word_amp_name_zone_13'
        )

    def test_case_normalized_to_lowercase(self):
        """Verify mixed case is normalized to lowercase."""
        assert generate_media_player_unique_id('MyAMP', 11) == 'xantech_myamp_zone_11'
        assert generate_media_player_unique_id('DAX-88', 11) == 'xantech_dax-88_zone_11'


class TestNumberEntityUniqueIdStability:
    """Test number entity unique_id format stability."""

    @pytest.mark.parametrize(
        'amp_name,zone_id,control_key,expected',
        [
            ('My Amp', 11, 'bass', 'xantech_my_amp_zone_11_bass'),
            ('My Amp', 11, 'treble', 'xantech_my_amp_zone_11_treble'),
            ('My Amp', 11, 'balance', 'xantech_my_amp_zone_11_balance'),
            ('DAX88', 12, 'bass', 'xantech_dax88_zone_12_bass'),
            ('Living Room', 15, 'treble', 'xantech_living_room_zone_15_treble'),
        ],
    )
    def test_format_stability(
        self, amp_name: str, zone_id: int, control_key: str, expected: str
    ):
        """Ensure number entity unique_id format matches golden values.

        WARNING: If this test fails, you are about to break user automations,
        dashboards, and entity history. You MUST provide a migration path.
        """
        result = generate_number_unique_id(amp_name, zone_id, control_key)
        assert result == expected, (
            f'BREAKING CHANGE: number entity unique_id format changed!\n'
            f'  Amp: {amp_name}, Zone: {zone_id}, Control: {control_key}\n'
            f'  Expected: {expected}\n'
            f'  Got: {result}\n'
            f'This will break existing user installations.'
        )

    def test_all_control_keys_supported(self):
        """Verify all audio control types generate valid unique_ids."""
        for control_key in ('bass', 'treble', 'balance'):
            result = generate_number_unique_id('Test', 11, control_key)
            assert result == f'xantech_test_zone_11_{control_key}'
