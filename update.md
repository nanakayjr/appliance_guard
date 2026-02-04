# Appliance Shield — HASS.IO Integration (Agent guide)

**Purpose:** This document instructs an AI developer agent (ChatGPT 5.1 Codex) to implement a Home Assistant (Hass.io) custom integration named **Appliance Shield**. The integration monitors a refrigerator/freezer using smart-plug power data (instantaneous power in W and optional cumulative energy in kWh) and optional ambient temperature (local sensor or weather API). It provides:

1. Continuous compressor-cycle detection and appliance health evaluation (fault detection: door open, bad seal, refrigerant/efficiency loss, thermostat/control anomalies).
2. Continuous estimation and update of the appliance's EU energy class using observed daily energy consumption, corrected for ambient conditions and appliance volume/type.

This document is organized as: overview, inputs & entities, architecture, algorithms (cycle detection, self-learning baseline, ambient model), EEI & energy-class estimation, Home Assistant integration design, data model & storage, UI/automation hooks, testing & validation, privacy & operational notes.

---

## 1. Overview & goals

- Build a robust, explainable pipeline that runs inside Home Assistant as a custom integration (async, persistent storage, low CPU/memory footprint).
- Use only smart-plug power data (W) and optionally cumulative energy (kWh). When available, use ambient temperature from either a local sensor or an external weather API.
- Self-learn a baseline performance for the appliance after installation and continuously adapt to seasonal/usage changes.
- Map measured energy consumption to the EU energy label (A–G) using the Energy Efficiency Index (EEI) calculation and rules derived from the EU delegated regulation for refrigerating appliances.
- Expose sensors, binary sensors, and services for automations and UI.

---

## 2. Inputs, configuration & entities

**Mandatory inputs**
- `sensor.smartplug_power` — instantaneous active power in Watts (required)

**Optional inputs**
- `sensor.smartplug_energy` — cumulative energy in kWh (recommended if available)
- `sensor.ambient_temp` — ambient temperature in °C (local sensor)
- `weather.home` or weather API key — fallback ambient temperature when local sensor not available
- `input_number.appliance_volume_l` — appliance usable volume in liters (provided by user or autodetected if model metadata exists)
- `input_select.appliance_type` — fridge / freezer / combined / wine / commercial-direct-sales
- `input_datetime.installation_date` — for baseline windows and legal traceability

**Exposed entities (examples)**
- `sensor.appliance_shield_power` (W) — filtered power input
- `binary_sensor.appliance_shield_compressor_on` — compressor state (on/off)
- `sensor.appliance_shield_cycle_ton` — last compressor ON duration (s)
- `sensor.appliance_shield_cycle_toff` — last OFF duration (s)
- `sensor.appliance_shield_daily_energy` (kWh/day) — measured energy today
- `sensor.appliance_shield_energy_class` — current estimated energy class (A..G)
- `sensor.appliance_shield_eei` — calculated Energy Efficiency Index
- `sensor.appliance_shield_health_score` — combined 0–100 score
- `binary_sensor.appliance_shield_door_open` — inferred door-open event
- `sensor.appliance_shield_confidence` — confidence in current class estimate (0..1)

---

## 3. Architecture & component breakdown

- **Input adaptor:** subscribes to the power sensor and optional energy/temperature sensors. Ensures resampling and denoising.
- **Data store:** persistent SQLite/JSON store to hold rolling windows (e.g., last 90 days) of cycle features, baseline models, user-provided metadata.
- **Signal processing module:** resample, median filter, hysteresis state machine for compressor detection.
- **Baseline learner:** self-adapting model that estimates nominal `Ton/Toff`, `P_on`, `P_idle`, daily baseline energy and seasonal trend.
- **Ambient model:** regression model (online) that maps ambient temperature → expected duty cycle / daily energy.
- **EEI calculator:** computes Energy Efficiency Index from measured daily energy normalized by reference consumption (volume & appliance type). Produces energy class mapping.
- **Health evaluator:** combines cycle irregularity, drift, OFF starvation, energy-per-cycle drift into a health score and fault flags.
- **API & HA entity layer:** exposes sensors, binary sensors, attributes, services and handles configuration.

