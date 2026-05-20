import pandas as pd
from pymongo import MongoClient
import os

# Read MONGO_URI from the env var; fall back to localhost when not set.
# To point the scripts at Atlas, export:
#   export MONGO_URI='mongodb+srv://...'  (Linux/Mac)
#   $env:MONGO_URI = '...'                (PowerShell)
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME   = os.getenv("DB_NAME", "cimba_db")

def get_db():
    client = MongoClient(MONGO_URI)
    return client[DB_NAME]

def read_collection(collection_name, query=None):
    """Reads a MongoDB collection into a Pandas DataFrame."""
    db = get_db()
    cursor = db[collection_name].find(query or {})
    df = pd.DataFrame(list(cursor))
    if not df.empty and '_id' in df.columns:
        df.drop(columns=['_id'], inplace=True)
    return df

def get_operational_asset_data(asset_id):
    """Fetches DAILY operational data for an asset from MongoDB.

    Returns a DataFrame with columns:
      - Date          (datetime.date)
      - kWh           (float, daily energy total)
      - control_temp  (float or NaN; daily mean indoor temp for FCU/AHU,
                       NaN for Pumps since they have no indoor temperature sensor)

    For hvac_energy (FCU/AHU): kWh = heating_kWh + cooling_kWh + fan_kWh
    For pump_energy (Pumps):   kWh = the `kWh` field on the doc

    Includes both real AND synthetic data (with `is_synthetic=true`) without
    distinguishing; the flag is preserved in Mongo but the baseline treats them
    equally.
    """
    db = get_db()
    rows = list(db.operational_data.find({"asset_id": asset_id}, {"_id": 0}))
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["Date"] = pd.to_datetime(df["Period"], errors="coerce").dt.date
    df = df.dropna(subset=["Date"])

    asset_type = df["type"].iloc[0] if "type" in df.columns and len(df) else None

    if asset_type == "hvac_energy":
        for c in ["heating_kWh", "cooling_kWh", "fan_kWh"]:
            if c not in df.columns:
                df[c] = 0.0
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        df["kWh"] = df["heating_kWh"] + df["cooling_kWh"] + df["fan_kWh"]
    else:
        df["kWh"] = pd.to_numeric(df.get("kWh", 0.0), errors="coerce").fillna(0.0)

    # Indoor temperature (FCU/AHU only; aggregated on the fly from operational_temperature).
    if asset_type == "hvac_energy":
        pipe = [
            {"$match": {"asset_id": asset_id, "control_temp": {"$ne": None}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$Period"}},
                "temp_mean": {"$avg": "$control_temp"},
            }},
        ]
        temp_rows = list(db.operational_temperature.aggregate(pipe))
        if temp_rows:
            temp_df = pd.DataFrame([
                {"Date": pd.to_datetime(r["_id"]).date(), "control_temp": r["temp_mean"]}
                for r in temp_rows
            ])
            df = df.merge(temp_df, on="Date", how="left")
        else:
            df["control_temp"] = None
    else:
        df["control_temp"] = None

    return df[["Date", "kWh", "control_temp"]].drop_duplicates("Date").sort_values("Date").reset_index(drop=True)

def write_collection(collection_name, df, delete_existing=True):
    """Writes a Pandas DataFrame to a MongoDB collection."""
    db = get_db()
    if delete_existing:
        db[collection_name].delete_many({})
    
    # Handle NaN values
    df = df.where(pd.notnull(df), None)
    records = df.to_dict('records')
    
    if records:
        db[collection_name].insert_many(records)
        print(f"  [MONGO] Saved {len(records)} records to '{collection_name}'")
    else:
        print(f"  [MONGO] No records to save to '{collection_name}'")
