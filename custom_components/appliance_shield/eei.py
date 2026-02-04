"""Energy efficiency index helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .const import (
    CLIMATE_CLASS_MULTIPLIERS,
    CONF_APPLIANCE_TYPE,
    CONF_CLIMATE_CLASS,
    CONF_FREEZER_VOLUME_LITERS,
    CONF_TARGET_ANNUAL_KWH,
    CONF_VOLUME_LITERS,
    DEFAULT_REFERENCE_TABLE,
    ENERGY_SCORE_EXTENDED,
    ENERGY_SCORE_PRIMARY,
)


@dataclass
class EEIResult:
    eei: Optional[float]
    primary_class: Optional[str]
    extended_class: Optional[str]
    normalized_daily_kwh: Optional[float]
    reference_daily_kwh: Optional[float]


def _score_from_index(eei: Optional[float], ladder: tuple[tuple[str, float], ...]) -> Optional[str]:
    if eei is None:
        return None
    for label, threshold in ladder:
        if eei <= threshold:
            return label
    return ladder[-1][0]


def reference_daily_energy(metadata: dict) -> Optional[float]:
    appliance_type = metadata.get(CONF_APPLIANCE_TYPE)
    volume = float(metadata.get(CONF_VOLUME_LITERS) or 0.0)
    freezer_volume = float(metadata.get(CONF_FREEZER_VOLUME_LITERS) or 0.0)
    climate = metadata.get(CONF_CLIMATE_CLASS, "N")
    if appliance_type not in DEFAULT_REFERENCE_TABLE:
        return None
    table = DEFAULT_REFERENCE_TABLE[appliance_type]
    effective_volume = volume
    if appliance_type == "fridge_freezer":
        effective_volume += 0.8 * freezer_volume
    elif appliance_type == "freezer":
        effective_volume = max(volume, freezer_volume)
    base_annual = table["base"] + table["per_liter"] * effective_volume
    multiplier = CLIMATE_CLASS_MULTIPLIERS.get(climate, 1.0)
    reference_annual = base_annual * multiplier
    return round(reference_annual / 365, 3)


def compute_eei(observed_daily_kwh: Optional[float], metadata: dict, correction_factor: float) -> EEIResult:
    if observed_daily_kwh is None:
        return EEIResult(None, None, None, None, reference_daily_energy(metadata))
    target_annual = metadata.get(CONF_TARGET_ANNUAL_KWH)
    reference_daily = (
        (float(target_annual) / 365.0)
        if target_annual
        else reference_daily_energy(metadata)
    )
    if not reference_daily:
        return EEIResult(None, None, None, None, None)
    normalized = observed_daily_kwh * correction_factor
    eei = round(normalized / reference_daily, 3)
    primary = _score_from_index(eei, ENERGY_SCORE_PRIMARY)
    extended = _score_from_index(eei, ENERGY_SCORE_EXTENDED)
    return EEIResult(eei, primary, extended, round(normalized, 3), reference_daily)
