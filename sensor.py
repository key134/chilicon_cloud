"""Platform for sensor integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from hashlib import sha256

import aiohttp
import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt

from .const import CHILICON_URL, CONF_INSTALLATION_HASH

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=15)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Required(CONF_INSTALLATION_HASH): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the sensor platform."""
    # We only want this platform to be set up via discovery.
    if discovery_info is None:
        return

    user = discovery_info[CONF_USERNAME]
    pw = discovery_info[CONF_PASSWORD]
    installation_hash = discovery_info[CONF_INSTALLATION_HASH]

    _LOGGER.debug("running chilicon_cloud.async_setup_platform()")
    session = async_get_clientsession(hass)
    cc = ChiliconCloud(session, user, pw, installation_hash)
    await cc.login()

    async_add_entities([ChiliconDailySensor(cc)], update_before_add=True)


class ChiliconDailySensor(SensorEntity):
    """Representation of a sensor."""

    _attr_name = "Chilicon Solar Energy Daily Generated"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, cc: ChiliconCloud):
        self.cc = cc
        installation_digest = sha256(cc.installation_hash.encode()).hexdigest()
        self._attr_unique_id = f"{installation_digest}_daily_energy"

    async def async_update(self) -> None:
        """Fetch new state data for the sensor.

        This is the only method that should fetch new data for Home Assistant.
        """
        kwh = await self.cc.fetch_data(retry=True)
        if kwh != -1:
            self._attr_native_value = kwh
            _LOGGER.debug("Updated energy: %s kWh", kwh)


class ChiliconCloud:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        installation_hash: str,
    ):
        self.session = session
        self.username = username
        self.password = password
        self.installation_hash = installation_hash

    async def login(self) -> bool:
        headers = {
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        form_data = {
            'username': self.username,
            'password': self.password,
            'Login': 'Login'
        }

        async with self.session.post(
            f"{CHILICON_URL}/login",
            data=form_data,
            headers=headers,
            allow_redirects=False,
        ) as resp:
            loc = resp.headers.get('Location')
            expected = f"/installation/{self.installation_hash}"
            if expected != loc:
                _LOGGER.warning(
                    "Chilicon login did not redirect to the configured installation"
                )
                return False
            return True

    async def fetch_data(self, retry: bool) -> float:
        _LOGGER.debug("Invoking ChiliconCloud.fetch_data()")

        dt_local = dt.now()
        today = dt_local.strftime("%Y-%m-%d")

        headers = {
            'Host': 'cloud.chiliconpower.com',
            'Referer': "{}/installation/{}".format(CHILICON_URL, self.installation_hash)
        }

        update_url = f"{CHILICON_URL}/ajax/fetchOwnerUpdate?today={today}"
        async with self.session.get(update_url, headers=headers) as resp:
            status = resp.status
            if status == 200:
                day_data, lifetime, current = await resp.json()
                _LOGGER.debug(
                    "Chilicon API response: lifetime=%r, current=%r",
                    lifetime,
                    current,
                )
                return self.calculate_today(day_data)

        _LOGGER.warning("Chilicon update returned HTTP %s", status)
        if status in (400, 401) and retry:
            await self.login()
            _LOGGER.debug("Retrying Chilicon update after login")
            return await self.fetch_data(retry=False)
        return -1

    def calculate_today(self, today_data):
        # The today array contains the average power generated for each 5 minute
        # period of the day. A value of -1 means that we can ignore that data point
        # either because it is in the future or because no power was generated in
        # that time. All of the readings are in watts.
        # To get the number of kilowatt-hours generated in a day, sum up all of the
        # readings divided by 12000. (Each 5 minute period is 1/12 of an hour, and
        # each watt is 1/1000 of a kilowatt)
        energy = 0.0
        for p in today_data:
            if p == -1:
                continue
            energy += p / 12000

        return energy
