"""Config flow for Appliance Shield."""

from __future__ import annotations

from typing import Any, Dict

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import voluptuous as vol

from .const import (
    APPLIANCE_TYPES,
    CLIMATE_CLASSES,
    CONF_APPLIANCE_TYPE,
    CONF_CLIMATE_CLASS,
    CONF_ENERGY_SENSOR,
    CONF_FREEZER_VOLUME_LITERS,
    CONF_POWER_SENSOR,
    CONF_TARGET_ANNUAL_KWH,
    CONF_VOLUME_LITERS,
    DOMAIN,
)


class ApplianceShieldConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Appliance Shield."""

    VERSION = 1

    def __init__(self) -> None:
        self._metadata: Dict[str, Any] | None = None

    async def async_step_user(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        if user_input is None:
            data_schema = vol.Schema(
                {
                    vol.Required(CONF_NAME): str,
                    vol.Required(CONF_APPLIANCE_TYPE, default=APPLIANCE_TYPES[0]): vol.In(APPLIANCE_TYPES),
                    vol.Required(CONF_VOLUME_LITERS, default=300): vol.All(vol.Coerce(float), vol.Range(min=10, max=900)),
                    vol.Optional(CONF_FREEZER_VOLUME_LITERS, default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=900)),
                    vol.Optional(CONF_CLIMATE_CLASS, default="N"): vol.In(CLIMATE_CLASSES),
                    vol.Optional(CONF_TARGET_ANNUAL_KWH): vol.All(vol.Coerce(float), vol.Range(min=10, max=2000)),
                }
            )
            return self.async_show_form(step_id="user", data_schema=data_schema)

        self._metadata = user_input
        return await self.async_step_sources()

    async def async_step_sources(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        if user_input is None:
            data_schema = vol.Schema(
                {
                    vol.Required(CONF_POWER_SENSOR): selector.selector(
                        {"entity": {"domain": "sensor", "device_class": "power"}}
                    ),
                    vol.Required(CONF_ENERGY_SENSOR): selector.selector(
                        {"entity": {"domain": "sensor", "device_class": "energy"}}
                    ),
                }
            )
            return self.async_show_form(step_id="sources", data_schema=data_schema)

        if not self._metadata:
            # Should never happen but guard.
            self._metadata = {}

        data = {**self._metadata, **user_input}
        unique_id = f"{data.get(CONF_NAME)}_{data[CONF_POWER_SENSOR]}"
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(title=data[CONF_NAME], data=data)

    async def async_step_import(self, user_input: Dict[str, Any]) -> FlowResult:
        """Support YAML import (structure mirrors user flow)."""
        return await self.async_step_user(user_input)

    @staticmethod
    def async_get_options_flow(entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return ApplianceShieldOptionsFlow(entry)


class ApplianceShieldOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Appliance Shield."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self.entry = entry

    async def async_step_init(self, user_input: Dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_interval = self.entry.options.get("scan_interval", 300)
        data_schema = vol.Schema(
            {
                vol.Optional("scan_interval", default=current_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=120, max=1800)
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=data_schema)
