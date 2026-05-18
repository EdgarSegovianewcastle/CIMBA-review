# 🧠 CIMBA - Contexto del Dataset de Temperaturas (The Spark)

Este documento proporciona el contexto técnico necesario para comprender los datos de temperatura contenidos en esta carpeta.

## 📌 Información General
*   **Proyecto:** CIMBA (Framework de Mantenimiento Predictivo).
*   **Edificio:** The Spark (Newcastle Helix).
*   **Fuente de Datos:** Portal **Demand Logic**.
*   **Período:** Enero 2025 - Marzo 2026 (15 meses).
*   **Frecuencia:** 5 minutos (5-minutely).

## 📂 Estructura de la Carpeta
*   `/CONSOLIDADOS/`: Contiene un único archivo CSV por activo que une todos los meses (Ene 2025 - Mar 2026).
*   `/PREPROCESADOS/`: Contiene datos agrupados por **día**. Para cada columna de temperatura original, se calculan la **media (_mean)**, **máxima (_max)** y **mínima (_min)**. Ideal para análisis de tendencias a largo plazo.
*   Archivos en raíz: Los archivos mensuales originales descargados del portal.

## 🛠️ Procesamiento y Limpieza (Abril 2026)
Se realizó una limpieza profunda para eliminar redundancias:
1.  **Eliminación de Duplicados:** Se descartaron versiones antiguas o descargas parciales (marcadas con sufijos como `(1)`, `(2)` o fechas de modificación más viejas).
2.  **Consolidación Cronológica:** Los archivos consolidados mantienen una única cabecera y datos limpios desde la línea 2.
3.  **Filtrado de Errores:** Se eliminó específicamente un archivo de la `FCU 09/01` de Febrero 2025 que solo contenía 1 día de datos, priorizando la descarga mensual completa.

## ⚠️ Explicación de Gaps (Datos en 0 o Vacíos)
Es fundamental entender que existen huecos en los datos que **no son errores de procesamiento**, sino fallos de registro en el origen (Demand Logic):

1.  **Caída Sistémica (Diciembre 2025 - Febrero 2026):**
    *   **Afectados:** Pisos 1, 2 y 3 (Fan Coil Units).
    *   **Causa:** Hubo una pérdida de comunicación/registro en el BMS/Portal durante este período para estos activos específicos.
    *   **Estado:** No recuperable. Las AHUs (Air Handling Units) y la AHU Kitchen **SÍ** tienen datos en este período.
2.  **FCU 06/01 y FCU 07/01 (Abril/Mayo 2025):**
    *   Existen archivos para estos meses pero están mayormente vacíos o con datos en "0". Representa un fallo puntual del sensor/controlador en esas fechas.

## 📊 Formato de Datos
Cada CSV consolidado contiene:
*   `Period`: Timestamp (YYYY-MM-DD HH:MM).
*   `Control Temperature`: Temperatura de consigna/control.
*   `Entering/Leaving Temperatures`: Temperaturas de entrada y salida de aire/agua.
*   `Setpoints`: Puntos de consigna de frío/calor.

