"""Appliance Shield integration entry point."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS
from .coordinator import ApplianceShieldCoordinator

ConfigEntryType = ConfigEntry


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up via YAML (placeholder to satisfy HA)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntryType) -> bool:
    """Set up Appliance Shield from a config entry."""
    coordinator = ApplianceShieldCoordinator(hass, entry)
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntryType) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntryType) -> None:
    """Reload a config entry on demand."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
