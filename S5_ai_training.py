"""
S5: AI Training (Random Forest), DAILY MODE, PER-TYPE, PER-FEATURE-SET
CIMBA Predictive Maintenance Framework

Trains ONE Random Forest PER asset TYPE (FCU, AHU, Pump) with a feature set
SPECIFIC to each type. The physical degradation varies by type (power,
schedules, wear mechanisms) and so do the available inputs (Pumps have
estimable flow but NO indoor temperature).

Feature sets:
  FCU/AHU: 5 generic features (power and external climate)
  Pump:    8 features (the 5 + 3 derived from flow estimated by affinity laws)

Default splits (overridable via env vars):
  FCU:  8 train + 3 val   (S5_FCU_VAL_IDS)
  AHU:  3 train + 0 val   (S5_AHU_VAL_IDS)
  Pump: 7 train + 0 val   (S5_PUMP_VAL_IDS)

Outputs:
  - database/models/rf_model_{FCU|AHU|Pump}.pkl
  - db.model_registry: 3 docs, one per type, with full metadata:
      model_id, version, asset_type, trained_on_asset_ids,
      applicable_filter, applies_to_asset_ids, features, metrics, etc.
  - db.training_matrix: features + target for the assets in the train split
  - db.train_val_split: 21 docs with asset_id + asset_type + role
"""

import io
import os
import sys

import joblib
import numpy as np
import pandas as pd
from bson.binary import Binary
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import train_test_split

import cimba_mongo as mongo
import cimba_paths as paths

ROLL = 7
ACCEL = 0.001
RF_N = 200
RF_D = 15
RF_S = 5
RF_L = 2
SEED = 42
TEST = 0.20
MIN_S = 30

GENERIC_FEATS = [
    "Daily_Fan_Power_Sum",
    "External_Mean_Temp",
    "Cumulative_Power_Consumed",
    "Rolling_7D_Power",
    "Power_Lag_1",
]


def enrich_registry_metadata(trained_on_asset_ids, asset_type, version, db):
    """Compute metadata derived from the assets that trained the model.

    Returns a dict ready to merge into the registry entry. This lets the
    dashboard / selection logic later pick the best model for a specific
    asset by matching manufacturer, building, etc.
    """
    docs = list(db.assets.find(
        {"asset_id": {"$in": trained_on_asset_ids}},
        {"_id": 0, "asset_id": 1, "Manufacturer": 1, "Model": 1,
         "building": 1, "system": 1, "rated_power_kW": 1, "install_year": 1},
    ))

    def distribution(field):
        out = {}
        for d in docs:
            v = d.get(field) or "Unknown"
            out[v] = out.get(v, 0) + 1
        return out

    manufacturers = distribution("Manufacturer")
    asset_models = distribution("Model")
    buildings = sorted({d.get("building") for d in docs if d.get("building")})
    systems = sorted({d.get("system") for d in docs if d.get("system")})
    powers = [d.get("rated_power_kW") for d in docs if d.get("rated_power_kW") is not None]
    years = [d.get("install_year") for d in docs if d.get("install_year") is not None]

    mfr_label = ", ".join(f"{k}x{v}" for k, v in sorted(manufacturers.items(), key=lambda x: -x[1]))
    display_name = f"{asset_type} RF v{version}, {mfr_label}" if mfr_label else f"{asset_type} RF v{version}"

    return {
        "display_name":              display_name,
        "n_trained_on":              len(trained_on_asset_ids),
        "manufacturers_distribution": manufacturers,
        "asset_models_distribution":  asset_models,
        "buildings":                 buildings,
        "systems":                   systems,
        "rated_power_kW_range":      [min(powers), max(powers)] if powers else None,
        "install_year_range":        [min(years), max(years)] if years else None,
    }

INDOOR_TEMP_FEATS = [
    "Daily_Indoor_Temp_Mean",
    "Daily_Indoor_Temp_Range",
    "Daily_Delta_Indoor_Outdoor",
]

# Feature set per type:
#   - FCU/AHU: GENERIC + 3 indoor temp (control_temp)   -> 8 features
#   - Pump:    GENERIC + 3 flow features (affinity law) -> 8 features (no indoor temp)
FEATS_PER_TYPE = {
    "FCU":  GENERIC_FEATS + INDOOR_TEMP_FEATS,
    "AHU":  GENERIC_FEATS + INDOOR_TEMP_FEATS,
    "Pump": GENERIC_FEATS + ["Daily_Flow_m3h_est", "Cumulative_Flow", "Rolling_7D_Flow"],
}

