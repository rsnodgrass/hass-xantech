# Xantech/Dayton Audio/Sonance Multi-Zone Amp Control for Home Assistant

![beta_badge](https://img.shields.io/badge/maturity-Beta-yellow.png)
![release_badge](https://img.shields.io/github/v/release/rsnodgrass/hass-xantech.svg)
![release_date](https://img.shields.io/github/release-date/rsnodgrass/hass-xantech.svg)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)

## Support

Visit the [community support discussion thread](https://community.home-assistant.io/t/xantech-dayton-audio-sonance-multi-zone-amps/450908) for issues with this integration. If you have a code change or bug fix, feel free to submit a Pull Request.

### FEATURES NOT SUPPORTED

* **Snapshot/Restore**: this has never been fully implemented, thus is not supported until someone contributes this code

## Supported Amplifiers

See *[pyxantech](https://github.com/rsnodgrass/pyxantech)* for a full list of supported hardware.

| Manufacturer | Model(s)                 | Zones | Supported  |   Series   | Notes                                            |
| ------------ | ------------------------ | :---: | :--------: | :--------: | ------------------------------------------------ |
| Xantech      | MRAUDIO8X8 / MRAUDIO8X8m |  6+2  |    YES     |  xantech8  | audio only; zones 7-8 are preamp outputs only    |
|              | MX88a / MX88ai           | **8** |    YES     |  xantech8  | audio only; ai = Ethernet support (MRIP)         |
|              | MRC88 / MRC88m           |  6+2  |    YES     |  xantech8  | audio + video; zones 7-8 are preamp outputs only |
|              | MX88 / MX88vi            | **8** |    YES     |  xantech8  | audio + video; vi = Ethernet support (MRIP)      |
|              | CM8X8 / CM8X8DR          |   8   | *UNTESTED* |  xantech8  | commercial rack mount matrix controller (BNC)    |
|              | ZPR68-10                 |   6   | *UNTESTED* |  zpr68-10  | 6-zone output; 8 source inputs                   |
|              | MRAUDIO4X4 / BXAUDIO4x4  |   4   |    *NO*    |    N/A     | audio only; only supports IR control             |
|              | MRC44 / MRC44CTL         |   4   |    *NO*    |    N/A     | audio + video; only supprots IR control          |
| Dayton Audio | DAX66                    |   6   | *UNTESTED* | monoprice6 | audio only                                       |
|              | DAX88                    |  6+2  |    YES     |   dax88    | audio only                                       |
| Sonance      | C4630 SE / 875D SE / 875D MKII |  4-6   | *UNTESTED* | sonance | audio only                                       |
| Monoprice    | MPR-SG6Z / 10761         |   6   | *UNTESTED* | monoprice6 | audio only                                       |
| Soundavo     | WS66i                    |   6   | *UNTESTED* | monoprice6 | audio only; see pyws66i; does not support telnet/IP control (yet) |

* The [Monoprice MPR-SG6Z](https://www.monoprice.com/product?p_id=10761) and [Dayton Audio DAX66](https://www.parts-express.com/dayton-audio-dax66-6-source-6-room-distributed-whole-house-audio-system-with-keypads-25-wpc--300-585) appear to have licensed or copied the serial interface from Xantech. Both Monoprice and Dayton Audio use a version of the Xantech multi-zone controller protocol.

* Some Xantech MX88 models only support RS232 control using the DB15 output on the rear. This requiries either Xantech's special DB15 to DB9 adapter cable (PN 05913665), or a custom built DB15 to DB9 cable using the pinouts (see [pyxantech for correct details on making this cable](https://github.com/rsnodgrass/pyxantech).

* This *MAY* work with Monoprice if you specific the `monoprice6` config type, but `pyxantech` has not been able to be tested with a physical amplifier.

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


#### Remote IP232 (UNTESTED)

With Home Assistant it is rumored that if you are using a remote IP232 module instead of a
direct serial onnection that you can specify file paths as *socket://<host>:<port>* in the
port setting. **This has not been tested.**

```yaml
media_player:
  - platform: xantech
    type: xantech8
    port: socket://192.168.1.10:888/
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

## Examples

#### @kbrown01

See the [example YAML](docs/kevin-example.yaml) for how this was setup.

![xantech_example_from_kbrown01](https://user-images.githubusercontent.com/723363/184977966-33081ba0-7245-43a7-8ea9-618e3f7a9ede.png)
![xantech_example_from_kbrown01](https://user-images.githubusercontent.com/723363/184979785-cb188985-f8a6-458d-bcb5-f2313daa9345.png)

## See Also

* [Community support discussion thread](https://community.home-assistant.io/t/xantech-dayton-audio-sonance-multi-zone-amps/450908)
* [pyxantech](https://github.com/rsnodgrass/pyxantech)
* [Home Assistant Monoprice integration](https://www.home-assistant.io/integrations/monoprice/)
* [RS232 to USB cable](https://www.amazon.com/RS232-to-USB/dp/B0759HSLP1?tag=carreramfi-20)

Sites with active community engagement around the Xantech, Monoprice, and Daytona Audio multi-zone amplifiers:

* [Monoprice Home Assistant community discussion](https://community.home-assistant.io/t/monoprice-whole-home-audio-controller-10761-success/19734/62)
* [AVSForum](https://www.avsforum.com/forum/36-home-v-distribution/1506842-any-experience-monoprice-6-zone-home-audio-multizone-controller-23.html)
* http://cocoontech.com/forums/topic/25893-monoprice-multi-zone-audio/

[![Buy Me A Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/DYks67r)
[![Donate](https://img.shields.io/badge/Donate-PayPal-green.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_donations&business=WREP29UDAMB6G)
