"""
S4 — Degradation Trajectory & RUL prediction — DAILY, LINEAR-OBSERVED-RATE
CIMBA Predictive Maintenance Framework

Logica de proyeccion (revisada 2026-04-27):
  observed_rate = (100 - AHI_actual) / (Total_Years_in_Operation * 365.25)
  trayectoria   = proyeccion lineal a observed_rate hacia el futuro
  parada        = AHI <= 5  OR  dias_simulados >= CIBSE_Life * 365 (safeguard)

Por que LINEAR y no la prediccion del modelo:
  - El target en S5 (Daily_Degradation) se construyo distribuyendo
    (100 - AHI) sobre los ~480 dias del histórico de Demandlogic, pero la
    realidad es que esos puntos de degradacion se acumularon en TODA la
    vida operativa del activo (5 anios = 1825 dias). El modelo aprende
    una tasa que es ~3-4x mas alta que la realidad, lo que produce
    proyecciones demasiado pesimistas (1-3 anios donde CIBSE espera 11-17).
  - Ademas la feature Cumulative_Power_Consumed crea positive feedback:
    en el futuro crece sin limite y el modelo predice rates aceleradas.
  - Hasta que tengamos AHI time-series real (multiples lecturas de
    maintenance), la proyeccion HONESTA es lineal del rate observado.

El modelo del registry SIGUE cargandose para guardar model_id en el
doc (trazabilidad arquitectural) pero NO se usa en el forecast.

Comparacion en db.degradation_summary:
  observed_daily_rate   <- (100 - AHI) / dias_operando
  cibse_daily_rate      <- 95 / CIBSE_Life / 365
  years_to_95_observed  <- proyeccion al rate real (lo que devolvemos)
  years_to_95_cibse     <- expected segun la spec del fabricante (95 / cibse_rate / 365)
  delta_years           <- diferencia (negativo = degrada mas rapido que spec)

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
TARGET_DEG = 95.0       # detener al alcanzar 95% degradacion (AHI = 5)
DEFAULT_CIBSE_LIFE = 20  # fallback si el asset no tiene el campo


def load_models(db):
    """Indexa los modelos del registry por asset_type. Carga del disco primero,
    fallback a db.model_files (Render filesystem es efimero, asi que despues de
    reboot el .pkl en disco no existe pero los bytes en Mongo si).

    Cuando hay varios modelos por asset_type (e.g. v1 y v2), elige el de version
    mas alta. Esto permite mantener versiones previas en el registry para auditoria
    sin que S4 las use."""
    # Sort por version desc para que la primera entry de cada asset_type sea la mas reciente
    docs = list(db.model_registry.find({}, {"_id": 0}).sort("version", -1))
    models = {}
    for d in docs:
        atype = d.get("asset_type")
        model_id = d.get("model_id")
        if not atype:
            continue
        if atype in models:
            # Ya tenemos un modelo mas reciente para este tipo
            continue

        model = None
        source = None

        # Intento 1: disco
        path = d.get("model_file")
        if path and os.path.exists(path):
            try:
                model = joblib.load(path)
                source = f"disk ({os.path.basename(path)})"
            except Exception as e:
                print(f"  [WARN] {atype}: error cargando del disco: {e}")

        # Intento 2: Mongo db.model_files
        if model is None and model_id:
            file_doc = db.model_files.find_one({"model_id": model_id})
            if file_doc and file_doc.get("bytes"):
                try:
                    model = joblib.load(io.BytesIO(file_doc["bytes"]))
                    source = f"mongo db.model_files ({file_doc.get('size_bytes', 0)/1024/1024:.1f} MB)"
                except Exception as e:
                    print(f"  [ERROR] {atype}: cargando desde Mongo: {e}")

        if model is None:
            print(f"  [WARN] {atype}: NO se pudo cargar el modelo (disco ni Mongo)")
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
        # Wrap a la misma fecha en un anio anterior dentro del dataset
        for back in range(1, 11):
            try:
                wrap = d.replace(year=d.year - back)
            except ValueError:
                continue
            if wrap in lookup:
                return lookup[wrap]
    return fallback


def build_historical_features(asset_id, asset_type, climate_lookup, max_climate, asset_meta):
    """Replica la logica de S5.build_training_data, sin target."""
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
    """Proyeccion LINEAL al observed_rate. Devuelve (trayectoria, safeguard, observed_rate, cibse_rate)."""
    cum_deg_start = max(0.0, 100.0 - float(ahi_current))
    if cum_deg_start >= TARGET_DEG:
        print(f"    [INFO] {asset_id}: AHI ya <= {100-TARGET_DEG}, no hay nada que proyectar")
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
    print("  S4 — DEGRADATION TRAJECTORY (DAILY, UNTIL 95% OR CIBSE_LIFE)")
    print("=" * 80)
    paths.ensure_directories()

    db = mongo.get_db()

    print("\n[STEP 1] Loading models from registry...")
    models = load_models(db)
    if not models:
        print("[ERROR] No hay modelos. Re-correr S5 primero.")
        sys.exit(1)

    print("\n[STEP 2] Loading climate lookup...")
    climate_lookup, max_climate = load_climate_lookup(db)
    print(f"  {len(climate_lookup)} dates. Last={max_climate}")

    print("\n[STEP 3] Loading AHI + assets...")
    ahi_df = mongo.read_collection("health_indexes")
    if ahi_df.empty:
        print("[ERROR] health_indexes empty. Re-correr S2.")
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
            print(f"  [SKIP] {aid}: no hay modelo para tipo '{atype}'")
            continue

        ahi = ahi_lookup.get(aid)
        if ahi is None:
            print(f"  [SKIP] {aid}: sin AHI en health_indexes")
            continue

        cibse_life = float(a.get("CIBSE Life Expectancy") or DEFAULT_CIBSE_LIFE)
        years_in_op = float(a.get("Total Years in Operation") or 0)
        if years_in_op <= 0:
            print(f"  [SKIP] {aid}: Total Years in Operation invalido ({years_in_op})")
            continue

        traj, safeguard, observed_rate, cibse_rate = project_trajectory(
            aid, atype, models[atype], ahi, years_in_op, cibse_life,
        )
        if not traj:
            continue

        for t in traj:
            t["predicted_at"] = predicted_at

        # Insert in chunks (Mongo limit ~16MB per request)
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
