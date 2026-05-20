"""
S3: Baseline Usage (DAILY version)
CIMBA Predictive Maintenance Framework

For each asset and each future day (next 30 days by default), the script looks
for the historical days with the most similar climate and the lowest consumption
(most efficient operation) and averages them to get an expected-consumption
baseline. It also stores the real consumption for days where data exists
(for validation).

Algorithm (per target day D):
  1. Filter candidates: same weekday type (weekday vs weekend) and EXCLUDE
     D itself if it is in the history.
  2. Climate distance: sqrt((Ens_Max-T)**2 + (Ens_Mean-T)**2 + (Ens_Min-T)**2)
     where Ens_X is the mean of the climate models (CMCC/FGOALS/HiRAM).
  3. Take the Y_POOL=10 most similar days in climate.
  4. From those, take the X_DAYS=7 with the lowest kWh (most efficient).
  5. baseline = mean of those 7 -> Total_Predicted_Power.

Reads (Mongo):
  - assets                : asset list
  - operational_data      : daily kWh per asset (real + synthetic)
  - operational_temperature: only used for FCU/AHU to enrich (optional)
  - climate_data          : climate model ensemble per day

Writes (Mongo):
  - baselines             : 1 doc per (asset_id, Period) with fields
                            asset_id, Period (str YYYY-MM-DD),
                            Total_Predicted_Power (kWh), Total_Real_Power (kWh or None)
  - baseline_validation   : per-asset metrics (CVRMSE on the validation set)

CSV outputs (legacy debugging): database/baselines/{asset_id}_baseline.csv
"""

import datetime
import os
import sys

import numpy as np
import pandas as pd

import cimba_mongo as mongo
import cimba_paths as paths

# Algorithm parameters.
X_DAYS = 7        # How many days are averaged to build the baseline.
Y_POOL = 10       # Initial pool size by climate similarity.
FORECAST_DAYS_AHEAD = 30  # How many future days are predicted.


def load_climate(db):
    """Load climate_data and add Ens_Max/Mean/Min columns (model ensemble)."""
    rows = list(db.climate_data.find({}, {"_id": 0}))
    if not rows:
        print("[ERROR] climate_data empty")
        sys.exit(1)
    df = pd.DataFrame(rows)
    # `time` is DD/MM/YYYY.
    df["Date"] = pd.to_datetime(df["time"], dayfirst=True, errors="coerce").dt.date
    df = df.dropna(subset=["Date"])

    max_cols = [c for c in df.columns if "max" in c.lower() and "temperature" in c.lower()]
    mean_cols = [c for c in df.columns if "mean" in c.lower() and "temperature" in c.lower()]
    min_cols = [c for c in df.columns if "min" in c.lower() and "temperature" in c.lower()]
    df["Ens_Max"] = df[max_cols].mean(axis=1)
    df["Ens_Mean"] = df[mean_cols].mean(axis=1)
    df["Ens_Min"] = df[min_cols].mean(axis=1)

    return df[["Date", "Ens_Max", "Ens_Mean", "Ens_Min"]].drop_duplicates("Date").sort_values("Date").reset_index(drop=True)


def gen_baseline(asset_id, hist_daily, climate_daily, target_dates, has_temperature):
    """For each target_date, predict kWh (and indoor temperature if applicable) using climate K-NN."""
    if hist_daily.empty:
        return []

    # Merge the history with the climate data.
    hist = hist_daily.merge(climate_daily, on="Date", how="left").dropna(subset=["Ens_Mean"])
    if hist.empty:
        return []
    hist["weekday"] = hist["Date"].apply(lambda d: d.weekday())

    climate_lookup = climate_daily.set_index("Date")

    # Subset with temperature available, for the temperature K-NN.
    hist_with_temp = hist.dropna(subset=["control_temp"]) if has_temperature else None

    out = []
    for d in target_dates:
        if d not in climate_lookup.index:
            continue
        target_climate = climate_lookup.loc[d]
        is_weekend = d.weekday() >= 5

        # ===== Baseline kWh =====
        cand = hist[(hist["weekday"] >= 5) == is_weekend].copy()
        cand = cand[cand["Date"] != d]
        if cand.empty:
            continue
        cand["dist"] = np.sqrt(
            (cand["Ens_Max"] - target_climate["Ens_Max"]) ** 2
            + (cand["Ens_Mean"] - target_climate["Ens_Mean"]) ** 2
            + (cand["Ens_Min"] - target_climate["Ens_Min"]) ** 2
        )
        top_y = cand.nsmallest(Y_POOL, "dist")
        if top_y.empty:
            continue
        top_x = top_y.nsmallest(X_DAYS, "kWh")
        predicted_kwh = float(top_x["kWh"].mean())

        # ===== Baseline control_temp (only if the asset has temperature data) =====
        predicted_temp = None
        if hist_with_temp is not None and not hist_with_temp.empty:
            cand_t = hist_with_temp[(hist_with_temp["weekday"] >= 5) == is_weekend].copy()
            cand_t = cand_t[cand_t["Date"] != d]
            if not cand_t.empty:
                cand_t["dist"] = np.sqrt(
                    (cand_t["Ens_Max"] - target_climate["Ens_Max"]) ** 2
                    + (cand_t["Ens_Mean"] - target_climate["Ens_Mean"]) ** 2
                    + (cand_t["Ens_Min"] - target_climate["Ens_Min"]) ** 2
                )
                top_t = cand_t.nsmallest(Y_POOL, "dist").nsmallest(X_DAYS, "dist")
                # For temperature we take the Y_POOL closest in climate and average their control_temp.
                # Efficiency is not relevant here, only the typical temperature for that climate.
                if not top_t.empty:
                    predicted_temp = round(float(top_t["control_temp"].mean()), 2)

        out.append({
            "asset_id": asset_id,
            "Period": d.isoformat(),
            "Total_Predicted_Power": round(predicted_kwh, 2),
            "control_temp_predicted": predicted_temp,
        })

    return out


