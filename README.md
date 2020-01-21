# Xantech Amp Controller for Home Assistant

NOTE: This is a fork of the Home Assistant monoprice controller. Ideally the goal
is to merge this back together with an amp controller that works for both since
they use almost the same protocol.

Add this repository to HACS and configure like the monoprice component here:
https://www.home-assistant.io/integrations/monoprice/

**except with `xantech` as the platform**

```yaml
# Example configuration.yaml entry
media_player:
  - platform: monoprice
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

