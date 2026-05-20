"""
S4: Degradation Trajectory & RUL prediction (DAILY, LINEAR-OBSERVED-RATE)
CIMBA Predictive Maintenance Framework

Projection logic (revised 2026-04-27):
  observed_rate = (100 - AHI_current) / (Total_Years_in_Operation * 365.25)
  trajectory    = linear projection at observed_rate into the future
  stop          = AHI <= 5  OR  simulated_days >= CIBSE_Life * 365 (safeguard)

Why LINEAR rather than the model's prediction:
  - The target in S5 (Daily_Degradation) was built by spreading
    (100 - AHI) over the ~480 days of Demandlogic history, but in reality
    those degradation points accumulated over the asset's WHOLE operating
    life (5 years = 1825 days). The model learns a rate ~3-4x higher than
    reality, which produces over-pessimistic projections (1 to 3 years
    where CIBSE expects 11 to 17).
  - The Cumulative_Power_Consumed feature also creates positive feedback:
    it grows unbounded in the future and the model predicts accelerating rates.
  - Until we have a real AHI time series (multiple maintenance readings),
    the HONEST projection is linear at the observed rate.

The registry model IS still loaded so we can store model_id on each doc
(architectural traceability) but it is NOT used in the forecast.

Comparison stored in db.degradation_summary:
  observed_daily_rate   <- (100 - AHI) / days_operating
  cibse_daily_rate      <- 95 / CIBSE_Life / 365
  years_to_95_observed  <- projection at the real rate (what we return)
  years_to_95_cibse     <- expected by manufacturer spec (95 / cibse_rate / 365)
  delta_years           <- difference (negative means degrades faster than spec)

Reads (Mongo):  model_registry, health_indexes, assets, climate_data, operational_data
Writes (Mongo): degradation_trajectories, degradation_summary
"""

import datetime
import io
import os
import sys

import joblib
import numpy as np
import pandas as pd

import cimba_mongo as mongo
import cimba_paths as paths

ROLL = 7
TARGET_DEG = 95.0       # Stop when reaching 95% degradation (AHI = 5).
DEFAULT_CIBSE_LIFE = 20  # Fallback when the asset has no value for this field.


def load_models(db):
    """Index registry models by asset_type. Load from disk first, fall back to
    db.model_files (Render's filesystem is ephemeral, so after a reboot the .pkl
    on disk no longer exists but the bytes in Mongo do).

    When there are several models per asset_type (e.g. v1 and v2), pick the
    highest version. This keeps previous versions in the registry for audit
    without S4 actually using them."""
    # Sort by version desc so the first entry per asset_type is the most recent.
    docs = list(db.model_registry.find({}, {"_id": 0}).sort("version", -1))
    models = {}
    for d in docs:
        atype = d.get("asset_type")
        model_id = d.get("model_id")
        if not atype:
            continue
        if atype in models:
            # We already have a more recent model for this type.
            continue

        model = None
        source = None

        # Attempt 1: disk.
        path = d.get("model_file")
        if path and os.path.exists(path):
            try:
                model = joblib.load(path)
                source = f"disk ({os.path.basename(path)})"
            except Exception as e:
                print(f"  [WARN] {atype}: error loading from disk: {e}")

        # Attempt 2: Mongo db.model_files.
        if model is None and model_id:
            file_doc = db.model_files.find_one({"model_id": model_id})
            if file_doc and file_doc.get("bytes"):
                try:
                    model = joblib.load(io.BytesIO(file_doc["bytes"]))
                    source = f"mongo db.model_files ({file_doc.get('size_bytes', 0)/1024/1024:.1f} MB)"
                except Exception as e:
                    print(f"  [ERROR] {atype}: loading from Mongo: {e}")

        if model is None:
            print(f"  [WARN] {atype}: could not load model (disk nor Mongo)")
            continue

        models[atype] = {
            "model": model,
            "features": d.get("features", []),
            "model_id": model_id,
            "version": d.get("version", 1),
        }
        print(f"  {atype}: model_id={model_id}  features={len(d.get('features', []))}  source={source}")

    return models


