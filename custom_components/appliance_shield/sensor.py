"""Sensor platform for Appliance Shield."""

from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import CONF_NAME
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CONFIDENCE,
    ATTR_DAILY_ENERGY_KWH,
    ATTR_EEI,
    ATTR_ENERGY_RESIDUAL_KWH,
    ATTR_EXPECTED_DAILY_KWH,
    ATTR_EXTENDED_SCORE,
    ATTR_EWMA_DAILY_KWH,
    ATTR_HEALTH_SCORE,
    ATTR_IDLE_HOURS,
    ATTR_ISSUES,
    ATTR_LAST_CYCLE_AVG_W,
    ATTR_LAST_CYCLE_MINUTES,
    ATTR_LAST_CYCLE_PEAK_W,
    ATTR_LAST_SAMPLE,
    ATTR_METADATA,
    ATTR_NORMALIZED_DAILY_KWH,
    ATTR_OBSERVED_ANNUAL_KWH,
    ATTR_REFERENCE_ANNUAL_KWH,
    ATTR_RESIDUAL_SIGMA,
    ATTR_RUNTIME_RATIO,
    ATTR_SAMPLE_WINDOW_HOURS,
    DOMAIN,
    ENERGY_SCORE_PRIMARY,
    HEALTH_STATE_INITIALIZING,
)
from .coordinator import ApplianceShieldCoordinator, ApplianceDiagnostics


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up sensors from a config entry."""
    coordinator: ApplianceShieldCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        ApplianceShieldHealthSensor(coordinator, entry.entry_id, entry.title or entry.data.get(CONF_NAME)),
        ApplianceShieldEnergyScoreSensor(coordinator, entry.entry_id, entry.title or entry.data.get(CONF_NAME)),
        ApplianceShieldEnergyIndexSensor(coordinator, entry.entry_id, entry.title or entry.data.get(CONF_NAME)),
    ]
    async_add_entities(entities)


class ApplianceShieldEntity(CoordinatorEntity[ApplianceShieldCoordinator], SensorEntity):
    """Base entity tied to the appliance coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ApplianceShieldCoordinator, entry_id: str, base_name: str | None) -> None:
        super().__init__(coordinator)
        self._entry_id = entry_id
        self._base_name = base_name or "Appliance"

    @property
    def device_info(self) -> Dict[str, Any]:
        metadata = self.coordinator.data.metadata if self.coordinator.data else {}
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


class ApplianceShieldHealthSensor(ApplianceShieldEntity):
    """Enum sensor describing appliance health."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [label for label, _ in (("initializing", 0), ("healthy", 1), ("attention", 2), ("critical", 3))]

    def __init__(self, coordinator: ApplianceShieldCoordinator, entry_id: str, base_name: str | None) -> None:
        super().__init__(coordinator, entry_id, base_name)
        self._attr_name = "Appliance health"
        self._attr_unique_id = f"{entry_id}_health"

    @property
    def native_value(self) -> str:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return HEALTH_STATE_INITIALIZING
        return diagnostics.health_state

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return {}
        return {
              ATTR_HEALTH_SCORE: diagnostics.health_score,
              ATTR_RUNTIME_RATIO: diagnostics.runtime_ratio,
              ATTR_DAILY_ENERGY_KWH: diagnostics.daily_energy_kwh,
              ATTR_SAMPLE_WINDOW_HOURS: diagnostics.sample_window_hours,
              ATTR_LAST_SAMPLE: diagnostics.last_sample_utc,
              ATTR_ISSUES: diagnostics.issues,
              ATTR_CONFIDENCE: diagnostics.confidence,
              ATTR_IDLE_HOURS: diagnostics.idle_hours,
              ATTR_LAST_CYCLE_MINUTES: diagnostics.last_cycle_minutes,
              ATTR_LAST_CYCLE_PEAK_W: diagnostics.last_cycle_peak_w,
              ATTR_LAST_CYCLE_AVG_W: diagnostics.last_cycle_avg_w,
              ATTR_ENERGY_RESIDUAL_KWH: diagnostics.energy_residual_kwh,
              ATTR_RESIDUAL_SIGMA: diagnostics.residual_sigma,
        }


class ApplianceShieldEnergyScoreSensor(ApplianceShieldEntity):
    """Enum sensor for EU-style energy scoring."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [band for band, _ in ENERGY_SCORE_PRIMARY]

    def __init__(self, coordinator: ApplianceShieldCoordinator, entry_id: str, base_name: str | None) -> None:
        super().__init__(coordinator, entry_id, base_name)
        self._attr_name = "Energy score"
        self._attr_unique_id = f"{entry_id}_energy_score"

    @property
    def native_value(self) -> Optional[str]:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return None
        return diagnostics.primary_score

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return {}
        return {
            ATTR_EEI: diagnostics.energy_efficiency_index,
            ATTR_EXTENDED_SCORE: diagnostics.extended_score,
            ATTR_EXPECTED_DAILY_KWH: diagnostics.expected_daily_kwh,
            ATTR_OBSERVED_ANNUAL_KWH: diagnostics.observed_annual_kwh,
            ATTR_REFERENCE_ANNUAL_KWH: diagnostics.reference_annual_kwh,
            ATTR_NORMALIZED_DAILY_KWH: diagnostics.normalized_daily_kwh,
            ATTR_CONFIDENCE: diagnostics.confidence,
            ATTR_METADATA: diagnostics.metadata,
        }


class ApplianceShieldEnergyIndexSensor(ApplianceShieldEntity):
    """Numeric sensor exposing the raw EEI for automations."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_unit_of_measurement = "EEI"

    def __init__(self, coordinator: ApplianceShieldCoordinator, entry_id: str, base_name: str | None) -> None:
        super().__init__(coordinator, entry_id, base_name)
        self._attr_name = "Energy index"
        self._attr_unique_id = f"{entry_id}_energy_index"

    @property
    def native_value(self) -> Optional[float]:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return None
        return diagnostics.energy_efficiency_index

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        diagnostics = self.coordinator.data
        if not diagnostics:
            return {}
        return {
            ATTR_EEI: diagnostics.energy_efficiency_index,
            ATTR_EXPECTED_DAILY_KWH: diagnostics.expected_daily_kwh,
            ATTR_OBSERVED_ANNUAL_KWH: diagnostics.observed_annual_kwh,
            ATTR_REFERENCE_ANNUAL_KWH: diagnostics.reference_annual_kwh,
            ATTR_EWMA_DAILY_KWH: diagnostics.ewma_daily_kwh,
            ATTR_NORMALIZED_DAILY_KWH: diagnostics.normalized_daily_kwh,
            ATTR_ENERGY_RESIDUAL_KWH: diagnostics.energy_residual_kwh,
            ATTR_RESIDUAL_SIGMA: diagnostics.residual_sigma,
            ATTR_CONFIDENCE: diagnostics.confidence,
        }
