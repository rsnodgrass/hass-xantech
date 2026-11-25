# Xantech/Dayton Audio/Sonance Multi-Zone Amp Control for Home Assistant

![beta_badge](https://img.shields.io/badge/maturity-Beta-yellow.png)
![release_badge](https://img.shields.io/github/v/release/rsnodgrass/hass-xantech.svg)
![release_date](https://img.shields.io/github/release-date/rsnodgrass/hass-xantech.svg)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![hacs_badge](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/hacs/integration)

## Support

Visit the [community support discussion thread](https://community.home-assistant.io/t/xantech-dayton-audio-sonance-multi-zone-amps/450908) for issues with this integration. The developers are just volunteers from the community and do not provide any support, so it is best to ask the entire community for help or questions. Please submit Pull Requests with bug fixes!

### Features

* **Zones as media players**: power, volume, mute and input for each zone.
* **Bass, treble and balance** sliders for each zone (optional).
* **Snapshot / restore** actions: save a zone's state and put it back later, for example
  around an announcement. The snapshot is kept in memory until Home Assistant restarts.
* **Several ways to connect**, with automatic fallback (see *Connection options*).
* **Everything is set up in the user interface.** `configuration.yaml` is not used.
* With the amplifier's network module (Dayton Audio DAX88): the names stored on the
  amplifier, auto-standby, backup and restore of the amplifier's settings, and a guarded
  factory reset (see *Settings stored on the amplifier*).

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

## Connection options

Every model can use every connection mode; what matters is what each link answers, not the
model name (rebadged hardware behaves the same). Choose the mode under the integration's
**Configure → Connection**:

| Mode | Uses |
| ---- | ---- |
| RS-232 only | a serial port, or RS-232 over IP as `socket://host:port` |
| Network module only | the amplifier's built-in Linkplay Wi-Fi/LAN module |
| RS-232, fall back to the network module | both of the above; the second takes over if the first stops answering |
| Network module, fall back to RS-232 | the same, network first |
| RS-232, fall back to a second RS-232 link | e.g. the cable first, an Ethernet RS-232 module or bridge second |

The integration uses one link at a time, switches when the active one stops answering, and goes
back to the first link when it works again. Entity ids never change when you switch modes.

| Model(s) | Ways to connect | Status |
| -------- | --------------- | ------ |
| All models in the table above | RS-232 cable; RS-232 over IP through a serial-to-network adapter (ser2net, an ESP32 bridge) as `socket://host:port` | cable as before; adapters untested here |
| Xantech MX88ai / MX88vi | also their built-in Ethernet (MRIP) module, as `socket://host:port` | untested |
| Dayton Audio DAX88 (and identical rebadges) | also the built-in Linkplay Wi-Fi/LAN module, found automatically on the LAN | tested on a DAX88 (MCU firmware V6.5) |
| Soundavo WS66i | Wi-Fi with its own IP control | not supported yet |

The network module is accepted for any amplifier type whose module answers the DAX88 control
protocol (one read-only status request is sent to check). Automatic discovery only offers
modules known to be DAX88-compatible, so other Linkplay speakers on the network are ignored.
Factory reset, backup and restore, auto-standby and the names stored on the amplifier
need the network module (see *Settings stored on the amplifier*).

## Installation

If you have trouble with installation and configuration, visit the [Xantech / Dayton Audio / Sonance multi-zone amps community discussion](https://community.home-assistant.io/t/xantech-dayton-audio-sonance-multi-zone-amps/450908/112). The developers are just volunteers from the community and do not provide any support, so it is best to ask the entire community for help or questions.

### Step 1: Install Custom Components

Make sure that [Home Assistant Community Store (HACS)](https://github.com/custom-components/hacs) is installed and then add the "Integration" repository: `rsnodgrass/hass-xantech`.

### Step 2: Add the amplifier

Everything is set up in Home Assistant's user interface. `configuration.yaml` is not used
(older versions of this README showed YAML; that no longer works).

1. Go to **Settings → Devices & services → Add integration** and choose
   **Xantech Multi-Zone Audio**. A Dayton Audio DAX88 on your network is also found
   automatically and offered under *Discovered*.
2. Choose how the amplifier is connected:
   * **RS-232**: pick the serial port from the list (USB serial ports and ESPHome serial
     proxy ports) and the amplifier type. For a device path such as `/dev/ttyUSB0`, or
     RS-232 over IP (a Xantech MRIP module, ser2net, an ESP32 bridge) as
     `socket://host:port`, choose **Enter manually**.
   * **Network**: enter the IP address of the amplifier's built-in Wi-Fi/LAN module.
3. Tick the zones you use, name them, and name the inputs you use. Leave an input empty to
   hide it. Over the network, the names stored on the amplifier are filled in for you.
   Zones are listed as numbered on the amplifier, with their name: *Zone 1 (Living Room)*.
   (Over RS-232 and in entity ids most models call that zone 11; an expansion unit's
   zones are 21-28 and 31-38 and are listed as *Amplifier 2, zone 1*.)

### Step 3: Change settings later

Open the integration and choose **Configure**. A page is saved when you submit it and
brings you back to this list, so you can change several things in one visit. A page
submitted unchanged changes nothing. **Done** closes the window.

| Page | What it changes |
| ---- | --------------- |
| Connection | RS-232, network, or one with automatic fallback to the other; ports and addresses |
| Zones | which zones are in use, and their names |
| Inputs | the names of the inputs; an empty name hides the input |
| Bass, treble and balance sliders | adds or removes the tone controls for every zone |
| Polling interval | how often the amplifier is asked for its state |

#### Settings stored on the amplifier

These pages appear when the amplifier's network module is connected (DAX88). RS-232 has no
commands for them.

| Page | What it does |
| ---- | ------------ |
| Names stored on the amplifier | the device, zone and input names the amplifier keeps (also shown in its own app). Up to 16 characters each and 132 in total. They can be copied into Home Assistant with one tick. |
| Auto-standby | switch all zones off after a time without an input signal: 30 minutes to 79 hours 30 minutes, or off |
| Backup and restore | save every name, zone setting and auto-standby to a file in `/config/xantech_backups`, and put a backup back |
| Factory reset | see below |

**A restore is not heard louder than the backup says.** A zone that is to be off is
switched off first, one that is to be on is switched on last, and a zone is muted before
and unmuted after its other settings. With *Also switch zones on or off* left unticked, a
zone that is on now and was off in the backup stays on, so its volume is not raised and it
is not unmuted; the result page names those zones. Backups of another amplifier are listed
last and marked.

Before a restore and before a factory reset the integration asks the amplifier's network
module who it is. If another amplifier has taken over the address, nothing is sent.

**Factory reset is protected against accidents.** It erases every name and zone setting on
the amplifier, so three things are needed:

1. *Allow factory reset* must be turned on under **Configure → Factory reset**. It is off by
   default, it is not a switch or button entity (so a dashboard, automation or voice command
   cannot trigger it), Home Assistant shows a notification while it is on, and it turns
   itself off after 30 minutes or after one reset.
2. You type `FACTORY RESET` to confirm.
3. A new backup of the current settings is always saved first and checked. If it cannot be
   saved, or holds something that could not be put back afterwards (for example a name
   with a character this integration cannot write), nothing is reset.

Afterwards, restore the backup under **Configure → Backup and restore**.

What you are told is what happened:

* If the amplifier could not be reached, nothing was sent and the page says so.
* Once the reset has been sent, a notification always names the backup to restore. If the
  amplifier does not confirm the reset or does not come back on the network, the page and
  the notification say that it was sent but not confirmed, never that nothing changed.
* A reset or a restore that is running finishes even if you close its window; a
  notification then tells you how it went.

The same things are available as actions for scripts: `xantech.export_settings`,
`xantech.import_settings` and `xantech.factory_reset` (with the same three protections; it
always saves its own backup and returns the file name).

#### What the device page shows

* **Firmware**: the amplifier's firmware version (read over the network module).
* **Connection** (under *Diagnostic*): the link in use right now. With a fallback mode it
  shows when Home Assistant has switched to the other link.

#### Coming from the original integration

Entity ids and names stay the same. Three things behave differently:

* **Dayton Audio DAX88 tone controls use real units.** Bass and treble are −12 to +12 dB
  (0 = flat) and balance is −10 to +10 (0 = centre). They used to be the amplifier's raw
  numbers, and bass and treble stopped at +2 dB. Automations and scenes that set these
  need new values: new bass/treble = old value − 12, new balance = old value − 10.
* **A zone that does not answer is "unavailable"**, not "off".
* **A DAX88 on your network is also found automatically.** If it is already set up here
  over RS-232, do not add it a second time: open that amplifier's
  **Configure → Connection** and add the network address there. The *Discovered* card
  then goes away.

#### Home Assistant Connect AUX-2 / ESPHome serial proxy

Requires Home Assistant 2026.5 or newer. Any RS-232 port that an ESPHome device shares as a
[serial proxy](https://www.home-assistant.io/connectivity/serial/) can control the amp, for example
**RS-232 Port 1** on the [Home Assistant Connect AUX-2](https://www.home-assistant.io/connectivity/serial/).

1. Cable the amp to the AUX-2 RS-232 port (see the DB15 cable note above for MX88 models).
2. Add the integration, choose **RS-232**, and pick the AUX-2 port from the **Serial proxies**
   group in the port list.
3. To use a USB device path or a `socket://host:port` URL instead, choose **Enter manually**.

The port only appears while the ESPHome device is online. Home Assistant retries the connection
on its own when the proxy comes back.

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

## Support This Project

This integration is maintained in my free time. Sponsorship directly funds new device support and faster updates.

[![Buy Me a Coffee](https://img.shields.io/badge/buy%20me%20a%20coffee-donate-yellow.svg)](https://buymeacoffee.com/DYks67r)