TGT = "Daily_Degradation"

DEFAULT_VAL_IDS = {
    "FCU":  ["FCU_09_01", "FCU_10_01", "FCU_11_01"],
    "AHU":  [],
    "Pump": [],
}

MODEL_VERSION = 2  # v2: FCU/AHU add 3 indoor temperature features (mean/range/delta).


def load_climate_daily(db):
    rows = list(db.climate_data.find({}, {"_id": 0}))
    if not rows:
        print("[ERROR] climate_data empty"); sys.exit(1)
    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["time"], dayfirst=True, errors="coerce").dt.date
    df = df.dropna(subset=["Date"])
    mean_cols = [c for c in df.columns if "mean" in c.lower() and "temperature" in c.lower()]
    df["External_Mean_Temp"] = df[mean_cols].mean(axis=1)
    return df[["Date", "External_Mean_Temp"]].drop_duplicates("Date").sort_values("Date").reset_index(drop=True)


def _load_indoor_temp_daily(db, asset_id):
    """Return a DataFrame with Date + control_temp/_max/_min aggregated per day."""
    pipe = [
        {"$match": {"asset_id": asset_id, "control_temp": {"$ne": None}}},
        {"$group": {
            "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$Period"}},
            "temp_mean": {"$avg": "$control_temp"},
            "temp_max":  {"$avg": "$control_temp_max"},
            "temp_min":  {"$avg": "$control_temp_min"},
        }},
    ]
    rows = list(db.operational_temperature.aggregate(pipe))
    if not rows:
        return pd.DataFrame(columns=["Date", "temp_mean", "temp_max", "temp_min"])
    return pd.DataFrame([
        {"Date": pd.to_datetime(r["_id"]).date(),
         "temp_mean": r["temp_mean"],
         "temp_max":  r.get("temp_max"),
         "temp_min":  r.get("temp_min")}
        for r in rows
    ]).sort_values("Date").reset_index(drop=True)


def build_training_data(asset_id, asset_type, ahi_value, climate_daily, asset_meta):
    hist = mongo.get_operational_asset_data(asset_id)
    if hist.empty:
        return None
    df = hist[["Date", "kWh"]].rename(columns={"kWh": "Daily_Fan_Power_Sum"})
    df = df.merge(climate_daily, on="Date", how="inner").sort_values("Date").reset_index(drop=True)
    if df.empty or len(df) < MIN_S:
        return None

    df["Days"] = df.index + 1
    df["Cumulative_Power_Consumed"] = df["Daily_Fan_Power_Sum"].cumsum()
    df["Rolling_7D_Power"] = df["Daily_Fan_Power_Sum"].rolling(ROLL, min_periods=1).mean()
    df["Power_Lag_1"] = df["Daily_Fan_Power_Sum"].shift(1).bfill()

    # Pump-specific features (flow derived from a cubic affinity law).
    if asset_type == "Pump":
        q_max = asset_meta.get("nominal_flow_m3h") if asset_meta else None
        p_max = asset_meta.get("rated_power_kW") if asset_meta else None
        if q_max and p_max and p_max > 0:
            avg_kw = df["Daily_Fan_Power_Sum"] / 24.0
            ratio = (avg_kw / p_max).clip(lower=0)
            df["Daily_Flow_m3h_est"] = q_max * (ratio ** (1.0 / 3.0))
        else:
            df["Daily_Flow_m3h_est"] = 0.0
        df["Cumulative_Flow"] = df["Daily_Flow_m3h_est"].cumsum()
        df["Rolling_7D_Flow"] = df["Daily_Flow_m3h_est"].rolling(ROLL, min_periods=1).mean()

    # FCU/AHU-specific features (indoor temperature).
    if asset_type in ("FCU", "AHU"):
        temp_df = _load_indoor_temp_daily(mongo.get_db(), asset_id)
        df = df.merge(temp_df, on="Date", how="left")
        df["Daily_Indoor_Temp_Mean"]      = df["temp_mean"]
        df["Daily_Indoor_Temp_Range"]     = (df["temp_max"] - df["temp_min"])
        df["Daily_Delta_Indoor_Outdoor"]  = (df["temp_mean"] - df["External_Mean_Temp"])
        df = df.drop(columns=["temp_mean", "temp_max", "temp_min"], errors="ignore")

    # Target.
    total_deg = max(0.0, 100.0 - float(ahi_value))
    df["EF"] = np.exp(ACCEL * df["Days"])
    df["DW"] = df["Daily_Fan_Power_Sum"] * df["EF"]
    tw = df["DW"].sum()
    df[TGT] = total_deg * (df["DW"] / tw) if tw > 0 else total_deg / len(df)

    feats = FEATS_PER_TYPE[asset_type]
    df["Asset_ID"] = asset_id
    return df[feats + [TGT, "Asset_ID", "Date"]].dropna()


