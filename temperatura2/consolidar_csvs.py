import os
import pandas as pd
import re
from datetime import datetime

base_dir = r"C:\YO\SecondBrain-vault\CIMBA\temperatura2"
output_dir = os.path.join(base_dir, "CONSOLIDADOS")

if not os.path.exists(output_dir):
    os.makedirs(output_dir)

def get_file_info(filename):
    # Regex to extract asset name and date range
    # Example: FCU_01_01_Temperature_2025-01-01_00_00-2025-01-31_23_55.csv
    # Example: AHU_1_Temperatures_2025-01-01_00_00-2025-01-31_23_55.csv
    match = re.search(r"^(.*?)(?:_Temperature|_Temperatures)_(\d{4}-\d{2}-\d{2})_.*?-(\d{4}-\d{2}-\d{2})", filename)
    if match:
        asset = match.group(1).replace("_", " ")
        start_date = match.group(2)
        end_date = match.group(3)
        return asset, start_date, end_date
    return None, None, None

files = []
for f in os.listdir(base_dir):
    if f.endswith(".csv"):
        path = os.path.join(base_dir, f)
        asset, start, end = get_file_info(f)
        if asset:
            files.append({
                "filename": f,
                "path": path,
                "asset": asset,
                "start": start,
                "end": end,
                "mtime": os.path.getmtime(path),
                "size": os.path.getsize(path)
            })

df_files = pd.DataFrame(files)

# CLEANUP LOGIC
to_delete = []

# Rule 1: FCU 09/01 Feb 2025 partial (1 day)
# FCU_09_01_Temperature_2025-02-01_00_00-2025-02-02_00_00.csv
idx_partial = df_files[(df_files['asset'] == 'FCU 09 01') & (df_files['start'] == '2025-02-01') & (df_files['end'] == '2025-02-02')].index
to_delete.extend(df_files.loc[idx_partial, 'filename'].tolist())

# Rule 2: General duplicates (same asset, same start date)
# We keep the one with the latest mtime
assets = df_files['asset'].unique()
for asset in assets:
    asset_df = df_files[df_files['asset'] == asset]
    starts = asset_df['start'].unique()
    for s in starts:
        duplicates = asset_df[asset_df['start'] == s]
        if len(duplicates) > 1:
            # Sort by mtime descending
            sorted_dups = duplicates.sort_values('mtime', ascending=False)
            # Keep the first one, mark others for deletion
            to_delete.extend(sorted_dups.iloc[1:]['filename'].tolist())

# Remove marked files from our processing list
df_clean = df_files[~df_files['filename'].isin(to_delete)].copy()

print(f"Archivos identificados para eliminar: {len(to_delete)}")
for f in to_delete:
    print(f" - {f}")

# CONSOLIDATION
for asset in assets:
    asset_files = df_clean[df_clean['asset'] == asset].sort_values('start')
    if asset_files.empty:
        continue
    
    print(f"Consolidando {asset} ({len(asset_files)} archivos)...")
    
    consolidated_path = os.path.join(output_dir, f"{asset.replace(' ', '_')}_Consolidado.csv")
    
    first_file = True
    with open(consolidated_path, 'w', encoding='utf-8') as outfile:
        for _, row in asset_files.iterrows():
            with open(row['path'], 'r', encoding='utf-8') as infile:
                lines = infile.readlines()
                if len(lines) < 7:
                    continue
                
                # For the first file of the asset, keep the header (Line 7)
                if first_file:
                    outfile.write(lines[6]) # Line 7 is index 6
                    first_file = False
                
                # Append data (Line 8 onwards)
                if len(lines) > 7:
                    outfile.writelines(lines[7:])

print("\n¡Proceso completado!")
