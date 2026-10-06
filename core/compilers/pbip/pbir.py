"""Neutral Presentation/Interaction IR -> PBIR (Power BI report) JSON.

Genera los artefactos PBIR (``version.json``, ``report.json``, ``pages.json``,
``page.json`` y ``visual.json``) a partir de los IR neutrales de presentaciÃ³n e
interacciÃ³n, resolviendo los bindings contra el :class:`SemanticModel`. No
reparsÃ©a Tableau: consume el IR neutral ya aceptado.

ConservaciÃ³n garantizada:

* **Layout**: la geometrÃ­a neutral (``VisualGeometry``) se traduce a la
  ``position`` PBIR del visual.
* **Bindings**: cada :class:`DataBinding` resuelto se emite como
  ``query.queryState`` (PBIR nativo) y se conserva verbatim en ``biBridge``.
* **Filtros**: los filtros neutrales traducibles se emiten como ``filterConfig``
  PBIR nativo en report/page/visual; lo no traducible queda como aproximaciÃ³n en
  el adaptador, nunca como una extensiÃ³n JSON no soportada.
* **Aproximaciones**: cada visual cuyo intent no tenga visualType PBIR nativo
  se degrada a ``textbox`` con una nota explÃ­cita.

DiseÃ±o (Ponytail): stdlib + ``core.contracts``. Determinista: los ids se
derivan por hash para reproducibilidad. Las URLs de schema PBIR se reutilizan
del generador legacy (valores canÃ³nicos de Microsoft), no se adivinan.

Boundary: stdlib + ``core.contracts`` only.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.contracts.presentation_ir import PagePresentation, PresentationIR, VisualPresentation

# --- PBIR schema URLs (valores canÃ³nicos reutilizados del generador legacy) ---
_SCHEMA_VERSION = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/versionMetadata/1.0.0/schema.json"
)
_SCHEMA_REPORT = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/report/3.1.0/schema.json"
)
_SCHEMA_PAGES = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/pagesMetadata/1.0.0/schema.json"
)
_SCHEMA_PAGE = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/page/2.0.0/schema.json"
)
_SCHEMA_VISUAL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/"
    "definition/visualContainer/2.5.0/schema.json"
)

#: Intent visual neutral -> visualType PBIR. El segundo elemento indica si la
#: traducciÃ³n es nativa (``True``) o una aproximaciÃ³n (``False``).
_VISUAL_TYPE_MAP: dict[str, tuple[str, bool]] = {
    "bar": ("clusteredBarChart", True),
    "column": ("clusteredColumnChart", True),
    "line": ("lineChart", True),
    "area": ("areaChart", True),
    "scatter": ("scatterChart", True),
    "pie": ("pieChart", True),
    "donut": ("doughnutChart", True),
    "treemap": ("treemap", True),
    "map": ("azureMap", True),
    "table": ("tableEx", True),
    "pivot_matrix": ("pivotTable", True),
    "waterfall": ("waterfallChart", True),
    "slicer_filter": ("slicer", True),
    "text_box": ("textbox", True),
    "image": ("image", True),
    "kpi_card": ("card", False),  # aproximaciÃ³n: kpi_card -> card simple
    "heatmap": ("tableEx", False),  # aproximaciÃ³n: sin visualType heatmap nativo
    "gauge": ("card", False),  # aproximaciÃ³n: gauge -> card
    "container": ("group", False),  # aproximaciÃ³n: contenedor -> group
    "custom_visual": ("textbox", False),  # fallback explÃ­cito
}


def _hex_id(*parts: str, length: int = 20) -> str:
    """Id hexadecimal determinista (estilo Power BI Desktop) desde ``parts``."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def page_id(page_name: str) -> str:
    """Id determinista de pÃ¡gina a partir de su nombre."""
    return _hex_id("page", page_name)


def visual_id(page_name: str, visual_id_value: str) -> str:
    """Id determinista de visual a partir de pÃ¡gina + visual_id."""
    return _hex_id("visual", page_name, visual_id_value)


def render_version_json() -> dict[str, Any]:
    """``definition/version.json`` requerido por Power BI Desktop."""
    return {"$schema": _SCHEMA_VERSION, "version": "2.0.0"}


