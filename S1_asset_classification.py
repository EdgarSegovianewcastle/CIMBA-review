"""
S1: Asset Classification
CIMBA Predictive Maintenance Framework

Reads:  database/assets/asset_register.csv
        database/assets/maintenance_records.csv
Writes: database/assets/classification_result.csv
"""
import pandas as pd
import numpy as np
import os, sys, re, io
import cimba_paths as paths
import cimba_mongo as mongo

try:
    from docx import Document as DocxDocument
    HAS_DOCX = True
except ImportError:
    HAS_DOCX = False

# ── Config ──
INTERVIEW_FILE = os.path.join(paths.ASSETS_DIR, "interview_transcript.docx")
W_IMP, W_FREQ, W_COST = 0.50, 0.30, 0.20
BINS = [0, 40, 70, 90, 101]; LABELS = ["Low", "Medium", "High", "Extreme"]
YES_TH, MAYBE_TH = 65, 45

BRICK_KEYWORDS = {
    "brick:HVAC_System": ["VRV","AHU","FAN COIL","HEAT PUMP","BOILER","CHILLER","EXTRACT FAN",
                          "HEATING","CONVECTOR","AIR SOURCE","AIR HANDLING","FANCOIL","FCU"],
    "brick:Lighting_System": ["LIGHTING","LAMPS","EMERGENCY LIGHTING","RELAMPING"],
    "brick:Fire_Safety_System": ["FIRE ALARM","FIRE DOOR","DRY RISER","REFUGE SYSTEM","SMOKE"],
    "brick:Water_System": ["WATER","LEGIONELLA","BOOSTER SET","SHOWERS","DRAINAGE","PUMPS","PUMP"],
    "brick:Security_System": ["ACCESS CONTROL","CCTV","AUTOMATIC DOORS","INTRUDER ALARM"],
    "brick:Building_Management_System": ["BMS","CONTROL SOLUTIONS"],
    "brick:Electrical_System": ["UPS","METERING","PAT","ELECTRICAL"],
}
JOB_TO_BRICK = {"HVAC":"brick:HVAC_System","Lighting":"brick:Lighting_System",
    "Fire Alarm Fault":"brick:Fire_Safety_System","Drainage":"brick:Water_System",
    "Water":"brick:Water_System","BMS":"brick:Building_Management_System",
    "Doors":"brick:Security_System","Network / IT":"brick:Electrical_System"}
FALLBACK_WEIGHTS = {"brick:Building_Management_System":10,"brick:HVAC_System":9,
    "brick:Fire_Safety_System":9,"brick:Water_System":8,"brick:Security_System":7,
    "brick:Electrical_System":6,"brick:Lighting_System":3,"brick:Equipment":4}

def classify_brick(name):
    if pd.isna(name): return "brick:Equipment"
    t = str(name).upper()
    for bc, kws in BRICK_KEYWORDS.items():
        if any(k in t for k in kws): return bc
    return "brick:Equipment"

def load_register():
    print("  [MONGO] Loading asset_register...")
    df = mongo.read_collection("assets")
    if not df.empty:
        cands = ["asset","description","asset name","asset_name"]
        col = next((c for c in df.columns if c.strip().lower() in cands), df.columns[0])
        return df, col
    print("  [WARN] Collection 'assets' empty, using example.")
    ex = "Asset Name,Asset ID,Location,Installation Date,Expected Life (yr)\nFan Coil Unit GF-01,FCU-C-001,Core GF,2018-03,15\nAir Handling Unit 01,AHU-C-001,Core Roof,2017-09,20"
    return pd.read_csv(io.StringIO(ex)), "Asset Name"

def load_costs():
    print("  [MONGO] Loading maintenance_records...")
    df = mongo.read_collection("maintenance_records")
    if not df.empty:
        df["p2pValue"] = pd.to_numeric(df.get("p2pValue", df.get("Cost",0)), errors="coerce").fillna(0)
        return df
    print("  [WARN] Collection 'maintenance_records' empty, using example.")
    ex = "jobGroupName,p2pValue\nHVAC,420\nHVAC,185\nWater,1200\nFire Alarm Fault,350"
    return pd.read_csv(io.StringIO(ex))

