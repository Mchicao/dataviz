"""
Módulo para generar visuales Power BI en formato PBIR.
Integra lógica avanzada de mapeo visual (Tableau -> Power BI).

Portado desde la versión alternativa.
"""

import json
import logging
import os
import re
import secrets
from typing import Any

logger = logging.getLogger(__name__)


class VisualMapper:
    """Clase para mapear tipos y campos de Tableau a Power BI."""

    CHART_MAP = {
        "bar": "clusteredBarChart",
        "line": "lineChart",
        "pie": "pieChart",
        "scatter": "scatterChart",
        "circle": "scatterChart",
        "text": "pivotTable",
        "text-table": "pivotTable",
        # Una marca square con filas, columnas y texto es la tabla resaltada
        # de Tableau. Un treemap pierde ambos ejes y termina como un bloque de
        # color; una matriz conserva la grilla y permite formato condicional.
        "square": "pivotTable",
        "area": "areaChart",
        "gantt": "clusteredBarChart",
        "automatic": "columnChart",
        # El visual geográfico estándar de Desktop es `map`; `mapVisual` se
        # interpreta como visual personalizado faltante.
        "multipolygon": "filledMap",
        "polygon": "filledMap",
        "point": "map",
        "geography": "map",
        "map": "map",
        # Highlight table / heat map
        "highlight": "pivotTable",
        "heat": "matrix",
        # Histogram / bin
        "histogram": "columnChart",
        # Bullet chart
        "bullet": "clusteredBarChart",
        # Box-and-whisker
        "boxplot": "clusteredBarChart",
    }

    def __init__(
        self,
        visual_id: str,
        hoja_tableau: dict,
        tabla_pbi: str | None,
        columnas_map: dict[str, str],
        measures_map: dict[str, str] | None = None,
    ):
        self.visual_id = visual_id
        self.hoja = hoja_tableau
        self.tabla_pbi = tabla_pbi
        self.columnas_map = columnas_map
        self.measures_map = measures_map or {}
        self.pbi_type = self._map_visual_type(hoja_tableau)
        self.all_filters: list[dict] = []
        self.dropped_fields: list[str] = []

    def _map_visual_type(self, hoja_tableau: dict[str, Any]) -> str:
        """Elige visual por familia Tableau y roles semánticos.

        Mapeo genérico (marca + roles), nunca por nombre de hoja ni de demo.
        Para marcas tabulares se distingue la tabla plana (sin dimensiones de
        fila/columna -> ``tableEx``) de la tabla dinámica (-> ``pivotTable``)
        según el contrato PBIR por familia.
        """
        if "Tipo_Visual" not in hoja_tableau:
            return "pivotTable"
        tableau_type = str(hoja_tableau.get("Tipo_Visual", "unknown")).lower()

        # Familia tabular: sólo hay matriz cuando Tableau tiene ambos ejes.
        # Varias dimensiones consecutivas en Rows representan columnas de una
        # tabla plana, no niveles expandibles de una jerarquía de Power BI.
        if tableau_type in {"text", "text-table"}:
            has_rows = bool(hoja_tableau.get("Campos_Filas"))
            has_columns = bool(hoja_tableau.get("Campos_Columnas"))
            if not has_rows and not has_columns and len(hoja_tableau.get("Campos_Medidas", [])) == 1:
                return "card"
            if not has_rows and not has_columns and hoja_tableau.get("Texto_Titulo"):
                return "textbox"
            return "pivotTable" if has_rows and has_columns else "tableEx"

        fields = [
            str(field).lower()
            for field in (
                hoja_tableau.get("Campos_Filas", []) + hoja_tableau.get("Campos_Columnas", [])
            )
        ]
        has_measure = bool(hoja_tableau.get("Campos_Medidas", []))
        has_latitude = any("latitude" in field or "latitud" in field for field in fields)
        has_longitude = any("longitude" in field or "longitud" in field for field in fields)
        has_date = any("date" in field or "fecha" in field for field in fields)

        # Marca explícita no tabular: respetarla salvo degradaciones de contrato.
        # En particular, una marca ``line`` sólo tiene sentido sobre un eje
        # temporal (o de Measure Values); sobre una dimensión categórica se
        # degrada a barras horizontales para no producir un连线 sin orden real.
        if tableau_type not in {"automatic", "auto"}:
            base = self.CHART_MAP.get(tableau_type, "pivotTable")
            if tableau_type == "circle" and has_date and has_measure:
                return "lineChart"
            # MAP-ORIENT-02: Tableau puede serializar barras categóricas como
            # ``line``; sin eje temporal, Desktop debe recrearlas horizontalmente.
            if tableau_type == "line" and not has_date:
                return "clusteredBarChart"
            return base

        if has_latitude and has_longitude:
            return "map"
        if has_date and has_measure:
            return "lineChart"
        if fields and has_measure:
            # ORIENT-01: Tableau dibuja la dimensión de Rows en el eje Y y la
            # medida de Columns en X. Power BI debe usar barras horizontales.
            measure_rows = bool(hoja_tableau.get("Campos_Medidas_Filas"))
            measure_columns = bool(hoja_tableau.get("Campos_Medidas_Columnas"))
            if hoja_tableau.get("Campos_Filas") and (measure_columns or not measure_rows):
                return "clusteredBarChart"
            return "columnChart"
        if has_measure:
            return "card"
        return "pivotTable"

    def _get_pbi_field_name(self, tableau_field: str) -> str | None:
        """Devuelve el nombre mapeado de la columna PBI con limpieza robusta."""
        # 1. Limpieza básica
        clean = tableau_field.replace("[", "").replace("]", "")

        # 2. Check directo
        if clean in self.columnas_map:
            return self.columnas_map[clean]

        # 3. Limpieza avanzada (Tableau Internal Names)
        # patterns: none:FIELD:nk, msg:FIELD:qk, sum:FIELD:qk
        # federated.xyz.sum:FIELD:qk

        candidate = clean

        # Si tiene federated, tomar la ultima parte significativa
        # Ejemplo: federated.0x5...sum:importe:qk -> sum:importe:qk
        if "federated." in candidate:
            parts = candidate.split(".")
            # Buscar parte que tenga : (indicador de campo)
            for p in reversed(parts):
                if ":" in p:
                    candidate = p
                    break

        # Si tiene patterns :XXX:
        if ":" in candidate:
            parts = candidate.split(":")
            # Heuristic: usually prefix:name:suffix (none:libro_mayor:nk)
            # or sum:importe:qk
            if len(parts) >= 2:
                # Tomar el segmento que parece el nombre (usualmente el índice 1)
                # O el penúltimo si hay muchos
                candidate = parts[1] if len(parts) >= 3 else parts[-1]

        # 4. Check candidato limpio
        if candidate in self.columnas_map:
            return self.columnas_map[candidate]

        # 5. Check Case Insensitive y Fuzzy basic
        candidate_lower = candidate.lower()
        for k, v in self.columnas_map.items():
            if k.lower() == candidate_lower:
                return v
            if k.lower() == clean.lower():
                return v

        # 6. Resolución de medidas/parámetros materializados en el modelo.
        #    Los visuales referencian cálculos/parámetros por su nombre interno.
        if self.measures_map and clean in self.measures_map:
            mapped_measure = self.measures_map[clean]
            if self._has_semantic_object_metadata() and not self._semantic_object_kind(
                mapped_measure
            ):
                self.dropped_fields.append(tableau_field)
                return None
            return mapped_measure

        # 7. Campos especiales Tableau que no son columnas ni medidas pero
        #    que tienen un equivalente funcional o deben silenciarse sin warning.
        # Latitude/Longitude (generated) — Tableau los crea para mapas.
        # Si la tabla tiene State/City/Region, usarlas como proxy geográfico.
        geo_proxy_map = {
            "latitude (generated)": ("State", "City", "Region", "Postal Code"),
            "longitude (generated)": ("State", "City", "Region", "Postal Code"),
        }
        for geo_key, proxies in geo_proxy_map.items():
            if clean.lower() == geo_key:
                for proxy in proxies:
                    for col_key, col_val in self.columnas_map.items():
                        if col_key.lower() == proxy.lower():
                            return col_val

        # Multiple Values — control multi-valor de parámetro Tableau.
        # No tiene equivalente en PBI; silenciar sin warning (es un artifact).
        source_name = str(self.hoja.get("Fuente_Datos", "")).replace("[", "").replace("]", "").strip()
        tableau_artifacts = {
            "measure names",
            "measure values",
            "multiple values",
        }
        if (source_name and clean.casefold() == source_name.casefold()) or clean.lower() in tableau_artifacts:
            self.dropped_fields.append(tableau_field)
            return None

        logger.warning("Campo Tableau no materializado, se omite del visual: %s", tableau_field)
        self.dropped_fields.append(tableau_field)
        return None

    def _build_column_projection(self, tableau_field: str) -> dict[str, Any] | None:
        """Construye proyección para una columna (dimensión)."""
        pbi_field = self._get_pbi_field_name(tableau_field)
        if not pbi_field:
            return None
        semantic_kind = self._semantic_object_kind(pbi_field)
        if semantic_kind == "measure":
            logger.warning(
                "Dimensión Tableau omitida: %s fue materializada como %s en %s",
                pbi_field,
                semantic_kind,
                self.tabla_pbi,
            )
            self.dropped_fields.append(tableau_field)
            return None
        entity = self.tabla_pbi if self.tabla_pbi else "TablaDesconocida"

        field_def = {
            "Column": {
                "Expression": {"SourceRef": {"Entity": entity}},
                "Property": pbi_field,
            }
        }

        self.all_filters.append(
            {"name": secrets.token_hex(10), "field": field_def, "type": "Categorical"}
        )

        return {
            "field": field_def,
            "queryRef": f"{entity}.{pbi_field}",
            "nativeQueryRef": self._display_label(tableau_field, pbi_field),
            "active": True,
        }

    def _build_aggregation_projection(
        self, tableau_field: str, agg_function: int = 0
    ) -> dict[str, Any] | None:
        """Construye proyección para un valor agregado.

        Function: 0=Sum, 1=Average, 2=Min, 3=Max, 4=Count, 5=CountNonNull
        """
        pbi_field = self._get_pbi_field_name(tableau_field)
        if not pbi_field:
            return None
        if self._semantic_object_kind(pbi_field) == "measure":
            return self._build_measure_projection(pbi_field)
        entity = self.tabla_pbi if self.tabla_pbi else "TablaDesconocida"

        field_def = {
            "Aggregation": {
                "Expression": {
                    "Column": {
                        "Expression": {"SourceRef": {"Entity": entity}},
                        "Property": pbi_field,
                    }
                },
                "Function": agg_function,
            }
        }

        self.all_filters.append(
            {"name": secrets.token_hex(10), "field": field_def, "type": "Advanced"}
        )

        agg_name = {0: "Sum", 1: "Average", 2: "Min", 3: "Max", 4: "Count"}.get(agg_function, "Sum")

        return {
            "field": field_def,
            "queryRef": f"{agg_name}({entity}.{pbi_field})",
            "nativeQueryRef": self._display_label(tableau_field, f"{agg_name} de {pbi_field}"),
            "active": True,
        }

    def _build_measure_projection(self, measure_name: str) -> dict[str, Any]:
        """Construye proyección para una medida (cálculo/parámetro) del modelo."""
        entity = self.tabla_pbi if self.tabla_pbi else "TablaDesconocida"

        field_def = {
            "Measure": {
                "Expression": {"SourceRef": {"Entity": entity}},
                "Property": measure_name,
            }
        }

        return {
            "field": field_def,
            "queryRef": f"{entity}.{measure_name}",
            "nativeQueryRef": self._display_label(measure_name, measure_name),
            "active": True,
        }

    def _build_encoding_projection(self, tableau_field: str) -> dict[str, Any] | None:
        """Proyecta un estante de marca sin perder si es medida o dimensión."""
        clean = tableau_field.replace("[", "").replace("]", "")
        if self.measures_map and clean in self.measures_map:
            return self._build_measure_projection(self.measures_map[clean])
        measure_fields = {
            field.replace("[", "").replace("]", "").lower()
            for field in self.hoja.get("Campos_Medidas", [])
        }
        if clean.lower() in measure_fields:
            return self._build_aggregation_projection(tableau_field, agg_function=0)
        return self._build_column_projection(tableau_field)

    def _encoding_projections(self, *roles: str) -> list[dict[str, Any]]:
        """Devuelve proyecciones únicas para los roles de marca indicados."""
        projections: list[dict[str, Any]] = []
        seen_query_refs: set[str] = set()
        encodings = self.hoja.get("Encodings", {})
        if not isinstance(encodings, dict):
            return projections
        for role in roles:
            fields = encodings.get(role, [])
            if not isinstance(fields, list):
                continue
            for field in fields:
                if not isinstance(field, str):
                    continue
                projection = self._build_encoding_projection(field)
                if projection and projection["queryRef"] not in seen_query_refs:
                    seen_query_refs.add(projection["queryRef"])
                    projections.append(projection)
        return projections

    @staticmethod
    def _unique_projections(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Conserva el primer rol equivalente para evitar referencias PBIR duplicadas."""
        unique: list[dict[str, Any]] = []
        seen_query_refs: set[str] = set()
        for group in groups:
            for projection in group:
                query_ref = projection.get("queryRef")
                if isinstance(query_ref, str) and query_ref not in seen_query_refs:
                    seen_query_refs.add(query_ref)
                    unique.append(projection)
        return unique

    @staticmethod
    def _column_projections(projections: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Limita roles categóricos a columnas, no agregaciones ni medidas."""
        return [
            projection
            for projection in projections
            if isinstance(projection.get("field"), dict) and "Column" in projection["field"]
        ]

    def _semantic_object_kind(self, pbi_field: str) -> str | None:
        return self.columnas_map.get(f"__kind__:{pbi_field.lower()}")

    def _has_semantic_object_metadata(self) -> bool:
        return any(str(key).startswith("__kind__:") for key in self.columnas_map)

    def _display_label(self, tableau_field: str, fallback: str) -> str:
        labels = self.hoja.get("Etiquetas_Campos", {})
        if not isinstance(labels, dict):
            return fallback
        clean = tableau_field.replace("[", "").replace("]", "")
        for field, label in labels.items():
            if str(field).lower() == clean.lower() and label:
                return str(label)
        return fallback

    @staticmethod
    def _pbi_string_literal(value: str) -> str:
        """Construye un literal seguro para un filtro categórico PBIR."""
        return "'" + value.replace("'", "''") + "'"

    def _add_tableau_categorical_filters(self) -> None:
        """Traduce filtros categóricos explícitos sin asumir filtros de rango.

        Los filtros de fecha y rango requieren resolver tipos y granularidad del
        modelo de destino. Se conservan en el IR y se registran para revisión,
        en lugar de escribir una condición PBIR que cambie su significado.
        """
        entity = self.tabla_pbi if self.tabla_pbi else "TablaDesconocida"
        for tableau_filter in self.hoja.get("Filtros", []):
            if not isinstance(tableau_filter, dict):
                continue
            values = tableau_filter.get("values", [])
            if tableau_filter.get("kind") != "categorical" or not values:
                if tableau_filter.get("field"):
                    logger.info(
                        "Filtro Tableau conservado para revisión manual: %s (%s)",
                        tableau_filter["field"],
                        tableau_filter.get("kind", "unknown"),
                    )
                continue

            tableau_field_name = str(tableau_filter.get("field", ""))
            clean_field_name = tableau_field_name.replace("[", "").replace("]", "")
            field_name = self._get_pbi_field_name(tableau_field_name)
            if not field_name:
                continue

            mapped_measure = self.measures_map.get(clean_field_name) if self.measures_map else None
            actual_kind = self._semantic_object_kind(field_name)
            if self._has_semantic_object_metadata() and actual_kind is None:
                logger.warning(
                    "Filtro Tableau omitido: %s no existe en la tabla %s",
                    field_name,
                    entity,
                )
                continue
            if actual_kind in {"measure", "calculatedcolumn"}:
                logger.warning(
                    "Filtro categórico Tableau omitido: %s es %s y no admite In",
                    field_name,
                    actual_kind,
                )
                continue
            field_kind = "Column"
            if actual_kind is None and mapped_measure:
                # Compatibilidad con fixtures/manual maps sin inventario TMDL.
                available_objects = set(self.columnas_map.values())
                if mapped_measure not in available_objects:
                    logger.warning(
                        "Filtro Tableau omitido: la medida %s no existe en la tabla %s",
                        mapped_measure,
                        entity,
                    )
                    continue
                field_kind = "Measure"
            field_def = {
                field_kind: {
                    "Expression": {"SourceRef": {"Entity": entity}},
                    "Property": field_name,
                }
            }
            source_name = "t"
            literal_values = []
            for value in values:
                if str(value).lower() == "%null%":
                    # Tableau %null% puede materializarse como BLANK o cadena
                    # vacía según el conector/CSV. Cubrir ambas representaciones.
                    literal_values.extend(["null", "''"])
                else:
                    literal_values.append(self._pbi_string_literal(str(value)))
            in_condition = {
                "In": {
                    "Expressions": [
                        {
                            field_kind: {
                                "Expression": {"SourceRef": {"Source": source_name}},
                                "Property": field_name,
                            }
                        }
                    ],
                    "Values": [
                        [{"Literal": {"Value": literal_value}}] for literal_value in literal_values
                    ],
                }
            }
            condition = (
                {"Not": {"Expression": in_condition}}
                if tableau_filter.get("operator") == "exclude"
                else in_condition
            )
            self.all_filters.append(
                {
                    "name": secrets.token_hex(10),
                    "field": field_def,
                    "type": "Categorical",
                    "filter": {
                        "Version": 2,
                        "From": [{"Name": source_name, "Entity": entity, "Type": 0}],
                        "Where": [{"Condition": condition}],
                    },
                    "howCreated": "User",
                }
            )

    def get_visual_json(self, x: int, y: int, w: int, h: int, z: int = 1) -> dict[str, Any]:
        """Genera el JSON completo del visual siguiendo la estructura PBI."""
        self.all_filters = []

        rows_proj = []
        cols_proj = []
        vals_proj = []

        for f in self.hoja.get("Campos_Filas", []):
            if projection := self._build_column_projection(f):
                rows_proj.append(projection)

        for c in self.hoja.get("Campos_Columnas", []):
            if projection := self._build_column_projection(c):
                cols_proj.append(projection)

        def build_value_projection(measure_name: str) -> dict[str, Any] | None:
            """Construye una proyección numérica para una medida Tableau."""
            clean = measure_name.replace("[", "").replace("]", "")
            mapped_measure = self.measures_map.get(clean) if self.measures_map else None
            if mapped_measure and (
                self._semantic_object_kind(mapped_measure) == "measure"
                or not self._has_semantic_object_metadata()
            ):
                return self._build_measure_projection(mapped_measure)
            return self._build_aggregation_projection(measure_name, agg_function=0)

        for m in self.hoja.get("Campos_Medidas", []):
            if projection := build_value_projection(m):
                vals_proj.append(projection)

        axis_measure_names = list(
            dict.fromkeys(
                self.hoja.get("Campos_Medidas_Filas", [])
                + self.hoja.get("Campos_Medidas_Columnas", [])
            )
        )
        axis_vals_proj = [
            projection
            for measure_name in axis_measure_names
            if (projection := build_value_projection(measure_name)) is not None
        ]
        if not axis_vals_proj:
            axis_vals_proj = vals_proj

        self._add_tableau_categorical_filters()

        color_proj = self._encoding_projections("color")
        detail_proj = self._encoding_projections("detail", "lod")
        size_proj = self._encoding_projections("size")
        tooltip_proj = self._encoding_projections("tooltip", "text")
        path_proj = self._encoding_projections("path")

        query_state: dict[str, Any] = {}

        if self.pbi_type == "pivotTable":
            # Contrato tabla dinámica: Rows/Columns/Values separados.
            query_state = {
                "Rows": {"projections": rows_proj},
                "Columns": {"projections": cols_proj},
                "Values": {"projections": vals_proj},
            }
        elif self.pbi_type == "tableEx":
            # Contrato tabla plana: un único rol Values (columnas y medidas en
            # orden de presentación). No separa Rows/Columns.
            query_state = {
                "Values": {"projections": self._unique_projections(rows_proj, cols_proj, vals_proj)}
            }
        elif self.pbi_type == "pieChart":
            query_state = {
                "Category": {"projections": (rows_proj + cols_proj)[:1]},
                "Y": {"projections": vals_proj[:1]},
            }
        elif self.pbi_type == "card":
            # Tarjeta de valor único: una sola proyección de medida. El
            # formato (labels/categoryLabels) se aplica tras construir
            # visual_obj, igual que el resto de formatos por tipo.
            query_state = {"Values": {"projections": vals_proj[:1]}}
        elif self.pbi_type in [
            "columnChart",
            "lineChart",
            "areaChart",
            "clusteredBarChart",
        ]:
            # Familia cartesiana (líneas/áreas/barras): Category, Y, Series.
            # Tooltips es opcional: sólo se emite si hay campo Tableau.
            uses_small_multiples = bool(
                rows_proj and cols_proj and axis_measure_names
            )
            query_state = {
                "Category": {"projections": rows_proj[:1] if rows_proj else cols_proj[:1]},
                "Y": {"projections": axis_vals_proj},
                "Series": {
                    "projections": self._unique_projections(
                        [] if uses_small_multiples else (cols_proj if rows_proj else []),
                        self._column_projections(color_proj),
                        self._column_projections(path_proj),
                    )
                },
            }
            if uses_small_multiples:
                query_state["SmallMultiples"] = {"projections": cols_proj[:1]}
            if tooltip_proj:
                query_state["Tooltips"] = {"projections": tooltip_proj}
        elif self.pbi_type == "scatterChart":
            # Contrato dispersión: Category (puede ser vacía) + X e Y como ejes
            # numéricos. Size/Legend/Details/Tooltips son opcionales y NO se
            # inventan sin campo Tableau correspondiente.
            if cols_proj:
                category_proj = cols_proj[:1]
                x_proj = cols_proj[:1]
                y_proj = rows_proj[:1] if rows_proj else vals_proj[:1]
            else:
                category_proj = rows_proj[:1]
                x_proj = vals_proj[:1]
                y_proj = vals_proj[1:2] if len(vals_proj) > 1 else vals_proj[:1]
            query_state = {
                "Category": {"projections": category_proj},
                "X": {"projections": x_proj},
                "Y": {"projections": y_proj},
            }
            if size_proj:
                query_state["Size"] = {"projections": size_proj[:1]}
            if color_proj:
                query_state["Legend"] = {"projections": color_proj[:1]}
            if detail_proj:
                query_state["Details"] = {"projections": detail_proj}
            if tooltip_proj:
                query_state["Tooltips"] = {"projections": tooltip_proj}
        elif self.pbi_type == "map":
            # Contrato mapa estándar: Category (dimensión geográfica tipo
            # Column) + Size (medida). Legend/Tooltips son opcionales.
            query_state = {
                "Category": {"projections": (rows_proj + cols_proj)[:1]},
                "Size": {"projections": size_proj[:1] or vals_proj[:1]},
            }
            if color_proj:
                query_state["Legend"] = {"projections": color_proj[:1]}
            if tooltip_proj:
                query_state["Tooltips"] = {"projections": tooltip_proj}
        elif self.pbi_type == "filledMap":
            # MAP-FILL-03: las regiones se agrupan por ubicación y la medida
            # controla la saturación del relleno coroplético.
            query_state = {
                "Category": {"projections": (rows_proj + cols_proj)[:1]},
                "Y": {"projections": vals_proj[:1]},
            }
            categorical_color = self._column_projections(color_proj)
            if categorical_color:
                query_state["Series"] = {"projections": categorical_color[:1]}
            if tooltip_proj:
                query_state["Tooltips"] = {"projections": tooltip_proj}
        else:
            query_state = {
                "Rows": {"projections": rows_proj},
                "Columns": {"projections": cols_proj},
                "Values": {"projections": vals_proj},
            }

        expansion_states = []
        if self.pbi_type == "pivotTable" and rows_proj:
            levels = []
            for i, row in enumerate(rows_proj):
                level = {"queryRefs": [row["queryRef"]], "isPinned": True}
                if i > 0:
                    level["isCollapsed"] = True
                levels.append(level)
            expansion_states = [{"roles": ["Rows"], "levels": levels, "root": {}}]

        if self.pbi_type == "textbox":
            visual_obj: dict[str, Any] = {
                "visualType": "textbox",
                "objects": {
                    "general": [
                        {
                            "properties": {
                                "paragraphs": [
                                    {
                                        "textRuns": [
                                            {
                                                "value": self.hoja.get("Texto_Titulo", ""),
                                                "textStyle": {
                                                    "fontFamily": "Tableau Book",
                                                    "fontSize": "12pt",
                                                    "fontWeight": "bold",
                                                },
                                            }
                                        ],
                                        "horizontalTextAlignment": "center",
                                    }
                                ]
                            }
                        }
                    ]
                },
                "drillFilterOtherVisuals": True,
            }
        else:
            visual_obj = {
                "visualType": self.pbi_type,
                "query": {"queryState": query_state},
                "drillFilterOtherVisuals": True,
            }

        order_contract = self.hoja.get("Orden") or {}
        order_field = str(order_contract.get("Campo", ""))
        mapped_order_field = self._get_pbi_field_name(order_field) if order_field else None
        if mapped_order_field and self.pbi_type != "textbox":

            def projection_property(projection: dict[str, Any]) -> str:
                """Obtiene la propiedad semántica de una proyección PBIR."""
                field = projection.get("field", {})
                if "Measure" in field:
                    return str(field["Measure"].get("Property", ""))
                if "Column" in field:
                    return str(field["Column"].get("Property", ""))
                aggregation = field.get("Aggregation", {})
                return str(aggregation.get("Expression", {}).get("Column", {}).get("Property", ""))

            sort_projection = next(
                (
                    projection
                    for projection in (*vals_proj, *rows_proj, *cols_proj)
                    if projection_property(projection).casefold() == mapped_order_field.casefold()
                ),
                None,
            )
            if sort_projection:
                visual_obj["query"]["sortDefinition"] = {
                    "sort": [
                        {
                            "field": sort_projection["field"],
                            "direction": order_contract.get("Direccion", "Ascending"),
                        }
                    ],
                    "isDefaultSort": True,
                }

        if expansion_states:
            visual_obj["expansionStates"] = expansion_states

        # Paso 4: Agregar estilos básicos para compatibilidad visual
        if self.pbi_type in {"pivotTable", "tableEx"}:
            visual_obj["objects"] = {
                "columnHeaders": [
                    {
                        "properties": {
                            "fontColor": {
                                "solid": {"color": {"expr": {"Literal": {"Value": "'#FFFFFF'"}}}}
                            },
                            "backColor": {
                                "solid": {"color": {"expr": {"Literal": {"Value": "'#79477F'"}}}}
                            },
                        }
                    }
                ],
                "grid": [
                    {
                        "properties": {
                            "gridVertical": {"expr": {"Literal": {"Value": "true"}}},
                            "gridHorizontal": {"expr": {"Literal": {"Value": "true"}}},
                        }
                    }
                ],
            }

        if self.pbi_type == "card":
            visual_obj["objects"] = {
                "labels": [
                    {
                        "properties": {
                            "labelDisplayUnits": {"expr": {"Literal": {"Value": "1D"}}},
                            "fontSize": {"expr": {"Literal": {"Value": "22D"}}},
                        }
                    }
                ],
                "categoryLabels": [
                    {"properties": {"show": {"expr": {"Literal": {"Value": "false"}}}}}
                ],
            }

        if self.pbi_type in {
            "columnChart",
            "lineChart",
            "areaChart",
            "clusteredBarChart",
        }:
            hidden_axis_title = [
                {"properties": {"showAxisTitle": {"expr": {"Literal": {"Value": "false"}}}}}
            ]
            chart_objects = visual_obj.setdefault("objects", {})
            chart_objects["categoryAxis"] = hidden_axis_title
            chart_objects["valueAxis"] = hidden_axis_title

        # Título del visual basado en nombre de hoja
        nombre_hoja = (
            self.hoja.get("Texto_Titulo")
            or self.hoja.get("Nombre_Hoja")
            or self.hoja.get("Nombre", "Visual")
        )

        result: dict[str, Any] = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
            "name": self.visual_id,
            "position": {
                "x": x,
                "y": y,
                "z": z,
                "height": h,
                "width": w,
                "tabOrder": 1,
            },
            "visual": visual_obj,
        }

        # [FIX-SCHEMA-2.5.0] Las propiedades del *contenedor* (title, background, lock,
        # etc.) DEBEN ir dentro de 'visualContainerObjects' —una clave DENTRO de 'visual'—,
        # NO dentro de 'visual.objects' (que reserva propiedades de formato propias del
        # tipo de visual: columnHeaders, grid, general, etc.).
        # Colocar 'title'/'background' en 'visual.objects' hace que Power BI Desktop
        # descarte el contenedor silenciosamente y muestre un lienzo en blanco.
        if "objects" not in visual_obj:
            visual_obj["objects"] = {}

        if self.pbi_type == "textbox":
            return result

        visual_obj["visualContainerObjects"] = {
            "title": [
                {
                    "properties": {
                        "text": {"expr": {"Literal": {"Value": f"'{nombre_hoja}'"}}},
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "titleWrap": {"expr": {"Literal": {"Value": "true"}}},
                        "fontColor": {
                            "solid": {"color": {"expr": {"Literal": {"Value": "'#79477F'"}}}}
                        },
                        "alignment": {"expr": {"Literal": {"Value": "'left'"}}},
                        "fontSize": {"expr": {"Literal": {"Value": "10D"}}},
                    }
                }
            ],
            "background": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": "true"}}},
                        "transparency": {"expr": {"Literal": {"Value": "0D"}}},
                    }
                }
            ],
        }

        if self.all_filters:
            result["filterConfig"] = {"filters": self.all_filters}

        return result


