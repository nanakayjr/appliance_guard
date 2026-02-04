"""Persistence helpers for Appliance Shield."""

from __future__ import annotations

from typing import Any, Dict

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DEFAULT_STORAGE_VERSION, DOMAIN


class ApplianceShieldStorage:
    """Simple wrapper around Home Assistant's Store helper."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, DEFAULT_STORAGE_VERSION, f"{DOMAIN}_{entry_id}")

    async def async_load(self) -> Dict[str, Any]:
        data = await self._store.async_load()
        return data or {}

    async def async_save(self, data: Dict[str, Any]) -> None:
        await self._store.async_save(data)
