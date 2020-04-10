"""
Xantech Multi-Zone Amplifier Control for Home Assistant
"""
import asyncio

from homeassistant.core import HomeAssistant

from .const import DOMAIN

PLATFORMS = ["media_player"]

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Xantech Multi-Zone Amplifier component."""
    return True