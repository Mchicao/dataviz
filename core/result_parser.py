"""
Result Parser - Parsea resultados de queries ejecutadas manualmente (Offline Mode).
Puede usar LLM si está configurado, o parsing básico de CSV/JSON.
"""

import pandas as pd

from core.contracts.dataset import Dataset


def parse_query_result(result: Dataset | dict[str, object]) -> dict[str, object]:
    """Normaliza el resultado neutral sin descartar diagnósticos."""
    if isinstance(result, Dataset):
        rows = [dict(row) for row in result.rows]
        return {
            "status": "success",
            "columns": list(result.columns),
            "data": rows,
            "row_count": len(rows),
            "diagnostics": [],
        }
    if not isinstance(result, dict):
        return {
            "status": "error",
            "error": "invalid_result",
            "message": "El resultado de consulta no es un objeto válido.",
            "diagnostics": [],
        }
    return dict(result)


def parse_offline_result(uploaded_file):
    """
    Intenta interpretar el archivo subido para extraer el conteo de filas.
    Retorna un diccionario con {'status': 'success', 'count': int} o error.
    """
    try:
        # 1. Determinar tipo de archivo
        filename = uploaded_file.name.lower()

        df = None
        if filename.endswith(".csv"):
            df = pd.read_csv(uploaded_file)
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(uploaded_file)
        elif filename.endswith(".json"):
            df = pd.read_json(uploaded_file)
        elif filename.endswith(".txt"):
            # Intento básico CSV con detección de separador
            try:
                df = pd.read_csv(uploaded_file, sep=None, engine="python")
            except Exception:
                # Si falla, leer texto raw para diagnóstico
                return {"status": "error", "message": "No se pudo parsear el TXT. Usa CSV o Excel."}

        if df is not None:
            # Estrategia 1: Buscar columna llamada "count", "count(*)", "cnt"
            cols = [c.lower() for c in df.columns]

            target_col = None
            for candidate in ["count", "count(*)", "cnt", "total", "filas"]:
                if candidate in cols:
                    target_col = df.columns[cols.index(candidate)]
                    break

            if target_col:
                val = df[target_col].iloc[0]
                return {"status": "success", "count": int(val)}

            # Estrategia 2: Si es 1x1, asumir que es el count
            if df.size == 1:
                return {"status": "success", "count": int(df.iloc[0, 0])}

            return {
                "status": "error",
                "message": "No se encontró columna de conteo (count, cnt, etc).",
            }

    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "error", "message": "Formato de archivo no reconocido."}
