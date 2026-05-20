# CIMBA: code review

Hi, and thanks for taking a look at this.

CIMBA is a predictive maintenance framework for HVAC assets. It runs in five
stages (S1 to S5): classify assets, compute a health index, build energy
baselines, project degradation, and train a Random Forest per asset type.

The site is the **Newcastle Helix Spark** building, 21 assets: 11 fan coil
units (FCU), 3 air handling units (AHU) and 7 pumps. Data covers
January 2025 to March 2026.

## The quick way (recommended)

Open `notebooks/CIMBA_pipeline_tour.ipynb` and run the cells from top to
bottom. The notebook reads files straight from this repository, so no
database is needed. It walks through each of the five stages with short
explanations and shows the inputs and outputs at each step.

You need Python 3.10 or newer and these packages:

```
pip install -r requirements.txt
```

That is enough for the notebook.

## What is in here

- `S1_asset_classification.py` ... `S5_ai_training.py`: the five pipeline stages.
- `cimba_mongo.py`, `cimba_paths.py`: shared helpers for database and paths.
- `migrate_spark.py`, `migrate_temperature.py`, `load_daily_temperatures.py`: loaders that take the raw CSVs and write them into MongoDB.
- `datos/`: daily energy per asset (FCUs, AHUs, pumps).
- `temepratura1/`: 5-minute temperature readings for FCUs.
- `temperatura2/`: 5-minute temperature readings for AHUs.
- `database/operational/`: 5-minute power readings for FCUs (legacy export, kept for reference).
- `database/assets/`, `database/climate/`: static metadata.
- `database/degradation/`, `database/models/`: pre-computed outputs from a real pipeline run, so you can compare against your own.
- `data_snapshots/`: JSON dump of the MongoDB collections, used by the notebook and by the seed script below.
- `notebooks/CIMBA_pipeline_tour.ipynb`: the guided tour.
- `tools/`: small helper scripts (Mongo export, Mongo seed, notebook builder).

Pumps have daily energy only (no 5-minute power, no indoor temperature).
AHUs have daily energy and 5-minute temperature but no 5-minute power.
FCUs have everything.

## The long way (only if you want to run the pipeline end to end)

If you would like to run the scripts against a real database:

1. Install MongoDB Community Edition and start it on `localhost:27017`.
2. Create a virtual environment and install the requirements (same as above).
3. Load the snapshot into your local MongoDB:
   ```
   python tools/seed_from_json.py
   ```
4. Load the raw CSVs into MongoDB:
   ```
   python migrate_spark.py
   python load_daily_temperatures.py
   ```
5. Run the pipeline in this order:
   ```
   python S1_asset_classification.py
   python S2_asset_health_index.py
   python S3_baseline_usage.py
   python S5_ai_training.py
   python S4_degradation_rate.py
   ```

The default connection string is `mongodb://localhost:27017`. If you need a
different one, set the `MONGO_URI` environment variable before running.

## Contact

Edgar Segovia, edgarsegovia92@gmail.com