def cvrmse(rows, hist_daily):
    """Coefficient of variation of RMSE (%). Compare prediction vs real historical value
    for the days where data exists."""
    if hist_daily.empty:
        return None
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Period"]).dt.date
    df = df.merge(hist_daily[["Date", "kWh"]].rename(columns={"kWh": "Real"}), on="Date", how="inner")
    if df.empty:
        return None
    err = df["Total_Predicted_Power"] - df["Real"]
    rmse = float(np.sqrt((err ** 2).mean()))
    mean_real = float(df["Real"].mean())
    if mean_real == 0:
        return None
    return round(rmse / mean_real * 100, 2)


def run_s3():
    print("\n" + "=" * 80 + "\nS3: BASELINE USAGE (DAILY MODE)\n" + "=" * 80)
    paths.ensure_directories()

    db = mongo.get_db()

    print("\n[STEP 1] Loading climate ensemble...")
    climate_daily = load_climate(db)
    print(f"  Climate: {len(climate_daily)} days ({climate_daily['Date'].min()} -> {climate_daily['Date'].max()})")

    print("\n[STEP 2] Computing target date range...")
    today = datetime.date.today()
    # Target: the full range with climate available between 2025-01-01 and today+30.
    end_target = today + datetime.timedelta(days=FORECAST_DAYS_AHEAD)
    target_dates = [d for d in climate_daily["Date"] if datetime.date(2025, 1, 1) <= d <= end_target]
    print(f"  Target: {len(target_dates)} days ({target_dates[0]} -> {target_dates[-1]})")

    print("\n[STEP 3] Loading asset list...")
    assets = list(db.assets.find({}, {"_id": 0, "asset_id": 1, "asset_type": 1, "asset_name": 1}))
    print(f"  Assets: {len(assets)}")

    print(f"\n[STEP 4] Clearing previous baselines...")
    db.baselines.delete_many({})

    print(f"\n[STEP 5] Generating baselines per asset...")
    all_validation = []
    total_inserted = 0

    for a in assets:
        aid = a["asset_id"]
        atype = a.get("asset_type")
        if atype not in ("Pump", "FCU", "AHU"):
            print(f"  [SKIP] {aid}: asset_type={atype} not supported yet")
            continue

        hist = mongo.get_operational_asset_data(aid)
        if hist.empty:
            print(f"  [SKIP] {aid}: no operational data")
            continue

        # Detect whether the asset has an indoor temperature history.
        has_temp = atype in ("FCU", "AHU") and hist["control_temp"].notna().any()

        rows = gen_baseline(aid, hist, climate_daily, target_dates, has_temperature=has_temp)
        if not rows:
            print(f"  [SKIP] {aid}: predictions could not be generated")
            continue

        db.baselines.insert_many(rows)
        total_inserted += len(rows)

        cv = cvrmse(rows, hist)
        n_with_temp = sum(1 for r in rows if r.get("control_temp_predicted") is not None)
        all_validation.append({
            "asset_id": aid, "asset_type": atype,
            "n_predictions": len(rows), "n_temp_predictions": n_with_temp,
            "CVRMSE_%": cv,
        })

        # Legacy CSV.
        pd.DataFrame(rows).to_csv(paths.s3_baseline_file(aid), index=False)

        cv_str = f"CVRMSE={cv}%" if cv is not None else "CVRMSE=n/a"
        temp_str = f"temp={n_with_temp}" if has_temp else "no temp"
        print(f"  [OK] {aid:14s} {atype:5s} {len(rows):4d} preds, {cv_str}, {temp_str}")

    if all_validation:
        val_df = pd.DataFrame(all_validation)
        mongo.write_collection("baseline_validation", val_df)
        val_df.to_csv(paths.S3_VALIDATION_SUMMARY, index=False)

    db.baselines.create_index([("asset_id", 1), ("Period", 1)])

    print("\n" + "=" * 80)
    print(f"[OK] Total docs in db.baselines: {total_inserted}")
    print(f"     Validation summary in db.baseline_validation: {len(all_validation)} assets")
    print("=" * 80)


if __name__ == "__main__":
    run_s3()
