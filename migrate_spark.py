"""
Migration script: load The Spark asset data into MongoDB
Phase 1: assets + asset_config
Phase 2: operational data from CSVs
"""
from pymongo import MongoClient
import csv, os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DATOS_DIR = os.path.join(os.path.dirname(__file__), "datos")

client = MongoClient(MONGO_URI)
db = client["cimba_db"]

# ── 1. DROP OLD COLLECTIONS ──────────────────────────────────────────────────
for col in ["assets", "asset_config", "health_indexes", "classification",
            "maintenance_records", "degradation_trajectories", "operational_data"]:
    db[col].drop()
    print(f"Dropped: {col}")

# ── 2. NEW ASSETS (21) ───────────────────────────────────────────────────────
assets = [
    # 7 PUMPS
    {
        "asset_id": "PUMP_CHW_1", "asset_name": "CHW Primary Pump 1",
        "Verified Asset Description": "CHW Primary Pump 1",
        "asset_type": "Pump", "system": "Chilled Water", "location": "Plant Room",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 25, "replacement_cost_GBP": 6000,
        "rated_power_kW": 10.0, "usage_vs_expected_pct": 50,
        "lifetime_years_duty": 39.7, "end_of_life_duty": "2062-01",
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": "e03ee29e-bc66-11ef-8683-0242ac110002",
        "building": "The Spark", "brick_class": "brick:Pump", "condition_notes": ""
    },
    {
        "asset_id": "PUMP_CHW_2", "asset_name": "CHW Primary Pump 2",
        "Verified Asset Description": "CHW Primary Pump 2",
        "asset_type": "Pump", "system": "Chilled Water", "location": "Plant Room",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 25, "replacement_cost_GBP": 6000,
        "rated_power_kW": 10.0, "usage_vs_expected_pct": 54,
        "lifetime_years_duty": 36.9, "end_of_life_duty": "2059-03",
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": "e040091c-bc66-11ef-8683-0242ac110002",
        "building": "The Spark", "brick_class": "brick:Pump", "condition_notes": ""
    },
    {
        "asset_id": "PUMP_DHW_1", "asset_name": "DHW Primary Pump 1",
        "Verified Asset Description": "DHW Primary Pump 1",
        "asset_type": "Pump", "system": "Domestic Hot Water", "location": "Plant Room",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 25, "replacement_cost_GBP": 3000,
        "rated_power_kW": 10.0, "usage_vs_expected_pct": 32,
        "lifetime_years_duty": 62.1, "end_of_life_duty": "2084-06",
        "Actual Condition Rating": "5 - Good",
        "uuid": "e0ce595a-bc67-11ef-8683-0242ac110002",
        "building": "The Spark", "brick_class": "brick:Pump", "condition_notes": ""
    },
    {
        "asset_id": "PUMP_DHW_2", "asset_name": "DHW Primary Pump 2",
        "Verified Asset Description": "DHW Primary Pump 2",
        "asset_type": "Pump", "system": "Domestic Hot Water", "location": "Plant Room",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 25, "replacement_cost_GBP": 3000,
        "rated_power_kW": 10.0, "usage_vs_expected_pct": 46,
        "lifetime_years_duty": 43.1, "end_of_life_duty": "2065-06",
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": "e0cf0238-bc67-11ef-8683-0242ac110002",
        "building": "The Spark", "brick_class": "brick:Pump", "condition_notes": ""
    },
    {
        "asset_id": "PUMP_DHW_S", "asset_name": "DHW Secondary Pump",
        "Verified Asset Description": "DHW Secondary Pump",
        "asset_type": "Pump", "system": "Domestic Hot Water", "location": "Plant Room",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 25, "replacement_cost_GBP": 6000,
        "rated_power_kW": 1.0, "usage_vs_expected_pct": 138,
        "lifetime_years_duty": 14.5, "end_of_life_duty": "2036-10",
        "Actual Condition Rating": "3 - On Notice",
        "uuid": "312f39be-bc68-11ef-87ca-0242ac110003",
        "building": "The Spark", "brick_class": "brick:Pump",
        "condition_notes": "Usage vs expected 138% - shortest projected lifetime. Priority monitoring."
    },
    {
        "asset_id": "PUMP_LTHW_1", "asset_name": "LTHW Primary Pump 1",
        "Verified Asset Description": "LTHW Primary Pump 1",
        "asset_type": "Pump", "system": "Low Temperature Hot Water", "location": "Plant Room",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 25, "replacement_cost_GBP": 6000,
        "rated_power_kW": 10.0, "usage_vs_expected_pct": 64,
        "lifetime_years_duty": 31.1, "end_of_life_duty": "2053-06",
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": "3ccad806-bc67-11ef-87ca-0242ac110003",
        "building": "The Spark", "brick_class": "brick:Pump", "condition_notes": ""
    },
    {
        "asset_id": "PUMP_LTHW_2", "asset_name": "LTHW Primary Pump 2",
        "Verified Asset Description": "LTHW Primary Pump 2",
        "asset_type": "Pump", "system": "Low Temperature Hot Water", "location": "Plant Room",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 25, "replacement_cost_GBP": 6000,
        "rated_power_kW": 10.0, "usage_vs_expected_pct": 59,
        "lifetime_years_duty": 34.1, "end_of_life_duty": "2056-06",
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": "3ccbc964-bc67-11ef-87ca-0242ac110003",
        "building": "The Spark", "brick_class": "brick:Pump", "condition_notes": ""
    },
    # 3 AHUs
    {
        "asset_id": "AHU_1", "asset_name": "AHU 1",
        "Verified Asset Description": "AHU 1",
        "asset_type": "AHU", "system": "Air Handling", "location": "Roof",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 15, "replacement_cost_GBP": 60000,
        "rated_power_kW": 30.0, "qty": 2,
        "Actual Condition Rating": "3 - On Notice",
        "uuid": "3bcc2ca0-bbbf-11ef-a598-0242ac110002",
        "building": "The Spark", "brick_class": "brick:AHU",
        "model": "Air Source ASV-1275-3220-EXT", "serial": "C2617-1",
        "condition_notes": "Filters dirty and fitted incorrectly on first inspection (CMSL 12/12/2024)."
    },
    {
        "asset_id": "AHU_2", "asset_name": "AHU 2",
        "Verified Asset Description": "AHU 2",
        "asset_type": "AHU", "system": "Air Handling", "location": "Roof",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 15, "replacement_cost_GBP": 60000,
        "rated_power_kW": 7.5, "qty": 1,
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": "3bcbe89e-bbbf-11ef-a598-0242ac110002",
        "building": "The Spark", "brick_class": "brick:AHU",
        "model": "Air Source ASV-1550-3220-EXT", "serial": "C2617-2",
        "condition_notes": ""
    },
    {
        "asset_id": "AHU_Kitchen", "asset_name": "AHU Kitchen",
        "Verified Asset Description": "AHU Kitchen",
        "asset_type": "AHU", "system": "Air Handling", "location": "Roof",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 15, "replacement_cost_GBP": 20000,
        "rated_power_kW": None, "qty": 1,
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": "edb65310-bbc8-11ef-8f16-0242ac110002",
        "building": "The Spark", "brick_class": "brick:AHU",
        "condition_notes": "Fewer telemetry points than AHU 1/2 - no static pressure or heat recovery."
    },
]

