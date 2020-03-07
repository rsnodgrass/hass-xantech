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
	default_source: 1
      13:
        name: "Kitchen"
	default_source: 1
    sources:
      1:
        name: "Sonos"
      5:
        name: "FireTV"
```

`default_source`: when zone is powered on through Home Assistant, the input source for this zone will be changed to this. This does not apply when the zone is powered on outside of Home Assistant.

#### Lovelace

```yaml
```

## See Also

* https://www.home-assistant.io/integrations/monoprice/
* [Monoprice Home Assistant community discussion](https://community.home-assistant.io/t/monoprice-whole-home-audio-controller-10761-success/19734/62)
