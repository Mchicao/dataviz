"""
Módulo para mapear tipos de visuales de Tableau a Power BI.
Genera JSON de visuales Power BI basados en visuales de Tableau.

Portado desde la versión alternativa con mejoras en modularidad y data binding.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

# Mapeo de tipos de visuales Tableau -> Power BI
MAPEO_TIPOS_VISUALES = {
    "bar": "clusteredColumnChart",
    "line": "lineChart",
    "circle": "scatterChart",
    "square": "scatterChart",
    "area": "areaChart",
    "text": "tableEx",
    "map": "map",
    "pie": "pieChart",
    "polygon": "shapeMap",
    "gantt": "table",
    "box": "clusteredColumnChart",
    "shape": "card",
    "automatic": "columnChart",
    "crosstab": "pivotTable",
    "heatmap": "pivotTable",
}


def mapear_tipo_visual_tableau_a_pbi(tipo_tableau: str) -> str:
    """
    Mapea un tipo de marca de Tableau a su equivalente en Power BI.

    Args:
        tipo_tableau: Tipo de marca de Tableau (bar, line, circle, etc.)

    Returns:
        Tipo de visual de Power BI
    """
    tipo_lower = tipo_tableau.lower() if tipo_tableau else "desconocido"
    return MAPEO_TIPOS_VISUALES.get(tipo_lower, "columnChart")


def generar_visual_pbi_basico(
    tipo_pbi: str,
    campos_filas: list[str],
    campos_columnas: list[str],
    campos_medidas: list[str],
    campos_color: list[str] | None = None,
) -> dict:
    """
    Genera el JSON básico de un visual de Power BI.

    Args:
        tipo_pbi: Tipo de visual de Power BI
        campos_filas: Campos para el eje X/filas
        campos_columnas: Campos para el eje Y/columnas
        campos_medidas: Medidas a mostrar
        campos_color: Campos para color (opcional)

    Returns:
        Diccionario con la estructura del visual en formato PBIR
    """
    campos_color = campos_color or []

    visual = {
        "visualType": tipo_pbi,
        "config": {},
        "layout": {"x": 0, "y": 0, "z": 0, "width": 600, "height": 400},
    }

    # Configuración específica según tipo de visual
    if tipo_pbi == "columnChart":
        visual["config"] = {
            "dataRoles": {
                "Category": campos_filas[:1] if campos_filas else [],
                "Y": campos_medidas[:1] if campos_medidas else [],
            },
            "dataViewMappings": [
                {
                    "categorical": {
                        "categories": {"for": {"in": "Category"}},
                        "values": {"for": {"in": "Y"}},
                    }
                }
            ],
        }

    elif tipo_pbi == "lineChart":
        visual["config"] = {
            "dataRoles": {
                "Category": campos_filas[:1] if campos_filas else [],
                "Y": campos_medidas[:1] if campos_medidas else [],
            },
            "dataViewMappings": [
                {
                    "categorical": {
                        "categories": {"for": {"in": "Category"}},
                        "values": {"for": {"in": "Y"}},
                    }
                }
            ],
        }

    elif tipo_pbi == "scatterChart":
        visual["config"] = {
            "dataRoles": {
                "X": campos_filas[:1] if campos_filas else [],
                "Y": campos_columnas[:1] if campos_columnas else [],
                "Size": campos_medidas[:1] if campos_medidas else [],
            }
        }

    elif tipo_pbi in ("tableEx", "table"):
        visual["config"] = {
            "dataRoles": {"Values": campos_filas + campos_columnas + campos_medidas}
        }

    elif tipo_pbi == "pieChart":
        visual["config"] = {
            "dataRoles": {
                "Category": campos_filas[:1] if campos_filas else [],
                "Y": campos_medidas[:1] if campos_medidas else [],
            }
        }

    elif tipo_pbi == "card":
        visual["config"] = {"dataRoles": {"Fields": campos_medidas[:1] if campos_medidas else []}}

    # Agregar campos de color si existen
    if campos_color and "dataRoles" in visual["config"]:
        visual["config"]["dataRoles"]["Color"] = campos_color[:1]

    return visual


class VisualMapper:
    """Clase wrapper para compatibilidad con código existente."""

    def __init__(self):
        self.chart_map = MAPEO_TIPOS_VISUALES

    def _get_pbi_id(self) -> str:
        return str(uuid.uuid4()).replace("-", "")[:20]

    def map_worksheet(self, worksheet_xml, datasource_name: str) -> dict:
        """Genera el JSON de un visual de Power BI leyendo la hoja de Tableau."""

        # 1. Detectar tipo de gráfico
        graph_style = worksheet_xml.find(".//style/graph")
        tableau_type = graph_style.get("type") if graph_style is not None else "text"
        pbi_type = mapear_tipo_visual_tableau_a_pbi(tableau_type)

        # 2. Extraer Columnas y Filas
        deps = worksheet_xml.findall(
            f".//datasource-dependencies[@datasource='{datasource_name}']//column"
        )

        rows = []
        values = []

        for dep in deps:
            raw_name = dep.get("name")
            role = dep.get("role")
            dtype = dep.get("datatype")

            clean_name = raw_name.replace("[", "").replace("]", "")

            field_obj = {
                "field": {
                    "Column": {
                        "Expression": {"SourceRef": {"Entity": datasource_name}},
                        "Property": clean_name,
                    }
                },
                "queryRef": f"{datasource_name}.{clean_name}",
            }

            if role == "measure" or dtype in ["real", "integer"]:
                field_obj["field"] = {
                    "Aggregation": {
                        "Expression": {
                            "Column": {
                                "Expression": {"SourceRef": {"Entity": datasource_name}},
                                "Property": clean_name,
                            }
                        },
                        "Function": 0,
                    }
                }
                values.append(field_obj)
            else:
                rows.append(field_obj)

        # 3. Construir QueryState
        if pbi_type in ("tableEx", "table", "pivotTable"):
            query_state = {
                "Rows": {"projections": rows},
                "Values": {"projections": values},
            }
        else:
            query_state = {
                "Category": {"projections": rows},
                "Y": {"projections": values},
            }

        # 4. JSON Final del Visual
        visual_json = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/1.0.0/schema.json",
            "name": self._get_pbi_id(),
            "visualType": pbi_type,
            "position": {"x": 10, "y": 10, "z": 0, "width": 1280, "height": 720},
            "visual": {
                "title": {
                    "show": True,
                    "text": worksheet_xml.get("name", "Visual Migrado"),
                },
                "query": {"queryState": query_state},
                "drillFilterOtherVisuals": True,
            },
            "metadata": {
                "source": "Tableau Migration",
                "originalType": tableau_type,
                "mappedType": pbi_type,
            },
        }
        return visual_json