# 11 FCUs
fcu_data = [
    ("FCU_01_01","FCU 01/01","3cdb9350-bc79-11ef-8683-0242ac110002", 1,17),
    ("FCU_02_01","FCU 02/01","3ce68b16-bc79-11ef-8683-0242ac110002", 2,20),
    ("FCU_03_01","FCU 03/01","3ce72a6c-bc79-11ef-8683-0242ac110002", 3,25),
    ("FCU_04_01","FCU 04/01","3ce147be-bc79-11ef-8683-0242ac110002", 4,25),
    ("FCU_05_01","FCU 05/01","3ce9743e-bc79-11ef-8683-0242ac110002", 5,29),
    ("FCU_06_01","FCU 06/01","3ce32bba-bc79-11ef-8683-0242ac110002", 6,25),
    ("FCU_07_01","FCU 07/01","3ce1ecb4-bc79-11ef-8683-0242ac110002", 7,25),
    ("FCU_08_01","FCU 08/01","3ce00b2e-bc79-11ef-8683-0242ac110002", 8,25),
    ("FCU_09_01","FCU 09/01","3ced6030-bc79-11ef-8683-0242ac110002", 9,25),
    ("FCU_10_01","FCU 10/01","3ce3bc7e-bc79-11ef-8683-0242ac110002",10,25),
    ("FCU_11_01","FCU 11/01","3ce48d5c-bc79-11ef-8683-0242ac110002",11,20),
]
for fid, fname, fuuid, floor, total_units in fcu_data:
    assets.append({
        "asset_id": fid, "asset_name": fname,
        "Verified Asset Description": f"Fan Coil Unit - Floor {floor:02d}",
        "asset_type": "FCU", "system": "Fan Coil",
        "location": f"Floor {floor:02d}",
        "install_year": 2021, "Total Years in Operation": 5,
        "CIBSE Life Expectancy": 15, "replacement_cost_GBP": 1000,
        "rated_power_kW": None,
        "Actual Condition Rating": "4 - Satisfactory",
        "uuid": fuuid, "building": "The Spark", "brick_class": "brick:FCU",
        "floor": floor, "total_units_floor": total_units,
        "condition_notes": "Condensate drain pipework horizontal - no noticeable drop (CMSL 12/12/2024)."
    })