def get_val_ids(asset_type):
    env = os.getenv(f"S5_{asset_type.upper()}_VAL_IDS", "")
    if env:
        return [x.strip() for x in env.split(",") if x.strip()]
    return DEFAULT_VAL_IDS.get(asset_type, [])


def train_one_model(asset_type, train_data, model_path, feats, model_id, db):
    matrix = pd.concat(train_data, ignore_index=True)
    X = matrix[feats]
    y = matrix[TGT]

    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=TEST, random_state=SEED)
    model = RandomForestRegressor(
        n_estimators=RF_N, max_depth=RF_D,
        min_samples_split=RF_S, min_samples_leaf=RF_L,
        random_state=SEED, n_jobs=-1,
    )
    model.fit(Xtr, ytr)
    pred = model.predict(Xte)
    mse = mean_squared_error(yte, pred)
    mae = mean_absolute_error(yte, pred)
    r2 = r2_score(yte, pred)

    # Save to disk (fast for local + when S4 runs right after on Render).
    joblib.dump(model, model_path)

    # Save bytes to Mongo (for Render after a reboot, since the filesystem is ephemeral).
    buffer = io.BytesIO()
    joblib.dump(model, buffer)
    buffer.seek(0)
    model_bytes = buffer.getvalue()
    db.model_files.update_one(
        {"model_id": model_id},
        {"$set": {
            "model_id": model_id,
            "asset_type": asset_type,
            "size_bytes": len(model_bytes),
            "bytes": Binary(model_bytes),
            "saved_at": pd.Timestamp.now().isoformat(),
        }},
        upsert=True,
    )

    fi = sorted(zip(feats, model.feature_importances_), key=lambda x: -x[1])

    print(f"\n  --- {asset_type} model ({len(feats)} features) ---")
    print(f"    Train samples: {len(Xtr)}  Test samples: {len(Xte)}")
    print(f"    R^2 = {r2:.4f}  MAE = {mae:.6f}  MSE = {mse:.8f}")
    for feat, imp in fi:
        bar = "#" * int(imp * 50)
        print(f"    {feat:30s} {imp:.4f} {bar}")
    print(f"    Disk: {model_path}")
    print(f"    Mongo db.model_files: {len(model_bytes) / 1024 / 1024:.2f} MB")

    return {
        "n_train_samples": len(Xtr), "n_test_samples": len(Xte),
        "mse": float(mse), "mae": float(mae), "r2": float(r2),
        "feature_importances": {f: float(i) for f, i in fi},
    }


def cleanup_legacy_model():
    legacy = paths.s5_model_file("HVAC_System")
    if os.path.exists(legacy):
        os.remove(legacy)
        print(f"  [CLEANUP] Removed legacy model: {legacy}")


