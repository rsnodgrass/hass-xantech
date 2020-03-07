# Xantech Multi-Zone Amplifier Control for Home Assistant

***NOTE: THIS IS NOT WORKING YET***

NOTE: This is a fork of the Home Assistant `monoprice` controller and ideally should be merged back into Home Assistant once the underlying library is merged back together. The Monoprice amps use the same protocol that Xantech originally developed in late 1990s.

NOTE: This *MAY* work with Monoprice if you specific the `monoprice6` config type, but `pyxantech` has not been able to be tested with a physical amplifier.

## Installation

If you have trouble with installation and configuration, visit the [Monoprice Home Assistant community discussion](https://community.home-assistant.io/t/monoprice-whole-home-audio-controller-10761-success/19734/62).

### Step 1: Install Custom Components

ake sure that [Home Assistant Community Store (HACS)](https://github.com/custom-components/hacs) is installed and then add the "Integration" repository: `rsnodgrass/hass-xantech`.


### Step 2: Configuration

Configuration is similar to the monoprice component here: https://www.home-assistant.io/integrations/monoprice/

#### Example configuration.yaml:

```yaml
media_player:
  - platform: xantech
    type: xantech8
    port: /dev/ttyUSB0
    zones:
      11:
        name: "Main Bedroom"
        default_source: 5
      12:
        name: "Living Room"
      13:
        name: "Kitchen"
    sources:
      1:
        name: "Sonos"
      5:
        name: "FireTV"
```

`default_source`: when zone is powered on through Home Assistant, the input source for this zone will be changed to this. This does not apply when the zone is powered on outside of Home Assistant.

#### Lovelace

Example of multiple room volume/power control with a single Spotify source for the entire house (credit: [kcarter13](https://community.home-assistant.io/u/kcarter13/)).

![xantech_example_from_kcarter13](https://community-home-assistant-assets.s3.dualstack.us-west-2.amazonaws.com/original/3X/0/0/00da61ea4238f9891cf360210f3bac7a4b867c0f.png)

```yaml
entities:
  - artwork: cover
    entity: media_player.spotify
    group: true
    hide:
      power: true
      volume: false
    shortcuts:
      columns: 4
      buttons:
        - name: Country Mix
          type: playlist
          id: 'spotify:user:spotify:playlist:37i9dQZF1DX1lVhptIYRda'
        - name: Classic Rock
          type: playlist
          id: 'spotify:user:spotify:playlist:37i9dQZF1DWXRqgorJj26U'
    info: short
    source: icon
    type: 'custom:mini-media-player'
  - entity: media_player.back_porch
    group: true
    hide:
      controls: true
    type: 'custom:mini-media-player'
  - entity: media_player.office
    group: true
    hide:
      controls: true
    type: 'custom:mini-media-player'
  - entity: media_player.garage
    group: true
    hide:
      controls: true
    type: 'custom:mini-media-player'
  - entity: media_player.kitchen_speaker
    group: true
    hide:
      controls: true
    type: 'custom:mini-media-player'
type: entities
```

## See Also

* https://www.home-assistant.io/integrations/monoprice/
* [Monoprice Home Assistant community discussion](https://community.home-assistant.io/t/monoprice-whole-home-audio-controller-10761-success/19734/62)
