"""Oráculo del ORIGEN para la dimensión de fidelidad visual (G0-VISUAL-OP-ORACLE).

Este módulo extrae metadatos estructurales (páginas, zonas, geometría,
títulos, kinds de visual, data_roles y conteo de marcas cuando sea derivable)
directamente desde los artefactos TWB/TWBX origen de Tableau, produciendo un
``reference_payload`` compatible con ``score_fidelity`` de ``visual_rubric.py``.

Lo que el TWB no declara (colores efectivos renderizados, fuentes de píxel)
se mantiene honestamente como ``not_evaluable`` con causa; nunca se inventa.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONTROL_ZONE_TYPES = frozenset({"filter", "paramctrl", "parameter", "quickfilter"})
_LAYOUT_ZONE_TYPES = frozenset({"", "empty", "layout-basic", "layout-flow"})
_MARK_TO_VISUAL_KIND = {
    "area": "area",
    "bar": "bar",
    "column": "column",
    "donut": "donut",
    "heatmap": "heatmap",
    "line": "line",
    "map": "map",
    "multipolygon": "map",
    "pie": "pie",
    "scatter": "scatter",
    "text": "text_box",
    "textbox": "text_box",
}
_ENCODING_ROLES = {
    "color": "color",
    "detail": "detail",
    "geometry": "shape",
    "label": "label",
    "lod": "detail",
    "path": "series",
    "size": "size",
    "text": "label",
}
_QUALIFIED_FIELD_RE = re.compile(r"\[[^\]]+\]\.\[([^\]]+)\]")
_BRACKETED_FIELD_RE = re.compile(r"\[([^\]]+)\]")
_PARAMETER_TITLE_EXPR = re.compile(r"<\s*\[Parameters\]\.\[([^\]]+)\]\s*>", re.IGNORECASE | re.DOTALL)
_SERIALIZED_FIELD_RE = re.compile(
    r"^(none|yr|qr|mn|wk|twk|dy|sum|avg|min|max|attr|count|cnt|cntd|med|usr|io):(.+):(nk|ok|qk)$",
    re.IGNORECASE,
)
_MEASURE_PREFIXES = frozenset({"sum", "avg", "min", "max", "count", "cnt", "cntd", "med"})
_TEMPORAL_PREFIXES = frozenset({"yr", "qr", "mn", "wk", "twk", "dy"})
_VIRTUAL_FIELDS_WITHOUT_NEUTRAL_REFERENCE = frozenset({"measure names", "measure values"})


@dataclass(frozen=True)
class _ColumnMetadata:
    role: str = ""
    field_type: str = ""
    datatype: str = ""


@dataclass(frozen=True)
class _FieldFacts:
    logical_name: str
    reference: str | None
    quantitative: bool
    temporal: bool
    cause: str = ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def _descendants(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element.iter() if _local_name(child.tag) == name]


def _top_level_items(root: ET.Element, container_name: str, item_name: str) -> list[ET.Element]:
    return [
        item
        for container in _children(root, container_name)
        for item in container
        if _local_name(item.tag) == item_name
    ]


def _read_tableau_xml(path: Path) -> tuple[ET.Element, str]:
    """Lee el XML del workbook sin ejecutar importadores ni compiladores."""
    try:
        if path.suffix.lower() == ".twbx":
            with zipfile.ZipFile(path) as archive:
                members = sorted(
                    (name for name in archive.namelist() if name.lower().endswith(".twb")),
                    key=lambda name: (len(Path(name).parts), name.casefold()),
                )
                if not members:
                    raise ValueError(f"TWBX sin workbook TWB: {path}")
                member = members[0]
                xml_bytes = archive.read(member)
        else:
            member = path.name
            xml_bytes = path.read_bytes()
        return ET.fromstring(xml_bytes), member
    except (ET.ParseError, UnicodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Artefacto Tableau no parseable: {path}: {exc}") from exc


def _field_tokens(text: str) -> list[str]:
    qualified = _QUALIFIED_FIELD_RE.findall(text)
    values = qualified or _BRACKETED_FIELD_RE.findall(text)
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _serialized_field_match(token: str) -> re.Match[str] | None:
    raw = token.strip()
    if raw.casefold().startswith("fval:"):
        raw = raw[5:]
    return _SERIALIZED_FIELD_RE.fullmatch(raw)


def _logical_field_name(token: str) -> str:
    match = _serialized_field_match(token)
    return match.group(2).strip() if match else token.strip().strip("[]")


def _metadata_by_field(element: ET.Element) -> dict[str, _ColumnMetadata]:
    candidates: dict[str, list[_ColumnMetadata]] = {}
    for column in element.iter():
        if _local_name(column.tag) != "column" or not column.attrib.get("name"):
            continue
        tokens = _field_tokens(str(column.attrib["name"]))
        logical = _logical_field_name(tokens[-1] if tokens else str(column.attrib["name"]))
        candidates.setdefault(logical.casefold(), []).append(
            _ColumnMetadata(
                role=str(column.attrib.get("role") or "").strip().casefold(),
                field_type=str(column.attrib.get("type") or "").strip().casefold(),
                datatype=str(column.attrib.get("datatype") or "").strip().casefold(),
            )
        )

    resolved: dict[str, _ColumnMetadata] = {}
    for logical, values in candidates.items():
        attributes = (
            {value.role for value in values if value.role},
            {value.field_type for value in values if value.field_type},
            {value.datatype for value in values if value.datatype},
        )
        resolved[logical] = _ColumnMetadata(
            role=next(iter(attributes[0])) if len(attributes[0]) == 1 else "",
            field_type=next(iter(attributes[1])) if len(attributes[1]) == 1 else "",
            datatype=next(iter(attributes[2])) if len(attributes[2]) == 1 else "",
        )
    return resolved


def _is_virtual_field_without_neutral_reference(logical: str) -> bool:
    normalized = re.sub(r"[\s_]+", " ", logical.strip().lstrip(":").casefold())
    return normalized in _VIRTUAL_FIELDS_WITHOUT_NEUTRAL_REFERENCE


def _field_facts(
    token: str,
    parameters: set[str],
    metadata: dict[str, _ColumnMetadata],
) -> _FieldFacts:
    logical = _logical_field_name(token)
    if logical in parameters:
        return _FieldFacts(logical, f"parameter:{logical}", quantitative=False, temporal=False)
    if _is_virtual_field_without_neutral_reference(logical):
        return _FieldFacts(
            logical,
            None,
            quantitative=False,
            temporal=False,
            cause=(
                f"el campo virtual Tableau '{logical}' no tiene una referencia neutral "
                "exacta"
            ),
        )

    match = _serialized_field_match(token)
    column = metadata.get(logical.casefold(), _ColumnMetadata())
    if column.role == "measure":
        reference_type = "measure"
    elif column.role == "dimension":
        reference_type = "field"
    elif column.field_type == "quantitative":
        reference_type = "measure"
    elif column.field_type in {"nominal", "ordinal"}:
        reference_type = "field"
    elif match and (
        match.group(1).lower() in _MEASURE_PREFIXES
        or (match.group(1).lower() == "usr" and match.group(3).lower() == "qk")
    ):
        reference_type = "measure"
    else:
        reference_type = "field"

    temporal = column.datatype in {"date", "datetime"} or bool(
        match and match.group(1).lower() in _TEMPORAL_PREFIXES
    )
    return _FieldFacts(
        logical,
        f"{reference_type}:{logical}",
        quantitative=reference_type == "measure",
        temporal=temporal,
    )


def _add_role(roles: dict[str, str], role: str, reference: str) -> None:
    key = role
    suffix = 2
    while key in roles:
        key = f"{role}_{suffix}"
        suffix += 1
    roles[key] = reference


def _last_nonempty_descendant(element: ET.Element, name: str) -> ET.Element | None:
    candidates = [
        child
        for child in element.iter()
        if _local_name(child.tag) == name and (child.text or "").strip()
    ]
    return candidates[-1] if candidates else None


def _worksheet_title(
    worksheet: ET.Element,
    fallback: str,
    parameter_display_values: dict[str, str] | None = None,
) -> str:
    for layout in _children(worksheet, "layout-options"):
        for title in _children(layout, "title"):
            value = "".join(title.itertext()).strip()
            if value:
                rendered = value.replace("<Sheet Name>", fallback)
                display_values = parameter_display_values or {}
                rendered = _PARAMETER_TITLE_EXPR.sub(
                    lambda match: display_values.get(match.group(1).strip(), match.group(0)),
                    rendered,
                )
                return re.sub(r"\s+", " ", rendered).strip()
    return fallback


def _forecast_enabled(element: ET.Element) -> bool:
    return any(
        _local_name(candidate.tag) == "forecast-specification"
        and _is_true(candidate.attrib.get("enabled"))
        for candidate in element.iter()
    )


def _forecast_worksheet_names(root: ET.Element) -> set[str]:
    names = {
        str(worksheet.attrib.get("name") or "").strip()
        for worksheet in _descendants(root, "worksheet")
        if _forecast_enabled(worksheet)
    }
    names.update(
        str(window.attrib.get("name") or "").strip()
        for window in _descendants(root, "window")
        if str(window.attrib.get("class") or "").strip().casefold() == "worksheet"
        and _forecast_enabled(window)
    )
    return {name for name in names if name}


def _worksheet_facts(
    worksheet: ET.Element,
    parameters: set[str],
    workbook_metadata: dict[str, _ColumnMetadata],
    *,
    forecast_enabled: bool,
    parameter_display_values: dict[str, str] | None = None,
) -> dict[str, Any]:
    name = str(worksheet.attrib.get("name") or "").strip()
    table = next(iter(_children(worksheet, "table")), None)
    if table is None:
        return {
            "name": name,
            "title": _worksheet_title(worksheet, name, parameter_display_values),
            "kind": "",
            "kind_cause": "worksheet sin tabla visual declarada",
            "data_roles": {},
            "role_causes": [],
        }

    metadata = dict(workbook_metadata)
    metadata.update(_metadata_by_field(worksheet))
    rows = _last_nonempty_descendant(table, "rows")
    columns = _last_nonempty_descendant(table, "cols")
    row_tokens = _field_tokens((rows.text or "") if rows is not None else "")
    column_tokens = _field_tokens((columns.text or "") if columns is not None else "")
    roles: dict[str, str] = {}
    role_causes: list[str] = []
    column_fields = [_field_facts(token, parameters, metadata) for token in column_tokens]
    row_fields = [_field_facts(token, parameters, metadata) for token in row_tokens]
    for field in column_fields:
        if field.reference is not None:
            _add_role(roles, "x_axis", field.reference)
        elif field.cause:
            role_causes.append(f"rol x_axis omitido: {field.cause}")
    for field in row_fields:
        if field.reference is not None:
            _add_role(roles, "y_axis", field.reference)
        elif field.cause:
            role_causes.append(f"rol y_axis omitido: {field.cause}")

    encoding_channels: set[str] = set()
    for element in table.iter():
        channel = _local_name(element.tag).lower()
        role = _ENCODING_ROLES.get(channel)
        if role is None:
            continue
        raw_field = str(element.attrib.get("column") or element.attrib.get("field") or "")
        tokens = _field_tokens(raw_field)
        if tokens:
            encoding_channels.add(channel)
        for token in tokens:
            field = _field_facts(token, parameters, metadata)
            if field.reference is not None:
                _add_role(roles, role, field.reference)
            elif field.cause:
                role_causes.append(f"rol {role} omitido: {field.cause}")

    mark_types = {
        str(mark.attrib.get("class") or "").strip().casefold()
        for mark in table.iter()
        if _local_name(mark.tag) == "mark" and str(mark.attrib.get("class") or "").strip()
    } or {"automatic"}
    ambiguous_marks = len(mark_types) > 1
    mark_type = next(iter(mark_types)) if not ambiguous_marks else ""
    kind = "" if ambiguous_marks else _MARK_TO_VISUAL_KIND.get(mark_type, "")
    shelf_refs = [roles[key] for key in roles if key.startswith(("x_axis", "y_axis"))]
    row_quantitative = any(field.quantitative for field in row_fields)
    column_quantitative = any(field.quantitative for field in column_fields)
    quantitative_shelf = row_quantitative or column_quantitative
    time_measure_axes = (
        any(field.temporal for field in row_fields) and column_quantitative
    ) or (any(field.temporal for field in column_fields) and row_quantitative)
    row_text = " ".join(row_tokens).casefold()
    column_text = " ".join(column_tokens).casefold()
    if ambiguous_marks:
        kind_cause = (
            "múltiples clases de marca Tableau en panes: "
            + ", ".join(sorted(mark_types))
        )
    elif not kind and "latitude" in row_text and "longitude" in column_text:
        kind = "map"
    elif not kind and "longitude" in row_text and "latitude" in column_text:
        kind = "map"
    elif not kind and "geometry" in encoding_channels:
        kind = "map"
    elif not kind and forecast_enabled and time_measure_axes:
        kind = "line"
    elif not kind and row_quantitative and column_quantitative:
        kind = "scatter"
    elif not kind and "size" in encoding_channels:
        kind = "treemap"
    elif not kind and shelf_refs and quantitative_shelf:
        kind = "bar"
    elif not kind and not shelf_refs and any(
        reference.startswith("measure:") for reference in roles.values()
    ):
        kind = "kpi"
    elif not kind and not shelf_refs and encoding_channels <= {"text", "label"} and encoding_channels:
        kind = "text_box"
    elif not kind and shelf_refs:
        kind = "table"

    if kind:
        kind_cause = ""
    elif not ambiguous_marks:
        kind_cause = (
            "tipo de marca Tableau no soportado y sin codificación suficiente para inferirlo"
        )

    return {
        "name": name,
        "title": _worksheet_title(worksheet, name, parameter_display_values),
        "kind": kind,
        "kind_cause": kind_cause,
        "data_roles": roles,
        "role_causes": list(dict.fromkeys(role_causes)),
    }


def _walk_zones(zones: ET.Element) -> list[ET.Element]:
    ordered: list[ET.Element] = []

    def visit(zone: ET.Element) -> None:
        ordered.append(zone)
        for child in _children(zone, "zone"):
            visit(child)

    for zone in _children(zones, "zone"):
        visit(zone)
    return ordered


def _zone_type(zone: ET.Element) -> str:
    return str(zone.attrib.get("type") or zone.attrib.get("type-v2") or "").strip().lower()


def _is_true(value: object) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes"}


def _geometry(zone: ET.Element, *, normalized: bool) -> dict[str, Any] | None:
    attributes = zone.attrib
    raw_values = (
        attributes.get("x"),
        attributes.get("y"),
        attributes.get("w", attributes.get("width")),
        attributes.get("h", attributes.get("height")),
    )
    if any(value is None for value in raw_values):
        return None
    try:
        x, y, width, height = (float(value) for value in raw_values)
        z_index = int(float(attributes.get("z-order", attributes.get("z_order", 0)) or 0))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    if normalized:
        x, width = x * 1280.0 / 100000.0, width * 1280.0 / 100000.0
        y, height = y * 720.0 / 100000.0, height * 720.0 / 100000.0
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z_index": z_index,
        "layout_mode": "floating" if _is_true(attributes.get("floating")) else "tiled",
    }


def _uses_normalized_layout(zones: list[ET.Element]) -> bool:
    for zone in zones:
        for key in ("x", "y", "w", "h", "width", "height"):
            try:
                if abs(float(zone.attrib.get(key, 0) or 0)) > 5000:
                    return True
            except ValueError:
                continue
    return False


def _set_names(root: ET.Element) -> set[str]:
    """Extract Tableau set/group names directly from raw workbook XML."""
    result: set[str] = set()
    for element in root.iter():
        if _local_name(element.tag) != "group" or not element.attrib.get("name"):
            continue
        has_group_filter = any(_local_name(child.tag) == "groupfilter" for child in element.iter())
        ui_builder = next(
            (
                str(value)
                for key, value in element.attrib.items()
                if key.rsplit("}", 1)[-1] == "ui-builder"
            ),
            "",
        )
        if has_group_filter or ui_builder == "filter-group":
            result.add(str(element.attrib["name"]).strip().strip("[]"))
    return result


def _parameter_names(root: ET.Element) -> set[str]:
    return {
        str(element.attrib.get("name") or "").strip().strip("[]")
        for element in root.iter()
        if _local_name(element.tag) == "column"
        and element.attrib.get("param-domain-type")
        and element.attrib.get("name")
    }


def _parameter_display_values(root: ET.Element) -> dict[str, str]:
    candidates: dict[str, set[str]] = {}
    for element in root.iter():
        if _local_name(element.tag) != "column" or not element.attrib.get("param-domain-type"):
            continue
        internal = str(element.attrib.get("name") or "").strip().strip("[]")
        if not internal:
            continue
        display = str(element.attrib.get("alias") or "").strip()
        if not display:
            display = str(element.attrib.get("value") or "").strip()
            if len(display) >= 2 and display[0] == display[-1] and display[0] in {"'", '"'}:
                display = display[1:-1]
        if display:
            candidates.setdefault(internal, set()).add(display)
    return {name: next(iter(values)) for name, values in candidates.items() if len(values) == 1}


def _parameter_captions(root: ET.Element) -> dict[str, str]:
    captions: dict[str, str] = {}
    for element in root.iter():
        if _local_name(element.tag) != "column" or not element.attrib.get("param-domain-type"):
            continue
        internal = str(element.attrib.get("name") or "").strip().strip("[]")
        caption = str(element.attrib.get("caption") or "").strip()
        if internal and caption:
            captions[internal] = caption
    return captions


def parameter_captions_from_tableau(source_path: str | Path) -> dict[str, str]:
    """Read Tableau parameter captions directly from TWB/TWBX XML.

    This intentionally bypasses ``compile_source_ast`` so D040 control titles
    are grounded in source metadata rather than the compiler under test.
    """
    path = Path(source_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Archivo fuente Tableau no encontrado: {path}")
    root, _ = _read_tableau_xml(path)

    return _parameter_captions(root)


def patch_parameter_control_titles_from_source(
    reference_payload: dict[str, Any],
    source_path: str | Path,
) -> dict[str, Any]:
    """Patch only parameter-control titles in a historical oracle payload.

    All non-title evidence remains byte-logically identical to the historical
    reference. This prevents regenerating the oracle with the compiler being
    evaluated while correcting titles that are explicitly declared in Tableau.
    """
    patched = copy.deepcopy(reference_payload)
    captions = parameter_captions_from_tableau(source_path)
    visuals = ((patched.get("plan") or {}).get("visuals") or [])
    for visual in visuals:
        if not isinstance(visual, dict):
            continue
        roles = visual.get("data_roles") or {}
        target = str(roles.get("filter_target") or "") if isinstance(roles, dict) else ""
        if not target.startswith("parameter:"):
            continue
        internal = target.removeprefix("parameter:")
        caption = captions.get(internal)
        if caption:
            visual["title"] = caption
    return patched


def compute_sha256(path: Path | str) -> str:
    """Calcula el hash SHA-256 de un archivo en bloques."""
    target_path = Path(path).resolve()
    if not target_path.is_file():
        raise FileNotFoundError(f"Archivo no encontrado para SHA-256: {target_path}")
    digest = hashlib.sha256()
    with open(target_path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_origin_oracle(
    source_path: str | Path,
    *,
    case_id: str | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """Extrae el reference_payload del oráculo visual desde un TWB/TWBX origen.

    Retorna un diccionario conforme al esquema que consume ``score_fidelity``
    en ``core/validation/visual_rubric.py``.
    """
    path = Path(source_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Archivo fuente Tableau no encontrado: {path}")

    source_sha256 = compute_sha256(path)
    cid = case_id or path.stem
    root, workbook_member = _read_tableau_xml(path)
    worksheet_elements = _top_level_items(root, "worksheets", "worksheet")
    dashboard_elements = _top_level_items(root, "dashboards", "dashboard")
    story_elements = _top_level_items(root, "stories", "story")
    parameter_captions = _parameter_captions(root)
    parameter_display_values = _parameter_display_values(root)
    parameters = _parameter_names(root) | _set_names(root)
    workbook_metadata = _metadata_by_field(root)
    forecast_worksheets = _forecast_worksheet_names(root)
    worksheets = {
        facts["name"]: facts
        for worksheet in worksheet_elements
        if (
            facts := _worksheet_facts(
                worksheet,
                parameters,
                workbook_metadata,
                forecast_enabled=str(worksheet.attrib.get("name") or "").strip()
                in forecast_worksheets,
                parameter_display_values=parameter_display_values,
            )
        )["name"]
    }

    visuals: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    placed_worksheets: set[str] = set()
    not_evaluable: list[dict[str, str]] = [
        {
            "fact": "results.visuals[].marks_count",
            "scope": "workbook",
            "status": "not_evaluable",
            "cause": "el XML TWB/TWBX no declara el conteo de marcas renderizadas",
        },
        {
            "fact": "visual.effective_colors",
            "scope": "workbook",
            "status": "not_evaluable",
            "cause": "el XML no determina la paleta efectiva después del render",
        },
        {
            "fact": "visual.effective_fonts",
            "scope": "workbook",
            "status": "not_evaluable",
            "cause": "el XML no determina las fuentes efectivas del sistema de render",
        },
        {
            "fact": "plan.interactions",
            "scope": "workbook",
            "status": "not_evaluable",
            "cause": "las acciones y controles Tableau no declaran siempre el alcance visual completo",
        },
    ]
    for story in story_elements:
        not_evaluable.append(
            {
                "fact": "page.story",
                "scope": str(story.attrib.get("name") or "story"),
                "status": "not_evaluable",
                "cause": "los story points Tableau no están soportados por el contrato visual 1.0.0",
            }
        )

    for dashboard in dashboard_elements:
        page_name = str(dashboard.attrib.get("name") or "").strip()
        if not page_name:
            continue
        zones_container = next(iter(_children(dashboard, "zones")), None)
        zones = _walk_zones(zones_container) if zones_container is not None else []
        normalized = _uses_normalized_layout(zones)
        page_visuals: list[dict[str, Any]] = []
        occurrence_by_name: dict[str, int] = {}

        worksheet_zones = [
            zone
            for zone in zones
            if _zone_type(zone) not in _CONTROL_ZONE_TYPES
            and (
                _zone_type(zone) == "worksheet"
                or str(zone.attrib.get("name") or "").strip() in worksheets
            )
        ]
        for zone in worksheet_zones:
            worksheet_name = str(zone.attrib.get("name") or zone.attrib.get("param") or "").strip()
            facts = worksheets.get(worksheet_name)
            if facts is None:
                not_evaluable.append(
                    {
                        "fact": "visual.worksheet_reference",
                        "scope": f"{page_name}::zone_{zone.attrib.get('id', '')}",
                        "status": "not_evaluable",
                        "cause": f"la zona referencia una worksheet no declarada: {worksheet_name}",
                    }
                )
                continue
            placed_worksheets.add(worksheet_name)
            base_name = f"{page_name}::{worksheet_name}"
            occurrence = occurrence_by_name.get(base_name, 0)
            occurrence_by_name[base_name] = occurrence + 1
            visual_name = (
                base_name
                if occurrence == 0
                else f"{base_name}::{zone.attrib.get('id', occurrence)}"
            )
            geometry = _geometry(zone, normalized=normalized)
            if geometry is None:
                not_evaluable.append(
                    {
                        "fact": "visual.geometry",
                        "scope": visual_name,
                        "status": "not_evaluable",
                        "cause": "la zona no declara x, y, ancho y alto numéricos positivos",
                    }
                )
            if not facts["kind"]:
                not_evaluable.append(
                    {
                        "fact": "visual.kind",
                        "scope": visual_name,
                        "status": "not_evaluable",
                        "cause": str(facts["kind_cause"]),
                    }
                )
            not_evaluable.extend(
                {
                    "fact": "visual.data_roles",
                    "scope": visual_name,
                    "status": "not_evaluable",
                    "cause": cause,
                }
                for cause in facts["role_causes"]
            )
            title = "" if str(zone.attrib.get("show-title") or "").casefold() == "false" else str(zone.attrib.get("title") or facts["title"])
            page_visuals.append(
                {
                    "name": visual_name,
                    "kind": facts["kind"],
                    "page": page_name,
                    "title": title,
                    "data_roles": dict(facts["data_roles"]),
                    "geometry": geometry or {},
                }
            )

        control_zones = [zone for zone in zones if _zone_type(zone) in _CONTROL_ZONE_TYPES]
        for index, zone in enumerate(control_zones):
            zone_type = _zone_type(zone)
            raw_target = str(
                zone.attrib.get("param")
                or zone.attrib.get("field")
                or zone.attrib.get("name")
                or ""
            ).strip()
            tokens = _field_tokens(raw_target)
            target = _logical_field_name(tokens[-1] if tokens else raw_target)
            control_name = f"{page_name}::control_{index}"
            geometry = _geometry(zone, normalized=normalized)
            if geometry is None:
                not_evaluable.append(
                    {
                        "fact": "control.geometry",
                        "scope": control_name,
                        "status": "not_evaluable",
                        "cause": "la zona de control no declara geometría numérica positiva completa",
                    }
                )
            is_parameter = zone_type in {"paramctrl", "parameter"}
            reference_type = "parameter" if is_parameter else "field"
            title = str(
                zone.attrib.get("title")
                or (parameter_captions.get(target) if is_parameter else "")
                or target
            ).strip()
            page_visuals.append(
                {
                    "name": control_name,
                    "kind": "slicer",
                    "page": page_name,
                    "title": title,
                    "data_roles": {"filter_target": f"{reference_type}:{target}"} if target else {},
                    "geometry": geometry or {},
                }
            )
            if not target:
                not_evaluable.append(
                    {
                        "fact": "control.target",
                        "scope": control_name,
                        "status": "not_evaluable",
                        "cause": "la zona de control no declara param, field ni name resoluble",
                    }
                )

        captured_zone_ids = {id(zone) for zone in (*worksheet_zones, *control_zones)}
        for zone in zones:
            zone_type = _zone_type(zone)
            if id(zone) in captured_zone_ids or zone_type in _LAYOUT_ZONE_TYPES:
                continue
            not_evaluable.append(
                {
                    "fact": "visual.zone",
                    "scope": f"{page_name}::zone_{zone.attrib.get('id', '')}",
                    "status": "not_evaluable",
                    "cause": f"zona Tableau '{zone_type}' sin proyección visual compatible",
                }
            )

        visuals.extend(page_visuals)
        pages.append(
            {"name": page_name, "page_id": page_name, "visuals_count": len(page_visuals)}
        )
        if control_zones:
            not_evaluable.append(
                {
                    "fact": "control.target_visuals",
                    "scope": page_name,
                    "status": "not_evaluable",
                    "cause": "las zonas declaran controles, pero no su alcance completo por worksheet",
                }
            )

    for worksheet_name, facts in worksheets.items():
        if worksheet_name in placed_worksheets:
            continue
        visual = {
            "name": worksheet_name,
            "kind": facts["kind"],
            "page": worksheet_name,
            "title": facts["title"],
            "data_roles": dict(facts["data_roles"]),
            "geometry": {},
        }
        visuals.append(visual)
        pages.append(
            {"name": worksheet_name, "page_id": worksheet_name, "visuals_count": 1}
        )
        not_evaluable.append(
            {
                "fact": "visual.geometry",
                "scope": worksheet_name,
                "status": "not_evaluable",
                "cause": "una worksheet sin dashboard no declara geometría de lienzo en el XML",
            }
        )
        if not facts["kind"]:
            not_evaluable.append(
                {
                    "fact": "visual.kind",
                    "scope": worksheet_name,
                    "status": "not_evaluable",
                    "cause": str(facts["kind_cause"]),
                }
            )
        not_evaluable.extend(
            {
                "fact": "visual.data_roles",
                "scope": worksheet_name,
                "status": "not_evaluable",
                "cause": cause,
            }
            for cause in facts["role_causes"]
        )

    provenance_root = Path(workspace_root).resolve() if workspace_root else _REPO_ROOT
    source_artifact = (
        path.relative_to(provenance_root) if path.is_relative_to(provenance_root) else path
    )
    return {
        "case_id": cid,
        "source_artifact": str(source_artifact).replace("\\", "/"),
        "source_sha256": source_sha256,
        "generated_at": datetime.now(UTC).isoformat(),
        "origin": "tableau",
        "plan": {
            "schema_version": "1.0.0",
            "description": "Tableau origin visual oracle reference payload",
            "pages": pages,
            "visuals": visuals,
            "interactions": [],
            "metadata": {
                "oracle_source": "raw_twb_xml",
                "workbook_member": workbook_member,
                "source_counts": {
                    "dashboards": len(dashboard_elements),
                    "worksheets": len(worksheet_elements),
                    "stories": len(story_elements),
                    "parameters": len(parameters),
                    "visuals": len(visuals),
                },
                "not_evaluable": not_evaluable,
            },
        },
        "results": {
            "visuals": {},
        },
    }


def generate_corpus_visual_oracle(
    inventory_path: str | Path,
    output_base_dir: str | Path,
) -> dict[str, Any]:
    """Genera y persiste el reference_payload para todas las demos pobladas del inventario."""
    inv_file = Path(inventory_path).resolve()
    if not inv_file.is_file():
        raise FileNotFoundError(f"Inventario no encontrado: {inv_file}")

    out_base = Path(output_base_dir).resolve()
    inventory = json.loads(inv_file.read_text(encoding="utf-8"))

    demos = inventory.get("denominator", {}).get("demos", [])
    populated_demos = [d for d in demos if d.get("status") == "populated"]

    generated_demos: list[str] = []
    blocked_demos: list[dict[str, Any]] = []
    details: dict[str, Any] = {}

    for demo in populated_demos:
        case_id = demo["case_id"]
        source_rel = demo["source_artifact"]
        source_path = _REPO_ROOT / source_rel

        try:
            ref_payload = extract_origin_oracle(
                source_path,
                case_id=case_id,
            )
            demo_out_dir = out_base / case_id
            demo_out_dir.mkdir(parents=True, exist_ok=True)
            target_file = demo_out_dir / "reference_payload.json"
            target_file.write_text(
                json.dumps(ref_payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            generated_demos.append(case_id)
            details[case_id] = {
                "status": "passed",
                "source_artifact": source_rel,
                "source_sha256": ref_payload["source_sha256"],
                "visuals_count": len(ref_payload["plan"]["visuals"]),
                "pages_count": len(ref_payload["plan"]["pages"]),
                "output_file": str(target_file.relative_to(_REPO_ROOT) if target_file.is_relative_to(_REPO_ROOT) else target_file),
            }
        except Exception as exc:
            blocked_demos.append(
                {
                    "case_id": case_id,
                    "source_artifact": source_rel,
                    "error": str(exc),
                }
            )
            details[case_id] = {
                "status": "blocked",
                "source_artifact": source_rel,
                "error": str(exc),
            }

    overall_status = "passed" if not blocked_demos else "partial" if generated_demos else "blocked"

    return {
        "status": overall_status,
        "generated_at": datetime.now(UTC).isoformat(),
        "demos_populated_count": len(populated_demos),
        "oracle_generated_count": len(generated_demos),
        "blocked_count": len(blocked_demos),
        "generated_demos": generated_demos,
        "blocked_demos": blocked_demos,
        "details": details,
    }


__all__ = [
    "compute_sha256",
    "extract_origin_oracle",
    "generate_corpus_visual_oracle",
    "parameter_captions_from_tableau",
    "patch_parameter_control_titles_from_source",
]
