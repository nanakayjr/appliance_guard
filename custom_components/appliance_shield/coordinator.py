"""Data coordinator for Appliance Shield."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Deque, Dict, List, Optional, Tuple
import logging
import math

from homeassistant.const import CONF_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    APPLIANCE_TYPES,
    ATTR_DAILY_ENERGY_KWH,
    ATTR_EEI,
    ATTR_EXPECTED_DAILY_KWH,
    ATTR_EXTENDED_SCORE,
    ATTR_ISSUES,
    ATTR_LAST_SAMPLE,
    ATTR_METADATA,
    ATTR_OBSERVED_ANNUAL_KWH,
    ATTR_REFERENCE_ANNUAL_KWH,
    ATTR_RUNTIME_RATIO,
    ATTR_SAMPLE_WINDOW_HOURS,
    CLIMATE_CLASS_MULTIPLIERS,
    CONF_APPLIANCE_TYPE,
    CONF_CLIMATE_CLASS,
    CONF_ENERGY_SENSOR,
    CONF_FREEZER_VOLUME_LITERS,
    CONF_POWER_SENSOR,
    CONF_TARGET_ANNUAL_KWH,
    CONF_VOLUME_LITERS,
    CYCLE_LONG_SIGMA,
    CYCLE_SHORT_SIGMA,
    DEFAULT_REFERENCE_TABLE,
    DEFAULT_SCAN_INTERVAL,
    ENERGY_SCORE_EXTENDED,
    ENERGY_SCORE_PRIMARY,
    EWMA_ALPHA,
    HEALTH_STATE_ATTENTION,
    HEALTH_STATE_CRITICAL,
    HEALTH_STATE_HEALTHY,
    HEALTH_STATE_INITIALIZING,
    IDLE_TIMEOUT_MINUTES_MIN,
    IDLE_TIMEOUT_MULTIPLIER,
    MAX_HISTORY_MINUTES,
    MIN_RESIDUAL_SAMPLES,
    POWER_RUNNING_THRESHOLD_W,
    POWER_SPIKE_THRESHOLD_W,
    RESIDUAL_SIGMA_ATTENTION,
    RESIDUAL_SIGMA_CRITICAL,
    SIGNATURE_MIN_CYCLES,
    STALL_PEAK_THRESHOLD_W,
    STANDBY_HIGH_W,
)

_LOGGER = logging.getLogger(__name__)


class RunningStats:
    """Online mean and variance tracker."""

    __slots__ = ("count", "mean", "_m2")

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
    def variance(self) -> Optional[float]:
        if self.count < 2:
            return None
        return self._m2 / (self.count - 1)

    @property
    def std(self) -> Optional[float]:
        variance = self.variance
        if variance is None or variance < 0:
            return None
        return math.sqrt(variance)

    def snapshot(self) -> Dict[str, Optional[float | int]]:
        """Return a serializable view of the stats."""
        std = self.std
        return {
            "mean": round(self.mean, 3) if self.count else None,
            "std": round(std, 3) if std is not None else None,
            "count": self.count,
        }


@dataclass
class CycleSnapshot:
    """Structured snapshot of compressor cycle features."""

    state: str
    signature_ready: bool
    idle_hours: Optional[float]
    last_on_minutes: Optional[float]
    last_avg_power_w: Optional[float]
    last_peak_power_w: Optional[float]
    standby_mean_w: Optional[float]
    feature_stats: Dict[str, Dict[str, Optional[float | int]]]


class CycleTracker:
    """Tracks compressor cycles using coarse-grained power samples."""

    def __init__(self) -> None:
        self._last_running: Optional[bool] = None
        self._current_on_start: Optional[datetime] = None
        self._current_sum = 0.0
        self._current_count = 0
        self._current_peak = 0.0
        self._last_off_start: Optional[datetime] = None
        self._last_cycle_end: Optional[datetime] = None
        self._last_on_minutes: Optional[float] = None
        self._last_avg_power: Optional[float] = None
        self._last_peak_power: Optional[float] = None
        self._state = "calibrating"
        self._on_stats = RunningStats()
        self._off_stats = RunningStats()
        self._avg_power_stats = RunningStats()
        self._standby_stats = RunningStats()

    def update(self, timestamp: datetime, power: Optional[float]) -> CycleSnapshot:
        running = power is not None and power >= POWER_RUNNING_THRESHOLD_W

        if self._last_running is None:
            # Initialize state machine without classification.
            self._last_running = running
            if running:
                self._start_on(timestamp, power)
            else:
                self._last_off_start = timestamp
            return self._snapshot(timestamp)

        sample_recorded = False
        if running and not self._last_running:
            self._handle_off_to_on(timestamp)
            self._start_on(timestamp, power)
            sample_recorded = True
        elif not running and self._last_running:
            self._handle_on_to_off(timestamp)
            self._last_off_start = timestamp

        if running:
            if self._current_on_start is None:
                self._start_on(timestamp, power)
                sample_recorded = True
            elif not sample_recorded and power is not None:
                self._current_sum += power
                self._current_count += 1
                self._current_peak = max(self._current_peak, power)
        else:
            if power is not None:
                self._standby_stats.update(power)
            if self._last_off_start is None:
                self._last_off_start = timestamp

        self._last_running = running
        self._state = self._classify(timestamp)
        return self._snapshot(timestamp)

    def _handle_off_to_on(self, timestamp: datetime) -> None:
        if self._last_off_start is None:
            return
        off_minutes = (timestamp - self._last_off_start).total_seconds() / 60
        if off_minutes > 0:
            self._off_stats.update(off_minutes)
        self._last_off_start = None

    def _handle_on_to_off(self, timestamp: datetime) -> None:
        if self._current_on_start is None:
            return
        on_minutes = (timestamp - self._current_on_start).total_seconds() / 60
        if on_minutes > 0:
            avg_power = (self._current_sum / self._current_count) if self._current_count else None
            if avg_power is not None:
                self._avg_power_stats.update(avg_power)
            self._on_stats.update(on_minutes)
            self._last_on_minutes = round(on_minutes, 2)
            self._last_avg_power = round(avg_power, 2) if avg_power is not None else None
            self._last_peak_power = round(self._current_peak, 2) if self._current_peak else None
            self._last_cycle_end = timestamp

        self._current_on_start = None
        self._current_sum = 0.0
        self._current_count = 0
        self._current_peak = 0.0

    def _start_on(self, timestamp: datetime, power: Optional[float]) -> None:
        self._current_on_start = timestamp
        self._current_sum = power or POWER_RUNNING_THRESHOLD_W
        self._current_count = 1
        self._current_peak = power or POWER_RUNNING_THRESHOLD_W

    def _idle_hours(self, timestamp: datetime) -> Optional[float]:
        if self._last_cycle_end is None:
            return None
        idle_hours = (timestamp - self._last_cycle_end).total_seconds() / 3600
        return round(idle_hours, 3)

    def _mean_cycle_period_minutes(self) -> Optional[float]:
        if self._on_stats.count < 1 or self._off_stats.count < 1:
            return None
        return self._on_stats.mean + self._off_stats.mean

    def _classify(self, timestamp: datetime) -> str:
        idle_hours = self._idle_hours(timestamp)
        mean_period = self._mean_cycle_period_minutes()
        if idle_hours is not None and mean_period:
            idle_minutes = idle_hours * 60
            idle_threshold = max(IDLE_TIMEOUT_MULTIPLIER * mean_period, IDLE_TIMEOUT_MINUTES_MIN)
            if idle_minutes > idle_threshold:
                return "idle"

        if self._on_stats.count < SIGNATURE_MIN_CYCLES or self._off_stats.count < SIGNATURE_MIN_CYCLES:
            return "calibrating"

        if self._last_peak_power is not None and self._last_peak_power < STALL_PEAK_THRESHOLD_W:
            return "stalled_cycle"

        std_on = self._on_stats.std or 0.0
        if self._last_on_minutes is not None and std_on > 0:
            lower = self._on_stats.mean - CYCLE_SHORT_SIGMA * std_on
            upper = self._on_stats.mean + CYCLE_LONG_SIGMA * std_on
            if self._last_on_minutes < lower:
                return "short_cycle"
            if self._last_on_minutes > upper:
                return "long_cycle"

        return "normal"

    def _feature_stats(self) -> Dict[str, Dict[str, Optional[float]]]:
        return {
            "on_minutes": self._on_stats.snapshot(),
            "off_minutes": self._off_stats.snapshot(),
            "avg_power_w": self._avg_power_stats.snapshot(),
            "standby_power_w": self._standby_stats.snapshot(),
        }

    def _snapshot(self, timestamp: datetime) -> CycleSnapshot:
        idle_hours = self._idle_hours(timestamp)
        standby_mean = self._standby_stats.mean if self._standby_stats.count else None
        return CycleSnapshot(
            state=self._state,
            signature_ready=(
                self._on_stats.count >= SIGNATURE_MIN_CYCLES and self._off_stats.count >= SIGNATURE_MIN_CYCLES
            ),
            idle_hours=idle_hours,
            last_on_minutes=self._last_on_minutes,
            last_avg_power_w=self._last_avg_power,
            last_peak_power_w=self._last_peak_power,
            standby_mean_w=round(standby_mean, 2) if standby_mean is not None else None,
            feature_stats=self._feature_stats(),
        )

@dataclass
class ApplianceDiagnostics:
    """Snapshot of appliance telemetry and analytics."""

    health_state: str
    issues: List[str]
    instantaneous_power_w: Optional[float]
    daily_energy_kwh: Optional[float]
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
    cycle_state: str
    idle_hours: Optional[float]
    ewma_daily_kwh: Optional[float]
    energy_residual_kwh: Optional[float]
    residual_sigma: Optional[float]
    signature_ready: bool
    feature_stats: Dict[str, Dict[str, Optional[float | int]]]
    last_cycle_minutes: Optional[float]
    last_cycle_peak_w: Optional[float]
    last_cycle_avg_w: Optional[float]
    standby_power_w: Optional[float]

    def as_dict(self) -> Dict[str, object]:
        """Return dict for coordinator consumers."""
        return asdict(self)


class ApplianceShieldCoordinator(DataUpdateCoordinator[ApplianceDiagnostics]):
    """Coordinator that evaluates appliance health."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        self._power_entity = entry.data[CONF_POWER_SENSOR]
        self._energy_entity = entry.data[CONF_ENERGY_SENSOR]
        self._power_history: Deque[Tuple[datetime, float]] = deque(maxlen=MAX_HISTORY_MINUTES)
        self._energy_history: Deque[Tuple[datetime, float]] = deque(maxlen=MAX_HISTORY_MINUTES)
        self._cycle_tracker = CycleTracker()
        self._ewma_daily: Optional[float] = None
        self._energy_residual: Optional[float] = None
        self._residual_stats = RunningStats()
        self._residual_sigma: Optional[float] = None

        update_interval = entry.options.get("scan_interval") if entry.options else None
        super().__init__(
            hass,
            _LOGGER,
            name=f"Appliance Shield ({entry.title or entry.data.get(CONF_NAME)})",
            update_interval=timedelta(seconds=max(int(update_interval), 120))
            if update_interval
            else DEFAULT_SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> ApplianceDiagnostics:
        """Fetch latest HA states and derive analytics."""
        now = dt_util.utcnow()
        power = self._state_to_float(self._power_entity)
        energy = self._state_to_float(self._energy_entity)

        if power is None and energy is None:
            raise UpdateFailed("Neither power nor energy sensor is reporting numeric data")

        cycle_snapshot = self._cycle_tracker.update(now, power)

        if power is not None:
            self._power_history.append((now, power))
        if energy is not None:
            if self._energy_history and energy < self._energy_history[-1][1]:
                # Counter reset, discard stale samples.
                self._energy_history.clear()
            self._energy_history.append((now, energy))

        self._prune_history(now)

        daily_energy = self._estimate_daily_energy()
        runtime_ratio = self._estimate_runtime_ratio()
        self._update_energy_baseline(daily_energy)
        metadata = self._metadata()
        expected_daily = self._expected_daily_kwh(metadata)
        reference_annual = (
            metadata.get(CONF_TARGET_ANNUAL_KWH)
            if metadata.get(CONF_TARGET_ANNUAL_KWH)
            else (expected_daily * 365 if expected_daily is not None else None)
        )
        observed_annual = daily_energy * 365 if daily_energy is not None else None
        eei = self._compute_eei(observed_annual, reference_annual)
        primary_score = self._score_from_index(eei, ENERGY_SCORE_PRIMARY)
        extended_score = self._score_from_index(eei, ENERGY_SCORE_EXTENDED)

        issues = self._evaluate_issues(
            power,
            daily_energy,
            runtime_ratio,
            expected_daily,
            cycle_snapshot,
        )
        health_state = self._derive_health_state(issues, daily_energy)

        sample_window_hours = self._sample_window_hours()
        last_sample = self._power_history[-1][0] if self._power_history else now

        diagnostics = ApplianceDiagnostics(
            health_state=health_state,
            issues=issues,
            instantaneous_power_w=power,
            daily_energy_kwh=daily_energy,
            runtime_ratio=runtime_ratio,
            observed_annual_kwh=observed_annual,
            expected_daily_kwh=expected_daily,
            reference_annual_kwh=reference_annual,
            energy_efficiency_index=eei,
            primary_score=primary_score,
            extended_score=extended_score,
            sample_window_hours=sample_window_hours,
            last_sample_utc=last_sample.isoformat(),
            metadata=metadata,
            cycle_state=cycle_snapshot.state,
            idle_hours=cycle_snapshot.idle_hours,
            ewma_daily_kwh=self._ewma_daily,
            energy_residual_kwh=self._energy_residual,
            residual_sigma=self._residual_sigma,
            signature_ready=cycle_snapshot.signature_ready,
            feature_stats=cycle_snapshot.feature_stats,
            last_cycle_minutes=cycle_snapshot.last_on_minutes,
            last_cycle_peak_w=cycle_snapshot.last_peak_power_w,
            last_cycle_avg_w=cycle_snapshot.last_avg_power_w,
            standby_power_w=cycle_snapshot.standby_mean_w,
        )
        return diagnostics

    def _state_to_float(self, entity_id: str) -> Optional[float]:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            _LOGGER.debug("Non-numeric state for %s: %s", entity_id, state.state)
            return None

    def _prune_history(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=MAX_HISTORY_MINUTES)
        while self._power_history and self._power_history[0][0] < cutoff:
            self._power_history.popleft()
        while self._energy_history and self._energy_history[0][0] < cutoff:
            self._energy_history.popleft()

    def _estimate_daily_energy(self) -> Optional[float]:
        if len(self._energy_history) < 2:
            return None
        oldest_time, oldest_value = self._energy_history[0]
        newest_time, newest_value = self._energy_history[-1]
        elapsed_hours = (newest_time - oldest_time).total_seconds() / 3600
        if elapsed_hours < 1:
            return None
        delta = newest_value - oldest_value
        if delta <= 0:
            return None
        daily = delta / elapsed_hours * 24
        return round(daily, 3)

    def _estimate_runtime_ratio(self) -> Optional[float]:
        if len(self._power_history) < 2:
            return None
        running_samples = sum(1 for _, value in self._power_history if value >= POWER_RUNNING_THRESHOLD_W)
        ratio = running_samples / len(self._power_history)
        return round(min(max(ratio, 0.0), 1.0), 3)

    def _update_energy_baseline(self, daily_energy: Optional[float]) -> None:
        if daily_energy is None:
            return
        if self._ewma_daily is None:
            self._ewma_daily = round(daily_energy, 3)
            self._energy_residual = 0.0
            self._residual_sigma = None
            return

        residual = daily_energy - self._ewma_daily
        self._energy_residual = round(residual, 3)
        self._residual_stats.update(residual)
        sigma = self._residual_stats.std
        self._residual_sigma = round(sigma, 3) if sigma is not None else None

        updated_baseline = EWMA_ALPHA * daily_energy + (1 - EWMA_ALPHA) * self._ewma_daily
        self._ewma_daily = round(updated_baseline, 3)

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
        }

    def _expected_daily_kwh(self, metadata: Dict[str, Optional[float | str]]) -> Optional[float]:
        appliance_type = metadata.get(CONF_APPLIANCE_TYPE)
        if appliance_type not in DEFAULT_REFERENCE_TABLE:
            return None
        table = DEFAULT_REFERENCE_TABLE[appliance_type]
        volume = metadata.get(CONF_VOLUME_LITERS) or 0.0
        freezer_volume = metadata.get(CONF_FREEZER_VOLUME_LITERS) or 0.0
        climate_multiplier = CLIMATE_CLASS_MULTIPLIERS.get(metadata.get(CONF_CLIMATE_CLASS, "N"), 1.0)

        effective_volume = volume
        if appliance_type == "fridge_freezer":
            effective_volume += 0.8 * freezer_volume
        elif appliance_type == "freezer":
            effective_volume = max(volume, freezer_volume)

        reference_annual = (table["base"] + table["per_liter"] * effective_volume) * climate_multiplier
        return round(reference_annual / 365, 3)

    def _compute_eei(self, observed_annual: Optional[float], reference_annual: Optional[float]) -> Optional[float]:
        if not observed_annual or not reference_annual:
            return None
        eei = observed_annual / reference_annual
        return round(eei, 3)

    def _score_from_index(self, eei: Optional[float], ladder: Tuple[Tuple[str, float], ...]) -> Optional[str]:
        if eei is None:
            return None
        for label, threshold in ladder:
            if eei <= threshold:
                return label
        return ladder[-1][0]

    def _evaluate_issues(
        self,
        instantaneous_power: Optional[float],
        daily_energy: Optional[float],
        runtime_ratio: Optional[float],
        expected_daily: Optional[float],
        cycle_snapshot: CycleSnapshot,
    ) -> List[str]:
        issues: List[str] = []
        if instantaneous_power is None:
            issues.append("power_sensor_unavailable")
        elif instantaneous_power >= POWER_SPIKE_THRESHOLD_W:
            issues.append("compressor_power_spike")

        if daily_energy is None:
            issues.append("insufficient_energy_window")
        elif expected_daily:
            deviation = (daily_energy - expected_daily) / expected_daily
            if deviation >= 0.7:
                issues.append("energy_far_above_baseline")
            elif deviation <= -0.5:
                issues.append("energy_far_below_baseline")
            elif abs(deviation) >= 0.35:
                issues.append("energy_out_of_range")

        if runtime_ratio is None:
            issues.append("insufficient_runtime_samples")
        else:
            if runtime_ratio <= 0.1:
                issues.append("runtime_too_low")
            elif runtime_ratio >= 0.8:
                issues.append("runtime_too_high")

        if cycle_snapshot.signature_ready:
            if cycle_snapshot.state == "short_cycle":
                issues.append("cycle_short_detected")
            elif cycle_snapshot.state == "long_cycle":
                issues.append("cycle_long_detected")
            elif cycle_snapshot.state == "stalled_cycle":
                issues.append("cycle_stalled_detected")
            elif cycle_snapshot.state == "idle":
                issues.append("cycle_idle_timeout")

        if cycle_snapshot.standby_mean_w is not None and cycle_snapshot.standby_mean_w >= STANDBY_HIGH_W:
            issues.append("standby_power_high")

        if (
            self._energy_residual is not None
            and self._residual_sigma not in (None, 0.0)
            and self._residual_stats.count >= MIN_RESIDUAL_SAMPLES
        ):
            residual_indicator = "energy_residual_high" if self._energy_residual > 0 else "energy_residual_low"
            z_score = self._energy_residual / self._residual_sigma
            if abs(z_score) >= RESIDUAL_SIGMA_CRITICAL:
                issues.append("energy_residual_extreme")
                issues.append(residual_indicator)
            elif abs(z_score) >= RESIDUAL_SIGMA_ATTENTION:
                issues.append("energy_residual_anomaly")
                issues.append(residual_indicator)

        return issues

    def _derive_health_state(self, issues: List[str], daily_energy: Optional[float]) -> str:
        if not self._energy_history or self._sample_window_hours() < 4:
            return HEALTH_STATE_INITIALIZING

        critical_tokens = {
            "compressor_power_spike",
            "energy_far_above_baseline",
            "cycle_stalled_detected",
            "cycle_idle_timeout",
            "energy_residual_extreme",
        }
        if any(token in issues for token in critical_tokens):
            return HEALTH_STATE_CRITICAL

        attention_tokens = {
            "energy_out_of_range",
            "energy_far_below_baseline",
            "runtime_too_low",
            "runtime_too_high",
            "cycle_short_detected",
            "cycle_long_detected",
            "standby_power_high",
            "energy_residual_anomaly",
            "energy_residual_high",
            "energy_residual_low",
        }
        if any(token in issues for token in attention_tokens):
            return HEALTH_STATE_ATTENTION

        if daily_energy is None:
            return HEALTH_STATE_ATTENTION

        return HEALTH_STATE_HEALTHY

    def _sample_window_hours(self) -> float:
        if len(self._energy_history) < 2:
            return 0.0
        oldest = self._energy_history[0][0]
        newest = self._energy_history[-1][0]
        hours = (newest - oldest).total_seconds() / 3600
        return round(hours, 2)
