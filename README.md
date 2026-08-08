# Appliance Shield

A Home Assistant (Hass.io) custom integration that watches high-consumption appliances (starting with refrigerators and freezers) by ingesting power/energy telemetry from existing smart plugs or smart breakers. Appliance Shield produces:

- **Health insights**: Detects abnormal runtime, compressor stalls, or idle patterns before food spoilage happens.
- **Energy class scoring**: Estimates the EU-style efficiency class (A–D focus, extended internally) using appliance metadata and recent consumption.
- **Actionable diagnostics**: Surfaces advisories and baseline expectations while staying light on CPU/RAM by leaning on Home Assistant's core helpers.

## Features

- Works with any existing `sensor` entities that expose instantaneous power (W) and optionally accumulated energy (kWh); if you only provide Watts, Appliance Shield integrates the samples to keep daily EEI math compliant.
- Config flow that captures appliance type, volume, efficiency targets, and telemetry entity IDs.
- Coordinator-driven sensors with bounded deques (≤24h history) to keep RAM usage predictable (<50 kB per appliance).
- Fully async implementation, no polling threads, and minimal database hits (no Recorder queries by default).
- Diagnostics data export to help fine-tune heuristics or share anonymized info when debugging.

## Installation

1. Copy `custom_components/appliance_shield` into your Home Assistant `config` directory.
2. Restart Home Assistant.
3. Use **Settings → Devices & Services → + Add Integration → Appliance Shield**.

## Configuration Flow Inputs

| Field | Purpose |
| --- | --- |
| Friendly name | Name for the appliance device and sensors. |
| Appliance type | `Fridge`, `Freezer`, or `Fridge-Freezer`. |
| Volume (L) | Internal net volume to scale expected energy use. |
| Freezer volume (L) | (Optional) Needed for combos to weight the freezer share. |
| Climate class | Impacts reference baselines (SN/N/ST/T). |
| Target annual energy (kWh) | Optional manufacturer label if you have it. |
| Power sensor | Entity supplying instantaneous watts. |
| Energy sensor | (Optional) Entity supplying cumulative kWh to increase accuracy; power-only setups are supported. |

You can re-open the flow later to adjust metadata or swap telemetry sources.

## Exposed Entities

| Entity | Description |
| --- | --- |
| `sensor.<name>_appliance_health` | Enum sensor with states: `initializing`, `healthy`, `attention`, `critical`. Attributes include runtime ratio, detected issues, sample window, and timestamps. |
| `sensor.<name>_energy_score` | Enum (A–D) with attributes for calculated EEI, extended EU band (A–G), expected vs observed annual kWh, and user-supplied metadata. |
| `sensor.<name>_energy_index` | Numeric EEI (unitless) for automations or dashboards. |
| `binary_sensor.<name>_compressor_running` | Exposes the live compressor state and recent runtime statistics. |
| `binary_sensor.<name>_door_open` | Indicates when the heuristics infer a stuck door / seal problem, plus supporting evidence. |

## Health & Scoring Logic (v0.4)

- **Ambient-normalized daily energy**: Every 24 h Appliance Shield computes the measured kWh/day (integrating power when no cumulative sensor exists) and applies an online linear regression so EEI maps to the official reference temperature. The regression requires at least 10 days of data before trusting a non-flat slope, and the resulting correction factor is clamped to a 0.6–1.6 band so a handful of noisy days can never wildly misclassify the energy score.
- **EU EEI compliance**: Reference consumption follows the delegated regulation lookup (type, volumes, climate class) and applies user-provided manufacturer targets when present. Ambient-corrected energy feeds the primary (A–D) sensor state, while the extended ladder (A–G) and raw EEI are exposed as attributes.
- **Cycle-informed health checks**: The hysteresis cycle detector contributes Ton/Toff, peak/average watts, idle hours, and runtime ratio into the health score. All anomaly comparisons use a predict-then-update pattern — a cycle or day is scored against the baseline *before* that same sample is folded into the EWMA, so a real regime shift is never diluted by its own contribution.
- **Robust door/seal detection**: An absolute-threshold rule (median off-time under 3 minutes with >80% recent runtime) flags a stuck-open door from cycle one, without waiting on a learned baseline. A separate multi-day duty-cycle drift tracker (25% above baseline for 3+ consecutive days) flags chronic seal/gasket degradation independent of any single cycle.
- **Debounced fault escalation**: Power spikes and sensor-unavailable conditions must persist across consecutive coordinator refreshes before escalating to `critical`/`attention`, preventing single noisy samples (e.g. a defrost heater) from causing false alarms, while still surfacing them as lower-severity issues immediately.
- **Persistent baselines & resets**: Rolling histories, EWMA statistics, and residual sigma are persisted via `storage` so restarts don’t reset confidence. A built-in `appliance_shield.reset_baseline` service clears models if the user services the appliance.

## Performance & Compliance Notes

- Uses `DataUpdateCoordinator` with a default 5-minute interval (configurable via YAML option, see `SCAN_INTERVAL`).
- Relies on Home Assistant's state machine instead of direct device polling to stay async-safe.
- Keeps only lightweight Python data structures (three fixed-size deques) per appliance.
- Follows Home Assistant manifest requirements (versioning, config_flow, diagnostics, quality scale TBD).

## Next Steps

- Extend heuristics for other appliance categories (washers, dryers) while reusing the scoring helpers.
- Add optional Recorder-powered statistics for higher precision when the database is enabled.
- Surface repair suggestions through the Repairs UI (future HA release requirement).

## License

MIT (see `LICENSE`).