db.assets.insert_many(assets)
print(f"Assets: {db.assets.count_documents({})}")

# ── 3. ASSET CONFIG ──────────────────────────────────────────────────────────
dl_map = {
    "CHW_primary_pump_1":"PUMP_CHW_1","CHW_primary_pump_2":"PUMP_CHW_2",
    "DHW_primary_pump_1":"PUMP_DHW_1","DHW_primary_pump_2":"PUMP_DHW_2",
    "DHW_secondary_pump":"PUMP_DHW_S","LTHW_primary_pump_1":"PUMP_LTHW_1",
    "LTHW_primary_pump_2":"PUMP_LTHW_2","AHU_1":"AHU_1","AHU_2":"AHU_2",
    "AHU_Kitchen":"AHU_Kitchen",
}
for i in range(1,12):
    dl_map[f"FCU {i:02d}/01"] = f"FCU_{i:02d}_01"

configs = []
pumps_dl = ["CHW_primary_pump_1","CHW_primary_pump_2","DHW_primary_pump_1",
            "DHW_primary_pump_2","DHW_secondary_pump","LTHW_primary_pump_1","LTHW_primary_pump_2"]
ahus_dl  = ["AHU_1","AHU_2","AHU_Kitchen"]

for p in pumps_dl:
    aid = dl_map[p]
    a = next(x for x in assets if x["asset_id"]==aid)
    configs.append({
        "asset_id": aid, "asset_name": a["asset_name"],
        "asset_type": "Pump", "location": "Plant Room",
        "demand_logic_id": p,
        "files": {
            "daily_energy_2025": f"{p}__Daily_energy__2025-01-01_to_2025-12-29.csv",
            "daily_energy_2026": f"{p}__Daily_energy__2025-12-31_to_2026-03-31.csv"
        }
    })

for ah in ahus_dl:
    aid = dl_map[ah]
    a = next(x for x in assets if x["asset_id"]==aid)
    configs.append({
        "asset_id": aid, "asset_name": a["asset_name"],
        "asset_type": "AHU", "location": "Roof",
        "demand_logic_id": ah,
        "files": {
            "daily_energy_2025": f"{ah}__Daily_energy_by_attribute__2025-01-01_to_2025-12-29.csv",
            "daily_energy_2026": f"{ah}__Daily_energy_by_attribute__2025-12-31_to_2026-03-31.csv",
            "heatmap_fan":     f"{ah}__Daily_fan_heatmap__all_history.csv",
            "heatmap_cooling": f"{ah}__Daily_cooling_heatmap__all_history.csv",
            "heatmap_heating": f"{ah}__Daily_heating_heatmap__all_history.csv"
        }
    })

for i in range(1,12):
    dl_key = f"FCU {i:02d}/01"
    slug   = f"FCU_{i:02d}-01"
    aid    = dl_map[dl_key]
    a = next(x for x in assets if x["asset_id"]==aid)
    configs.append({
        "asset_id": aid, "asset_name": a["asset_name"],
        "asset_type": "FCU", "location": a["location"],
        "demand_logic_id": dl_key,
        "files": {
            "daily_energy_2025": f"{slug}__Daily_energy__2025-01-01_to_2025-12-29.csv",
            "daily_energy_2026": f"{slug}__Daily_energy__2025-12-31_to_2026-03-31.csv",
            "heatmap_fan":     f"{slug}__fan_heatmap__all_history.csv",
            "heatmap_cooling": f"{slug}__cooling_heatmap__all_history.csv",
            "heatmap_heating": f"{slug}__heating_heatmap__all_history.csv"
        }
    })

