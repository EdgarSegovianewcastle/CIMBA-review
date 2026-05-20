"""
S2: Asset Health Index Calculation
CIMBA Predictive Maintenance Framework

Reads:  database/assets/condition_report.csv
        database/assets/classification_result.csv (S1, for Brick_Class)
Writes: database/assets/asset_health_index.csv (now includes Brick_Class)
"""
import pandas as pd, numpy as np, re, os, sys, io
import cimba_paths as paths
import cimba_mongo as mongo

COL_NAME="Verified Asset Description"; COL_YEARS="Total Years in Operation"
COL_LIFE="CIBSE Life Expectancy"; COL_COND="Actual Condition Rating"
W_AGE,W_COND=0.40,0.60; BETA=1.2; SCALE=5; OVERRIDE=2

def load_data():
    print("  [MONGO] Loading assets (condition data)...")
    df = mongo.read_collection("assets")
    if not df.empty:
        return df
    print("  [WARNING] Collection 'assets' empty. Using example.")
    return pd.read_csv(io.StringIO(f"{COL_NAME},{COL_YEARS},{COL_LIFE},{COL_COND}\nFan Coil Unit GF-01,7,15,3 - On Notice"))

def load_brick():
    print("  [MONGO] Loading classification...")
    df = mongo.read_collection("classification")
    if not df.empty:
        nc = df.columns[0]
        print(f"  [OK] classification results loaded from Mongo.")
        return df[[nc,"Brick_Class"]].rename(columns={nc:"_s1"})
    print("  [INFO] classification collection empty. Default Brick_Class.")
    return None

def rating(v):
    m = re.search(r"\d+", str(v)); return int(m.group()) if m else 3

def ahi(t,L,r):
    ha = max(0.0,1.0-(min(t/L,1.0)**BETA)) if L>0 else 0.0
    hc = r/SCALE
    if r<=OVERRIDE: return round(hc*100,2)
    return round((W_AGE*ha+W_COND*hc)*100,2)

def cat(a):
    if a>=80: return "Excellent"
    elif a>=60: return "Good"
    elif a>=40: return "Fair"
    return "Critical"

def run_s2():
    print("\n"+"="*80+"\nS2: ASSET HEALTH INDEX (MONGO MODE)\n"+"="*80)
    paths.ensure_directories()
    print("\n[STEP 1] Loading data...")
    df = load_data(); df.columns = df.columns.astype(str).str.strip()
    
    # Map columns if they have different names in Mongo
    # Example: 'Asset Name' -> COL_NAME
    if COL_NAME not in df.columns:
        cands = ["Asset Name", "asset_name", "AssetID", "Asset ID"]
        found = next((c for c in df.columns if c in cands), None)
        if found: df.rename(columns={found: COL_NAME}, inplace=True)

    df[COL_YEARS] = pd.to_numeric(df.get(COL_YEARS, 0), errors="coerce").fillna(0)
    df[COL_LIFE] = pd.to_numeric(df.get(COL_LIFE, 20), errors="coerce").fillna(20)
    df["Rating_Numeric"] = df.get(COL_COND, "3").apply(rating)
    
    print("\n[STEP 2] Classification Data...")
    s1 = load_brick()
    
    print("\n[STEP 3] Calculating AHI...")
    df["AHI (%)"] = df.apply(lambda r: ahi(r[COL_YEARS],r[COL_LIFE],r["Rating_Numeric"]), axis=1)
    df["Estimated_RUL (Years)"] = df.apply(lambda r: round(r[COL_LIFE]*r["AHI (%)"]/100,1), axis=1)
    df["Health_Category"] = df["AHI (%)"].apply(cat)
    
    if s1 is not None:
        df = df.merge(s1, left_on=COL_NAME, right_on="_s1", how="left")
        df.drop(columns=["_s1"], inplace=True, errors="ignore")
        df["Brick_Class"] = df["Brick_Class"].fillna("brick:HVAC_System")
    else: df["Brick_Class"] = "brick:HVAC_System"
    
    print("\n"+"="*80+"\nRESULTS\n"+"="*80)
    disp = [COL_NAME,COL_YEARS,COL_LIFE,COL_COND,"AHI (%)","Health_Category","Estimated_RUL (Years)","Brick_Class"]
    # Filter only existing columns for display
    disp = [c for c in disp if c in df.columns]
    pd.set_option("display.width",120); pd.set_option("display.max_columns",None)
    print(df[disp].to_string(index=False))
    
    # Include asset_id so the frontend can match by id rather than by name.
    out = ["asset_id",COL_NAME,COL_YEARS,COL_LIFE,"Rating_Numeric","AHI (%)","Health_Category","Estimated_RUL (Years)","Brick_Class"]
    out = [c for c in out if c in df.columns]
    
    # Save to MongoDB
    mongo.write_collection("health_indexes", df[out])
    # Keep CSV
    df[out].to_csv(paths.S2_HEALTH_INDEX, index=False)
    
    print(f"\n[OUTPUT] MongoDB collection 'health_indexes' updated.\n"+"="*80)

if __name__ == "__main__": run_s2()
