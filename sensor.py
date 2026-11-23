"""Chilicon Cloud production sensors."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import voluptuous as vol

from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_INSTALLATION_HASH
from .coordinator import ChiliconCloud, ChiliconCoordinator

# The coordinator serializes cloud I/O for all entities.
PARALLEL_UPDATES = 0

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
    """Set up the sensors from YAML discovery."""
    if discovery_info is None:
        return

    installation_hash = discovery_info[CONF_INSTALLATION_HASH]
    coordinator = ChiliconCoordinator(
        hass,
        ChiliconCloud(
            async_create_clientsession(hass),
            discovery_info[CONF_USERNAME],
            discovery_info[CONF_PASSWORD],
            installation_hash,
        ),
    )
    # Add the entities even when the initial poll fails so polling can recover.
    await coordinator.async_refresh()
    installation_digest = sha256(installation_hash.encode()).hexdigest()
    async_add_entities(
        [
            ChiliconDailySensor(coordinator, installation_digest),
            ChiliconPowerSensor(coordinator, installation_digest),
            ChiliconLifetimeSensor(coordinator, installation_digest),
        ]
    )


class ChiliconSensor(CoordinatorEntity[ChiliconCoordinator], SensorEntity):
    """Represent one field of the shared production snapshot."""

    _field: str

    def __init__(
        self, coordinator: ChiliconCoordinator, installation_digest: str
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{installation_digest}_{self._field}"

    @property
    def native_value(self) -> float | None:
        """Return the latest value when the coordinator has a successful poll."""
        if self.available and self.coordinator.data is not None:
            return getattr(self.coordinator.data, self._field)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Report the last successful cloud poll time."""
        if self.coordinator.data is None:
            return None
        return {"last_successful_poll": self.coordinator.data.polled_at.isoformat()}


class ChiliconDailySensor(ChiliconSensor):
    """Report the production accumulated on the requested local date."""

    _field = "daily_energy"
    _attr_name = "Chilicon Solar Energy Daily Generated"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING


class ChiliconPowerSensor(ChiliconSensor):
    """Report the instantaneous solar power in watts."""

    _field = "power"
    _attr_name = "Chilicon Solar Power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT


class ChiliconLifetimeSensor(ChiliconSensor):
    """Expose the raw reported lifetime energy for optional inspection."""

    _field = "lifetime_energy"
    _attr_name = "Chilicon Solar Lifetime Energy"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_entity_registry_enabled_default = False