def render_report_json(
    display_name: str,
    presentation: PresentationIR | None,
    *,
    filters_annotation: list[dict[str, Any]],
) -> dict[str, Any]:
    """``definition/report.json`` con tema base y ``filterConfig`` PBIR nativo.

    ``filters_annotation`` conserva el nombre histÃ³rico del parÃ¡metro para no
    romper callers, pero sus elementos son ``FilterContainer`` PBIR vÃ¡lidos, no
    metadata propietaria. Los filtros no traducibles nunca se insertan aquÃ­.
    """
    _ = display_name, presentation
    doc: dict[str, Any] = {
        "$schema": _SCHEMA_REPORT,
        "themeCollection": {
            "baseTheme": {
                "name": "CY24SU10",
                "reportVersionAtImport": {
                    "visual": "1.8.97",
                    "report": "2.0.97",
                    "page": "1.3.97",
                },
                "type": "SharedResources",
            }
        },
        "settings": {
            "useStylableVisualContainerHeader": True,
            "useEnhancedTooltips": True,
            "useDefaultAggregateDisplayName": True,
        },
    }
    if filters_annotation:
        doc["filterConfig"] = {"filters": filters_annotation}
    return doc


def render_pages_json(page_ids: list[str]) -> dict[str, Any]:
    """``definition/pages/pages.json`` con el orden y la pÃ¡gina activa."""
    return {
        "$schema": _SCHEMA_PAGES,
        "pageOrder": page_ids,
        "activePageName": page_ids[0] if page_ids else "",
    }


