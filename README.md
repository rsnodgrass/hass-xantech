# Monoprice & Xantech Multi-Zone Amplifier Control for Home Assistant

NOTE: This is a fork of the Home Assistant `monoprice` controller and ideally should
be merged back into Home Assistant once the underlying library is merged back
together. The Monoprice amps use the same protocol that Xantech originally developed
in late 1990s.

Install [HACS](https://hacs.xyz/), add this repository 'rsnodgrass/hass-xantech', and configure like the monoprice component here:
https://www.home-assistant.io/integrations/monoprice/

**except with `xantech` as the platform**

```yaml
# Example configuration.yaml entry
media_player:
  - platform: xantech
    type: xantech8
    port: /dev/ttyUSB0
    zones:
      11:
        name: Main Bedroom
      12:
        name: Living Room
      13:
        name: Kitchen
    sources:
      1:
        name: Sonos
      5:
        name: FireTV
```