db.asset_config.insert_many(configs)
print(f"Asset configs: {db.asset_config.count_documents({})}")

# ── 4. LOAD OPERATIONAL DATA FROM CSVs ──────────────────────────────────────
# Pump files: date, kWh, pump
# AHU/FCU files: date, Cooling/Fan/Heating input energy, asset

def safe_float(val):
    try:
        f = float(val)
        return None if f != f else f  # NaN check
    except (ValueError, TypeError):
        return None

total_docs = 0
batch = []
BATCH_SIZE = 2000

def flush(batch):
    if batch:
        db.operational_data.insert_many(batch)
    return []

# -- Pumps --
for p in pumps_dl:
    aid = dl_map[p]
    for win in ["2025-01-01_to_2025-12-29", "2025-12-31_to_2026-03-31"]:
        fname = f"{p}__Daily_energy__{win}.csv"
        fpath = os.path.join(DATOS_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  MISSING: {fname}")
            continue
        with open(fpath, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                date_str = row["date"][:10]
                batch.append({
                    "Period": date_str, "asset_id": aid,
                    "type": "pump_energy",
                    "kWh": safe_float(row.get("kWh")),
                    "demand_logic_id": p
                })
                total_docs += 1
                if len(batch) >= BATCH_SIZE:
                    batch = flush(batch)

# -- AHUs --
for ah in ahus_dl:
    aid = dl_map[ah]
    for win in ["2025-01-01_to_2025-12-29", "2025-12-31_to_2026-03-31"]:
        fname = f"{ah}__Daily_energy_by_attribute__{win}.csv"
        fpath = os.path.join(DATOS_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  MISSING: {fname}")
            continue
        with open(fpath, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                date_str = row["date"][:10]
                batch.append({
                    "Period": date_str, "asset_id": aid,
                    "type": "hvac_energy",
                    "cooling_kWh": safe_float(row.get("Cooling input energy")),
                    "fan_kWh":     safe_float(row.get("Fan input energy")),
                    "heating_kWh": safe_float(row.get("Heating input energy")),
                    "demand_logic_id": ah
                })
                total_docs += 1
                if len(batch) >= BATCH_SIZE:
                    batch = flush(batch)

# -- FCUs --
for i in range(1,12):
    dl_key = f"FCU {i:02d}/01"
    slug   = f"FCU_{i:02d}-01"
    aid    = dl_map[dl_key]
    for win in ["2025-01-01_to_2025-12-29", "2025-12-31_to_2026-03-31"]:
        fname = f"{slug}__Daily_energy__{win}.csv"
        fpath = os.path.join(DATOS_DIR, fname)
        if not os.path.exists(fpath):
            print(f"  MISSING: {fname}")
            continue
        with open(fpath, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                date_str = row["date"][:10]
                batch.append({
                    "Period": date_str, "asset_id": aid,
                    "type": "hvac_energy",
                    "cooling_kWh": safe_float(row.get("Cooling input energy")),
                    "fan_kWh":     safe_float(row.get("Fan input energy")),
                    "heating_kWh": safe_float(row.get("Heating input energy")),
                    "demand_logic_id": dl_key
                })
                total_docs += 1
                if len(batch) >= BATCH_SIZE:
                    batch = flush(batch)

flush(batch)

db.operational_data.create_index([("asset_id", 1), ("Period", 1)])
print(f"Operational data: {db.operational_data.count_documents({})} docs ({total_docs} processed)")

# ── SUMMARY ─────────────────────────────────────────────────────────────────
print("\n=== FINAL STATE ===")
for col in ["assets","asset_config","operational_data","health_indexes",
            "classification","maintenance_records","degradation_trajectories"]:
    print(f"  {col}: {db[col].count_documents({})} docs")

stats = db.command("dbstats")
print(f"\nDB size: {stats['dataSize']/1e6:.1f} MB")
client.close()