def render_page_json(
    page: PagePresentation,
    pid: str,
    *,
    bridge_filters: list[dict[str, Any]] | None = None,
    visual_interactions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """``definition/pages/<id>/page.json`` con filtros/interacciones PBIR nativos.

    ``bridge_filters`` conserva el nombre histÃ³rico del parÃ¡metro; sus elementos
    son ``FilterContainer`` PBIR vÃ¡lidos. ``visual_interactions`` usa el contrato
    nativo ``source/target/type`` de Page schema 2.0.0.
    """
    doc: dict[str, Any] = {
        "$schema": _SCHEMA_PAGE,
        "name": pid,
        "displayName": page.display_name or page.name,
        "width": int(page.width),
        "height": int(page.height),
        "displayOption": "FitToPage",
    }
    if bridge_filters:
        doc["filterConfig"] = {"filters": bridge_filters}
    if visual_interactions:
        doc["visualInteractions"] = visual_interactions
    return doc


def _position(visual: VisualPresentation) -> dict[str, int]:
    """Traduce ``VisualGeometry`` a la ``position`` PBIR (enteros)."""
    geom = visual.geometry
    return {
        "x": int(round(geom.x)),
        "y": int(round(geom.y)),
        "z": int(geom.z_index),
        "width": int(round(geom.width)),
        "height": int(round(geom.height)),
        "tabOrder": 0,
    }


def _projection(binding: dict[str, Any]) -> dict[str, Any]:
    source = {"SourceRef": {"Entity": binding["table"]}}
    if binding["kind"] == "aggregation":
        function = {"sum": 0, "avg": 1, "count": 2, "min": 3, "max": 4}.get(
            binding.get("aggregation"), 0
        )
        field = {
            "Aggregation": {
                "Expression": {
                    "Column": {
                        "Expression": source,
                        "Property": binding["name"],
                    }
                },
                "Function": function,
            }
        }
        aggregate_name = {0: "Sum", 1: "Average", 2: "Count", 3: "Min", 4: "Max"}[
            function
        ]
        query_ref = f"{aggregate_name}({binding['table']}.{binding['name']})"
    elif binding["kind"] == "measure":
        field = {
            "Measure": {
                "Expression": source,
                "Property": binding["name"],
            }
        }
        query_ref = f"{binding['table']}.{binding['name']}"
    else:
        field = {
            "Column": {
                "Expression": source,
                "Property": binding["name"],
            }
        }
        query_ref = f"{binding['table']}.{binding['name']}"
    return {
        "field": field,
        "queryRef": query_ref,
        # PBIR uses this as an execution key, not merely a label. It must keep
        # the physical model name; changing it to a caption makes Desktop load
        # the model while every visual remains BLANK.
        "nativeQueryRef": binding["name"],
        "active": True,
    }


def _query_state(resolved_bindings: list[dict[str, Any]], visual_type: str) -> dict[str, Any]:
    projections = [
        (binding, _projection(binding))
        for binding in resolved_bindings
        if binding["kind"] in {"column", "measure", "aggregation"}
    ]
    if not projections:
        return {}
    if visual_type in {"tableEx", "pivotTable", "slicer", "card"}:
        return {"Values": {"projections": [item for _, item in projections]}}
    categories = [item for binding, item in projections if binding["kind"] == "column"]
    values = [
        item for binding, item in projections if binding["kind"] in {"measure", "aggregation"}
    ]
    state: dict[str, Any] = {}
    category_bucket = "Group" if visual_type == "treemap" else "Category"
    value_bucket = (
        "Values" if visual_type == "treemap" else "Size" if visual_type == "azureMap" else "Y"
    )
    if categories:
        state[category_bucket] = {"projections": categories}
    if values:
        state[value_bucket] = {"projections": values}
    if not state and projections:
        state["Values"] = {"projections": [item for _, item in projections]}
    return state


def _visual_objects(visual_type: str) -> dict[str, Any]:
    """Minimal formatting that prevents technical model ids from leaking."""
    hidden_axis_title = [
        {"properties": {"showAxisTitle": {"expr": {"Literal": {"Value": "false"}}}}}
    ]
    if visual_type in {
        "clusteredBarChart",
        "clusteredColumnChart",
        "lineChart",
        "areaChart",
        "scatterChart",
    }:
        return {
            "categoryAxis": hidden_axis_title,
            "valueAxis": hidden_axis_title,
        }
    if visual_type == "card":
        return {
            "categoryLabels": [
                {"properties": {"show": {"expr": {"Literal": {"Value": "false"}}}}}
            ]
        }
    return {}


def render_visual_json(
    page: PagePresentation,
    visual: VisualPresentation,
    resolved_bindings: list[dict[str, Any]],
    *,
    filters: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Genera ``visual.json`` para un visual resuelto.

    Devuelve ``(visual_json, notas)``. Si el intent no tiene visualType PBIR
    nativo, se emite un ``textbox`` de respaldo con la nota explÃ­cita y los
    bindings se conservan en ``biBridge`` (no se pierden).
    """
    notes: list[str] = []
    intent = str(visual.intent.value) if hasattr(visual.intent, "value") else str(visual.intent)
    mapped = _VISUAL_TYPE_MAP.get(intent)
    vid = visual_id(page.name, visual.visual_id)

    if mapped is None:
        notes.append(f"intent visual desconocido {intent!r}: degradado a textbox")
        return _textbox_fallback(vid, visual, intent, reason=f"unknown intent {intent!r}"), notes

    visual_type, is_native = mapped
    if not is_native:
        notes.append(f"intent {intent!r} aproximado como {visual_type!r}")

    position = _position(visual)
    query_state = _query_state(resolved_bindings, visual_type)

    # Un slicer sin query no es interactivo y Power BI lo muestra vacÃ­o. No
    # simulamos soporte: degradamos cualquier visual de datos no resoluble a un
    # placeholder explÃ­cito y conservamos sus bindings neutrales en biBridge.
    if not query_state and visual_type not in {"textbox", "image", "group"}:
        notes.append(
            f"visual {intent!r} sin bindings resueltos: degradado a textbox con bindings preservados"
        )
        return _textbox_fallback(vid, visual, intent, reason="unresolved bindings"), notes

    visual_doc: dict[str, Any] = {
        "$schema": _SCHEMA_VISUAL,
        "name": vid,
        "position": position,
        "visual": {
            "visualType": visual_type,
            "objects": _visual_objects(visual_type),
        },
    }
    title_objects = _title_objects(visual)
    if title_objects:
        visual_doc["visual"]["visualContainerObjects"] = title_objects
    if query_state:
        visual_doc["visual"]["query"] = {"queryState": query_state}
    if filters:
        visual_doc["filterConfig"] = {"filters": filters}
    return visual_doc, notes


def _title_objects(visual: VisualPresentation) -> dict[str, Any]:
    """Visual-container title and explicit subtitle state for PBIR."""
    if not visual.title:
        return {}
    return {
        "title": [
            {
                "properties": {
                    "text": {"expr": {"Literal": {"Value": f"'{visual.title}'"}}},
                    "show": {"expr": {"Literal": {"Value": "true"}}},
                    "titleWrap": {"expr": {"Literal": {"Value": "true"}}},
                }
            }
        ],
        # Desktop otherwise derives a second title from physical field ids,
        # exposing Calculation_* names even when the authored title is valid.
        "subTitle": [
            {"properties": {"show": {"expr": {"Literal": {"Value": "false"}}}}}
        ],
    }


def _textbox_fallback(
    vid: str, visual: VisualPresentation, intent: str, *, reason: str
) -> dict[str, Any]:
    """Visual ``textbox`` de respaldo que conserva la intenciÃ³n y bindings."""
    position = _position(visual)
    message = visual.title or f"[bridge] visual neutro '{intent}' pendiente de autorÃ­a"
    return {
        "$schema": _SCHEMA_VISUAL,
        "name": vid,
        "position": position,
        "visual": {
            "visualType": "textbox",
            "objects": {
                "general": [
                    {
                        "properties": {
                            "paragraphs": [
                                {
                                    "textRuns": [
                                        {
                                            "value": f"{message}\n[{reason}]",
                                            "textStyle": {
                                                "fontFamily": "Segoe UI",
                                                "fontSize": "14px",
                                            },
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            },
        },
    }



__all__ = [
    "page_id",
    "render_page_json",
    "render_pages_json",
    "render_report_json",
    "render_version_json",
    "render_visual_json",
    "visual_id",
]
