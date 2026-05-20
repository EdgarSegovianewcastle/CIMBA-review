# database/operational/ (legacy)

These CSV files are **legacy 5-minute exports** from an earlier version of the
pipeline. They are no longer consumed by any current script.

## Current pipeline

The current migrator is `migrate_spark.py`, which reads **daily** energy data
directly from `datos/` and loads Mongo with documents of `type="hvac_energy"`
(FCUs and AHUs) or `type="pump_energy"` (pumps).

The current consumer of operational data is `cimba_mongo.get_operational_asset_data()`,
which queries Mongo (not these CSVs) and returns daily-aggregated `(Date, kWh, control_temp)`
per asset.

## Why these files are kept

For historical reference. The 5-minute granularity in these files is not available
for AHUs and pumps (only FCUs), so they cannot serve as a complete operational
dataset anyway.

## What to do when adding new operational data

- Drop the new daily CSVs into `datos/` (format `{asset_demand_logic_id}__Daily_energy*.csv`)
- Run `python migrate_spark.py`
- Do **not** add new files here.
