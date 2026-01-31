# Appliance Shield

A Home Assistant (Hass.io) custom integration that watches high-consumption appliances (starting with refrigerators and freezers) by ingesting power/energy telemetry from existing smart plugs or smart breakers. Appliance Shield produces:

- **Health insights**: Detects abnormal runtime, compressor stalls, or idle patterns before food spoilage happens.
- **Energy class scoring**: Estimates the EU-style efficiency class (A–D focus, extended internally) using appliance metadata and recent consumption.
- **Actionable diagnostics**: Surfaces advisories and baseline expectations while staying light on CPU/RAM by leaning on Home Assistant's core helpers.

## Features

- Works with any existing `sensor` entities that expose instantaneous power (W) and accumulated energy (kWh).
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
| Energy sensor | Entity supplying cumulative kWh. |

You can re-open the flow later to adjust metadata or swap telemetry sources.

## Exposed Entities

| Entity | Description |
| --- | --- |
| `sensor.<name>_appliance_health` | Enum sensor with states: `initializing`, `healthy`, `attention`, `critical`. Attributes include runtime ratio, detected issues, sample window, and timestamps. |
| `sensor.<name>_energy_score` | Enum (A–D) with attributes for calculated EEI, extended EU band (A–G), expected vs observed annual kWh, and user-supplied metadata. |
| `sensor.<name>_energy_index` | Numeric EEI (unitless) for automations or dashboards. |

## Health & Scoring Logic (v0.2)

- **Daily energy with EWMA baseline**: 24 h deltas still drive consumption, but an exponentially weighted moving average (α = 0.1) now tracks the appliance-specific baseline so residuals highlight sudden jumps or drops. Z-score thresholds (3σ attention, 5σ critical) gate alerts.
- **Compressor signature modeling**: Each update feeds a cycle tracker that records on/off durations, average/peak running power, and standby draw via bounded online statistics. After a short learning period (~12 cycles) the tracker classifies `normal`, `short_cycle`, `long_cycle`, `stalled_cycle`, or `idle` behavior.
- **State machine faulting**: Idle time-outs (>4× period or ≥2 h), low peak power (<40 W), and persistent rapid cycling escalate directly to the `critical` or `attention` buckets. Standby draw above 15 W also raises efficiency concerns.
- **Runtime ratio + instantaneous checks**: Legacy heuristics (runtime band, >350 W spikes, missing telemetry) remain as supporting evidence, ensuring backwards-compatible coverage while the richer model calibrates.
- **Energy score**: Still derived from the EU EEI formula (`EEI = observed_annual / reference_annual`) with A–D surface states and full A–G band plus raw EEI exposed through attributes.

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
