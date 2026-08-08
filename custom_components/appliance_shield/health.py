"""Health scoring utilities for Appliance Shield."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .baseline import BaselineModel
from .const import DOOR_OPEN_RUNTIME_RATIO, DOOR_OPEN_TOFF_MINUTES_ABS
from .signal_processing import CycleSummary


@dataclass
class HealthResult:
    state: str
    score: float
    issues: List[str]
    door_open: bool
    compressor_running: bool


# Issue tags that always indicate an urgent, user-visible fault.
CRITICAL_TAGS = {"energy_residual_extreme"}

# Issue tags that warrant operator attention but are not yet critical.
ATTENTION_TAGS = {
    "energy_residual_anomaly",
    "cycle_on_long",
    "cycle_on_short",
    "cycle_off_short",
    "cycle_off_long",
    "runtime_out_of_band",
    "energy_far_above_baseline",
    "energy_far_below_baseline",
    "energy_out_of_range",
    "seal_degradation_suspected",
    "power_sensor_unavailable_persistent",
}


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

        # Static energy-deviation flags raised by the coordinator (computed
        # against the pre-update baseline) also cost health points, not just
        # visibility in the issues list.
        if "energy_far_above_baseline" in working_issues:
            score -= 15
        elif "energy_out_of_range" in working_issues:
            score -= 8
        if "energy_far_below_baseline" in working_issues:
            score -= 10

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

        # Absolute-threshold door-open/stuck-seal detection. This does not
        # depend on a learned baseline, so it stays effective during burn-in
        # or whenever the appliance's own EWMA hasn't converged yet - a
        # short-cycling fridge should never go undetected just because it's
        # newly installed.
        recent_toff_median = baseline.recent_toff_median()
        if (
            not door_open
            and recent_toff_median is not None
            and recent_toff_median < DOOR_OPEN_TOFF_MINUTES_ABS
            and runtime_ratio is not None
            and runtime_ratio > DOOR_OPEN_RUNTIME_RATIO
        ):
            door_open = True
            if "door_open_inferred" not in working_issues:
                working_issues.append("door_open_inferred")
            score -= 20

        # Chronic duty-cycle drift persisted for several consecutive days -
        # classic signature of a failing door seal/gasket or refrigerant
        # loss rather than a single noisy cycle.
        if baseline.seal_degradation_suspected:
            working_issues.append("seal_degradation_suspected")
            score -= 20

        if "power_sensor_unavailable_persistent" in working_issues:
            score -= 25

        state = "healthy"
        has_critical = any(tag in working_issues for tag in CRITICAL_TAGS)
        # A power spike only escalates to critical once it has been observed
        # for multiple consecutive refreshes; a single noisy sample (e.g. a
        # defrost heater or icemaker) is surfaced as a transient issue only.
        if "compressor_power_spike" in working_issues and "compressor_power_spike_transient" not in working_issues:
            has_critical = True

        if has_critical:
            state = "critical"
        elif any(tag in working_issues for tag in ATTENTION_TAGS):
            state = "attention"

        score = max(0.0, min(100.0, round(score, 1)))
        return HealthResult(
            state=state,
            score=score,
            issues=working_issues,
            door_open=door_open,
            compressor_running=compressor_running,
        )