def run_s5():
    print("\n" + "=" * 80)
    print("  S5: AI TRAINING (PER-TYPE, PER-FEATURE-SET)")
    print("=" * 80)
    paths.ensure_directories()
    cleanup_legacy_model()

    db = mongo.get_db()

    print("\n[STEP 1] Loading reference data...")
    ahi_df = mongo.read_collection("health_indexes")
    if ahi_df.empty or "asset_id" not in ahi_df.columns:
        print("[ERROR] health_indexes empty or without asset_id. Run S2 again.")
        sys.exit(1)
    ahi_lookup = {row["asset_id"]: row.get("AHI (%)", row.get("ahi", 100)) for _, row in ahi_df.iterrows()}
    climate = load_climate_daily(db)
    assets_full = list(db.assets.find({}, {"_id": 0}))
    asset_meta = {a["asset_id"]: a for a in assets_full}
    print(f"  health_indexes: {len(ahi_df)}  climate: {len(climate)}  assets: {len(assets_full)}")

    print("\n[STEP 2] Building feature matrices per type...")
    by_type = {"FCU": {}, "AHU": {}, "Pump": {}}
    for a in assets_full:
        aid = a["asset_id"]
        atype = a.get("asset_type")
        if atype not in by_type:
            continue
        ahi = ahi_lookup.get(aid)
        if ahi is None:
            continue
        td = build_training_data(aid, atype, ahi, climate, a)
        if td is None or td.empty:
            print(f"  [SKIP] {aid}: no data")
            continue
        by_type[atype][aid] = td

    for atype, data in by_type.items():
        feats = FEATS_PER_TYPE[atype]
        print(f"  {atype}: {len(data)} assets, {sum(len(v) for v in data.values())} samples, {len(feats)} features")

    print("\n[STEP 3] Train per type...")
    all_split_records = []
    all_registry = []
    all_train_matrices = []

    for atype, data in by_type.items():
        if not data:
            continue
        feats = FEATS_PER_TYPE[atype]

        available = sorted(data.keys())
        val_ids = [a for a in get_val_ids(atype) if a in available]
        train_ids = [a for a in available if a not in val_ids]

        for aid in train_ids:
            all_split_records.append({"asset_id": aid, "asset_type": atype, "role": "train"})
        for aid in val_ids:
            all_split_records.append({"asset_id": aid, "asset_type": atype, "role": "validation"})

        if not train_ids:
            print(f"\n  [{atype}] no train ids, skip")
            continue

        train_data = [data[a] for a in train_ids]
        model_path = paths.s5_model_file(atype)
        model_id = f"rf_{atype.lower()}_v{MODEL_VERSION}"
        metrics = train_one_model(atype, train_data, model_path, feats, model_id, db)

        # Current assets that match the filter (the assets the model applies to).
        applies_ids = sorted([a["asset_id"] for a in assets_full if a.get("asset_type") == atype])

        registry_entry = {
            "model_id":            f"rf_{atype.lower()}_v{MODEL_VERSION}",
            "version":             MODEL_VERSION,
            "asset_type":          atype,
            "model_file":          model_path,
            "features":            feats,
            "trained_on_asset_ids": train_ids,
            "validation_asset_ids": val_ids,
            "applicable_filter":   {"asset_type": atype},
            "applies_to_asset_ids": applies_ids,
            "applies_to_count":    len(applies_ids),
            "trained_at":          pd.Timestamp.now().isoformat(),
            **enrich_registry_metadata(train_ids, atype, MODEL_VERSION, db),
            **metrics,
        }
        all_registry.append(registry_entry)

        for a in train_ids:
            df_a = data[a].copy()
            df_a["asset_type"] = atype
            all_train_matrices.append(df_a)

    if not all_registry:
        print("[ERROR] No model was trained")
        sys.exit(1)

    # Persistence.
    split_df = pd.DataFrame(all_split_records)
    mongo.write_collection("train_val_split", split_df)
    split_df.to_csv(os.path.join(paths.MODELS_DIR, "train_val_split.csv"), index=False)

    reg_df = pd.DataFrame(all_registry)
    # Upsert by model_id: preserves previous versions (v1, v2, ...) in the registry.
    # Re-running with the same MODEL_VERSION overwrites that entry; bumping the version adds a new one.
    db_registry = mongo.get_db().model_registry
    for entry in all_registry:
        db_registry.update_one(
            {"model_id": entry["model_id"]},
            {"$set": entry},
            upsert=True,
        )
    print(f"  [MONGO] Upserted {len(all_registry)} entries into 'model_registry' (previous versions preserved)")
    reg_df_csv = reg_df.copy()
    for c in ["features", "trained_on_asset_ids", "validation_asset_ids", "applies_to_asset_ids", "applicable_filter", "feature_importances"]:
        if c in reg_df_csv.columns:
            reg_df_csv[c] = reg_df_csv[c].apply(lambda v: str(v) if v is not None else "")
    reg_df_csv.to_csv(paths.S5_MODEL_REGISTRY, index=False)

    matrix = pd.concat(all_train_matrices, ignore_index=True, sort=False)
    matrix_for_mongo = matrix.copy()
    matrix_for_mongo["Date"] = pd.to_datetime(matrix_for_mongo["Date"])
    mongo.write_collection("training_matrix", matrix_for_mongo)
    matrix.to_csv(paths.S5_TRAINING_MATRIX, index=False)

    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    for r in all_registry:
        print(f"  {r['asset_type']:5s} v{r['version']}: R^2={r['r2']:.4f}  feats={len(r['features'])}  train={r['n_train_samples']}  test={r['n_test_samples']}")
        print(f"         trained_on:    {r['trained_on_asset_ids']}")
        print(f"         applies_to ({r['applies_to_count']}): {r['applies_to_asset_ids']}")
        print(f"         model_file:    {os.path.basename(r['model_file'])}")
    print("=" * 80)


if __name__ == "__main__":
    run_s5()
