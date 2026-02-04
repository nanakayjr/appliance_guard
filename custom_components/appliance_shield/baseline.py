"""Baseline learning helpers for Appliance Shield."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Deque, Dict, List, Optional

from .const import DAILY_HISTORY_DAYS, DEFAULT_BURN_IN_DAYS, EWMA_ALPHA


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
                self.recent_toff.append(off_minutes)
        self.ton_ewma = _ewma(self.ton_ewma, on_minutes, EWMA_ALPHA)
        self.recent_ton.append(on_minutes)
        self.cycle_energy_ewma = _ewma(self.cycle_energy_ewma, energy_kwh, EWMA_ALPHA)

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
        self.burn_in_days = int(data.get("burn_in_days", self.burn_in_days))
        self.last_reset_utc = data.get("last_reset_utc") or self.last_reset_utc
        history = data.get("daily_history", [])
        self.daily_history.extend(history[-DAILY_HISTORY_DAYS:])
