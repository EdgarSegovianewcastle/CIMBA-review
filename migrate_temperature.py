"""
migrate_temperature.py
Unifica los CSVs mensuales de temperatura de un FCU/AHU descargados de Demand Logic
y los carga a MongoDB Atlas en la colección `operational_temperature`.

Uso:
    python migrate_temperature.py <asset_id> <carpeta_con_csvs>

Ejemplo:
    python migrate_temperature.py FCU_01_01 temepratura1
"""

import glob
import os
import sys

import pandas as pd
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "cimba_db"
COLLECTION = "operational_temperature"

CSV_METADATA_ROWS = 6  # 5 líneas key:value + 1 fila de URLs antes de los headers

COLUMN_MAP = {
    "Control Temperature": "control_temp",
    "Cooling Setpoint": "cooling_setpoint",
    "Entering Temperature": "entering_temp",
    "Heating Setpoint": "heating_setpoint",
    "Leaving Temperature": "leaving_temp",
    "Leaving Temperature Setpoint": "leaving_temp_setpoint",
    "Temperature Setpoint": "temp_setpoint",
}


def detect_prefix(columns):
    """Detecta el prefijo del nombre de columna (p.ej. 'FCU 01/01 ')."""
    for c in columns:
        if c == "Period":
            continue
        for metric in COLUMN_MAP:
            if c.endswith(metric):
                return c[: -len(metric)]
    return ""


def clean_column(col, prefix):
    if col == "Period":
        return "Period"
    metric = col[len(prefix):] if col.startswith(prefix) else col
    return COLUMN_MAP.get(metric, metric.lower().replace(" ", "_"))


def unify(folder, asset_id, output_csv):
    files = sorted(glob.glob(os.path.join(folder, "*.csv")))
    if not files:
        print(f"[ERROR] No se encontraron CSVs en {folder}")
        return None

    print(f"[INFO] {len(files)} archivos a unificar:")
    for f in files:
        print(f"  - {os.path.basename(f)}")

    dfs = []
    for f in files:
        df = pd.read_csv(f, skiprows=CSV_METADATA_ROWS)
        prefix = detect_prefix(df.columns)
        df.columns = [clean_column(c, prefix) for c in df.columns]
        dfs.append(df)

    df = pd.concat(dfs, ignore_index=True)
    df["Period"] = pd.to_datetime(df["Period"], errors="coerce")
    df = df.dropna(subset=["Period"])

    before = len(df)
    df = df.sort_values("Period").drop_duplicates(subset=["Period"], keep="first").reset_index(drop=True)
    deduped = before - len(df)

    for c in df.columns:
        if c != "Period":
            df[c] = pd.to_numeric(df[c], errors="coerce")

    print(f"\n[INFO] Unificado: {len(df)} filas (deduplicadas: {deduped})")
    print(f"       Rango: {df['Period'].min()} -> {df['Period'].max()}")
    print(f"       Columnas: {[c for c in df.columns if c != 'Period']}")

    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    df_out = df.copy()
    df_out.insert(1, "asset_id", asset_id)
    df_out.to_csv(output_csv, index=False)
    print(f"[OK] CSV unificado escrito: {output_csv}")

    return df


def load_mongo(df, asset_id):
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    db = client[DB_NAME]
    col = db[COLLECTION]

    deleted = col.delete_many({"asset_id": asset_id}).deleted_count
    print(f"\n[INFO] Borrados {deleted} docs previos de {asset_id} en '{COLLECTION}'")

    records = []
    for _, row in df.iterrows():
        rec = {"asset_id": asset_id, "Period": row["Period"].to_pydatetime()}
        for c in df.columns:
            if c == "Period":
                continue
            v = row[c]
            rec[c] = None if pd.isna(v) else float(v)
        records.append(rec)

    if records:
        col.insert_many(records)
        col.create_index([("asset_id", 1), ("Period", 1)])

    print(f"[OK] Insertados {len(records)} docs en '{COLLECTION}' para {asset_id}")
    print(f"     Índice (asset_id, Period) garantizado")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    asset_id = sys.argv[1]
    folder = sys.argv[2]
    output_csv = os.path.join("datos", f"{asset_id}__Temperature__unified.csv")

    df = unify(folder, asset_id, output_csv)
    if df is not None:
        load_mongo(df, asset_id)


if __name__ == "__main__":
    main()
