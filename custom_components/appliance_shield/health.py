"""Health scoring utilities for Appliance Shield."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .baseline import BaselineModel
from .signal_processing import CycleSummary


@dataclass
class HealthResult:
    state: str
    score: float
    issues: List[str]
    door_open: bool
    compressor_running: bool


class HealthEvaluator:
    """Combine heuristics into a health score and state."""

    def evaluate(
        self,
        issues: List[str],
        baseline: BaselineModel,
        cycle_summary: Optional[CycleSummary],
        runtime_ratio: Optional[float],
        residual_z: Optional[float],
        compressor_running: bool,
    ) -> HealthResult:
        working_issues = list(issues)
        score = 100.0
        door_open = False

        if runtime_ratio is None:
            working_issues.append("runtime_unknown")
        else:
            if runtime_ratio < 0.15 or runtime_ratio > 0.8:
                working_issues.append("runtime_out_of_band")
                score -= 15

        if residual_z is not None:
            score -= min(40, abs(residual_z) * 5)
            if abs(residual_z) >= 5:
                working_issues.append("energy_residual_extreme")
            elif abs(residual_z) >= 3:
                working_issues.append("energy_residual_anomaly")

        if baseline.ton_ewma and cycle_summary:
            delta = (cycle_summary.on_minutes - baseline.ton_ewma) / max(baseline.ton_ewma, 0.1)
            if delta >= 0.35:
                working_issues.append("cycle_on_long")
                score -= 12
            elif delta <= -0.35:
                working_issues.append("cycle_on_short")
                score -= 8

        if baseline.toff_ewma and cycle_summary and cycle_summary.off_minutes is not None:
            off_delta = (cycle_summary.off_minutes - baseline.toff_ewma) / max(baseline.toff_ewma, 0.1)
            if off_delta <= -0.5:
                working_issues.append("cycle_off_short")
                score -= 12
                if runtime_ratio and runtime_ratio > 0.8:
                    door_open = True
                    working_issues.append("door_open_inferred")
            elif off_delta >= 1.0:
                working_issues.append("cycle_off_long")
                score -= 8

        state = "healthy"
        if any(tag in working_issues for tag in ("compressor_power_spike", "energy_residual_extreme")):
            state = "critical"
        elif any(
            tag in working_issues
            for tag in (
                "energy_residual_anomaly",
                "cycle_on_long",
                "cycle_on_short",
                "cycle_off_short",
                "cycle_off_long",
                "runtime_out_of_band",
            )
        ):
            state = "attention"

        score = max(0.0, min(100.0, round(score, 1)))
        return HealthResult(
            state=state,
            score=score,
            issues=working_issues,
            door_open=door_open,
            compressor_running=compressor_running,
        )
