# Monoprice & Xantech Multi-Zone Amplifier Control for Home Assistant

NOTE: This is a fork of the Home Assistant `monoprice` controller and ideally should
be merged back into Home Assistant once the underlying library is merged back
together. The Monoprice amps use the same protocol that Xantech originally developed
in late 1990s.

Install [HACS](https://hacs.xyz/), add this repository 'rsnodgrass/hass-xantech', and configure like the monoprice component here:
https://www.home-assistant.io/integrations/monoprice/

**except with `xantech` as the platform**

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
        name: Sonos
      5:
        name: FireTV
```

