"""Signal processing helpers for Appliance Shield."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Deque, List, Optional

from .const import POWER_RUNNING_THRESHOLD_W, PERCENTILE_WINDOW


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    index = pct * (len(sorted_values) - 1)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    frac = index - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


class PercentileWindow:
    """Tracks recent power samples for adaptive thresholds."""

    def __init__(self, maxlen: int = PERCENTILE_WINDOW) -> None:
        self._values: Deque[float] = deque(maxlen=maxlen)

    def add(self, value: float) -> None:
        self._values.append(value)

    def percentile(self, pct: float) -> Optional[float]:
        if not self._values:
            return None
        sorted_values = sorted(self._values)
        return _percentile(sorted_values, pct)


@dataclass
class CycleSummary:
    """Describes a completed compressor cycle."""

    started_at: datetime
    ended_at: datetime
    on_minutes: float
    off_minutes: Optional[float]
    avg_power_w: float
    peak_power_w: float
    energy_kwh: float


@dataclass
class CycleState:
    """Current compressor state snapshot."""

    compressor_running: bool
    cycle_summary: Optional[CycleSummary]
    idle_estimate_w: float
    running_estimate_w: float
    on_threshold_w: float
    off_threshold_w: float
    idle_hours: Optional[float]


class CycleDetector:
    """Detects compressor cycles using hysteresis on resampled power."""

    def __init__(self) -> None:
        self._percentiles = PercentileWindow()
        self._compressor_running = False
        self._on_started: Optional[datetime] = None
        self._last_cycle_end: Optional[datetime] = None
        self._current_sum_w = 0.0
        self._current_samples = 0
        self._current_peak_w = 0.0
        self._current_energy_kwh = 0.0
        self._last_timestamp: Optional[datetime] = None

    def _current_thresholds(self) -> tuple[float, float, float, float]:
        idle = self._percentiles.percentile(0.05) or POWER_RUNNING_THRESHOLD_W / 2
        running = self._percentiles.percentile(0.95) or max(idle + 20.0, 50.0)
        span = max(running - idle, 20.0)
        on_threshold = idle + 0.3 * span
        off_threshold = idle + 0.1 * span
        return idle, running, on_threshold, off_threshold

    def update(self, timestamp: datetime, power_w: Optional[float]) -> CycleState:
        cycle_summary: Optional[CycleSummary] = None
        if power_w is not None:
            self._percentiles.add(power_w)
            self._integrate_energy(timestamp, power_w)

        idle_estimate, running_estimate, on_threshold, off_threshold = self._current_thresholds()

        if power_w is not None:
            if not self._compressor_running and power_w >= on_threshold:
                self._start_on_cycle(timestamp, power_w)
            elif self._compressor_running and power_w <= off_threshold:
                cycle_summary = self._finish_cycle(timestamp)

        if self._compressor_running and power_w is not None:
            self._current_sum_w += power_w
            self._current_samples += 1
            self._current_peak_w = max(self._current_peak_w, power_w)

        idle_hours = None
        if not self._compressor_running and self._last_cycle_end is not None:
            idle_hours = round((timestamp - self._last_cycle_end).total_seconds() / 3600.0, 3)

        return CycleState(
            compressor_running=self._compressor_running,
            cycle_summary=cycle_summary,
            idle_estimate_w=idle_estimate,
            running_estimate_w=running_estimate,
            on_threshold_w=on_threshold,
            off_threshold_w=off_threshold,
            idle_hours=idle_hours,
        )

    def _integrate_energy(self, timestamp: datetime, power_w: float) -> None:
        if self._compressor_running and self._last_timestamp is not None:
            elapsed = (timestamp - self._last_timestamp).total_seconds()
            if elapsed > 0:
                self._current_energy_kwh += (power_w / 1000.0) * (elapsed / 3600.0)
        self._last_timestamp = timestamp

    def _start_on_cycle(self, timestamp: datetime, power_w: float) -> None:
        self._compressor_running = True
        self._on_started = timestamp
        self._current_sum_w = power_w if power_w is not None else POWER_RUNNING_THRESHOLD_W
        self._current_samples = 1
        self._current_peak_w = power_w or POWER_RUNNING_THRESHOLD_W
        self._current_energy_kwh = 0.0

    def _finish_cycle(self, timestamp: datetime) -> Optional[CycleSummary]:
        if not self._compressor_running or self._on_started is None:
            return None
        self._compressor_running = False
        on_minutes = max((timestamp - self._on_started).total_seconds() / 60.0, 0.0)
        off_minutes = None
        if self._last_cycle_end is not None:
            off_minutes = max((self._on_started - self._last_cycle_end).total_seconds() / 60.0, 0.0)
        avg_power = (
            self._current_sum_w / self._current_samples if self._current_samples else POWER_RUNNING_THRESHOLD_W
        )
        peak_power = self._current_peak_w or avg_power
        summary = CycleSummary(
            started_at=self._on_started,
            ended_at=timestamp,
            on_minutes=round(on_minutes, 2),
            off_minutes=round(off_minutes, 2) if off_minutes is not None else None,
            avg_power_w=round(avg_power, 2),
            peak_power_w=round(peak_power, 2),
            energy_kwh=round(self._current_energy_kwh, 4),
        )
        self._last_cycle_end = timestamp
        self._on_started = None
        self._current_sum_w = 0.0
        self._current_samples = 0
        self._current_peak_w = 0.0
        self._current_energy_kwh = 0.0
        return summary