def load_climate_lookup(db):
    rows = list(db.climate_data.find({}, {"_id": 0}))
    if not rows:
        return {}, None
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["time"], dayfirst=True, errors="coerce").dt.date
    df = df.dropna(subset=["Date"])
    mean_cols = [c for c in df.columns if "mean" in c.lower() and "temperature" in c.lower()]
    df["External_Mean_Temp"] = df[mean_cols].mean(axis=1)
    lookup = {row["Date"]: float(row["External_Mean_Temp"]) for _, row in df.iterrows()}
    max_date = df["Date"].max()
    return lookup, max_date


def get_climate(d, lookup, max_date, fallback=12.0):
    if d in lookup:
        return lookup[d]
    if max_date and d > max_date:
        # Wrap to the same date in a previous year inside the dataset.
        for back in range(1, 11):
            try:
                wrap = d.replace(year=d.year - back)
            except ValueError:
                continue
            if wrap in lookup:
                return lookup[wrap]
    return fallback


def build_historical_features(asset_id, asset_type, climate_lookup, max_climate, asset_meta):
    """Mirror S5.build_training_data, without the target."""
    hist = mongo.get_operational_asset_data(asset_id)
    if hist.empty:
        return None
    df = hist[["Date", "kWh"]].rename(columns={"kWh": "Daily_Fan_Power_Sum"})
    df["External_Mean_Temp"] = df["Date"].apply(lambda d: get_climate(d, climate_lookup, max_climate))
    df = df.sort_values("Date").reset_index(drop=True)
    df["Cumulative_Power_Consumed"] = df["Daily_Fan_Power_Sum"].cumsum()
    df["Rolling_7D_Power"] = df["Daily_Fan_Power_Sum"].rolling(ROLL, min_periods=1).mean()
    df["Power_Lag_1"] = df["Daily_Fan_Power_Sum"].shift(1).bfill()

    if asset_type == "Pump":
        q_max = (asset_meta or {}).get("nominal_flow_m3h")
        p_max = (asset_meta or {}).get("rated_power_kW")
        if q_max and p_max and p_max > 0:
            avg_kw = df["Daily_Fan_Power_Sum"] / 24.0
            ratio = (avg_kw / p_max).clip(lower=0)
            df["Daily_Flow_m3h_est"] = q_max * (ratio ** (1.0 / 3.0))
        else:
            df["Daily_Flow_m3h_est"] = 0.0
        df["Cumulative_Flow"] = df["Daily_Flow_m3h_est"].cumsum()
        df["Rolling_7D_Flow"] = df["Daily_Flow_m3h_est"].rolling(ROLL, min_periods=1).mean()

    return df


def project_trajectory(asset_id, asset_type, model_info, ahi_current,
                       total_years_in_operation, cibse_life):
    """Linear projection at observed_rate. Returns (trajectory, safeguard, observed_rate, cibse_rate)."""
    cum_deg_start = max(0.0, 100.0 - float(ahi_current))
    if cum_deg_start >= TARGET_DEG:
        print(f"    [INFO] {asset_id}: AHI already <= {100-TARGET_DEG}, nothing to project")
        return [], False, 0.0, 0.0

    days_operating_so_far = max(1.0, float(total_years_in_operation) * 365.25)
    observed_daily_rate = cum_deg_start / days_operating_so_far
    cibse_daily_rate = TARGET_DEG / (float(cibse_life) * 365.25)

    max_days = int(round(cibse_life * 365.25))
    today = datetime.date.today()

    trajectory = []
    cum = cum_deg_start
    safeguard = True
    model_id = model_info["model_id"] if model_info else None

    for d_offset in range(1, max_days + 1):
        d = today + datetime.timedelta(days=d_offset)
        cum += observed_daily_rate
        trajectory.append({
            "asset_id": asset_id, "asset_type": asset_type, "model_id": model_id,
            "Date": d.isoformat(),
            "Daily_Degradation": round(observed_daily_rate, 8),
            "Cumulative_Degradation": round(cum, 4),
            "AHI_Predicted": round(max(0.0, 100.0 - cum), 4),
        })
        if cum >= TARGET_DEG:
            safeguard = False
            break

    return trajectory, safeguard, observed_daily_rate, cibse_daily_rate


