"""Diagnostics support for Appliance Shield."""

from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_ENERGY_SENSOR,
    CONF_POWER_SENSOR,
    DOMAIN,
)

REDACT_KEYS = {CONF_POWER_SENSOR, CONF_ENERGY_SENSOR}


def _serialize_coordinator(coordinator) -> dict:
    if not coordinator.data:
        return {}
    return coordinator.data.as_dict()


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry):
    """Return diagnostics data for a config entry."""
    coordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is None:
        raise HomeAssistantError("Coordinator missing for diagnostics request")

    redacted_entry = async_redact_data(entry.data, REDACT_KEYS)
    return {
        "config_entry": redacted_entry,
        "options": entry.options,
        "diagnostics": _serialize_coordinator(coordinator),
    }