All heavy computations are incremental/online (EWMA, recursive least squares or simple OLS with rolling window) to remain lightweight.

---

## 4. Preprocessing & signal pipeline

1. **Resampling:** reindex power readings to a regular cadence (default 60 s). Use the latest available value to fill-forward short gaps (max_gap default 120 s). Store timestamped samples.

2. **Spike removal / median filter:** apply median filter with window size 3–5 to remove short spikes.

3. **Low-pass smoothing for baseline estimation only:** when estimating long-term trends, use an exponential moving average (alpha configurable, default 0.02/day converted to sampling frequency).

4. **Adaptive percentiles:** compute rolling percentiles for `P_idle` (5th) and `P_on` (95th) using a long window (7–14 days) to adapt thresholds to appliance and environment.

---

## 5. Compressor cycle detection (state machine)

**Rationale:** Fridges are effectively a two-state device (compressor ON/OFF). Detect state robustly using hysteresis.

**Algorithm (pseudocode):**

```
# Inputs: P_t (filtered power), rolling P_idle (5th pct), P_on (95th pct)
P_range = max(P_on - P_idle, MIN_RANGE)
on_threshold  = P_idle + 0.30 * P_range
off_threshold = P_idle + 0.10 * P_range

state = OFF
for each sample:
    if state == OFF and P_t >= on_threshold:
        state = ON; record ton start
    elif state == ON and P_t <= off_threshold:
        state = OFF; record ton end; compute T_on
    # Debounce: require stable condition for N samples (configurable)
```

**Outputs per cycle:** `T_on`, `T_off`, `E_cycle` (using P samples), `P_mean_on`, timestamp.

**Notes:** For variable-speed compressors or micro-inverter-based appliances, this algorithm will degrade — handle that explicitly by detecting low-amplitude noisy transitions (high CV) and marking appliance as "non-fixed-speed".

---

## 6. Self-learning baseline

**Goal:** Learn canonical cycle parameters and daily energy baseline automatically after installation, then adapt continuously.

**Phases:**
- **Initialization (burn-in):** first 7 days (configurable) collect cycle statistics and daily energy. For first 24–72 hours, avoid making final energy-class decisions (lower confidence).
- **Online adaptation:** maintain rolling windows (7d/30d/90d) of cycle statistics and an EWMA baseline.

**Estimated baseline variables:**
- `baseline_Ton_mean`, `baseline_Toff_mean`
- `baseline_P_on_mean`, `baseline_idle`
- `baseline_daily_energy` (kWh/day), and its EWMA-smoothed seasonal trend

**Adaptation algorithm:**
- Update per-cycle stats into sliding window buffers (e.g., last 500 cycles or 90 days).
- Update EWMAs: `EMA_new = alpha * sample + (1-alpha) * EMA_old`, where `alpha` chosen per timescale.
- Detect regime shift: if recent mean moves > X% out of baseline (configurable, suggested 15%), tag as transient vs persistent using a persistence counter (e.g. needs 3 consecutive days to confirm).

**Baseline rollback:** If the user reports a manual reset (service exposed), enable immediate reinitialization and preserve raw historical data.

---

## 7. Ambient-temperature compensation model

**Purpose:** EU test conditions and real-life energy consumption vary with ambient temperature. The model compensates for ambient-driven energy variation so the EEI estimation references standard conditions.

**Input options:**
- Local `sensor.ambient_temp` — best option
- If missing, use Home Assistant built-in weather integration (e.g. `weather.home`) or an external API (OpenWeatherMap, Meteostat). The integration should support both.

**Model choice:** lightweight online linear regression or a small polynomial model mapping `T_ambient` → expected `daily_energy` (or duty cycle). Use a sliding timestamped dataset: `(T_ambient_daily_mean, daily_energy)` for the last N days (N=30–90).

**Formula (example):**
```
E_expected(T) = a0 + a1 * (T - T_ref)
```
Where `T_ref` is the regulatory reference (use 16°C for some lab tests; integration stores T_ref depending on appliance class).

**Training:** simple OLS on last N days or recursive least squares to be online. Regularize (ridge) to avoid overfitting when N small.

