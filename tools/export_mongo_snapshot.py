"""Export a snapshot of Atlas to JSON files inside data_snapshots/.

Only dumps collections the reviewer needs to run S1 to S5 locally,
plus small calculated outputs for comparison. Sensitive collections
(users, sessions) are excluded.

Run with MONGO_URI pointing to Atlas:
    set MONGO_URI=mongodb+srv://...
    python tools/export_mongo_snapshot.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

from bson import ObjectId
from pymongo import MongoClient

# What we export and why.
STATIC_INPUT = [
    "projects",
    "assets",
    "asset_config",
    "maintenance_records",
    "climate_data",
    "weather_daily",
]

OUTPUTS_FOR_REFERENCE = [
    "health_indexes",
    "degradation_summary",
    "baseline_validation",
    "model_registry",
    "train_val_split",
]

ALL = STATIC_INPUT + OUTPUTS_FOR_REFERENCE

OUT_DIR = Path(__file__).resolve().parent.parent / "data_snapshots"


def to_jsonable(value):
    if isinstance(value, ObjectId):
        return {"$oid": str(value)}
    if isinstance(value, (datetime, date)):
        return {"$date": value.isoformat()}
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [to_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def main() -> int:
    uri = os.environ.get("MONGO_URI")
    if not uri:
        print("ERROR: set MONGO_URI first.", file=sys.stderr)
        return 1

    client = MongoClient(uri, serverSelectionTimeoutMS=10000)
    db = client["cimba_db"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    summary = []
    for name in ALL:
        coll = db[name]
        docs = [to_jsonable(d) for d in coll.find()]
        target = OUT_DIR / f"{name}.json"
        with target.open("w", encoding="utf-8") as f:
            json.dump(docs, f, indent=2, ensure_ascii=False)
        summary.append((name, len(docs), target.stat().st_size))

    print(f"Exported {len(summary)} collections to {OUT_DIR}")
    print()
    print(f"{'collection':<25} {'docs':>7} {'size (KB)':>10}")
    print("-" * 46)
    for name, count, size in summary:
        print(f"{name:<25} {count:>7} {size / 1024:>10.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
