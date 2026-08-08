"""Baseline learning helpers for Appliance Shield."""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Deque, Dict, List, Optional

from .const import (
    DAILY_HISTORY_DAYS,
    DEFAULT_BURN_IN_DAYS,
    DOOR_OPEN_RECENT_CYCLES,
    DUTY_DRIFT_PERSIST_DAYS,
    DUTY_DRIFT_THRESHOLD,
    EWMA_ALPHA,
)


def _ewma(old: Optional[float], new: float, alpha: float) -> float:
    if old is None:
        return new
    return alpha * new + (1 - alpha) * old


@dataclass
class BaselineSnapshot:
    """Serializable view of the baseline model."""

    cycle_count: int
    daily_energy_ewma: Optional[float]
    ton_ewma: Optional[float]
    toff_ewma: Optional[float]
    duty_cycle_ewma: Optional[float]
    cycle_energy_ewma: Optional[float]
    duty_ratio_ewma: Optional[float]
    duty_drift_streak: int
    burn_in_days: int
    last_reset_utc: str
    daily_history: List[Dict[str, float]]


class BaselineModel:
    """Learns appliance-specific baselines online."""

    def __init__(self, burn_in_days: int = DEFAULT_BURN_IN_DAYS) -> None:
        self.burn_in_days = burn_in_days
        self.cycle_count = 0
        self.daily_energy_ewma: Optional[float] = None
        self.ton_ewma: Optional[float] = None
        self.toff_ewma: Optional[float] = None
        self.duty_cycle_ewma: Optional[float] = None
        self.cycle_energy_ewma: Optional[float] = None
        # Trailing 24h duty-cycle (runtime ratio) baseline used to catch
        # chronic seal/gasket degradation independent of individual cycles.
        self.duty_ratio_ewma: Optional[float] = None
        self.duty_drift_streak: int = 0
        self.recent_toff: Deque[float] = deque(maxlen=50)
        self.recent_ton: Deque[float] = deque(maxlen=50)
        self.recent_duty: Deque[float] = deque(maxlen=200)
        self.daily_history: Deque[Dict[str, float]] = deque(maxlen=DAILY_HISTORY_DAYS)
        self.last_reset_utc = datetime.utcnow().isoformat()

    def reset(self) -> None:
        self.__init__(self.burn_in_days)

    def update_cycle(self, on_minutes: float, off_minutes: Optional[float], energy_kwh: float) -> None:
        if on_minutes <= 0:
            return
        self.cycle_count += 1
        if off_minutes is not None and off_minutes >= 0:
            total = on_minutes + off_minutes
            if total > 0:
                duty_cycle = on_minutes / total
                self.duty_cycle_ewma = _ewma(self.duty_cycle_ewma, duty_cycle, EWMA_ALPHA)
                self.recent_duty.append(duty_cycle)
            self.toff_ewma = _ewma(self.toff_ewma, off_minutes, EWMA_ALPHA)
            self.recent_toff.append(off_minutes)
        self.ton_ewma = _ewma(self.ton_ewma, on_minutes, EWMA_ALPHA)
        self.recent_ton.append(on_minutes)
        self.cycle_energy_ewma = _ewma(self.cycle_energy_ewma, energy_kwh, EWMA_ALPHA)

    def recent_toff_median(self, sample_count: int = DOOR_OPEN_RECENT_CYCLES) -> Optional[float]:
        """Median off-time over the most recent cycles (robust to single-cycle noise)."""
        if not self.recent_toff:
            return None
        window = list(self.recent_toff)[-sample_count:]
        if len(window) < min(sample_count, 2):
            return None
        return statistics.median(window)

    def update_daily_runtime_ratio(self, runtime_ratio: Optional[float]) -> None:
        """Track trailing duty-cycle drift with day-level persistence.

        Uses a predict-then-update pattern: the deviation is measured against
        the baseline *before* today's sample is absorbed into it, so a
        genuine regime shift isn't diluted by its own contribution.
        """
        if runtime_ratio is None:
            return
        if self.duty_ratio_ewma is not None and self.duty_ratio_ewma > 0:
            deviation = (runtime_ratio - self.duty_ratio_ewma) / self.duty_ratio_ewma
            if deviation >= DUTY_DRIFT_THRESHOLD:
                self.duty_drift_streak += 1
            else:
                self.duty_drift_streak = 0
        self.duty_ratio_ewma = _ewma(self.duty_ratio_ewma, runtime_ratio, EWMA_ALPHA)

    @property
    def seal_degradation_suspected(self) -> bool:
        return self.duty_drift_streak >= DUTY_DRIFT_PERSIST_DAYS

    def update_daily_energy(self, day_key: str, energy_kwh: float) -> None:
        self.daily_energy_ewma = _ewma(self.daily_energy_ewma, energy_kwh, EWMA_ALPHA)
        self.daily_history.append({"day": day_key, "energy_kwh": round(energy_kwh, 3)})

    def confidence(self) -> float:
        window_days = min(len(self.daily_history), self.burn_in_days)
        if self.burn_in_days <= 0:
            return 1.0
        coverage = min(1.0, window_days / max(1.0, float(self.burn_in_days)))
        cycles = min(1.0, self.cycle_count / max(1.0, float(self.burn_in_days) * 12))
        return round((coverage + cycles) / 2, 3)

    def as_snapshot(self) -> BaselineSnapshot:
        return BaselineSnapshot(
            cycle_count=self.cycle_count,
            daily_energy_ewma=self.daily_energy_ewma,
            ton_ewma=self.ton_ewma,
            toff_ewma=self.toff_ewma,
            duty_cycle_ewma=self.duty_cycle_ewma,
            cycle_energy_ewma=self.cycle_energy_ewma,
            duty_ratio_ewma=self.duty_ratio_ewma,
            duty_drift_streak=self.duty_drift_streak,
            burn_in_days=self.burn_in_days,
            last_reset_utc=self.last_reset_utc,
            daily_history=list(self.daily_history),
        )

    def as_dict(self) -> Dict[str, object]:
        return asdict(self.as_snapshot())

    def load(self, data: Optional[Dict[str, object]]) -> None:
        if not data:
            return
        self.cycle_count = int(data.get("cycle_count", 0))
        self.daily_energy_ewma = data.get("daily_energy_ewma")
        self.ton_ewma = data.get("ton_ewma")
        self.toff_ewma = data.get("toff_ewma")
        self.duty_cycle_ewma = data.get("duty_cycle_ewma")
        self.cycle_energy_ewma = data.get("cycle_energy_ewma")
        self.duty_ratio_ewma = data.get("duty_ratio_ewma")
        self.duty_drift_streak = int(data.get("duty_drift_streak", 0))
        self.burn_in_days = int(data.get("burn_in_days", self.burn_in_days))
        self.last_reset_utc = data.get("last_reset_utc") or self.last_reset_utc
        history = data.get("daily_history", [])
        self.daily_history.extend(history[-DAILY_HISTORY_DAYS:])

