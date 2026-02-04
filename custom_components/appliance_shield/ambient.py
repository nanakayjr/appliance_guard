"""Ambient temperature compensation model."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Optional

from .const import DEFAULT_REFERENCE_TEMP_C


@dataclass
class AmbientSnapshot:
    """Serializable state for the ambient regression model."""

    slope: float
    intercept: float
    reference_temp: float
    count: int
    sum_t: float
    sum_y: float
    sum_tt: float
    sum_ty: float


class AmbientModel:
    """Lightweight online linear regression E = a + b*T."""

    def __init__(self, reference_temp: float = DEFAULT_REFERENCE_TEMP_C) -> None:
        self.reference_temp = reference_temp
        self.count = 0
        self.sum_t = 0.0
        self.sum_y = 0.0
        self.sum_tt = 0.0
        self.sum_ty = 0.0
        self.last_energy: Optional[float] = None

    def update(self, ambient_temp_c: float, daily_energy_kwh: float) -> None:
        self.count += 1
        self.sum_t += ambient_temp_c
        self.sum_y += daily_energy_kwh
        self.sum_tt += ambient_temp_c * ambient_temp_c
        self.sum_ty += ambient_temp_c * daily_energy_kwh
        self.last_energy = daily_energy_kwh

    def _coefficients(self) -> tuple[float, float]:
        if self.count < 3:
            intercept = (self.sum_y / self.count) if self.count else (self.last_energy or 0.0)
            return (0.0, intercept)
        denom = (self.count * self.sum_tt) - (self.sum_t ** 2)
        if abs(denom) < 1e-6:
            intercept = (self.sum_y / self.count) if self.count else 0.0
            return (0.0, intercept)
        slope = ((self.count * self.sum_ty) - (self.sum_t * self.sum_y)) / denom
        intercept = (self.sum_y - slope * self.sum_t) / self.count
        return (slope, intercept)

    def correction_factor(self, ambient_temp_c: Optional[float]) -> float:
        if ambient_temp_c is None:
            return 1.0
        slope, intercept = self._coefficients()
        expected = intercept + slope * ambient_temp_c
        reference = intercept + slope * self.reference_temp
        if expected <= 0 or reference <= 0:
            return 1.0
        return reference / expected

    def as_dict(self) -> Dict[str, float | int]:
        slope, intercept = self._coefficients()
        return asdict(
            AmbientSnapshot(
                slope=round(slope, 6),
                intercept=round(intercept, 6),
                reference_temp=self.reference_temp,
                count=self.count,
                sum_t=round(self.sum_t, 6),
                sum_y=round(self.sum_y, 6),
                sum_tt=round(self.sum_tt, 6),
                sum_ty=round(self.sum_ty, 6),
            )
        )

    def load(self, data: Optional[Dict[str, float | int]]) -> None:
        if not data:
            return
        self.reference_temp = float(data.get("reference_temp", self.reference_temp))
        self.count = int(data.get("count", 0))
        self.sum_t = float(data.get("sum_t", 0.0))
        self.sum_y = float(data.get("sum_y", 0.0))
        self.sum_tt = float(data.get("sum_tt", 0.0))
        self.sum_ty = float(data.get("sum_ty", 0.0))
        self.last_energy = float(data.get("last_energy", self.last_energy or 0.0))