def get_weights():
    if not os.path.exists(INTERVIEW_FILE) or not HAS_DOCX:
        return FALLBACK_WEIGHTS.copy()
    doc = DocxDocument(INTERVIEW_FILE)
    content = " ".join(p.text for p in doc.paragraphs).lower()
    w = {k:5 for k in FALLBACK_WEIGHTS}
    pats = {"brick:Building_Management_System":[r"bms",r"heart"],"brick:HVAC_System":[r"hvac",r"heating"],
            "brick:Fire_Safety_System":[r"fire",r"life safety"],"brick:Water_System":[r"water",r"legionella"],
            "brick:Lighting_System":[r"lighting",r"smaller"],"brick:Security_System":[r"security",r"access"]}
    for bc, ps in pats.items():
        for p in ps:
            if re.search(p, content):
                w[bc] = 10 if "bms" in p else (3 if "smaller" in p else 9)
    return w

def calc_criticality(freq, cost, imp):
    s = min(imp,10)*W_IMP*10 + min(freq,10)*W_FREQ*10 + min(cost/100,10)*W_COST*10
    if imp >= 9: s = max(s, 85)
    return min(s, 100)

def run_s1():
    print("\n" + "="*80 + "\nS1: ASSET CLASSIFICATION (MONGO MODE)\n" + "="*80)
    paths.ensure_directories()
    print("\n[1] Loading data...")
    df, col = load_register(); costs = load_costs()
    print(f"  {len(df)} assets, {len(costs)} cost records.")

    print("\n[2] Classifying into Brick classes...")
    df["Brick_Class"] = df[col].apply(classify_brick)
    for c, n in df["Brick_Class"].value_counts().items(): print(f"  {c}: {n}")

    print("\n[3] Interview weights...")
    weights = get_weights()

    print("\n[4] Cost statistics...")
    costs["Brick_Class"] = costs["jobGroupName"].map(JOB_TO_BRICK)
    cstats = costs.groupby("Brick_Class").agg(
        fc=("p2pValue","count"), avg=("p2pValue","mean"),
        mn=("p2pValue","min"), mx=("p2pValue","max")).fillna(0).to_dict("index")

    print("\n[5] Criticality index...")
    def evaluate(row):
        bc = row["Brick_Class"]
        st = cstats.get(bc, {"fc":0,"avg":0,"mn":0,"mx":0})
        imp = weights.get(bc, 5)
        sc = calc_criticality(st["fc"], st["avg"], imp)
        suit = "Yes" if sc>=YES_TH else ("Maybe" if sc>=MAYBE_TH else "No")
        return pd.Series({"Criticality_Index":round(sc/100,2),"Expert_Importance":imp,
            "Min_Cost":round(st["mn"],2),"Mean_Cost":round(st["avg"],2),
            "Max_Cost":round(st["mx"],2),"Suitable_for_PdM":suit})

    res = df.apply(evaluate, axis=1)
    df = pd.concat([df, res], axis=1)
    df["Priority_Level"] = pd.cut(df["Criticality_Index"]*100, bins=BINS, labels=LABELS, right=False)
    df = df.sort_values("Criticality_Index", ascending=False)

    print("\n" + "="*80 + "\nRESULTS\n" + "="*80)
    disp = [col,"Brick_Class","Criticality_Index","Suitable_for_PdM","Priority_Level",
            "Expert_Importance","Min_Cost","Mean_Cost","Max_Cost"]
    pd.set_option("display.width",120); pd.set_option("display.max_columns",None)
    print(df[disp].to_string(index=False))
    
    # Save to MongoDB
    mongo.write_collection("classification", df[disp])
    # Also keep CSV for legacy/debugging
    df[disp].to_csv(paths.S1_CLASSIFICATION, index=False)
    
    print(f"\n[OUTPUT] MongoDB collection 'classification' updated.")
    for c in ["Yes","Maybe","No"]: print(f"  {c}: {(df['Suitable_for_PdM']==c).sum()}")
    print("="*80)

if __name__ == "__main__": run_s1()

if __name__ == "__main__": run_s1()