def crear_visuales_para_pagina(
    carpeta_visuals: str,
    item_tableau: dict,
    posicion: dict | None = None,
    mapa_fuentes: dict[str, str] | None = None,
    columnas_por_tabla: dict[str, dict[str, str]] | None = None,
    measures_map: dict[str, str] | None = None,
    miembros_por_tabla: dict[str, dict[str, list[str]]] | None = None,
):
    """
    Crea visuales para una página.
    Si item_tableau es un Dashboard, crea todos sus worksheets.
    """
    try:
        mapa_norm = {}
        if mapa_fuentes:
            mapa_norm = {str(k).lower(): v for k, v in mapa_fuentes.items()}

        lista_hojas = []
        if "Hojas" in item_tableau and isinstance(item_tableau["Hojas"], list):
            lista_hojas = item_tableau["Hojas"]
            logger.info(
                f"Procesando {len(lista_hojas)} worksheets para dashboard {item_tableau.get('Nombre')}"
            )
        else:
            lista_hojas = [item_tableau]

        num_visuals = len(lista_hojas)
        has_dashboard_specials = bool(
            item_tableau.get("Titulo_Dashboard")
            or item_tableau.get("Controles")
            or item_tableau.get("Objetos")
        )
        if num_visuals == 0 and not has_dashboard_specials:
            return

        PAGE_W = 1920
        PAGE_H = 1080

        layouts = [
            worksheet.get("Layout")
            for worksheet in lista_hojas
            if isinstance(worksheet.get("Layout"), dict)
            and all(key in worksheet["Layout"] for key in ("x", "y", "width", "height"))
        ]
        special_layouts = [
            item.get("Layout")
            for item in [
                item_tableau.get("Titulo_Dashboard") or {},
                *item_tableau.get("Controles", []),
                *item_tableau.get("Objetos", []),
            ]
            if isinstance(item.get("Layout"), dict)
            and all(key in item["Layout"] for key in ("x", "y", "width", "height"))
        ]
        layouts.extend(special_layouts)
        source_left = min((int(layout["x"]) for layout in layouts), default=0)
        source_top = min((int(layout["y"]) for layout in layouts), default=0)
        source_width = max(
            (int(layout["x"]) + int(layout["width"]) - source_left for layout in layouts),
            default=PAGE_W,
        )
        source_height = max(
            (int(layout["y"]) + int(layout["height"]) - source_top for layout in layouts),
            default=PAGE_H,
        )
        scale_x = PAGE_W / source_width if source_width > PAGE_W else 1
        scale_y = PAGE_H / source_height if source_height > PAGE_H else 1

        cols = 2 if num_visuals > 1 else 1
        rows = max(1, (num_visuals + 1) // 2)

        grid_width = (PAGE_W // cols) - 20
        grid_height = (PAGE_H // rows) - 20

        for idx, hoja in enumerate(lista_hojas):
            # Omitir hojas sin ningún campo de datos real: en Tableau algunas
            # hojas son sólo hosts de leyenda/tooltip (sin shelves de filas,
            # columnas ni medidas). Un encoding suelto (p.ej. ``text`` para una
            # etiqueta) sin medida/columna subyacente no produce proyecciones y
            # genera un contenedor vacío en Desktop, así que se omite de forma
            # genérica (no por nombre).
            tiene_shelves_datos = bool(
                hoja.get("Campos_Filas")
                or hoja.get("Campos_Columnas")
                or hoja.get("Campos_Medidas")
            )
            if not tiene_shelves_datos and hoja.get("Tipo_Visual") != "textbox":
                logger.info(
                    "Hoja sin shelves de datos, se omite: %s",
                    hoja.get("Nombre_Hoja") or hoja.get("Texto_Titulo"),
                )
                continue

            row = idx // cols
            col = idx % cols

            v_w = grid_width
            v_h = grid_height
            x = col * (v_w + 20) + 10
            y = row * (v_h + 20) + 10
            layout = hoja.get("Layout")
            z_order = 1
            if layout:
                try:
                    x = int(layout["x"])
                    y = int(layout["y"])
                    v_w = int(layout["width"])
                    v_h = int(layout["height"])
                    z_order = int(layout.get("z_order", 1))
                    if scale_x != 1 or scale_y != 1:
                        x = round((x - source_left) * scale_x)
                        y = round((y - source_top) * scale_y)
                        v_w = max(1, round(v_w * scale_x))
                        v_h = max(1, round(v_h * scale_y))
                except (KeyError, TypeError, ValueError):
                    logger.warning(
                        "Layout Tableau inválido para %s; se usa cuadrícula.",
                        hoja.get("Nombre_Hoja"),
                    )

            if hoja.get("Tipo_Visual") == "textbox":
                # TITLE-HOST-02: conserva subtítulos creados en Tableau mediante
                # una worksheet sin shelves (incluidos parámetros materializados).
                visual_id = secrets.token_hex(10)
                _write_json(
                    carpeta_visuals,
                    visual_id,
                    _build_textbox_json(
                        visual_id,
                        str(hoja.get("Texto_Titulo") or hoja.get("Nombre_Hoja") or ""),
                        x,
                        y,
                        v_w,
                        v_h,
                        z_order,
                    ),
                )
                logger.info(
                    "Hoja de título materializada como textbox: %s",
                    hoja.get("Nombre_Hoja") or hoja.get("Texto_Titulo"),
                )
                continue

            fuente_raw = str(hoja.get("Fuente_Datos") or "").lower()

            if "federated." in fuente_raw:
                fuente_raw = fuente_raw.split(".")[-1]

            tabla_pbi = None

            if mapa_norm:
                if fuente_raw in mapa_norm:
                    tabla_pbi = mapa_norm[fuente_raw]

                if not tabla_pbi:
                    norm_fuente = (
                        fuente_raw.lower().replace("[", "").replace("]", "").replace(".", "_")
                    )
                    tabla_pbi = mapa_norm.get(norm_fuente)

                if not tabla_pbi:
                    for k in sorted(mapa_norm.keys(), key=len, reverse=True):
                        if k in fuente_raw or fuente_raw in k:
                            tabla_pbi = mapa_norm[k]
                            break

                if not tabla_pbi:
                    limpia = re.sub(r"\(.*?\)", "", fuente_raw).replace("  ", " ").strip()
                    tabla_pbi = mapa_norm.get(limpia)
                    if not tabla_pbi:
                        for k in sorted(mapa_norm.keys(), key=len, reverse=True):
                            if k in limpia or limpia in k:
                                tabla_pbi = mapa_norm[k]
                                break

                if not tabla_pbi and mapa_norm:
                    tabla_pbi = next(iter(mapa_norm.values()))

            columnas_map = (
                columnas_por_tabla.get(tabla_pbi, {}) if columnas_por_tabla and tabla_pbi else {}
            )

            panel_measures = list(dict.fromkeys(hoja.get("Paneles_Medidas", [])))
            visual_specs: list[tuple[dict[str, Any], int, int, int, int]] = []
            row_dimensions = list(dict.fromkeys(hoja.get("Campos_Filas", [])))
            column_dimensions = list(dict.fromkeys(hoja.get("Campos_Columnas", [])))
            axis_measures = list(
                dict.fromkeys(
                    hoja.get("Campos_Medidas_Filas", [])
                    + hoja.get("Campos_Medidas_Columnas", [])
                )
            )
            trellis_field = (
                column_dimensions[0]
                if row_dimensions and column_dimensions and axis_measures
                else None
            )
            trellis_members: list[str] = []
            if trellis_field and tabla_pbi and miembros_por_tabla:
                table_members = miembros_por_tabla.get(tabla_pbi, {})
                mapped_trellis = next(
                    (
                        mapped
                        for source, mapped in columnas_map.items()
                        if not str(source).startswith("__kind__:")
                        and str(source).casefold() == str(trellis_field).casefold()
                    ),
                    trellis_field,
                )
                trellis_members = next(
                    (
                        list(values)
                        for column, values in table_members.items()
                        if str(column).casefold() == str(mapped_trellis).casefold()
                    ),
                    [],
                )

            tableau_type = str(hoja.get("Tipo_Visual", "")).lower()
            kpi_measures = list(dict.fromkeys(hoja.get("Campos_Medidas", [])))
            is_multi_kpi = (
                tableau_type in {"text", "text-table"}
                and not row_dimensions
                and not column_dimensions
                and len(kpi_measures) > 1
            )
            if is_multi_kpi:
                # KPI-CARDS-01: Tableau serializa varios valores de texto en
                # una sola hoja. Desktop los representa como tarjetas hermanas
                # para conservar el peso visual de cada indicador.
                gap = 12
                card_width = max(1, (v_w - gap * (len(kpi_measures) - 1)) // len(kpi_measures))
                labels = hoja.get("Etiquetas_Campos", {})
                for card_index, measure in enumerate(kpi_measures):
                    card_sheet = dict(hoja)
                    card_sheet["Campos_Medidas"] = [measure]
                    card_sheet["Texto_Titulo"] = str(labels.get(measure, measure))
                    visual_specs.append(
                        (
                            card_sheet,
                            x + card_index * (card_width + gap),
                            y,
                            card_width,
                            v_h,
                        )
                    )
            elif trellis_field and trellis_members:
                # TRELLIS-01: PBIR no garantiza que un rol SmallMultiples
                # escrito externamente se active en Desktop. Materializar los
                # miembros como paneles filtrados conserva el trellis real.
                gap = 8
                panel_height = max(
                    1,
                    (v_h - gap * (len(trellis_members) - 1)) // len(trellis_members),
                )
                for member_index, member in enumerate(trellis_members):
                    panel_sheet = dict(hoja)
                    panel_sheet["Campos_Columnas"] = [
                        field for field in column_dimensions if field != trellis_field
                    ]
                    panel_sheet["Filtros"] = [
                        *hoja.get("Filtros", []),
                        {
                            "kind": "categorical",
                            "field": trellis_field,
                            "operator": "include",
                            "values": [member],
                        },
                    ]
                    panel_sheet["Texto_Titulo"] = str(member)
                    visual_specs.append(
                        (
                            panel_sheet,
                            x,
                            y + member_index * (panel_height + gap),
                            v_w,
                            panel_height,
                        )
                    )
            elif len(panel_measures) > 1:
                # PANE-02: Tableau puede alojar varios panes con escalas X
                # independientes dentro de un worksheet. Se materializan como
                # visuales contiguos, cada uno con una sola medida/escala.
                gap = 20
                pane_width = max(1, (v_w - gap * (len(panel_measures) - 1)) // len(panel_measures))
                for panel_index, measure in enumerate(panel_measures):
                    panel_sheet = dict(hoja)
                    panel_sheet["Campos_Medidas"] = [measure]
                    panel_sheet["Campos_Medidas_Filas"] = [
                        measure
                    ] if measure in hoja.get("Campos_Medidas_Filas", []) else []
                    panel_sheet["Campos_Medidas_Columnas"] = [
                        measure
                    ] if measure in hoja.get("Campos_Medidas_Columnas", []) else []
                    panel_sheet["Texto_Titulo"] = hoja.get("Etiquetas_Campos", {}).get(
                        measure, measure
                    )
                    visual_specs.append(
                        (
                            panel_sheet,
                            x + panel_index * (pane_width + gap),
                            y,
                            pane_width,
                            v_h,
                        )
                    )
            else:
                visual_specs.append((hoja, x, y, v_w, v_h))

            for panel_sheet, panel_x, panel_y, panel_w, panel_h in visual_specs:
                visual_id = secrets.token_hex(10)
                mapper = VisualMapper(
                    visual_id,
                    panel_sheet,
                    tabla_pbi,
                    columnas_map,
                    measures_map=measures_map,
                )

                visual_json = mapper.get_visual_json(panel_x, panel_y, panel_w, panel_h, z_order)

                query_state = visual_json["visual"].get("query", {}).get("queryState", {})
                tiene_proyecciones = any(role.get("projections") for role in query_state.values())
                if visual_json["visual"]["visualType"] != "textbox" and not tiene_proyecciones:
                    logger.info(
                        "Hoja sin proyecciones resolubles en el modelo, se omite: %s",
                        panel_sheet.get("Nombre_Hoja") or panel_sheet.get("Texto_Titulo"),
                    )
                    continue

                _write_json(carpeta_visuals, visual_id, visual_json)

                logger.info(
                    "Visual[%s/%s] creado: %s -> %s",
                    idx + 1,
                    num_visuals,
                    panel_sheet.get("Texto_Titulo") or panel_sheet.get("Nombre_Hoja"),
                    visual_json["visual"]["visualType"],
                )

        def scaled_special(layout: dict[str, Any]) -> tuple[int, int, int, int, int]:
            x = round((int(layout["x"]) - source_left) * scale_x)
            y = round((int(layout["y"]) - source_top) * scale_y)
            width = max(1, round(int(layout["width"]) * scale_x))
            height = max(1, round(int(layout["height"]) * scale_y))
            return x, y, width, height, int(layout.get("z_order", 1))

        dashboard_title = item_tableau.get("Titulo_Dashboard")
        if dashboard_title and dashboard_title.get("Layout"):
            x, y, width, height, z_order = scaled_special(dashboard_title["Layout"])
            visual_id = secrets.token_hex(10)
            _write_json(
                carpeta_visuals,
                visual_id,
                _build_textbox_json(
                    visual_id,
                    dashboard_title.get("Texto", ""),
                    x,
                    y,
                    width,
                    height,
                    z_order,
                    font_size="18pt",
                    background="#dfedeb",
                ),
            )

        for dashboard_object in item_tableau.get("Objetos", []):
            if dashboard_object.get("Tipo") != "web":
                continue
            layout = dashboard_object.get("Layout")
            url = str(dashboard_object.get("Url", "")).strip()
            if not isinstance(layout, dict) or not url:
                continue
            x, y, width, height, z_order = scaled_special(layout)
            visual_id = secrets.token_hex(10)
            _write_json(
                carpeta_visuals,
                visual_id,
                _build_textbox_json(
                    visual_id,
                    url,
                    x,
                    y,
                    width,
                    height,
                    z_order,
                    font_size="12pt",
                    background="#ffffff",
                ),
            )

        default_table = next(iter(mapa_norm.values()), None)
        for control in item_tableau.get("Controles", []):
            control_type = control.get("Tipo")
            if control_type not in {"year_filter", "categorical_filter", "parameter"}:
                continue
            layout = control.get("Layout")
            if not isinstance(layout, dict):
                continue
            if control_type in {"year_filter", "categorical_filter"} and not default_table:
                continue
            if control_type in {"year_filter", "categorical_filter"}:
                source_key = str(control.get("Fuente_Datos", "")).lower()
                slicer_table = mapa_norm.get(source_key, default_table)
                slicer_field = control["Campo"]
                table_objects = (
                    columnas_por_tabla.get(slicer_table, {}) if columnas_por_tabla else {}
                )
                mapped_slicer_field = table_objects.get(slicer_field)
                if not mapped_slicer_field:
                    mapped_slicer_field = next(
                        (
                            value
                            for key, value in table_objects.items()
                            if not str(key).startswith("__kind__:")
                            and str(key).lower() == str(slicer_field).lower()
                        ),
                        None,
                    )
                object_kind = table_objects.get(
                    f"__kind__:{str(mapped_slicer_field or slicer_field).lower()}"
                )
                has_object_metadata = any(str(key).startswith("__kind__:") for key in table_objects)
                if (has_object_metadata and object_kind != "column") or (
                    not has_object_metadata and not mapped_slicer_field
                ):
                    logger.warning(
                        "Slicer Tableau omitido: %s no es una columna de %s",
                        slicer_field,
                        slicer_table,
                    )
                    continue
                slicer_field = mapped_slicer_field
                selected_value = control.get("Valor") or None
            else:
                slicer_table = f"{control['Parametro']} Options"
                slicer_field = "Label"
                if columnas_por_tabla is not None:
                    option_objects = columnas_por_tabla.get(slicer_table, {})
                    mapped_label = next(
                        (
                            value
                            for key, value in option_objects.items()
                            if not str(key).startswith("__kind__:")
                            and str(key).casefold() == "label"
                        ),
                        None,
                    )
                    if mapped_label is None:
                        # PARAM-CTRL-01: evita bindings a tablas de opciones inexistentes.
                        logger.warning(
                            "Control de parámetro omitido: %s no tiene tabla de opciones",
                            control.get("Parametro", ""),
                        )
                        continue
                    slicer_field = mapped_label
                default_value = str(control.get("Valor", ""))
                selected_value = next(
                    (
                        option.get("label")
                        for option in control.get("Opciones", [])
                        if str(option.get("value")) == default_value
                    ),
                    default_value,
                )
            x, y, width, height, z_order = scaled_special(layout)
            visual_id = secrets.token_hex(10)
            _write_json(
                carpeta_visuals,
                visual_id,
                _build_slicer_json(
                    visual_id,
                    slicer_table,
                    slicer_field,
                    control.get("Titulo", slicer_field).rstrip(":"),
                    selected_value,
                    x,
                    y,
                    width,
                    height,
                    z_order,
                ),
            )

    except Exception as e:
        logger.error(f"Error creando visuales para página: {e}", exc_info=True)


def obtener_metadatos_tablas_pbi(
    carpeta_semantic_model: str,
) -> dict[str, dict[str, str]]:
    """
    Escanea la carpeta 'definition/tables' del Semantic Model TMDL
    y extrae nombres de tablas y sus columnas/medidas.

    Returns:
        { 'NombreTabla': {'NombreColumnaClean': 'NombreColumnaReal', ...} }
    """
    metadata: dict[str, dict[str, str]] = {}
    tables_dir = os.path.join(carpeta_semantic_model, "definition", "tables")

    if not os.path.exists(tables_dir):
        return metadata

    for filename in os.listdir(tables_dir):
        if not filename.endswith(".tmdl"):
            continue

        tmdl_path = os.path.join(tables_dir, filename)
        try:
            with open(tmdl_path, encoding="utf-8") as f:
                lines = f.readlines()

            if not lines:
                continue

            first_line = lines[0].strip()
            if not first_line.startswith("table "):
                continue

            table_name = first_line.replace("table ", "").strip().strip("'").strip('"')
            metadata[table_name] = {}

            for line in lines:
                line = line.strip()
                if line.startswith("column ") or line.startswith("measure "):
                    parts = line.split(" ", 1)
                    if len(parts) < 2:
                        continue

                    rest = parts[1]
                    if rest.startswith("'") or rest.startswith('"'):
                        quote = rest[0]
                        end_quote = rest.find(quote, 1)
                        if end_quote != -1:
                            col_name = rest[1:end_quote]
                        else:
                            col_name = rest.strip()
                    else:
                        if "=" in rest:
                            col_name = rest.split("=")[0].strip()
                        else:
                            col_name = rest.strip()

                    clean_name = col_name.lower().replace(" ", "").replace("_", "")
                    metadata[table_name][clean_name] = col_name
                    metadata[table_name][col_name.lower()] = col_name

        except Exception:
            pass

    return metadata


def _write_json(folder: str, visual_id: str, content: dict):
    """Escribe el JSON del visual en la estructura de carpetas PBIR."""
    visual_folder = os.path.join(folder, visual_id)
    os.makedirs(visual_folder, exist_ok=True)
    with open(os.path.join(visual_folder, "visual.json"), "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2, ensure_ascii=False)


def _build_textbox_json(
    visual_id: str,
    text: str,
    x: int,
    y: int,
    width: int,
    height: int,
    z: int,
    font_size: str = "12pt",
    background: str | None = None,
    show: bool = True,
) -> dict[str, Any]:
    """Crea un textbox PBIR con posición y estilo Tableau básicos."""
    visual: dict[str, Any] = {
        "visualType": "textbox",
        "objects": {
            "general": [
                {
                    "properties": {
                        "paragraphs": [
                            {
                                "textRuns": [
                                    {
                                        "value": text,
                                        "textStyle": {
                                            "fontFamily": "Tableau Book",
                                            "fontSize": font_size,
                                            "fontWeight": "bold",
                                        },
                                    }
                                ],
                                "horizontalTextAlignment": "center",
                            }
                        ]
                    }
                }
            ]
        },
        "drillFilterOtherVisuals": True,
    }
    if background:
        visual["visualContainerObjects"] = {
            "background": [
                {
                    "properties": {
                        "show": {"expr": {"Literal": {"Value": str(show).lower()}}},
                        "color": {
                            "solid": {"color": {"expr": {"Literal": {"Value": f"'{background}'"}}}}
                        },
                        "transparency": {"expr": {"Literal": {"Value": "0D"}}},
                    }
                }
            ]
        }
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
        "name": visual_id,
        "position": {
            "x": x,
            "y": y,
            "z": z,
            "height": height,
            "width": width,
            "tabOrder": z,
        },
        "visual": visual,
    }


def _build_slicer_json(
    visual_id: str,
    table: str,
    field: str,
    title: str,
    selected_value: Any,
    x: int,
    y: int,
    width: int,
    height: int,
    z: int,
) -> dict[str, Any]:
    """Crea un slicer desplegable y conserva la selección inicial Tableau."""
    projection = {
        "field": {
            "Column": {
                "Expression": {"SourceRef": {"Entity": table}},
                "Property": field,
            }
        },
        "queryRef": f"{table}.{field}",
        "nativeQueryRef": field,
        "active": True,
    }
    objects: dict[str, Any] = {
        "data": [{"properties": {"mode": {"expr": {"Literal": {"Value": "'Dropdown'"}}}}}],
        "header": [{"properties": {"text": {"expr": {"Literal": {"Value": f"'{title}'"}}}}}],
    }
    if selected_value not in (None, ""):
        literal = (
            f"{selected_value}L"
            if isinstance(selected_value, int)
            else f"'{str(selected_value).replace(chr(39), chr(39) * 2)}'"
        )
        objects["general"] = [
            {
                "properties": {
                    "filter": {
                        "filter": {
                            "Version": 2,
                            "From": [{"Name": "0", "Entity": table, "Type": 0}],
                            "Where": [
                                {
                                    "Condition": {
                                        "In": {
                                            "Expressions": [
                                                {
                                                    "Column": {
                                                        "Expression": {
                                                            "SourceRef": {"Source": "0"}
                                                        },
                                                        "Property": field,
                                                    }
                                                }
                                            ],
                                            "Values": [[{"Literal": {"Value": literal}}]],
                                        }
                                    }
                                }
                            ],
                        }
                    }
                }
            }
        ]
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.5.0/schema.json",
        "name": visual_id,
        "position": {
            "x": x,
            "y": y,
            "z": z,
            "height": height,
            "width": width,
            "tabOrder": z,
        },
        "visual": {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [projection]}}},
            "objects": objects,
            "drillFilterOtherVisuals": True,
        },
    }


def obtener_metadatos_desde_bim(carpeta_semantic_model: str) -> dict[str, dict[str, str]]:
    """
    Lee el archivo model.bim y extrae nombres de tablas y columnas.
    Formato: { 'NombreTabla': {'nombre_columna_lower': 'NombreColumnaReal', ...} }
    """
    metadata: dict[str, dict[str, str]] = {}
    bim_path = os.path.join(carpeta_semantic_model, "model.bim")

    if not os.path.exists(bim_path):
        return metadata

    try:
        with open(bim_path, encoding="utf-8") as f:
            bim_data = json.load(f)

        tables = bim_data.get("model", {}).get("tables", [])
        for table in tables:
            table_name = table.get("name", "")
            if not table_name:
                continue

            metadata[table_name] = {}
            for col in table.get("columns", []):
                col_name = col.get("name", "")
                if col_name:
                    # Guardar con clave normalizada y valor original
                    metadata[table_name][col_name.lower()] = col_name
                    metadata[table_name][col_name.lower().replace(" ", "")] = col_name

    except Exception as e:
        logger.warning(f"Error leyendo model.bim: {e}")

    return metadata
