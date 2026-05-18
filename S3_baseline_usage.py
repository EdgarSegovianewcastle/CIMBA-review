"""
S3 — Baseline Usage (DAILY version)
CIMBA Predictive Maintenance Framework

Para cada activo y cada dia futuro (next 30 dias por defecto), busca los
dias historicos con clima mas similar y consumo mas bajo (operacion eficiente)
y los promedia para obtener un baseline de consumo esperado. Tambien guarda
el consumo real para los dias en los que existe data (validacion).

Algoritmo (por dia objetivo D):
  1. Filtrar candidatos: mismos dia-de-semana (laborable vs fin de semana)
     y EXCLUIR el propio dia D si esta en historico.
  2. Distancia climatica: sqrt((Ens_Max-T)**2 + (Ens_Mean-T)**2 + (Ens_Min-T)**2)
     donde Ens_X es el promedio de los modelos climaticos (CMCC/FGOALS/HiRAM).
  3. Tomar Y_POOL=10 mas similares en clima.
  4. De esos, tomar X_DAYS=7 con menor kWh (operacion mas eficiente).
  5. baseline = mean(esos 7) -> Total_Predicted_Power.

Reads (Mongo):
  - assets                : lista de activos
  - operational_data      : kWh diario por activo (real + sintetico)
  - operational_temperature: solo se usa para FCU/AHU para enriquecer (opcional)
  - climate_data          : ensemble de modelos climaticos por dia

Writes (Mongo):
  - baselines             : 1 doc por (asset_id, Period) con campos
                            asset_id, Period (str YYYY-MM-DD),
                            Total_Predicted_Power (kWh), Total_Real_Power (kWh o None)
  - baseline_validation   : metricas por activo (CVRMSE en validation set)

CSV outputs (legacy debugging): database/baselines/{asset_id}_baseline.csv
"""

import datetime
import os
import sys

import numpy as np
import pandas as pd

import cimba_mongo as mongo
import cimba_paths as paths

# Parametros del algoritmo
X_DAYS = 7        # Cantos dias se promedian para el baseline
Y_POOL = 10       # Pool inicial por similitud climatica
FORECAST_DAYS_AHEAD = 30  # Cuantos dias futuros se predicen


def load_climate(db):
    """Carga climate_data y agrega columnas Ens_Max/Mean/Min (ensemble de modelos)."""
    rows = list(db.climate_data.find({}, {"_id": 0}))
    if not rows:
        print("[ERROR] climate_data empty")
        sys.exit(1)
    df = pd.DataFrame(rows)
    # time es DD/MM/YYYY
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
    """Para cada target_date, predice kWh (y temperatura indoor si aplica) usando K-NN climatico."""
    if hist_daily.empty:
        return []

    # Merge histórico con clima
    hist = hist_daily.merge(climate_daily, on="Date", how="left").dropna(subset=["Ens_Mean"])
    if hist.empty:
        return []
    hist["weekday"] = hist["Date"].apply(lambda d: d.weekday())

    climate_lookup = climate_daily.set_index("Date")

    # Subset con temperatura disponible para el K-NN de temperatura
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

        # ===== Baseline control_temp (solo si el asset tiene temperatura) =====
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
                # Para temperatura tomamos los Y_POOL mas cercanos en clima y promediamos su control_temp
                # (no importa minimizar consumo, importa la temperatura tipica en ese clima)
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
    """Coefficient of variation of RMSE (%). Compara prediccion vs valor historico real
    para los dias en los que existe data."""
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
    print("\n" + "=" * 80 + "\nS3 — BASELINE USAGE (DAILY MODE)\n" + "=" * 80)
    paths.ensure_directories()

    db = mongo.get_db()

    print("\n[STEP 1] Loading climate ensemble...")
    climate_daily = load_climate(db)
    print(f"  Climate: {len(climate_daily)} dias ({climate_daily['Date'].min()} -> {climate_daily['Date'].max()})")

    print("\n[STEP 2] Computing target date range...")
    today = datetime.date.today()
    # Target: todo el rango con clima disponible que cae entre 2025-01-01 y hoy+30
    end_target = today + datetime.timedelta(days=FORECAST_DAYS_AHEAD)
    target_dates = [d for d in climate_daily["Date"] if datetime.date(2025, 1, 1) <= d <= end_target]
    print(f"  Target: {len(target_dates)} dias ({target_dates[0]} -> {target_dates[-1]})")

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
            print(f"  [SKIP] {aid}: asset_type={atype} no soportado todavia")
            continue

        hist = mongo.get_operational_asset_data(aid)
        if hist.empty:
            print(f"  [SKIP] {aid}: no operational data")
            continue

        # Detectar si el asset tiene historico de temperatura indoor
        has_temp = atype in ("FCU", "AHU") and hist["control_temp"].notna().any()

        rows = gen_baseline(aid, hist, climate_daily, target_dates, has_temperature=has_temp)
        if not rows:
            print(f"  [SKIP] {aid}: no se pudieron generar predicciones")
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

        # CSV legacy
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
    print(f"[OK] Total docs en db.baselines: {total_inserted}")
    print(f"     Validation summary en db.baseline_validation: {len(all_validation)} assets")
    print("=" * 80)


if __name__ == "__main__":
    run_s3()
