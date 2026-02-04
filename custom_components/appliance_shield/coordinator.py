"""Data coordinator for Appliance Shield v0.3."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional
import logging

from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .ambient import AmbientModel
from .baseline import BaselineModel
from .const import (
    APPLIANCE_TYPES,
    CONF_AMBIENT_SENSOR,
    CONF_APPLIANCE_TYPE,
    CONF_CLIMATE_CLASS,
    CONF_ENERGY_SENSOR,
    CONF_FREEZER_VOLUME_LITERS,
    CONF_POWER_SENSOR,
    CONF_TARGET_ANNUAL_KWH,
    CONF_VOLUME_LITERS,
    CONF_WEATHER_ENTITY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_HISTORY_MINUTES,
    POWER_RUNNING_THRESHOLD_W,
    POWER_SPIKE_THRESHOLD_W,
)
from .eei import compute_eei
from .health import HealthEvaluator, HealthResult
from .signal_processing import CycleDetector, CycleSummary
from .storage import ApplianceShieldStorage

_LOGGER = logging.getLogger(__name__)


class RunningStats:
    """Online mean/variance helper."""

    def __init__(self) -> None:
        self.count = 0
        self.mean = 0.0
        self._m2 = 0.0

    def update(self, value: float) -> None:
        self.count += 1
        delta = value - self.mean
        self.mean += delta / self.count
        delta2 = value - self.mean
        self._m2 += delta * delta2

    @property
    def std(self) -> Optional[float]:
        if self.count < 2:
            return None
        variance = self._m2 / (self.count - 1)
        if variance < 0:
            return None
        return variance ** 0.5

    def as_dict(self) -> Dict[str, float | int]:
        return {"count": self.count, "mean": self.mean, "m2": self._m2}

    def load(self, data: Optional[Dict[str, float | int]]) -> None:
        if not data:
            return
        self.count = int(data.get("count", 0))
        self.mean = float(data.get("mean", 0.0))
        self._m2 = float(data.get("m2", 0.0))


@dataclass
class ApplianceDiagnostics:
    """Snapshot returned to HA entities and diagnostics."""

    health_state: str
    health_score: float
    issues: List[str]
    instantaneous_power_w: Optional[float]
    ambient_temp_c: Optional[float]
    daily_energy_kwh: Optional[float]
    normalized_daily_kwh: Optional[float]
    ewma_daily_kwh: Optional[float]
    runtime_ratio: Optional[float]
    observed_annual_kwh: Optional[float]
    expected_daily_kwh: Optional[float]
    reference_annual_kwh: Optional[float]
    energy_efficiency_index: Optional[float]
    primary_score: Optional[str]
    extended_score: Optional[str]
    sample_window_hours: float
    last_sample_utc: Optional[str]
    metadata: Dict[str, Optional[float | str]]
    compressor_running: bool
    door_open: bool
    confidence: float
    idle_hours: Optional[float]
    last_cycle_minutes: Optional[float]
    last_cycle_peak_w: Optional[float]
    last_cycle_avg_w: Optional[float]
    energy_residual_kwh: Optional[float]
    residual_sigma: Optional[float]

    def as_dict(self) -> Dict[str, object]:
        return asdict(self)


class ApplianceShieldCoordinator(DataUpdateCoordinator[ApplianceDiagnostics]):
    """Coordinator that performs analytics, storage, and health evaluation."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._power_entity = entry.data[CONF_POWER_SENSOR]
        self._energy_entity = entry.data.get(CONF_ENERGY_SENSOR)
        self._ambient_sensor = entry.data.get(CONF_AMBIENT_SENSOR)
        self._weather_entity = entry.data.get(CONF_WEATHER_ENTITY)

        self._power_history: Deque[tuple[datetime, float]] = deque(maxlen=MAX_HISTORY_MINUTES)
        self._energy_history: Deque[tuple[datetime, float]] = deque(maxlen=MAX_HISTORY_MINUTES)
        self._cycle_detector = CycleDetector()
        self._baseline = BaselineModel()
        self._ambient_model = AmbientModel()
        self._health = HealthEvaluator()
        self._storage = ApplianceShieldStorage(hass, entry.entry_id)
        self._storage_loaded = False
        self._pending_save = False
        self._residual_stats = RunningStats()
        self._energy_residual_kwh: Optional[float] = None
        self._last_logged_day: Optional[str] = None
        self._last_cycle_summary: Optional[CycleSummary] = None

        update_interval = entry.options.get("scan_interval") if entry.options else None
        super().__init__(
            hass,
            _LOGGER,
            name=f"Appliance Shield ({entry.title or entry.data.get(CONF_NAME)})",
            update_interval=timedelta(seconds=max(int(update_interval), 120))
            if update_interval
            else DEFAULT_SCAN_INTERVAL,
        )

    async def async_reset_baseline(self) -> None:
        self._baseline.reset()
        self._ambient_model = AmbientModel()
        self._residual_stats = RunningStats()
        self._energy_residual_kwh = None
        self._last_logged_day = None
        self._pending_save = True
        await self._storage.async_save(self._serialize_state())
        await self.async_request_refresh()

    async def _ensure_storage_loaded(self) -> None:
        if self._storage_loaded:
            return
        data = await self._storage.async_load()
        self._baseline.load(data.get("baseline"))
        self._ambient_model.load(data.get("ambient"))
        self._residual_stats.load(data.get("residual_stats"))
        self._last_logged_day = data.get("last_logged_day")
        self._energy_residual_kwh = data.get("last_residual")
        self._storage_loaded = True

    async def _async_update_data(self) -> ApplianceDiagnostics:
        await self._ensure_storage_loaded()
        now = dt_util.utcnow()
        power = self._state_to_float(self._power_entity)
        energy = self._state_to_float(self._energy_entity)

        if power is None and energy is None:
            raise UpdateFailed("Neither power nor energy sensor is reporting numeric data")

        ambient_temp = self._ambient_temperature()
        cycle_state = self._cycle_detector.update(now, power)
        if cycle_state.cycle_summary:
            self._baseline.update_cycle(
                cycle_state.cycle_summary.on_minutes,
                cycle_state.cycle_summary.off_minutes,
                cycle_state.cycle_summary.energy_kwh,
            )
            self._last_cycle_summary = cycle_state.cycle_summary
            self._pending_save = True

        self._update_histories(now, power, energy)
        daily_energy = self._estimate_daily_energy()
        runtime_ratio = self._estimate_runtime_ratio()
        self._update_daily_models(now, daily_energy, ambient_temp)

        metadata = self._metadata()
        correction = self._ambient_model.correction_factor(ambient_temp)
        eei_result = compute_eei(daily_energy, metadata, correction)
        normalized_daily = eei_result.normalized_daily_kwh or daily_energy
        observed_annual = normalized_daily * 365 if normalized_daily is not None else None
        reference_annual = (
            eei_result.reference_daily_kwh * 365 if eei_result.reference_daily_kwh else None
        )

        residual_sigma = self._residual_stats.std
        residual_z = None
        if self._energy_residual_kwh is not None and residual_sigma not in (None, 0.0):
            residual_z = self._energy_residual_kwh / residual_sigma

        issues = self._evaluate_issues(power, daily_energy, runtime_ratio)
        health_result = self._health.evaluate(
            issues,
            self._baseline,
            self._last_cycle_summary,
            runtime_ratio,
            residual_z,
            cycle_state.compressor_running,
        )

        sample_window_hours = self._sample_window_hours()
        last_sample = self._power_history[-1][0] if self._power_history else now

        diagnostics = ApplianceDiagnostics(
            health_state=health_result.state,
            health_score=health_result.score,
            issues=health_result.issues,
            instantaneous_power_w=power,
            ambient_temp_c=ambient_temp,
            daily_energy_kwh=daily_energy,
            normalized_daily_kwh=eei_result.normalized_daily_kwh,
            ewma_daily_kwh=self._baseline.daily_energy_ewma,
            runtime_ratio=runtime_ratio,
            observed_annual_kwh=observed_annual,
            expected_daily_kwh=eei_result.reference_daily_kwh,
            reference_annual_kwh=reference_annual,
            energy_efficiency_index=eei_result.eei,
            primary_score=eei_result.primary_class,
            extended_score=eei_result.extended_class,
            sample_window_hours=sample_window_hours,
            last_sample_utc=last_sample.isoformat(),
            metadata=metadata,
            compressor_running=health_result.compressor_running,
            door_open=health_result.door_open,
            confidence=self._baseline.confidence(),
            idle_hours=cycle_state.idle_hours,
            last_cycle_minutes=(self._last_cycle_summary.on_minutes if self._last_cycle_summary else None),
            last_cycle_peak_w=(self._last_cycle_summary.peak_power_w if self._last_cycle_summary else None),
            last_cycle_avg_w=(self._last_cycle_summary.avg_power_w if self._last_cycle_summary else None),
            energy_residual_kwh=self._energy_residual_kwh,
            residual_sigma=round(residual_sigma, 3) if residual_sigma else None,
        )

        if self._pending_save:
            await self._storage.async_save(self._serialize_state())
            self._pending_save = False

        return diagnostics

    def _serialize_state(self) -> Dict[str, object]:
        return {
            "baseline": self._baseline.as_dict(),
            "ambient": self._ambient_model.as_dict(),
            "residual_stats": self._residual_stats.as_dict(),
            "last_logged_day": self._last_logged_day,
            "last_residual": self._energy_residual_kwh,
        }

    def _state_to_float(self, entity_id: Optional[str]) -> Optional[float]:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            _LOGGER.debug("Non-numeric state for %s: %s", entity_id, state.state)
            return None

    def _ambient_temperature(self) -> Optional[float]:
        if self._ambient_sensor:
            temp = self._state_to_float(self._ambient_sensor)
            if temp is not None:
                return temp
        if self._weather_entity:
            weather = self.hass.states.get(self._weather_entity)
            if weather and (temp := weather.attributes.get("temperature")) is not None:
                try:
                    return float(temp)
                except (ValueError, TypeError):
                    return None
        return None

    def _update_histories(self, now: datetime, power: Optional[float], energy: Optional[float]) -> None:
        if power is not None:
            self._power_history.append((now, power))
        if energy is not None:
            if self._energy_history and energy < self._energy_history[-1][1]:
                self._energy_history.clear()
            self._energy_history.append((now, energy))
        cutoff = now - timedelta(minutes=MAX_HISTORY_MINUTES)
        while self._power_history and self._power_history[0][0] < cutoff:
            self._power_history.popleft()
        while self._energy_history and self._energy_history[0][0] < cutoff:
            self._energy_history.popleft()

    def _estimate_daily_energy(self) -> Optional[float]:
        daily = self._estimate_daily_energy_from_cumulative()
        if daily is not None:
            return daily
        return self._estimate_daily_energy_from_power()

    def _estimate_daily_energy_from_cumulative(self) -> Optional[float]:
        if len(self._energy_history) < 2:
            return None
        oldest_time, oldest_value = self._energy_history[0]
        newest_time, newest_value = self._energy_history[-1]
        elapsed_hours = (newest_time - oldest_time).total_seconds() / 3600
        if elapsed_hours < 6:
            return None
        delta = newest_value - oldest_value
        if delta <= 0:
            return None
        daily = delta / elapsed_hours * 24
        return round(daily, 3)

    def _estimate_daily_energy_from_power(self) -> Optional[float]:
        if len(self._power_history) < 2:
            return None
        oldest_time = self._power_history[0][0]
        newest_time = self._power_history[-1][0]
        coverage_hours = (newest_time - oldest_time).total_seconds() / 3600
        if coverage_hours < 6:
            return None
        samples = list(self._power_history)
        prev_time, prev_power = samples[0]
        energy_kwh = 0.0
        for timestamp, power in samples[1:]:
            delta_hours = (timestamp - prev_time).total_seconds() / 3600
            if delta_hours <= 0:
                prev_time, prev_power = timestamp, power
                continue
            if delta_hours > 1.0:
                prev_time, prev_power = timestamp, power
                continue
            avg_kw = ((prev_power + power) / 2.0) / 1000.0
            energy_kwh += avg_kw * delta_hours
            prev_time, prev_power = timestamp, power
        if energy_kwh <= 0 or coverage_hours <= 0:
            return None
        daily = energy_kwh / coverage_hours * 24
        return round(daily, 3)

    def _estimate_runtime_ratio(self) -> Optional[float]:
        if len(self._power_history) < 2:
            return None
        running_samples = sum(1 for _, value in self._power_history if value >= POWER_RUNNING_THRESHOLD_W)
        ratio = running_samples / len(self._power_history)
        return round(min(max(ratio, 0.0), 1.0), 3)

    def _update_daily_models(
        self,
        now: datetime,
        daily_energy: Optional[float],
        ambient_temp: Optional[float],
    ) -> None:
        if daily_energy is None:
            return
        day_key = now.date().isoformat()
        if self._last_logged_day == day_key:
            return
        self._baseline.update_daily_energy(day_key, daily_energy)
        if ambient_temp is not None:
            self._ambient_model.update(ambient_temp, daily_energy)
        if self._baseline.daily_energy_ewma is not None:
            residual = daily_energy - self._baseline.daily_energy_ewma
            self._energy_residual_kwh = round(residual, 3)
            self._residual_stats.update(residual)
        self._last_logged_day = day_key
        self._pending_save = True

    def _metadata(self) -> Dict[str, Optional[float | str]]:
        data = self.entry.data
        return {
            CONF_NAME: self.entry.title or data.get(CONF_NAME),
            CONF_APPLIANCE_TYPE: data.get(CONF_APPLIANCE_TYPE, APPLIANCE_TYPES[0]),
            CONF_VOLUME_LITERS: data.get(CONF_VOLUME_LITERS),
            CONF_FREEZER_VOLUME_LITERS: data.get(CONF_FREEZER_VOLUME_LITERS),
            CONF_CLIMATE_CLASS: data.get(CONF_CLIMATE_CLASS, "N"),
            CONF_TARGET_ANNUAL_KWH: data.get(CONF_TARGET_ANNUAL_KWH),
            CONF_POWER_SENSOR: data.get(CONF_POWER_SENSOR),
            CONF_ENERGY_SENSOR: data.get(CONF_ENERGY_SENSOR),
            CONF_AMBIENT_SENSOR: data.get(CONF_AMBIENT_SENSOR),
            CONF_WEATHER_ENTITY: data.get(CONF_WEATHER_ENTITY),
        }

    def _evaluate_issues(
        self,
        instantaneous_power: Optional[float],
        daily_energy: Optional[float],
        runtime_ratio: Optional[float],
    ) -> List[str]:
        issues: List[str] = []
        if instantaneous_power is None:
            issues.append("power_sensor_unavailable")
        elif instantaneous_power >= POWER_SPIKE_THRESHOLD_W:
            issues.append("compressor_power_spike")

        if daily_energy is None:
            issues.append("insufficient_energy_window")
        else:
            expected = self._baseline.daily_energy_ewma
            if expected:
                deviation = (daily_energy - expected) / expected
                if deviation >= 0.7:
                    issues.append("energy_far_above_baseline")
                elif deviation <= -0.5:
                    issues.append("energy_far_below_baseline")
                elif abs(deviation) >= 0.35:
                    issues.append("energy_out_of_range")

        if runtime_ratio is None:
            issues.append("insufficient_runtime_samples")

        return issues

    def _sample_window_hours(self) -> float:
        history: Deque[tuple[datetime, float]]
        if len(self._energy_history) >= 2:
            history = self._energy_history
        elif len(self._power_history) >= 2:
            history = self._power_history
        else:
            return 0.0
        oldest = history[0][0]
        newest = history[-1][0]
        hours = (newest - oldest).total_seconds() / 3600
        return round(hours, 2)
