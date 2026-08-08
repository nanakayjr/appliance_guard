"""Binary sensor platform for Appliance Shield."""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import CONF_NAME
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CONFIDENCE,
    ATTR_DAILY_ENERGY_KWH,
    ATTR_ISSUES,
    ATTR_LAST_SAMPLE,
    ATTR_METADATA,
    ATTR_RUNTIME_RATIO,
    ATTR_SAMPLE_WINDOW_HOURS,
    DOMAIN,
)
from .coordinator import ApplianceShieldCoordinator


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Register Appliance Shield binary sensors."""
    coordinator: ApplianceShieldCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        ApplianceShieldCompressorBinarySensor(
            coordinator,
            entry.entry_id,
            entry.title or entry.data.get(CONF_NAME),
        ),
        ApplianceShieldDoorBinarySensor(
            coordinator,
            entry.entry_id,
            entry.title or entry.data.get(CONF_NAME),
        ),
    ]
    async_add_entities(entities)


class ApplianceShieldBinaryEntity(CoordinatorEntity[ApplianceShieldCoordinator], BinarySensorEntity):
    """Base class for Appliance Shield binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ApplianceShieldCoordinator, entry_id: str, base_name: str | None) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._base_name = base_name or "Appliance"

    @property
    def device_info(self) -> Dict[str, Any]:
        diagnostics = self.coordinator.data
        metadata = diagnostics.metadata if diagnostics else {}
        return {
            "identifiers": {(DOMAIN, self._entry_id)},
            "manufacturer": "Appliance Shield",
            "name": self._base_name,
            "model": str(metadata.get("appliance_type", "unknown")).replace("_", " ").title(),
            "configuration_url": "https://github.com/nanakayjr/appliance_guard",
        }

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None


class ApplianceShieldCompressorBinarySensor(ApplianceShieldBinaryEntity):
    """Binary sensor reflecting compressor runtime state."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator: ApplianceShieldCoordinator, entry_id: str, base_name: str | None) -> None:
        super().__init__(coordinator, entry_id, base_name)
        self._attr_name = "Compressor running"
        self._attr_unique_id = f"{entry_id}_compressor_running"

    @property
    def is_on(self) -> bool:
        diagnostics = self.coordinator.data
        return bool(diagnostics and diagnostics.compressor_running)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return {}
        return {
            ATTR_RUNTIME_RATIO: diagnostics.runtime_ratio,
            ATTR_SAMPLE_WINDOW_HOURS: diagnostics.sample_window_hours,
            ATTR_LAST_SAMPLE: diagnostics.last_sample_utc,
            ATTR_DAILY_ENERGY_KWH: diagnostics.daily_energy_kwh,
        }


class ApplianceShieldDoorBinarySensor(ApplianceShieldBinaryEntity):
    """Binary sensor representing inferred door-open faults."""

    _attr_device_class = BinarySensorDeviceClass.DOOR

    def __init__(self, coordinator: ApplianceShieldCoordinator, entry_id: str, base_name: str | None) -> None:
        super().__init__(coordinator, entry_id, base_name)
        self._attr_name = "Door open inferred"
        self._attr_unique_id = f"{entry_id}_door_open"

    @property
    def is_on(self) -> bool:
        diagnostics = self.coordinator.data
        return bool(diagnostics and diagnostics.door_open)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return {}
        return {
            ATTR_ISSUES: diagnostics.issues,
            ATTR_CONFIDENCE: diagnostics.confidence,
            ATTR_METADATA: diagnostics.metadata,
        }
