import pandas as pd
import os

folder = 'CONSOLIDADOS'
files = [f for f in os.listdir(folder) if f.endswith('.csv')]

for file in files:
    path = os.path.join(folder, file)
    print(f"Limpiando {file}...")
    
    # Leer el archivo buscando la fila que contiene 'Period'
    found_header = False
    data_lines = []
    
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
        for i, line in enumerate(lines):
            if 'Period' in line and not found_header:
                # Comprobar que no sea una línea de metadatos (como "Period frequency")
                if 'Period,' in line or line.strip() == 'Period':
                    data_lines = lines[i:]
                    found_header = True
                    break
    
    if found_header:
        # Escribir el archivo de nuevo solo con las líneas de datos
        with open(path, 'w', encoding='utf-8') as f:
            f.writelines(data_lines)
        print(f"  Header encontrado y limpiado.")
    else:
        print(f"  ADVERTENCIA: No se encontró la cabecera 'Period' en {file}")

print("\n--- Limpieza de consolidados finalizada ---")
