"""
load_daily_temperatures.py

Loads pre-processed DAILY temperatures (mean/max/min) from
temperatura2/PREPROCESADOS/ into MongoDB. Simple schema: 1 doc per
(asset_id, date).

Doc schema:
  {
    asset_id: "FCU_05_01",
    Period:   ISODate("2025-01-01"),  # midday, to avoid timezone glitches
    control_temp:     20.72,   # = mean (name kept for compatibility with endpoints)
    control_temp_max: 22.60,
    control_temp_min: 19.31,
    granularity: "daily"
  }

Compatibility with existing code:
  - /api/temperature/{asset_id}: does $avg over control_temp. With 1 doc/day
    it returns that single value. Works without changes.
  - cimba_mongo.get_operational_asset_data: same, uses $avg.
  - S3: no changes needed.

FCU uses the column "<asset_name> Control Temperature_*" (the only one in the
pre-processed file). AHU uses "<asset_name> Supply Control Temperature_*"
(Supply zone = indoor air).
"""

import argparse
import os
import sys

import pandas as pd
from pymongo import MongoClient

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = "cimba_db"
COLLECTION = "operational_temperature"


def asset_id_from_filename(name):
    base = os.path.basename(name)
    if base.endswith("_Diario.csv"):
        return base[: -len("_Diario.csv")]
    return base.split(".")[0]


def detect_asset_type(asset_id):
    if asset_id.startswith("FCU_"):
        return "FCU"
    if asset_id.startswith("AHU_"):
        return "AHU"
    return None


def find_columns(df, asset_type):
    """Return (col_mean, col_max, col_min) used for control_temp."""
    if asset_type == "FCU":
        target = "Control Temperature"
    else:
        target = "Supply Control Temperature"
    cols = {}
    for stat in ("mean", "max", "min"):
        for c in df.columns:
            if c.endswith(f"{target}_{stat}"):
                cols[stat] = c
                break
    return cols.get("mean"), cols.get("max"), cols.get("min")


def load_one_asset(path, db):
    asset_id = asset_id_from_filename(path)
    atype = detect_asset_type(asset_id)
    if atype is None:
        print(f"  [SKIP] {asset_id}: unknown type")
        return 0

    df = pd.read_csv(path)
    col_mean, col_max, col_min = find_columns(df, atype)
    if not col_mean:
        print(f"  [SKIP] {asset_id}: control_temp_mean column not found")
        return 0

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", col_mean])

    # Schema: Period at 12:00 to align with DAY = group queries.
    docs = []
    for _, row in df.iterrows():
        period = row["Date"].replace(hour=12, minute=0, second=0, microsecond=0).to_pydatetime()
        docs.append({
            "asset_id": asset_id,
            "Period": period,
            "control_temp": float(row[col_mean]) if pd.notna(row[col_mean]) else None,
            "control_temp_max": float(row[col_max]) if (col_max and pd.notna(row[col_max])) else None,
            "control_temp_min": float(row[col_min]) if (col_min and pd.notna(row[col_min])) else None,
            "granularity": "daily",
        })

    deleted = db[COLLECTION].delete_many({"asset_id": asset_id}).deleted_count
    if docs:
        db[COLLECTION].insert_many(docs)
    print(f"  [OK] {asset_id:14s} ({atype}) deleted={deleted}, inserted={len(docs)}")
    return len(docs)


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default="temperatura2/PREPROCESADOS")
    parser.add_argument("--purge-all", action="store_true",
                        help="Delete the WHOLE operational_temperature collection before loading")
    args = parser.parse_args(argv[1:])

    folder = args.folder
    if not os.path.isdir(folder):
        print(f"[ERROR] folder does not exist: {folder}")
        sys.exit(1)

    db = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)[DB_NAME]

    if args.purge_all:
        n = db[COLLECTION].delete_many({}).deleted_count
        print(f"[PURGE] {n} docs deleted from {COLLECTION}")
        print()

    files = sorted(f for f in os.listdir(folder) if f.endswith("_Diario.csv"))
    print(f"[INFO] {len(files)} files in {folder}")

    total = 0
    for f in files:
        path = os.path.join(folder, f)
        total += load_one_asset(path, db)

    db[COLLECTION].create_index([("asset_id", 1), ("Period", 1)])

    print()
    print(f"[OK] total docs inserted: {total}")
    print()
    print("[INFO] final summary per asset:")
    pipeline = [{"$group": {"_id": "$asset_id", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    for d in db[COLLECTION].aggregate(pipeline):
        print(f"  {d['_id']:14s} {d['n']:>5d} docs")

    stats = db.command("collStats", COLLECTION)
    print()
    print(f"[INFO] collection size: {stats['size'] / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main(sys.argv)
