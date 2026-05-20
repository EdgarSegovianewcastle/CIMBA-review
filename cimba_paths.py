"""
cimba_paths.py: Shared Path Configuration
CIMBA Predictive Maintenance Framework

Folder structure:
    CIMBA/
    ├── S1 to S5 scripts + cimba_paths.py
    └── database/
        ├── assets/          (registers, configs, S1/S2 outputs)
        ├── operational/     (per-asset BMS CSVs)
        ├── climate/         (Open-Meteo data)
        ├── baselines/       (S3 output)
        ├── models/          (S5 output)
        └── degradation/     (S4 output)
"""
import os

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR   = os.path.join(ROOT_DIR, "database")

ASSETS_DIR      = os.path.join(DB_DIR, "assets")
OPERATIONAL_DIR = os.path.join(DB_DIR, "operational")
CLIMATE_DIR     = os.path.join(DB_DIR, "climate")
BASELINES_DIR   = os.path.join(DB_DIR, "baselines")
MODELS_DIR      = os.path.join(DB_DIR, "models")
DEGRADATION_DIR = os.path.join(DB_DIR, "degradation")

# User inputs
ASSET_REGISTER      = os.path.join(ASSETS_DIR, "asset_register.csv")
MAINTENANCE_RECORDS = os.path.join(ASSETS_DIR, "maintenance_records.csv")
CONDITION_REPORT    = os.path.join(ASSETS_DIR, "condition_report.csv")
ASSET_CONFIG        = os.path.join(ASSETS_DIR, "asset_config.csv")
CLIMATE_DATA        = os.path.join(CLIMATE_DIR, "climate_data.csv")

# S1 output
S1_CLASSIFICATION = os.path.join(ASSETS_DIR, "classification_result.csv")
# S2 output
S2_HEALTH_INDEX   = os.path.join(ASSETS_DIR, "asset_health_index.csv")
# S3 outputs
S3_VALIDATION_SUMMARY = os.path.join(BASELINES_DIR, "validation_summary.csv")
def s3_baseline_file(asset_id): return os.path.join(BASELINES_DIR, f"{asset_id}_baseline.csv")
# S5 outputs
S5_MODEL_REGISTRY  = os.path.join(MODELS_DIR, "model_registry.csv")
S5_TRAINING_MATRIX = os.path.join(MODELS_DIR, "training_matrix.csv")
def s5_model_file(class_label):
    clean = class_label.replace("brick:", "").replace(":", "_")
    return os.path.join(MODELS_DIR, f"rf_model_{clean}.pkl")
# S4 outputs
S4_DEGRADATION = os.path.join(DEGRADATION_DIR, "degradation_results.csv")
S4_SUMMARY     = os.path.join(DEGRADATION_DIR, "prognostics_summary.csv")

def ensure_directories():
    for d in [ASSETS_DIR, OPERATIONAL_DIR, CLIMATE_DIR, BASELINES_DIR, MODELS_DIR, DEGRADATION_DIR]:
        os.makedirs(d, exist_ok=True)