**Usage:** compute `E_normalized = measured_daily_energy * f_correction(T_measured)` where `f_correction` scales energy to the reference ambient (e.g., 16°C). The correction factor can be additive or multiplicative depending on regression fit; document the formula in attributes for transparency.

---

## 8. EEI and Energy-Class estimation (map to EU label)

**Principle:** The Energy Efficiency Index (EEI) used in EU refrigerating appliances is the ratio of the appliance's annual energy consumption to a reference consumption that depends on volume/type. For field estimation from daily energy, convert daily→annual (extrapolate) or compute a normalized EEI variant using a reference daily energy.

**Implementation approach (practical & explainable):**

1. **Daily normalization**: compute `E_day_measured` from the smart plug. If only power is available, integrate power samples over the day to get kWh/day. If `sensor.smartplug_energy` (cumulative kWh) exists, compute delta over midnight boundary.

2. **Ambient correction:** compute `E_day_at_ref = E_day_measured * correction_factor(T_daily_mean)` where `correction_factor` maps measured ambient to regulatory reference (see ambient model).

3. **Reference consumption (daily):** compute `E_ref_day` using the regulatory formula for the appliance type and usable volume V (litres). The EEI uses a reference annual consumption; convert it to daily by dividing by 365. The exact formula for `E_ref` depends on appliance family (fridge, freezer, combined). Use the EEI reference constants embedded in the integration (initially derived from the EU delegated regulation parsing) and allow user override via config.

4. **EEI_est = E_day_at_ref / E_ref_day** (dimensionless)

5. **Map EEI_est → class A..G** using the band thresholds. The integration stores the current thresholds (rescaled labels since 1 March 2021). Example mapping logic and thresholds are stored in the integration; show the derived class and confidence. The integration should also provide a `raw_eei` attribute and a `classification_basis` attribute listing V and formula used.

**Practical notes:**
- Because a single day might be atypical, compute class using an `N`-day rolling median (suggested 7–14 days) and expose confidence metrics (variance of daily normalized E).
- Flag low-confidence classification during burn-in or when energy sensor missing.

---

## 9. Health evaluation & fault rules

Produce a `health_score` 0–100 synthesized from several weighted sub-scores:
- **Cycle regularity** (20%) — low CV of `T_on`/`T_off` yields high score.
- **Duty cycle drift** (20%) — deviation from baseline daily duty cycle.
- **Off-time starvation / door-seal** (30%) — repeated short `T_off` events.
- **Energy-per-cycle drift (efficiency)** (20%) — rising `E_cycle` per ton.
- **Control anomalies** (10%) — highly irregular short cycles.

**Fault flags (examples):**
- `door_open` (immediate): median `T_off` < 3 min over last 30 min AND compressor ON > 80% of last 20 minutes.
- `seal_degradation` (chronic): 7-day median duty cycle > 25% above baseline AND persistence > 3 days.
- `refrigerant_loss_or_compressor` (deg): mean `T_on` and `E_cycle` increased by > 25% vs baseline and `T_off` not shrinking.
- `thermostat_control` (anomaly): CV_Ton and CV_Toff both > 0.7.

Each flag should include a confidence value and the leading evidence (attributes with raw numbers) for explainability.

---

## 10. Home Assistant integration & manifest

**Integration name:** `appliance_shield`

**manifest.json (high-level):**
- Use standard HA `manifest.json` keys: name, version, documentation, dependencies (none), requirements (e.g. `numpy` optional but prefer pure-Python), code owners.
- Implement `config_flow` for guided configuration (select sensor entities, input volume, type, weather fallback).

**Platform files:**
- `__init__.py` — set up integration, load stored baseline, register services.
- `const.py` — keys and defaults.
- `coordinator.py` — central async data coordinator listening to HA sensors.
- `signal_processing.py` — cycle detection code.
- `baseline.py` — EWMA and rolling-window storage.
- `ambient_model.py` — online regression.
- `eei.py` — reference formulas and class thresholds (configurable via YAML or options flow).
- `health.py` — scoring and fault rules.
- `services.py` — expose service `appliance_shield.reset_baseline`, `appliance_shield.report_snapshot`.

**Entities registration:** create sensor and binary sensor entities as in section 2. Use `async_add_entities` and ensure `async_update` is efficient.

