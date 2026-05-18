import pandas as pd
import os

input_dir = 'CONSOLIDADOS'
output_dir = 'PREPROCESADOS'

if not os.path.exists(output_dir):
    os.makedirs(output_dir)

files = [f for f in os.listdir(input_dir) if f.endswith('.csv')]

for file in files:
    input_path = os.path.join(input_dir, file)
    print(f"Procesando {file}...")
    
    # Leer CSV saltando líneas corruptas
    try:
        df = pd.read_csv(input_path, on_bad_lines='skip')
    except Exception as e:
        print(f"Error crítico leyendo {file}: {e}")
        continue
    
    # Convertir Period a datetime, forzando errores a NaT (Not a Time)
    df['Period'] = pd.to_datetime(df['Period'], errors='coerce')
    
    # Eliminar filas donde la fecha no sea válida (como filas de metadatos que sobrevivieron)
    df = df.dropna(subset=['Period'])
    
    # Limpiar nombres de columnas (quitar espacios y comillas accidentales)
    df.columns = [col.strip().replace('"', '') for col in df.columns]
    
    # Identificar columnas de temperatura (que contengan "Temperature")
    temp_cols = [col for col in df.columns if 'Temperature' in col]
    
    if not temp_cols:
        print(f"No se encontraron columnas de temperatura en {file}. Saltando.")
        continue
        
    # Agrupar por fecha (día)
    df['Date'] = df['Period'].dt.date
    
    # Definir agregaciones: mean, max, min
    agg_dict = {}
    for col in temp_cols:
        # Asegurar que los datos sean numéricos para evitar errores en agregación
        df[col] = pd.to_numeric(df[col], errors='coerce')
        agg_dict[col] = ['mean', 'max', 'min']
        
    daily_df = df.groupby('Date').agg(agg_dict)
    
    # Aplanar las columnas de multi-índice (ej: 'Temp' 'mean' -> 'Temp_mean')
    daily_df.columns = [f"{col}_{stat}" for col, stat in daily_df.columns]
    daily_df = daily_df.reset_index()
    
    # Guardar el archivo
    output_filename = file.replace('_Consolidado.csv', '_Diario.csv')
    output_path = os.path.join(output_dir, output_filename)
    daily_df.to_csv(output_path, index=False)
    print(f"Completado: {output_filename}")

print("\n--- Procesamiento diario finalizado ---")
