"""Load the Chilicon Cloud sensor from YAML."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.typing import ConfigType

from .const import CONF_INSTALLATION_HASH, DOMAIN


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Chilicon Cloud from YAML."""
    # Pass the integration configuration to the sensor via discovery_info.
    discovery_info = {
        CONF_USERNAME: config[DOMAIN][CONF_USERNAME],
        CONF_PASSWORD: config[DOMAIN][CONF_PASSWORD],
        CONF_INSTALLATION_HASH: config[DOMAIN][CONF_INSTALLATION_HASH],
    }
    hass.async_create_task(
        async_load_platform(hass, Platform.SENSOR, DOMAIN, discovery_info, config)
    )

    return True