**Options & UI:**
- Options flow: adjust `burn_in_days`, `rolling_window_days`, `alpha` values, custom EEI thresholds override, volume override.
- Lovelace card: show daily energy chart, EEI trend, health score, and last 24h cycle histogram.

---

## 11. Data model & storage

Persist the following in integration storage (use `hass.helpers.storage`):
- `config`: mapping of entity ids and metadata
- `rolling_cycles` : compact list of cycle summaries (timestamp, Ton, Toff, E_cycle, P_mean) — rotate older than 90 days
- `daily_energy` : last 365 days of daily totals
- `baseline` : EMA values, last trained ambient model params

Keep storage compact: summarise raw samples into cycle features; do not store per-second samples long-term.

---

## 12. Testing & validation

**Unit tests:** mock power traces for normal, door open, bad seal, refrigerant leak scenarios. Validate cycle detection, baseline convergence, ambient compensation and class mapping.

**Integration testing:** install on a test HA instance, connect to a simulated smart-plug feed (MQTT) replaying real traces.

**Acceptance criteria:**
- Compressor ON/OFF detection F1 score > 0.98 on test traces.
- Door open detection true positive within 5 min for sustained door open.
- Energy class stable for appliances with steady usage over a 14-day median.

---

## 13. Privacy, safety & operational notes

- Keep all processing local to Home Assistant unless the user explicitly enables cloud features (e.g., to share anonymized stats). Do not upload raw power samples.
- Store only aggregated cycle features and daily totals for long-term data. Allow user to opt-out of storing history.
- Expose a clear service to delete all collected data and reset baseline.

---

## 14. Developer checklist / Implementation steps (priority order)

1. Create integration skeleton and manifest.
2. Implement signal adaptor and resampling (60 s default) and hysteresis-based cycle detection tests.
3. Implement synchronous data store and cycle summary rotation.
4. Implement baseline learner with burn-in logic and EWMA.
5. Implement ambient model (OLS) and simple correction function.
6. Implement EEI calculator with configurable reference formulas and default thresholds.
7. Implement health scorer and fault rules with attributes for transparency.
8. Expose sensors and services; implement options flow.
9. Build UI Lovelace card with charts (daily energy, Ton/Toff histograms).
10. Add tests and run integration-validation traces.

---

## 15. Pseudocode snippets (key routines)

**Cycle detection (already in section 5) — see signal_processing.py**

**Baseline EWMA update**
```
# called per day or per cycle depending on variable
EMA_value = alpha * sample + (1-alpha) * EMA_value
if abs(EMA_value - sample) > threshold*EMA_value for persist_days:
    mark_regime_shift()
```

**Ambient regression (online OLS stub)**
```
# keep XTX and XTy and update each day
XTX += [ [1, x], [x, x^2?] ]
XTy += [ y, x*y ]
theta = inv(XTX + lambda*I) @ XTy
```

**EEI calculation (conceptual)**
```
E_day_at_ref = E_day_measured * correction(T)
E_ref_day = eei_ref_for_type_and_volume(appliance_type, volume) / 365
EEI_est = E_day_at_ref / E_ref_day
class = map_eei_to_class(EEI_est, thresholds)
```

---

## 16. Monitoring, logging & user-facing messages

- Log important events at INFO (baseline reset, classification change, major health faults). Use DEBUG for per-cycle logs behind a config flag.
- In UI attributes always show the numeric evidence that led to a flagged fault (e.g., `median_toff_30min: 120s, ton_pct: 89%`).

---

## 17. Future enhancements (not required for first release)

- Integrate with product database (EPREL) to auto-populate volume and declared consumption for precise EEI mapping.
- Add a lightweight HMM for more nuanced multi-state appliances.
- Use anomaly detection (isolation forest) on cycle features to find rare faults.
- Provide guided troubleshooting steps in the UI for each fault flag.

---

## 18. Deliverables for the AI agent

Produce the following artifacts in sequence:
1. Home Assistant custom integration repo scaffold with `manifest.json` and module files.
2. Fully implemented cycle detection, baseline learner, ambient model, EEI calculation and health scoring modules.
3. Unit tests and integration test traces.
4. Lovelace card configuration, configuration flow, and documentation.

---

_End of document._