def run_s4():
    print("\n" + "=" * 80)
    print("  S4: DEGRADATION TRAJECTORY (DAILY, UNTIL 95% OR CIBSE_LIFE)")
    print("=" * 80)
    paths.ensure_directories()

    db = mongo.get_db()

    print("\n[STEP 1] Loading models from registry...")
    models = load_models(db)
    if not models:
        print("[ERROR] No models available. Run S5 first.")
        sys.exit(1)

    print("\n[STEP 2] Loading climate lookup...")
    climate_lookup, max_climate = load_climate_lookup(db)
    print(f"  {len(climate_lookup)} dates. Last={max_climate}")

    print("\n[STEP 3] Loading AHI + assets...")
    ahi_df = mongo.read_collection("health_indexes")
    if ahi_df.empty:
        print("[ERROR] health_indexes empty. Run S2 again.")
        sys.exit(1)
    ahi_lookup = {row["asset_id"]: row.get("AHI (%)") for _, row in ahi_df.iterrows()}

    assets = list(db.assets.find({}, {"_id": 0}))
    print(f"  {len(assets)} assets")

    print("\n[STEP 4] Clearing previous trajectories...")
    deleted = db.degradation_trajectories.delete_many({}).deleted_count
    print(f"  Deleted {deleted} previous docs")

    predicted_at = pd.Timestamp.now().isoformat()
    print(f"\n[STEP 5] Computing trajectories... (predicted_at={predicted_at})")

    total_inserted = 0
    summary = []

    for a in assets:
        aid = a["asset_id"]
        atype = a.get("asset_type")
        if atype not in models:
            print(f"  [SKIP] {aid}: no model for type '{atype}'")
            continue

        ahi = ahi_lookup.get(aid)
        if ahi is None:
            print(f"  [SKIP] {aid}: no AHI in health_indexes")
            continue

        cibse_life = float(a.get("CIBSE Life Expectancy") or DEFAULT_CIBSE_LIFE)
        years_in_op = float(a.get("Total Years in Operation") or 0)
        if years_in_op <= 0:
            print(f"  [SKIP] {aid}: Total Years in Operation invalid ({years_in_op})")
            continue

        traj, safeguard, observed_rate, cibse_rate = project_trajectory(
            aid, atype, models[atype], ahi, years_in_op, cibse_life,
        )
        if not traj:
            continue

        for t in traj:
            t["predicted_at"] = predicted_at

        # Insert in chunks (Mongo limit ~16MB per request).
        BATCH = 5000
        for i in range(0, len(traj), BATCH):
            db.degradation_trajectories.insert_many(traj[i:i + BATCH])
        total_inserted += len(traj)

        last = traj[-1]
        years_predicted = len(traj) / 365.25
        cum_remaining = TARGET_DEG - (100.0 - ahi)
        years_cibse = (cum_remaining / cibse_rate) / 365.25 if cibse_rate > 0 else None
        delta = round(years_predicted - years_cibse, 2) if years_cibse is not None else None

        marker = "  [SAFEGUARD]" if safeguard else ""
        cibse_str = f"{years_cibse:5.1f}y" if years_cibse is not None else "  n/a"
        delta_str = f"{delta:+.1f}" if delta is not None else "n/a"
        print(f"  [OK]  {aid:14s} {atype:5s}  AHI {ahi:5.1f} -> {last['AHI_Predicted']:5.1f}  "
              f"predicted={years_predicted:5.1f}y  cibse={cibse_str}  delta={delta_str}{marker}")

        summary.append({
            "asset_id": aid, "asset_type": atype,
            "model_id": models[atype]["model_id"] if atype in models else None,
            "ahi_start": float(ahi), "ahi_end": last["AHI_Predicted"],
            "total_years_in_operation": years_in_op,
            "cibse_life_expectancy": cibse_life,
            "observed_daily_rate": round(observed_rate, 8),
            "cibse_daily_rate": round(cibse_rate, 8),
            "days_to_target": len(traj),
            "years_to_95_predicted": round(years_predicted, 2),
            "years_to_95_cibse_linear": round(years_cibse, 2) if years_cibse is not None else None,
            "delta_years": delta,
            "safeguard_hit": safeguard,
            "predicted_at": predicted_at,
        })

    db.degradation_trajectories.create_index([("asset_id", 1), ("Date", 1)])

    if summary:
        sum_df = pd.DataFrame(summary)
        mongo.write_collection("degradation_summary", sum_df)

    print("\n" + "=" * 80)
    print(f"[OK] db.degradation_trajectories: {total_inserted} docs")
    print(f"     db.degradation_summary: {len(summary)} assets")
    print("=" * 80)


if __name__ == "__main__":
    run_s4()
