# Xantech Multi-Zone Amplifier Control for Home Assistant

![beta_badge](https://img.shields.io/badge/maturity-Beta-yellow.png)
![release_badge](https://img.shields.io/github/v/release/rsnodgrass/hass-xantech.svg)
![release_date](https://img.shields.io/github/release-date/rsnodgrass/hass-xantech.svg)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/custom-components/hacs)

[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=WREP29UDAMB6G)

NOTE: This *MAY* work with Monoprice if you specific the `monoprice6` config type, but `pyxantech` has not been able to be tested with a physical amplifier.

## Supported Amplifiers

See *[pyxantech](https://github.com/rsnodgrass/pyxantech)* for a full list of supported hardware.

| Manufacturer | Model(s)                 | Zones | Supported | Notes |
| ------------ | ------------------------ |:-----:|:---------:| ----- |
| Xantech      | MRAUDIO8X8 / MRAUDIO8X8m | 6+2   | YES       | audio only; zones 7-8 are preamp outputs only |
|              | MX88a / MX88ai           | **8** | YES       | audio only; ai = Ethernet support (MRIP) |
|              | MRC88 / MRC88m           | 6+2   | YES       | audio + video; zones 7-8 are preamp outputs only |
|              | MX88 / MX88vi            | **8** | YES       | audio + video; vi = Ethernet support (MRIP) |
|              | MRAUDIO4X4 / BXAUDIO4x4  | 4     | *NO*      | audio only; 4-zone uses different protocol (need RS232 spec) |
|              | MRC44 / MRC44CTL         | 4     | *NO*      | audio + video; 4-zone uses different protocol (need RS232 spec) |
|              | CM8X8 / CM8X8DR          | 8     | *MAYBE*   | commercial rack mount matrix controller (BNC) |
| Monoprice    | MPR-SG6Z / 10761         | 6     | *MAYBE*   | audio only |
| Dayton Audio | DAX66                    | 6     | *MAYBE*   | audio only |

* The [Monoprice MPR-SG6Z](https://www.monoprice.com/product?p_id=10761) and [Dayton Audio DAX66](https://www.parts-express.com/dayton-audio-dax66-6-source-6-room-distributed-whole-house-audio-system-with-keypads-25-wpc--300-585) appear to have licensed or copies the serial interface from Xantech. Both Monoprice and Dayton Audio use a version of the Xantech multi-zone controller protocol.

* Some Xantech MX88 models only support RS232 control using the DB15 output on the rear. This requiries either Xantech's special DB15 to DB9 adapter cable (PN 05913665), or a custom built DB15 to DB9 cable using the pinouts shown in the MX88 manual.

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

* [pyxantech](https://github.com/rsnodgrass/pyxantech)
* [Home Assistant Monoprice integration](https://www.home-assistant.io/integrations/monoprice/)

Sites with active community engagement around the Xantech, Monoprice, and Daytona Audio multi-zone amplifiers:

* [Monoprice Home Assistant community discussion](https://community.home-assistant.io/t/monoprice-whole-home-audio-controller-10761-success/19734/62)
* [AVSForum](https://www.avsforum.com/forum/36-home-v-distribution/1506842-any-experience-monoprice-6-zone-home-audio-multizone-controller-23.html)
* http://cocoontech.com/forums/topic/25893-monoprice-multi-zone-audio/
