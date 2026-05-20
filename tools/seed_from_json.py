"""Load the JSON snapshot under data_snapshots/ into a local MongoDB.

Use this if you want to run the S1 to S5 scripts end to end on your machine.
Skip it if you only plan to read the code or use the notebook tour.

Steps:
    1. Install MongoDB Community Edition and start it on localhost:27017.
    2. (optional) set MONGO_URI; otherwise the default is mongodb://localhost:27017.
    3. python tools/seed_from_json.py

The script drops each target collection before reloading, so it is safe to run
multiple times.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

from bson import ObjectId
from pymongo import MongoClient

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "data_snapshots"
DB_NAME = "cimba_db"


def from_jsonable(value):
    if isinstance(value, dict):
        if set(value.keys()) == {"$oid"}:
            return ObjectId(value["$oid"])
        if set(value.keys()) == {"$date"}:
            return datetime.fromisoformat(value["$date"])
        return {k: from_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_jsonable(v) for v in value]
    return value


def main() -> int:
    uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
    if not SNAPSHOT_DIR.exists():
        print(f"ERROR: snapshot folder not found: {SNAPSHOT_DIR}", file=sys.stderr)
        return 1

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client[DB_NAME]

    json_files = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not json_files:
        print(f"ERROR: no JSON files in {SNAPSHOT_DIR}", file=sys.stderr)
        return 1

    print(f"Loading {len(json_files)} collections into {uri} / {DB_NAME}")
    print()

    for path in json_files:
        name = path.stem
        with path.open(encoding="utf-8") as f:
            raw_docs = json.load(f)
        docs = [from_jsonable(d) for d in raw_docs]

        coll = db[name]
        coll.drop()
        if docs:
            coll.insert_many(docs)
        print(f"  {name:<25} {len(docs):>6} docs")

    print()
    print("Done. You can now run migrate_spark.py and the S1 to S5 scripts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
