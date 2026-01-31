"""Constants for the Appliance Shield integration."""

from __future__ import annotations

from datetime import timedelta
from homeassistant.const import Platform

DOMAIN = "appliance_shield"
PLATFORMS = [Platform.SENSOR]
DATA_COORDINATOR = "coordinator"

CONF_APPLIANCE_TYPE = "appliance_type"
CONF_VOLUME_LITERS = "volume_liters"
CONF_FREEZER_VOLUME_LITERS = "freezer_volume_liters"
CONF_CLIMATE_CLASS = "climate_class"
CONF_TARGET_ANNUAL_KWH = "target_annual_kwh"
CONF_POWER_SENSOR = "power_entity_id"
CONF_ENERGY_SENSOR = "energy_entity_id"

APPLIANCE_TYPES = ["fridge", "freezer", "fridge_freezer"]
CLIMATE_CLASSES = ["SN", "N", "ST", "T"]

DEFAULT_SCAN_INTERVAL = timedelta(minutes=5)
MAX_HISTORY_MINUTES = 24 * 60
POWER_RUNNING_THRESHOLD_W = 8.0
POWER_SPIKE_THRESHOLD_W = 350.0
STALL_PEAK_THRESHOLD_W = 40.0
STANDBY_HIGH_W = 15.0
SIGNATURE_MIN_CYCLES = 12
CYCLE_SHORT_SIGMA = 2.0
CYCLE_LONG_SIGMA = 2.0
IDLE_TIMEOUT_MULTIPLIER = 4.0
IDLE_TIMEOUT_MINUTES_MIN = 120.0
EWMA_ALPHA = 0.1
RESIDUAL_SIGMA_ATTENTION = 3.0
RESIDUAL_SIGMA_CRITICAL = 5.0
MIN_RESIDUAL_SAMPLES = 10

HEALTH_STATE_INITIALIZING = "initializing"
HEALTH_STATE_HEALTHY = "healthy"
HEALTH_STATE_ATTENTION = "attention"
HEALTH_STATE_CRITICAL = "critical"
HEALTH_STATES = [
    HEALTH_STATE_INITIALIZING,
    HEALTH_STATE_HEALTHY,
    HEALTH_STATE_ATTENTION,
    HEALTH_STATE_CRITICAL,
]

ENERGY_SCORE_PRIMARY = (
    ("A", 0.64),
    ("B", 0.80),
    ("C", 0.95),
    ("D", 1.10),
)

ENERGY_SCORE_EXTENDED = ENERGY_SCORE_PRIMARY + (
    ("E", 1.25),
    ("F", 1.50),
    ("G", float("inf")),
)

DEFAULT_REFERENCE_TABLE = {
    "fridge": {"base": 155.0, "per_liter": 0.32},
    "freezer": {"base": 190.0, "per_liter": 0.45},
    "fridge_freezer": {"base": 175.0, "per_liter": 0.38},
}

CLIMATE_CLASS_MULTIPLIERS = {
    "SN": 0.95,
    "N": 1.00,
    "ST": 1.05,
    "T": 1.10,
}

ATTR_DAILY_ENERGY_KWH = "daily_energy_kwh"
ATTR_RUNTIME_RATIO = "runtime_ratio"
ATTR_SAMPLE_WINDOW_HOURS = "sample_window_hours"
ATTR_LAST_SAMPLE = "last_sample_utc"
ATTR_EXPECTED_DAILY_KWH = "expected_daily_kwh"
ATTR_OBSERVED_ANNUAL_KWH = "observed_annual_kwh"
ATTR_REFERENCE_ANNUAL_KWH = "reference_annual_kwh"
ATTR_EEI = "energy_efficiency_index"
ATTR_EXTENDED_SCORE = "extended_score"
ATTR_ISSUES = "issues"
ATTR_METADATA = "metadata"
ATTR_CYCLE_STATE = "cycle_state"
ATTR_IDLE_HOURS = "idle_hours"
ATTR_LAST_CYCLE_MINUTES = "last_cycle_minutes"
ATTR_LAST_CYCLE_PEAK_W = "last_cycle_peak_w"
ATTR_LAST_CYCLE_AVG_W = "last_cycle_avg_w"
ATTR_SIGNATURE_READY = "signature_ready"
ATTR_FEATURE_STATS = "feature_stats"
ATTR_STANDBY_POWER_W = "standby_power_w"
ATTR_EWMA_DAILY_KWH = "ewma_daily_energy_kwh"
ATTR_ENERGY_RESIDUAL_KWH = "energy_residual_kwh"
ATTR_RESIDUAL_SIGMA = "energy_residual_sigma"
