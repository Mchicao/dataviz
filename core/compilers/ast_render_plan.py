"""SourceAST-native compiler: SourceAST -> neutral IRs + DataVIZ RenderPlan.

This is the maintained compiler for the neutral DataVIZ pipeline. It consumes
:class:`core.contracts.source_ast.SourceAST` (the importer-emitted origin-aware
tree) directly and emits the four neutral artifacts:

* :class:`~core.contracts.semantic_ir.SemanticIR` -- capability map per element;
* :class:`~core.contracts.presentation_ir.PresentationIR` -- pages/visuals/bindings;
* :class:`~core.contracts.interaction_ir.InteractionIR` -- filters/cross-filters;
* :class:`~core.compilers.render_plan.RenderPlan` -- the renderizable visual plan.

Boundary rules (binding long-term architecture):

* Depends only on ``core.contracts.*``, ``core.compilers.base`` and
  ``core.compilers.render_plan`` (one-way, no cycles).
* Does **not** import the legacy snapshot contract/runtime, the snapshot
  compiler, the Tableau parser, nor the importer. It never re-parses TWB XML:
  it reads AST node kinds, attributes and children only.
* Emits structural data; it does not render or execute.

Design (Ponytail): reuses the neutral mapping tables already proven by the
legacy bridge (mark -> visual kind, field role -> neutral reference) as small
local constants, sourcing them from AST nodes instead of snapshot dicts. Field
bindings are read from worksheet node ``attributes`` (neutral keys), which is
the documented escape hatch of :class:`SourceNode` until the importer resolves
shelf/mark field classification natively.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Literal

from core.compilers.base import SCHEMA_VERSION as PLAN_SCHEMA_VERSION
from core.compilers.base import DestinationPlan
from core.compilers.render_plan import SCHEMA_VERSION as RENDER_PLAN_SCHEMA_VERSION
from core.compilers.render_plan import ForecastSpec, InteractionSpec, RenderPlan, VisualSpec
from core.compilers.tableau_formula import parse_tableau_formula
from core.contracts.interaction_ir import (
    SCHEMA_VERSION as INTERACTION_SCHEMA_VERSION,
)
from core.contracts.interaction_ir import (
    CrossFilterBehavior,
    CrossFilterRule,
    DrillSpec,
    FilterCondition,
    FilterOperator,
    FilterScope,
    InteractionIR,
    PageInteractionSpec,
)
from core.contracts.presentation_ir import (
    SCHEMA_VERSION as PRESENTATION_SCHEMA_VERSION,
)
from core.contracts.presentation_ir import (
    ContainerKind,
    DataBinding,
    FieldRole,
    PagePresentation,
    PresentationIR,
    VisualGeometry,
    VisualIntentKind,
    VisualPresentation,
)
from core.contracts.provenance import OriginKind
from core.contracts.query_ast import Predicate, QuerySpec, SelectItem, SortKey
from core.contracts.semantic_ir import (
    MODEL_SCHEMA_VERSION,
    Capability,
    DataType,
    Entity,
    Expression,
    Field,
    Metric,
    Parameter,
    SemanticIR,
    SemanticModel,
    Support,
    walk_expression,
)
from core.contracts.semantic_ir import (
    SCHEMA_VERSION as SEMANTIC_SCHEMA_VERSION,
)
from core.contracts.source_ast import NodeKind, SourceAST, SourceNode

# --- Mark -> neutral visual kind (mirrors the legacy bridge mapping) ---------
_MARK_TO_VISUAL_KIND: dict[str, str] = {
    "bar": "bar",
    "column": "column",
    "line": "line",
    "area": "area",
    "pie": "pie",
    "donut": "donut",
    "scatter": "scatter",
    "heatmap": "heatmap",
    "map": "map",
    "multipolygon": "map",
    "textbox": "text_box",
    "text": "text_box",
}

# Neutral worksheet binding sources -> RenderPlan role. The neutral keys
# (columns/rows/measures/...) are the AST-native spelling of the legacy
# Campos_* shelves; the produced roles are identical to the legacy bridge.
_BINDING_SOURCES: tuple[tuple[str, str], ...] = (
    ("columns", "x_axis"),
    ("rows", "y_axis"),
    ("measures", "value"),
    ("measure_columns", "series"),
    ("measure_rows", "value"),
)

# Encoding channel -> RenderPlan role (same closed mapping as the legacy bridge).
_ENCODING_ROLES: dict[str, str] = {
    "color": "color",
    "detail": "detail",
    "geometry": "shape",
    "label": "label",
    "lod": "detail",
    "path": "series",
    "size": "size",
    "text": "label",
}

# RenderPlan visual kind -> PresentationIR visual intent.
_KIND_TO_INTENT: dict[str, str] = {
    "bar": "bar",
    "column": "column",
    "line": "line",
    "area": "area",
    "pie": "pie",
    "donut": "donut",
    "scatter": "scatter",
    "heatmap": "heatmap",
    "treemap": "treemap",
    "map": "map",
    "kpi": "kpi_card",
    "table": "table",
    "matrix": "pivot_matrix",
    "text_box": "text_box",
    "slicer": "slicer_filter",
    "card": "kpi_card",
}

# RenderPlan role -> PresentationIR field role (only mappable roles become bindings).
_ROLE_TO_FIELD_ROLE: dict[str, str] = {
    "x_axis": "x_axis",
    "y_axis": "y_axis",
    "value": "value",
    "series": "series",
    "color": "color",
    "size": "size",
    "label": "label",
    "filter_target": "filter_target",
    "tooltip": "tooltip",
    "row": "row",
    "column": "column",
}


@dataclass(frozen=True)
class SourceASTCompilation:
    """Verifiable neutral artifacts produced from a :class:`SourceAST`.

    Attributes:
        semantic_model: executable target-neutral semantic model.
        capability_report: support classification of every source element.
        presentation_ir: neutral pages/visuals/bindings presentation contract.
        interaction_ir: neutral filters/cross-filters interaction contract.
        render_plan: DataVIZ render plan wrapped in a destination envelope.
    """

    semantic_model: SemanticModel
    capability_report: SemanticIR
    presentation_ir: PresentationIR
    interaction_ir: InteractionIR
    render_plan: DestinationPlan


# ---------------------------------------------------------------------------
# Pure helpers (source-agnostic; operate on plain data, no AST coupling)
# ---------------------------------------------------------------------------


def _as_list(value: object) -> list[str]:
    """Normalize a serialized field list to a list of non-empty strings."""
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _add_role(roles: dict[str, str], role: str, value: str) -> None:
    """Append a binding, deduplicating only within the same role family."""
    if not value:
        return
    if any(
        existing == value and (key == role or key.startswith(f"{role}_"))
        for key, existing in roles.items()
    ):
        return
    key = role
    index = 1
    while key in roles:
        index += 1
        key = f"{role}_{index}"
    roles[key] = value


def _classify_formula(formula: str) -> tuple[Support, str]:
    """Classify a Tableau formula; returns status + mandatory reason if not supported."""
    if not formula:
        return Support.UNSUPPORTED, "Empty Tableau calculation cannot be executed"
    try:
        parse_tableau_formula(formula)
    except Exception:
        return Support.UNSUPPORTED, "Formula is outside the executable neutral grammar"
    return Support.SUPPORTED, ""


def _classify_filter(
    raw_type: str, attributes: Mapping[str, Any] | None = None
) -> tuple[Support, str]:
    """Classify a Tableau filter by its declared type/class."""

    filter_attributes = attributes or {}
    filter_kind = str(filter_attributes.get("filter_kind") or "")
    if filter_kind == "relative_date" and _relative_date_bounds(filter_attributes) is not None:
        return Support.SUPPORTED, ""
    if filter_kind == "top_n" and _top_n_clause(filter_attributes) is not None:
        return Support.SUPPORTED, ""
    manual_reason = str(filter_attributes.get("manual_reason") or "").strip()
    if manual_reason:
        return Support.UNSUPPORTED, manual_reason
    if not raw_type:
        return Support.SUPPORTED, ""
    if "ACTION" in raw_type.upper():
        return (
            Support.UNSUPPORTED,
            "Action filter has no direct Power BI equivalent",
        )
    return Support.SUPPORTED, ""


def _neutral_ref(
    name: str,
    measures: set[str],
    calculated_fields: set[str],
    parameters: set[str],
) -> str:
    """Convert a source field name to a neutral DataVIZ reference."""
    clean = _logical_field_name(name)
    if clean in measures:
        return f"measure:{clean}"
    if clean in parameters:
        return f"parameter:{clean}"
    if clean in calculated_fields:
        return f"field:{clean}"
    return f"field:{clean}"


_TABLEAU_VIRTUAL_FIELDS = {
    "Forecast Indicator",
    "Geometry (generated)",
    "Latitude (generated)",
    "Longitude (generated)",
    "Measure Names",
    "Measure Values",
    "Multiple Values",
}


def _logical_field_name(name: str) -> str:
    """Remove Tableau aggregation/type wrappers without losing the field id."""
    raw = name.strip()
    clean = raw.rsplit("].[", 1)[-1].strip("[]").lstrip(":")
    parts = clean.split(":")
    for index in range(len(parts) - 1, 0, -1):
        if parts[index].lower() in {"nk", "ok", "qk", "uk"}:
            return parts[index - 1]
    return clean


def _measure_names_filter_refs(
    filter_node: SourceNode, supported_measures: set[str]
) -> tuple[str, ...]:
    """Return safely bindable measures selected by a Tableau Measure Names filter."""
    if _logical_field_name(filter_node.name) != "Measure Names":
        return ()
    if str(filter_node.attributes.get("operator") or "") not in {"eq", "in"}:
        return ()
    values = filter_node.attributes.get("values")
    if not isinstance(values, list) or not values:
        return ()
    names = tuple(_logical_field_name(str(value)) for value in values)
    if not all(name in supported_measures for name in names):
        return ()
    return tuple(f"measure:{name}" for name in dict.fromkeys(names))


def _has_virtual_encoding(attrs: Mapping[str, Any], virtual_field: str) -> bool:
    """Return whether a worksheet explicitly uses a Tableau virtual field encoding."""
    encodings = attrs.get("encodings")
    if not isinstance(encodings, Mapping):
        return False
    return any(
        _logical_field_name(str(field_name)) == virtual_field
        for fields in encodings.values()
        for field_name in _as_list(fields)
    )


_QUALIFIED_TITLE_EXPR = re.compile(r"\[([^\[\]]+)\]\.\[([^\[\]]+)\]")
_FIELD_TITLE_EXPR = re.compile(r"\[([^\[\]]+)\]")
_TECHNICAL_TITLE_ONLY = re.compile(
    r"^\s*<\s*\[[^\]]+\]\.\[[^\]]+\]\s*>\s*$", re.DOTALL
)
_BARE_TABLEAU_FIELD = re.compile(
    r"^(none|yr|mn|wk|dy|sum|avg|min|max|attr|count|cntd):(.+):(nk|ok|qk)$",
    re.IGNORECASE,
)


def _last_meaningful_segment(value: str) -> str:
    """Discard dashboard prefixes and numeric duplicate-zone suffixes."""
    parts = [part.strip() for part in value.split("::") if part.strip()]
    return next((part for part in reversed(parts) if not part.isdigit()), value)


def _tableau_field_title(segments: str) -> str:
    """Reduce ``none:Ship Mode:nk`` to the authored field name."""
    parts = [part.strip() for part in segments.split(":") if part.strip()]
    if len(parts) >= 3:
        return ":".join(parts[1:-1])
    if len(parts) == 2:
        return parts[1]
    return parts[0] if parts else ""


def _display_title(
    raw: object,
    fallback_name: str = "",
    parameter_display_values: Mapping[str, str] | None = None,
) -> str:
    """Return a rendered human title instead of a raw shelf expression."""
    raw_text = str(raw or "").strip()
    parameter_values = parameter_display_values or {}
    parameter_placeholder = any(
        match.group(1).strip().casefold() == "parameters"
        and _tableau_field_title(match.group(2)) in parameter_values
        for match in _QUALIFIED_TITLE_EXPR.finditer(raw_text)
    )
    source = (
        fallback_name
        if fallback_name and _TECHNICAL_TITLE_ONLY.fullmatch(raw_text) and not parameter_placeholder
        else raw_text or fallback_name
    )
    base = _last_meaningful_segment(source)
    if not base:
        return ""
    unwrapped = base[1:-1].strip() if base.startswith("<") and base.endswith(">") else base
    bare = _BARE_TABLEAU_FIELD.fullmatch(unwrapped)
    if bare:
        return bare.group(2).strip()
    def qualified_title(match: re.Match[str]) -> str:
        field = _tableau_field_title(match.group(2))
        if match.group(1).strip().casefold() == "parameters":
            return parameter_values.get(field, field)
        return field

    resolved = _QUALIFIED_TITLE_EXPR.sub(qualified_title, unwrapped)
    resolved = _FIELD_TITLE_EXPR.sub(
        lambda match: parameter_values.get(
            _tableau_field_title(match.group(1)),
            _tableau_field_title(match.group(1)),
        ),
        resolved,
    )
    cleaned = re.sub(r"\s+", " ", re.sub(r"[<>]", " ", resolved)).strip(" >+,;/|-<")
    return cleaned or base


def _field_metadata_by_datasource(
    view: _ASTView,
) -> tuple[dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Build datasource-qualified field aliases and presentation captions."""
    lookups: dict[str, dict[str, str]] = {}
    display_names: dict[str, dict[str, str]] = {}
    for datasource in view.datasources:
        internal_names: set[str] = set()
        caption_candidates: dict[str, set[str]] = {}
        captions_by_name: dict[str, set[str]] = {}
        for node in datasource.children:
            if node.kind not in {NodeKind.FIELD, NodeKind.CALCULATION}:
                continue
            internal = _logical_field_name(node.name)
            internal_names.add(internal)
            attrs = node.attributes
            unnamed = next(
                (str(value).strip() for key, value in attrs.items() if key.endswith("}unnamed")),
                "",
            )
            caption = str(attrs.get("caption") or "").strip()
            if unnamed and re.fullmatch(r"[A-Za-z]+\([^)]*\)", caption):
                caption = unnamed
            display_name = caption or unnamed
            if display_name:
                caption_candidates.setdefault(display_name, set()).add(internal)
                captions_by_name.setdefault(internal, set()).add(display_name)

        lookup = {name: name for name in internal_names}
        lookup.update(
            {
                caption: next(iter(candidates))
                for caption, candidates in caption_candidates.items()
                if caption not in internal_names and len(candidates) == 1
            }
        )
        lookups[datasource.name] = lookup
        display_names[datasource.name] = {
            name: next(iter(captions))
            for name, captions in captions_by_name.items()
            if len(captions) == 1
        }
    return lookups, display_names


def _unique_parameter_metadata(view: _ASTView) -> tuple[dict[str, str], dict[str, str]]:
    """Resolve only unambiguous parameter aliases across datasource boundaries."""
    internal_names = {_logical_field_name(parameter.name) for parameter in view.parameters}
    aliases: dict[str, set[str]] = {}
    captions_by_name: dict[str, set[str]] = {}
    for parameter in view.parameters:
        internal = _logical_field_name(parameter.name)
        caption = str(parameter.attributes.get("caption") or "").strip()
        if caption:
            aliases.setdefault(caption, set()).add(internal)
            captions_by_name.setdefault(internal, set()).add(caption)
    lookup = {name: name for name in internal_names}
    lookup.update(
        {
            caption: next(iter(candidates))
            for caption, candidates in aliases.items()
            if caption not in internal_names and len(candidates) == 1
        }
    )
    display_names = {
        name: next(iter(captions))
        for name, captions in captions_by_name.items()
        if len(captions) == 1
    }
    return lookup, display_names


def _parameter_display_values(view: _ASTView) -> dict[str, str]:
    """Return only unambiguous authored display values for neutral parameters."""
    candidates: dict[str, set[str]] = {}
    for parameter in view.parameters:
        name = _logical_field_name(parameter.name)
        alias = str(parameter.attributes.get("alias") or "").strip()
        value = alias or str(parameter.attributes.get("value") or "").strip()
        if value:
            candidates.setdefault(name, set()).add(value)
    return {
        name: next(iter(values))
        for name, values in candidates.items()
        if len(values) == 1
    }


def _datasource_for_reference(view: _ASTView, value: object) -> str:
    """Resolve a datasource name/caption without guessing across multiple matches."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    exact = next((datasource.name for datasource in view.datasources if datasource.name == raw), "")
    if exact:
        return exact
    caption_matches = [
        datasource.name
        for datasource in view.datasources
        if str(datasource.attributes.get("caption") or "").strip() == raw
    ]
    if len(caption_matches) == 1:
        return caption_matches[0]
    worksheet = next((worksheet for worksheet in view.worksheets if worksheet.name == raw), None)
    if worksheet is not None:
        dependencies = _as_list(worksheet.attributes.get("datasources"))
        if len(dependencies) == 1:
            return dependencies[0]
    datasources = [datasource.name for datasource in view.datasources if datasource.name != "Parameters"]
    return datasources[0] if len(datasources) == 1 else ""


def _shift_month(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 + months
    year, month_zero = divmod(month_index, 12)
    return date(year, month_zero + 1, 1)


def _period_start(reference: date, period: str) -> date | None:
    if period == "day":
        return reference
    if period == "week":
        return reference - timedelta(days=reference.weekday())
    if period == "month":
        return reference.replace(day=1)
    if period == "quarter":
        return date(reference.year, ((reference.month - 1) // 3) * 3 + 1, 1)
    if period == "year":
        return date(reference.year, 1, 1)
    return None


def _shift_period(value: date, period: str, count: int) -> date:
    if period == "day":
        return value + timedelta(days=count)
    if period == "week":
        return value + timedelta(weeks=count)
    if period == "month":
        return _shift_month(value, count)
    if period == "quarter":
        return _shift_month(value, count * 3)
    return date(value.year + count, 1, 1)


def _relative_date_bounds(
    attributes: Mapping[str, Any], reference: date | None = None
) -> tuple[str, str] | None:
    spec = attributes.get("relative_date")
    if not isinstance(spec, Mapping):
        return None
    period = str(spec.get("period") or "").lower()
    current_start = _period_start(reference or date.today(), period)
    if current_start is None:
        return None
    try:
        first = int(spec.get("first_period") or 0)
        last = int(spec.get("last_period") or 0)
    except (TypeError, ValueError):
        return None
    if first > last:
        first, last = last, first
    lower = _shift_period(current_start, period, first)
    upper = _shift_period(current_start, period, last + 1) - timedelta(days=1)
    return lower.isoformat(), upper.isoformat()


def _top_n_clause(attributes: Mapping[str, Any]) -> tuple[SortKey, int] | None:
    spec = attributes.get("top_n")
    if not isinstance(spec, Mapping):
        return None
    count = spec.get("count")
    if isinstance(count, bool) or not isinstance(count, (str, int, float)):
        return None
    try:
        limit = int(count)
    except (TypeError, ValueError):
        return None
    expression_text = str(spec.get("expression") or "").strip()
    if limit <= 0 or not expression_text:
        return None
    try:
        expression = parse_tableau_formula(expression_text)
    except Exception:
        return None
    raw_direction = str(spec.get("direction") or "").lower()
    direction: Literal["asc", "desc"]
    if raw_direction == "asc":
        direction = "asc"
    elif raw_direction == "desc":
        direction = "desc"
    else:
        direction = "asc" if str(spec.get("end") or "top").lower() == "bottom" else "desc"
    return SortKey(expression=expression, direction=direction), limit


def _visual_query(
    attrs: Mapping[str, Any],
    roles: Mapping[str, str],
    default_datasource: str | None,
    supported_measures: set[str],
    filters: list[SourceNode] | None = None,
) -> QuerySpec | None:
    """Compile visual bindings to a typed expression query."""
    datasources = [
        str(value)
        for value in _as_list(attrs.get("datasources"))
        if value and str(value) != "Parameters"
    ]
    datasource = datasources[0] if len(datasources) == 1 else default_datasource
    if not datasource or not roles:
        return None
    select: list[SelectItem] = []
    group_by: list[Expression] = []
    seen: set[str] = set()
    has_measure = False
    for reference in roles.values():
        if reference in seen or ":" not in reference:
            continue
        seen.add(reference)
        kind, name = reference.split(":", 1)
        if not name or kind not in {"field", "measure", "parameter"}:
            continue
        if kind == "measure" and name not in supported_measures:
            return None
        expression_kind = {
            "field": "field_ref",
            "measure": "measure_ref",
            "parameter": "parameter_ref",
        }[kind]
        expression = Expression(kind=expression_kind, name=name)
        select.append(SelectItem(expression=expression, alias=name))
        if kind == "field":
            group_by.append(expression)
        elif kind == "measure":
            has_measure = True
    predicates: list[Predicate] = []
    sort_by: list[SortKey] = []
    limit: int | None = None
    for filter_node in filters or []:
        relative_bounds = _relative_date_bounds(filter_node.attributes)
        if relative_bounds is not None:
            field_name = _logical_field_name(filter_node.name)
            predicates.append(
                Predicate(
                    op="between",
                    expression=Expression(kind="field_ref", name=field_name),
                    values=list(relative_bounds),
                )
            )
            continue
        top_n = _top_n_clause(filter_node.attributes)
        if top_n is not None:
            sort_key, top_limit = top_n
            sort_expression = sort_key.expression
            if (
                sort_expression.kind == "agg"
                and len(sort_expression.children) == 1
                and sort_expression.children[0].kind == "field_ref"
                and sort_expression.children[0].name in supported_measures
            ):
                sort_key = replace(
                    sort_key,
                    expression=Expression(
                        kind="measure_ref", name=sort_expression.children[0].name
                    ),
                )
            sort_by.append(sort_key)
            limit = top_limit if limit is None else min(limit, top_limit)
            continue
        operator = str(filter_node.attributes.get("operator") or "")
        values = filter_node.attributes.get("values")
        if (
            operator
            not in {
                "eq",
                "ne",
                "gt",
                "gte",
                "lt",
                "lte",
                "in",
                "not_in",
                "between",
                "contains",
                "not_contains",
                "starts_with",
                "not_starts_with",
                "ends_with",
                "not_ends_with",
            }
            or not isinstance(values, list)
            or not values
        ):
            continue
        field_name = _logical_field_name(filter_node.name)
        if field_name in _TABLEAU_VIRTUAL_FIELDS:
            continue
        predicates.append(
            Predicate(
                op=operator,
                expression=Expression(kind="field_ref", name=field_name),
                values=values,
            )
        )
    return QuerySpec(
        from_datasource=datasource,
        select=select,
        filters=predicates,
        group_by=group_by if has_measure else [],
        sort_by=sort_by,
        limit=limit,
    )


def _bindings(
    attrs: Mapping[str, Any],
    measures: set[str],
    calculated_fields: set[str],
    parameters: set[str],
    filters: list[SourceNode] | None = None,
) -> dict[str, str]:
    """Derive RenderPlan data_roles from neutral worksheet binding attributes."""
    roles: dict[str, str] = {}
    for source_key, role in _BINDING_SOURCES:
        for field_name in _as_list(attrs.get(source_key)):
            if _logical_field_name(field_name) not in _TABLEAU_VIRTUAL_FIELDS:
                _add_role(
                    roles,
                    role,
                    _neutral_ref(field_name, measures, calculated_fields, parameters),
                )
    encodings = attrs.get("encodings")
    if isinstance(encodings, Mapping):
        for encoding, fields in encodings.items():
            role = _ENCODING_ROLES.get(str(encoding).lower(), "detail")
            for field_name in _as_list(fields):
                logical_name = _logical_field_name(field_name)
                if logical_name not in _TABLEAU_VIRTUAL_FIELDS:
                    _add_role(
                        roles,
                        role,
                        _neutral_ref(field_name, measures, calculated_fields, parameters),
                    )
                    continue
                if logical_name == "Measure Names":
                    for filter_node in filters or []:
                        for reference in _measure_names_filter_refs(filter_node, measures):
                            _add_role(roles, role, reference)
    return roles


def _visual_kind(
    mark_type: str,
    attrs: Mapping[str, Any],
    roles: Mapping[str, str] | None = None,
) -> str:
    """Map a worksheet mark and its bindings to a neutral visual kind."""
    raw = str(mark_type or attrs.get("mark_type") or "").lower()
    mapped = _MARK_TO_VISUAL_KIND.get(raw) if raw != "automatic" else None
    if mapped:
        return mapped
    rows = _as_list(attrs.get("rows"))
    columns = _as_list(attrs.get("columns"))
    measures = _as_list(attrs.get("measures"))
    encodings = attrs.get("encodings")
    encoding_channels = (
        {str(channel).lower() for channel, values in encodings.items() if _as_list(values)}
        if isinstance(encodings, Mapping)
        else set()
    )
    encoding_values = (
        [value for values in encodings.values() for value in _as_list(values)]
        if isinstance(encodings, Mapping)
        else []
    )
    has_quantitative_encoding = any(
        any(marker in value.casefold() for marker in (":qk", "sum:", "avg:", "min:", "max:"))
        for value in encoding_values
    )
    row_text = " ".join(rows).casefold()
    column_text = " ".join(columns).casefold()
    if "latitude" in row_text and "longitude" in column_text:
        return "map"
    if "longitude" in row_text and "latitude" in column_text:
        return "map"
    if "geometry" in encoding_channels:
        return "map"
    scatter_mark = raw in {"circle", "shape"} or (
        raw == "automatic" and bool({"lod", "detail"} & encoding_channels)
    )
    if scatter_mark:
        row_is_quantitative = any(
            marker in row_text for marker in (":qk", "sum:", "avg:", "min:", "max:")
        )
        column_is_quantitative = any(
            marker in column_text for marker in (":qk", "sum:", "avg:", "min:", "max:")
        )
        if row_is_quantitative and column_is_quantitative:
            return "scatter"
    if "size" in encoding_channels:
        return "treemap"
    shelf_fields = [*rows, *columns]
    has_quantitative_shelf = any(
        any(marker in field.lower() for marker in (":qk", "sum:", "avg:", "min:", "max:"))
        for field in shelf_fields
    )
    if shelf_fields and has_quantitative_shelf:
        return "bar"
    has_measure_role = any(
        str(reference).startswith("measure:") for reference in (roles or {}).values()
    )
    if (
        not shelf_fields
        and (has_quantitative_encoding or has_measure_role)
        and encoding_channels <= {"text", "label", "lod", "color"}
    ):
        return "kpi"
    if not shelf_fields and encoding_channels <= {"text", "label"} and encoding_channels:
        return "text_box"
    if not shelf_fields and "label" in encoding_channels and "color" in encoding_channels:
        return "kpi"
    title = str(attrs.get("title") or "").strip()
    if title and not rows and not columns and not measures and not encoding_channels:
        return "text_box"
    if measures and not rows and not columns:
        return "kpi"
    return "table"


def _geometry(zone_attrs: Mapping[str, Any], *, normalized: bool = False) -> dict[str, Any]:
    """Build the neutral geometry dict from a layout-zone node attributes."""
    x = float(zone_attrs.get("x", 0) or 0)
    y = float(zone_attrs.get("y", 0) or 0)
    width = float(zone_attrs.get("w", zone_attrs.get("width", 0)) or 0)
    height = float(zone_attrs.get("h", zone_attrs.get("height", 0)) or 0)
    if normalized:
        x, width = x * 1280.0 / 100000.0, width * 1280.0 / 100000.0
        y, height = y * 720.0 / 100000.0, height * 720.0 / 100000.0
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "z_index": int(zone_attrs.get("z_order", 0) or 0),
        "layout_mode": "floating" if zone_attrs.get("floating") else "tiled",
    }


def _mark_type(worksheet: SourceNode) -> str:
    """Return the mark type declared by the worksheet's first VISUAL child."""
    for child in worksheet.children:
        if child.kind is NodeKind.VISUAL:
            return child.name
    return ""


def _forecast_spec(
    worksheet: SourceNode | None,
    roles: dict[str, str],
) -> ForecastSpec | None:
    """Compila la intención predictiva cuando fecha y medida son ejecutables."""
    if worksheet is None:
        return None
    node = next((child for child in worksheet.children if child.kind is NodeKind.FORECAST), None)
    if node is None or not bool(node.attributes.get("enabled")):
        return None
    time_ref = next((ref for ref in roles.values() if ref.startswith("field:")), "")
    value_ref = next((ref for ref in roles.values() if ref.startswith("measure:")), "")
    if not time_ref or not value_ref:
        return None
    period = str(node.attributes.get("period", "month"))
    horizon = {"day": 7, "week": 52, "month": 12, "quarter": 4, "year": 3}[period]
    roles.update(
        {
            "color": "field:Forecast Indicator",
            "lower_bound": "field:Forecast Lower",
            "upper_bound": "field:Forecast Upper",
        }
    )
    return ForecastSpec(
        time_field=time_ref.split(":", 1)[1],
        value_field=value_ref.split(":", 1)[1],
        period=period,
        horizon=horizon,
        confidence_level=float(node.attributes.get("confidence_level", 95)),
        ignore_last=int(node.attributes.get("ignore_last", 0)),
        fill_missing=bool(node.attributes.get("fill_missing")),
        seasonal=str(node.attributes.get("model_family", "")).lower() == "auto-season",
        prediction_intervals=bool(node.attributes.get("prediction_intervals")),
    )


def _walk(node: SourceNode):
    """Yield ``node`` and every nested child (depth-first, pre-order)."""
    yield node
    for child in node.children:
        yield from _walk(child)


# ---------------------------------------------------------------------------
# AST view: structural projection of a SourceAST tree
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ASTView:
    """Structural projection of a SourceAST tree used by the compiler."""

    dashboards: list[SourceNode] = field(default_factory=list)
    worksheets: list[SourceNode] = field(default_factory=list)
    datasources: list[SourceNode] = field(default_factory=list)
    calculations: list[SourceNode] = field(default_factory=list)
    parameters: list[SourceNode] = field(default_factory=list)
    filters: list[SourceNode] = field(default_factory=list)
    actions: list[SourceNode] = field(default_factory=list)


def _build_view(ast: SourceAST) -> _ASTView:
    """Project the AST root children into the compiler's structural view."""
    top = ast.root.children
    all_nodes = list(_walk(ast.root))
    return _ASTView(
        dashboards=[n for n in top if n.kind is NodeKind.PAGE and n.origin_tag == "dashboard"],
        worksheets=[n for n in top if n.kind is NodeKind.PAGE and n.origin_tag == "worksheet"],
        datasources=[n for n in top if n.kind is NodeKind.DATASOURCE],
        calculations=[n for n in all_nodes if n.kind is NodeKind.CALCULATION],
        parameters=[n for n in all_nodes if n.kind is NodeKind.PARAMETER],
        filters=[n for n in all_nodes if n.kind is NodeKind.FILTER],
        actions=[n for n in all_nodes if n.origin_tag == "action"],
    )


# ---------------------------------------------------------------------------
# SemanticIR from AST
# ---------------------------------------------------------------------------


def _build_semantic_ir(
    view: _ASTView, ast: SourceAST, supported_measures: set[str]
) -> SemanticIR:
    """Emit one Capability per source element, in canonical order."""
    capabilities: list[Capability] = []
    counters: dict[str, int] = {}
    bound_virtual_filter_ids = {
        child.node_id
        for worksheet in view.worksheets
        if _has_virtual_encoding(worksheet.attributes, "Measure Names")
        for child in worksheet.children
        if child.kind is NodeKind.FILTER
        and bool(_measure_names_filter_refs(child, supported_measures))
    }

    def emit(kind: str, name: str, status: Support, reason: str = "") -> None:
        source_index = counters.get(kind, 0)
        capabilities.append(
            Capability(
                kind=kind,
                name=name,
                status=status,
                reason=reason,
                source_index=source_index,
            )
        )
        counters[kind] = source_index + 1

    for db in view.dashboards:
        emit("dashboard", db.name, Support.SUPPORTED)
    for ws in view.worksheets:
        emit("worksheet", ws.name, Support.SUPPORTED)
    for ds in view.datasources:
        emit("datasource", ds.name, Support.SUPPORTED)
    for calc in view.calculations:
        formula = str(calc.attributes.get("formula") or "")
        status, reason = (
            (Support.SUPPORTED, "")
            if calc.attributes.get("group_mapping") is not None
            else _classify_formula(formula)
        )
        emit("calculated_field", calc.name, status, reason)
    for param in view.parameters:
        if param.attributes.get("security_policy"):
            emit(
                "source_security_policy",
                param.name,
                Support.MANUAL,
                "Tableau identity/user filter requires explicit RLS translation review",
            )
        else:
            emit("parameter", param.name, Support.SUPPORTED)
    for flt in view.filters:
        raw_type = str(
            flt.attributes.get("filter_type")
            or flt.attributes.get("class")
            or flt.attributes.get("type")
            or ""
        )
        if flt.node_id in bound_virtual_filter_ids:
            status, reason = Support.SUPPORTED, ""
        elif _logical_field_name(flt.name) in _TABLEAU_VIRTUAL_FIELDS:
            status, reason = (
                Support.MANUAL,
                "Tableau virtual-field filter cannot be safely lowered to visual bindings",
            )
        else:
            status, reason = _classify_filter(raw_type, flt.attributes)
        emit("filter", flt.name, status, reason)
    for action in view.actions:
        command = str(action.attributes.get("command") or "")
        action_type = str(action.attributes.get("type") or "").lower()
        target_url = str(action.attributes.get("target_url") or "")
        if command == "tsc:tsl-filter" or action_type == "filter":
            emit("action", action.name, Support.SUPPORTED)
        elif target_url:
            emit("action", action.name, Support.MANUAL, "URL action execution pending")
        elif action_type == "highlight" or command == "tsc:brush":
            emit("action", action.name, Support.MANUAL, "Highlight action rendering pending")
        else:
            emit("action", action.name, Support.UNSUPPORTED, "Unknown Tableau action semantics")

    return SemanticIR(
        schema_version=SEMANTIC_SCHEMA_VERSION,
        source_schema_version=ast.schema_version,
        capabilities=capabilities,
    )


_TABLEAU_TYPES: dict[str, DataType] = {
    "bool": DataType.BOOLEAN,
    "boolean": DataType.BOOLEAN,
    "date": DataType.DATE,
    "datetime": DataType.DATETIME,
    "integer": DataType.INTEGER,
    "int": DataType.INTEGER,
    "real": DataType.DECIMAL,
    "float": DataType.DECIMAL,
    "double": DataType.DECIMAL,
    "number": DataType.DECIMAL,
    "string": DataType.STRING,
}


def _data_type(raw: object) -> DataType:
    """Map a Tableau datatype to the closed neutral type enum."""
    return _TABLEAU_TYPES.get(str(raw or "").lower(), DataType.UNKNOWN)


def _parameter_default(value: object) -> object:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        expression = parse_tableau_formula(text)
    except Exception:
        return text
    return expression.value if expression.kind == "literal" else text


def _formula_expression(formula: str) -> tuple[Expression, str]:
    """Lower a complete Tableau formula or fail without fabricating BLANK."""
    return parse_tableau_formula(formula), ""


def _calculation_expression(node: SourceNode) -> Expression:
    mapping = node.attributes.get("group_mapping")
    source = str(node.attributes.get("group_source") or "")
    if source and isinstance(mapping, Mapping):
        return Expression(kind="lookup_map", name=source, value=dict(mapping))
    expression = parse_tableau_formula(str(node.attributes.get("formula") or ""))
    table_calc = node.attributes.get("table_calc")
    if expression.kind == "window" and isinstance(table_calc, Mapping):
        expression = replace(expression, value=dict(table_calc))
    return expression


def _has_aggregate(expression: Expression) -> bool:
    return any(node.kind in {"agg", "lod", "window"} for node in walk_expression(expression))


def _set_is_dynamic(node: SourceNode) -> bool:
    """True when the raw set payload declares a dynamic selection (Top-N / end).

    A dynamic set is detected from the raw ``groupfilter`` payload carrying a
    ``function="end"`` attribute at any nesting depth (e.g. Top-N). Static sets
    populate members through ``function="member"`` and never emit ``end``, so
    they stay materialized.
    """
    payload = node.raw_payload
    if not isinstance(payload, Mapping):
        return False
    stack: list[object] = [payload]
    while stack:
        current = stack.pop()
        if not isinstance(current, Mapping):
            continue
        attrib = current.get("attrib")
        if isinstance(attrib, Mapping) and str(attrib.get("function", "")).casefold() == "end":
            return True
        children = current.get("children")
        if isinstance(children, (list, tuple)):
            stack.extend(children)
    return False


def _resolve_set_refs(
    expression: Expression,
    sets: Mapping[str, tuple[str, tuple[object, ...] | None, bool]],
) -> Expression:
    if expression.kind == "field_ref" and expression.name in sets:
        target, members, _is_dynamic = sets[expression.name]
        return Expression(
            kind="set_membership",
            name=expression.name,
            entity=target,
            value=members,
        )
    children = tuple(_resolve_set_refs(child, sets) for child in expression.children)
    return replace(expression, children=children) if children != expression.children else expression


def _resolve_semantic_refs(
    expression: Expression,
    metric_names: set[str],
    *,
    current_metric: str = "",
) -> Expression:
    """Convierte referencias Tableau entre cálculos al símbolo neutral correcto."""
    children = tuple(
        _resolve_semantic_refs(child, metric_names, current_metric=current_metric)
        for child in expression.children
    )
    resolved = (
        replace(expression, children=children) if children != expression.children else expression
    )
    if (
        resolved.kind == "field_ref"
        and resolved.name in metric_names
        and resolved.name != current_metric
    ):
        return Expression(kind="measure_ref", name=resolved.name)
    return resolved


def _infer_literal_type(value: object) -> DataType:
    if isinstance(value, bool):
        return DataType.BOOLEAN
    if isinstance(value, int):
        return DataType.INTEGER
    if isinstance(value, float):
        return DataType.DECIMAL
    if isinstance(value, str):
        return DataType.STRING
    return DataType.UNKNOWN


def _annotate_expression_types(
    expression: Expression,
    *,
    field_types_by_entity: Mapping[str, Mapping[str, DataType]],
    field_types_by_name: Mapping[str, frozenset[DataType]],
    parameter_types: Mapping[str, DataType],
    metric_types: Mapping[str, DataType],
) -> Expression:
    children = tuple(
        _annotate_expression_types(
            child,
            field_types_by_entity=field_types_by_entity,
            field_types_by_name=field_types_by_name,
            parameter_types=parameter_types,
            metric_types=metric_types,
        )
        for child in expression.children
    )
    resolved = replace(expression, children=children) if children != expression.children else expression
    data_type = resolved.data_type
    if data_type is not DataType.UNKNOWN:
        return resolved
    if resolved.kind == "literal":
        data_type = _infer_literal_type(resolved.value)
    elif resolved.kind == "parameter_ref":
        data_type = parameter_types.get(resolved.name, DataType.UNKNOWN)
    elif resolved.kind == "measure_ref":
        data_type = metric_types.get(resolved.name, DataType.UNKNOWN)
    elif resolved.kind == "field_ref":
        if resolved.entity:
            data_type = field_types_by_entity.get(resolved.entity, {}).get(
                resolved.name, DataType.UNKNOWN
            )
        else:
            candidates = field_types_by_name.get(resolved.name, frozenset())
            if len(candidates) == 1:
                data_type = next(iter(candidates))
    elif resolved.kind == "set_membership":
        data_type = DataType.BOOLEAN
    elif resolved.kind == "binary" and resolved.op in {"eq", "ne", "lt", "le", "gt", "ge", "and", "or"}:
        data_type = DataType.BOOLEAN
    elif resolved.kind == "binary" and resolved.op == "concat":
        data_type = DataType.STRING
    return replace(resolved, data_type=data_type) if data_type is not resolved.data_type else resolved


def _default_aggregation(attributes: Mapping[str, Any]) -> str:
    """Normaliza la agregación declarada por Tableau a la gramática neutral."""
    raw = str(attributes.get("aggregation") or "sum").strip().casefold()
    return {
        "average": "avg",
        "avg": "avg",
        "count": "count",
        "countd": "count_distinct",
        "count distinct": "count_distinct",
        "median": "median",
        "min": "min",
        "minimum": "min",
        "max": "max",
        "maximum": "max",
        "sum": "sum",
    }.get(raw, "sum")


def _build_semantic_model(view: _ASTView, ast: SourceAST) -> SemanticModel:
    """Build the executable neutral model directly from Tableau SourceAST."""
    entities: list[Entity] = []
    metrics: list[Metric] = []
    parameters: list[Parameter] = []
    seen_metrics: set[str] = set()
    seen_parameters: set[str] = set()
    field_types: dict[str, DataType] = {
        child.name.strip("[]"): _data_type(child.attributes.get("datatype"))
        for datasource in view.datasources
        for child in datasource.children
        if child.kind is NodeKind.FIELD
    }
    # Canonical set registry: target field, member selection, dynamic flag.
    # Dynamic (Top-N / end) sets keep ``None`` as their selection because no
    # member list can be materialized; static sets carry their member tuple.
    sets: dict[str, tuple[str, tuple[object, ...] | None, bool]] = {}
    for datasource in view.datasources:
        for child in datasource.children:
            if (
                child.kind is NodeKind.PARAMETER
                and child.origin_tag == "set"
                and not child.attributes.get("security_policy")
            ):
                is_dynamic = _set_is_dynamic(child)
                sets[child.name.strip("[]")] = (
                    _logical_field_name(str(child.attributes.get("set_target") or "")),
                    None if is_dynamic else tuple(child.attributes.get("default_members", ())),
                    is_dynamic,
                )
    for datasource in view.datasources:
        fields: list[Field] = []
        for child in datasource.children:
            name = child.name.strip("[]")
            if child.kind is NodeKind.FIELD:
                # Tableau injects relationship/object identifiers that are not
                # user fields and usually carry no datatype. Emitting them as
                # TMDL Variant makes an otherwise valid PBIP fail to open.
                if name.lower().startswith("__tableau_internal_object_id__"):
                    continue
                fields.append(
                    Field(
                        name=name,
                        data_type=_data_type(child.attributes.get("datatype")),
                        source_column=name,
                    )
                )
                if (
                    str(child.attributes.get("role", "")).lower() == "measure"
                    and name not in seen_metrics
                ):
                    metrics.append(
                        Metric(
                            name=name,
                            expression=Expression(
                                kind="agg",
                                name=_default_aggregation(child.attributes),
                                children=(Expression(kind="field_ref", name=name),),
                            ),
                            entity=datasource.name.strip("[]"),
                            data_type=_data_type(child.attributes.get("datatype")),
                        )
                    )
                    seen_metrics.add(name)
            elif child.kind is NodeKind.CALCULATION and name not in seen_metrics:
                formula = str(child.attributes.get("formula") or "")
                if formula and _classify_formula(formula)[0] is Support.UNSUPPORTED:
                    continue
                try:
                    expression = _calculation_expression(child)
                    expression = _resolve_set_refs(expression, sets)
                except Exception as exc:
                    raise RuntimeError(
                        f"calculation {name!r} is supported but failed to compile: {exc}"
                    ) from exc
                if str(child.attributes.get("role", "")).lower() == "dimension":
                    fields.append(
                        Field(
                            name=name,
                            data_type=_data_type(child.attributes.get("datatype")),
                            expression=expression,
                        )
                    )
                else:
                    if not _has_aggregate(expression):
                        expression = Expression(
                            kind="agg",
                            name=_default_aggregation(child.attributes),
                            children=(expression,),
                        )
                    metrics.append(
                        Metric(
                            name=name,
                            expression=expression,
                            entity=datasource.name.strip("[]"),
                            data_type=_data_type(child.attributes.get("datatype")),
                        )
                    )
                    seen_metrics.add(name)
            elif child.kind is NodeKind.PARAMETER:
                if child.attributes.get("security_policy"):
                    continue
                if name in seen_parameters:
                    continue
                is_set = child.origin_tag == "set"
                if is_set:
                    set_info = sets.get(name)
                    set_target = set_info[0] if set_info else ""
                    set_members = set_info[1] if set_info else ()
                    set_dynamic = set_info[2] if set_info else False
                    parameters.append(
                        Parameter(
                            name=name,
                            data_type=field_types.get(set_target, DataType.UNKNOWN),
                            is_set=True,
                            default_value=(None if set_dynamic else tuple(set_members)),
                        )
                    )
                else:
                    parameters.append(
                        Parameter(
                            name=name,
                            data_type=_data_type(child.attributes.get("datatype")),
                            default_value=_parameter_default(child.attributes.get("value")),
                        )
                    )
                seen_parameters.add(name)
        if fields or any(child.kind is NodeKind.CALCULATION for child in datasource.children):
            entities.append(Entity(name=datasource.name.strip("[]"), fields=fields))
    metric_names = {metric.name for metric in metrics}
    metrics = [
        replace(
            metric,
            expression=_resolve_semantic_refs(
                metric.expression,
                metric_names,
                current_metric=metric.name,
            ),
        )
        for metric in metrics
    ]
    entities = [
        replace(
            entity,
            fields=[
                replace(
                    model_field,
                    expression=(
                        _resolve_semantic_refs(model_field.expression, metric_names)
                        if model_field.expression is not None
                        else None
                    ),
                )
                for model_field in entity.fields
            ],
        )
        for entity in entities
    ]
    field_types_by_entity = {
        entity.name: {field.name: field.data_type for field in entity.fields}
        for entity in entities
    }
    by_name: dict[str, set[DataType]] = {}
    for entity in entities:
        for model_field in entity.fields:
            by_name.setdefault(model_field.name, set()).add(model_field.data_type)
    field_types_by_name = {name: frozenset(types) for name, types in by_name.items()}
    parameter_types = {parameter.name: parameter.data_type for parameter in parameters}
    metric_types = {metric.name: metric.data_type for metric in metrics}
    metrics = [
        replace(
            metric,
            expression=_annotate_expression_types(
                metric.expression,
                field_types_by_entity=field_types_by_entity,
                field_types_by_name=field_types_by_name,
                parameter_types=parameter_types,
                metric_types=metric_types,
            ),
        )
        for metric in metrics
    ]
    entities = [
        replace(
            entity,
            fields=[
                replace(
                    model_field,
                    expression=(
                        _annotate_expression_types(
                            model_field.expression,
                            field_types_by_entity=field_types_by_entity,
                            field_types_by_name=field_types_by_name,
                            parameter_types=parameter_types,
                            metric_types=metric_types,
                        )
                        if model_field.expression is not None
                        else None
                    ),
                )
                for model_field in entity.fields
            ],
        )
        for entity in entities
    ]
    return SemanticModel(
        schema_version=MODEL_SCHEMA_VERSION,
        name=ast.root.name or "tableau_model",
        entities=entities,
        metrics=metrics,
        parameters=parameters,
        description="Compiled directly from Tableau SourceAST.",
    )


# ---------------------------------------------------------------------------
# Controls -> slicer visuals + filter interactions (AST-native)
# ---------------------------------------------------------------------------


def _materialize_control_query(visual: VisualSpec, datasource: str) -> VisualSpec:
    reference = visual.data_roles.get("filter_target", "")
    if not reference.startswith("field:"):
        return visual
    name = reference.split(":", 1)[1]
    expression = Expression(kind="field_ref", name=name)
    return replace(
        visual,
        query=QuerySpec(
            from_datasource=datasource,
            select=[SelectItem(expression=expression, alias=name)],
            group_by=[expression],
        ),
    )


def _controls(
    db: SourceNode,
    db_name: str,
    sheet_visual_names: list[str],
    measures: set[str],
    calculated_fields: set[str],
    parameters: set[str],
    normalized_layout: bool,
    empty_set_fields: set[str],
    empty_set_parameters: set[str],
    parameter_display_names: Mapping[str, str] | None = None,
    *,
    view: _ASTView | None = None,
    page_datasources: set[str] | None = None,
) -> tuple[list[VisualSpec], list[InteractionSpec], list[FilterCondition]]:
    """Project dashboard controls as slicer visuals + filter interactions."""
    visuals: list[VisualSpec] = []
    interactions: list[InteractionSpec] = []
    conditions: list[FilterCondition] = []
    controls = db.attributes.get("controls", [])
    if not isinstance(controls, list):
        return visuals, interactions, conditions
    field_names_by_datasource = _field_metadata_by_datasource(view)[0] if view is not None else {}

    for index, raw in enumerate(controls):
        if not isinstance(raw, Mapping):
            continue
        control_id = f"{db_name}::control_{index}"
        field_name = str(raw.get("field") or raw.get("parameter") or "").strip()
        control_datasource = ""
        if view is not None and raw.get("field"):
            control_datasource = _datasource_for_reference(view, raw.get("datasource"))
            if not control_datasource and field_name:
                owners = [
                    datasource
                    for datasource, lookup in field_names_by_datasource.items()
                    if field_name in lookup or _logical_field_name(field_name) in lookup
                ]
                if len(owners) == 1:
                    control_datasource = owners[0]
            if not control_datasource and page_datasources and len(page_datasources) == 1:
                control_datasource = next(iter(page_datasources))
            if control_datasource:
                field_name = field_names_by_datasource.get(control_datasource, {}).get(
                    field_name, field_name
                )
        ref = (
            _neutral_ref(field_name, measures, calculated_fields, parameters) if field_name else ""
        )
        layout = raw.get("layout")
        geometry = (
            _geometry(layout, normalized=normalized_layout) if isinstance(layout, Mapping) else {}
        )
        geometry.setdefault("layout_mode", "tiled")
        title = _display_title(raw.get("title") or raw.get("type") or "", field_name)
        authored_title = str(raw.get("title") or "").strip()
        parameter_title = (parameter_display_names or {}).get(field_name, "")
        if parameter_title and (not authored_title or _logical_field_name(authored_title) == field_name):
            title = parameter_title
        visual = VisualSpec(
            name=control_id,
            kind="slicer",
            page=db_name,
            title=title,
            data_roles={"filter_target": ref} if ref else {},
            geometry=geometry,
            liveness_policy=_control_liveness_policy(
                ref, empty_set_fields, empty_set_parameters
            ),
        )
        if control_datasource:
            visual = _materialize_control_query(visual, control_datasource)
        visuals.append(visual)
        raw_values = raw.get("values")
        if isinstance(raw_values, list):
            control_values = tuple(v for v in raw_values if v is not None)
        else:
            control_values = ()
        for target in sheet_visual_names:
            interactions.append(
                InteractionSpec(
                    name=f"{control_id}->{target}",
                    source=control_id,
                    target=target,
                    kind="filter",
                    data_role=ref,
                    values=control_values,
                )
            )
        # Tableau uses values such as "All" as a UI sentinel for a cleared
        # selection. It must not become a literal PBIR filter for the string
        # "All". Multi-select controls are represented with IN.
        all_sentinels = {"all", "(all)", "%all%", "*"}
        condition_values = (
            ()
            if len(control_values) == 1
            and isinstance(control_values[0], str)
            and control_values[0].strip().casefold() in all_sentinels
            else control_values
        )
        condition_operator = (
            FilterOperator.IN if len(condition_values) > 1 else FilterOperator.EQUALS
        )
        conditions.append(
            FilterCondition(
                filter_id=control_id,
                name=title,
                target_field=field_name or control_id,
                operator=condition_operator,
                values=condition_values,
                scope=FilterScope.PAGE,
                target_visual_ids=tuple(sheet_visual_names),
                is_interactive_slicer=True,
            )
        )
    return visuals, interactions, conditions


# ---------------------------------------------------------------------------
# Presentation / Interaction IR builders
# ---------------------------------------------------------------------------


def _to_visual_geometry(geometry: Mapping[str, Any]) -> VisualGeometry:
    """Build a VisualGeometry from a neutral geometry dict."""
    mode_raw = geometry.get("layout_mode", "tiled")
    try:
        layout_mode = ContainerKind(mode_raw)
    except ValueError:
        layout_mode = ContainerKind.TILED
    return VisualGeometry(
        x=float(geometry.get("x", 0.0)),
        y=float(geometry.get("y", 0.0)),
        width=float(geometry.get("width", 0.0)),
        height=float(geometry.get("height", 0.0)),
        z_index=int(geometry.get("z_index", 0)),
        layout_mode=layout_mode,
    )


def _to_presentation_visual(
    visual: VisualSpec, display_names: Mapping[str, str] | None = None
) -> VisualPresentation:
    """Lift a RenderPlan VisualSpec into a neutral VisualPresentation."""
    intent_str = _KIND_TO_INTENT.get(visual.kind, "custom_visual")
    intent = VisualIntentKind(intent_str)
    bindings: list[DataBinding] = []
    for idx, (role, ref) in enumerate(visual.data_roles.items()):
        field_role_str = _ROLE_TO_FIELD_ROLE.get(role)
        if field_role_str is None:
            continue
        field_name = ref.split(":", 1)[1] if ":" in ref else ref
        bindings.append(
            DataBinding(
                binding_id=f"{visual.name}::{role}_{idx}",
                field_name=field_name,
                role=FieldRole(field_role_str),
                display_name=(display_names or {}).get(field_name) or field_name,
            )
        )
    return VisualPresentation(
        visual_id=visual.name,
        intent=intent,
        title=visual.title,
        geometry=_to_visual_geometry(visual.geometry),
        bindings=tuple(bindings),
    )


def _cross_filters(
    db: SourceNode,
    actions: list[SourceNode],
    visual_names: list[str],
) -> list[CrossFilterRule]:
    """Derive cross-filter rules from dashboard action nodes."""
    rules: list[CrossFilterRule] = []
    for idx, action in enumerate(actions):
        source_dashboard = str(action.attributes.get("source_dashboard") or "")
        source_worksheet = str(action.attributes.get("source_worksheet") or "")
        if (source_dashboard and source_dashboard != db.name) or not source_worksheet:
            continue
        if action.attributes.get("target_url"):
            continue
        source_prefix = f"{db.name}::{source_worksheet}"
        source_visuals = [
            name
            for name in visual_names
            if name == source_prefix or name.startswith(f"{source_prefix}::")
        ]
        if not source_visuals:
            continue
        command = str(action.attributes.get("command") or "")
        action_type = str(action.attributes.get("type") or "")
        behavior = (
            CrossFilterBehavior.FILTER
            if command == "tsc:tsl-filter" or action_type == "filter"
            else CrossFilterBehavior.HIGHLIGHT
        )
        for source_visual in source_visuals:
            rules.append(
                CrossFilterRule(
                    rule_id=f"{db.name}::action_{idx}_{action.name}",
                    source_visual_id=source_visual,
                    target_visual_ids=tuple(name for name in visual_names if name != source_visual),
                    behavior=behavior,
                )
            )
    return rules


def _empty_set_parameters(model: SemanticModel) -> set[str]:
    """Names of set parameters that start with an explicitly empty selection.

    Only ``is_set`` parameters whose canonical selection is the empty tuple
    count as empty. Dynamic/Top-N selections (``default_value is None``) are
    live sets that require marks, not empty placeholders.
    """
    return {
        parameter.name
        for parameter in model.parameters
        if parameter.is_set
        and parameter.default_value is not None
        and not parameter.default_value
    }


def _empty_set_calculations(model: SemanticModel) -> set[str]:
    empty_sets = _empty_set_parameters(model)
    return {
        model_field.name
        for entity in model.entities
        for model_field in entity.fields
        if model_field.expression is not None
        and any(
            node.kind == "set_membership" and node.name in empty_sets
            for node in walk_expression(model_field.expression)
        )
    }


def _visual_liveness_policy(
    kind: str,
    geometry: Mapping[str, Any],
    filters: list[SourceNode],
    empty_set_calculations: set[str],
) -> str:
    if kind == "text_box" or (kind == "table" and float(geometry.get("height", 48)) < 48):
        return "decorative"
    if any(
        _logical_field_name(filter_node.name) in empty_set_calculations for filter_node in filters
    ):
        return "allows_empty_state"
    return "requires_marks"


def _control_liveness_policy(
    ref: str,
    empty_set_fields: set[str],
    empty_set_parameters: set[str],
) -> str:
    """Explicit liveness for slicer controls; empty bindings degrade loudly."""
    kind, _, name = ref.partition(":")
    if kind == "parameter" and name in empty_set_parameters:
        return "allows_empty_state"
    if kind == "field" and name in empty_set_fields:
        return "allows_empty_state"
    return "requires_marks"


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


class SourceASTRenderPlanCompiler:
    """Compile a neutral :class:`SourceAST` into the four DataVIZ artifacts.

    Walks the AST node tree (no TWB parsing, no legacy snapshot dependency) and
    derives SemanticIR, PresentationIR, InteractionIR and a RenderPlan envelope.
    Field bindings are read from worksheet node ``attributes`` (neutral keys);
    mark types from VISUAL child nodes; geometry from layout-zone attributes.
    """

    def __init__(self, *, origin: str = OriginKind.TABLEAU.value) -> None:
        allowed = {k.value for k in OriginKind}
        if origin not in allowed:
            raise ValueError(f"origin must be one of {sorted(allowed)}, got {origin!r}")
        self._origin = origin

    @property
    def origin(self) -> str:
        """Origin of the compiled source (an :class:`OriginKind` value)."""
        return self._origin

    @property
    def destination(self) -> str:
        """Stable destination id this compiler emits."""
        return "dataviz_render"

    def compile(self, source_ast: SourceAST) -> SourceASTCompilation:
        """Compile a SourceAST into SemanticIR/PresentationIR/InteractionIR/RenderPlan."""
        if not isinstance(source_ast, SourceAST):
            raise TypeError("source_ast must be a SourceAST")
        source_ast.validate()

        view = _build_view(source_ast)
        semantic_model = _build_semantic_model(view, source_ast)
        supported_measures = {metric.name for metric in semantic_model.metrics}
        capability_report = _build_semantic_ir(view, source_ast, supported_measures)
        empty_set_calculations = _empty_set_calculations(semantic_model)
        empty_set_parameters = _empty_set_parameters(semantic_model)
        _, field_display_names = _field_metadata_by_datasource(view)
        _, parameter_display_names = _unique_parameter_metadata(view)
        parameter_display_values = _parameter_display_values(view)
        calculated_fields = {
            field.name
            for entity in semantic_model.entities
            for field in entity.fields
            if field.expression is not None
        }
        param_names = {p.name.strip("[]") for p in view.parameters}
        data_sources = [ds.name for ds in view.datasources if ds.name != "Parameters"]
        default_datasource = data_sources[0] if len(data_sources) == 1 else None

        def display_names_for(visual: VisualSpec) -> Mapping[str, str]:
            datasource = (
                visual.query.from_datasource if visual.query is not None else default_datasource
            )
            return field_display_names.get(datasource or "", {})

        worksheets_by_name = {ws.name: ws for ws in view.worksheets}
        placed_worksheets = {
            str(zone.attributes.get("name") or zone.name or "")
            for db in view.dashboards
            for zone in _walk(db)
            if zone.kind is NodeKind.LAYOUT_ZONE
            and str(zone.attributes.get("name") or zone.name or "") in worksheets_by_name
        }

        visuals: list[VisualSpec] = []
        interactions: list[InteractionSpec] = []
        presentation_pages: list[PagePresentation] = []
        interaction_pages: list[PageInteractionSpec] = []
        declared_drill_paths = _declared_drill_paths(view)

        for db in view.dashboards:
            db_name = db.name
            sheet_visual_names: list[str] = []
            page_visuals: list[VisualPresentation] = []

            worksheet_zones = [
                z
                for z in _walk(db)
                if z.kind is NodeKind.LAYOUT_ZONE
                and (
                    str(z.attributes.get("type") or z.attributes.get("type-v2") or "").lower()
                    == "worksheet"
                    or z.name in worksheets_by_name
                )
            ]
            normalized_layout = any(
                max(
                    abs(float(z.attributes.get("x", 0) or 0)),
                    abs(float(z.attributes.get("y", 0) or 0)),
                    abs(float(z.attributes.get("w", z.attributes.get("width", 0)) or 0)),
                    abs(float(z.attributes.get("h", z.attributes.get("height", 0)) or 0)),
                )
                > 5000
                for z in _walk(db)
                if z.kind is NodeKind.LAYOUT_ZONE
            )
            visual_name_counts: dict[str, int] = {}
            page_datasources: set[str] = set()
            for zone in worksheet_zones:
                ws_ref = str(zone.attributes.get("name") or zone.name or "")
                ws_node = worksheets_by_name.get(ws_ref)
                attrs = ws_node.attributes if ws_node is not None else {}
                mark = _mark_type(ws_node) if ws_node is not None else ""
                base_visual_name = f"{db_name}::{ws_ref}"
                occurrence = visual_name_counts.get(base_visual_name, 0)
                visual_name_counts[base_visual_name] = occurrence + 1
                visual_name = (
                    base_visual_name
                    if occurrence == 0
                    else f"{base_visual_name}::{zone.attributes.get('id', occurrence)}"
                )
                sheet_visual_names.append(visual_name)
                worksheet_filters = [
                    child
                    for child in (ws_node.children if ws_node else ())
                    if child.kind is NodeKind.FILTER
                ]
                roles = _bindings(
                    attrs,
                    supported_measures,
                    calculated_fields,
                    param_names,
                    worksheet_filters,
                )
                query = _visual_query(
                    attrs,
                    roles,
                    default_datasource,
                    supported_measures,
                    worksheet_filters,
                )
                forecast = _forecast_spec(ws_node, roles)
                kind = "line" if forecast is not None else _visual_kind(mark, attrs, roles)
                geometry = _geometry(zone.attributes, normalized=normalized_layout)
                spec = VisualSpec(
                    name=visual_name,
                    kind=kind,
                    page=db_name,
                    title=_display_title(
                        attrs.get("title"), ws_ref, parameter_display_values
                    ),
                    data_roles=roles,
                    geometry=geometry,
                    query=query,
                    forecast=forecast,
                    liveness_policy=_visual_liveness_policy(
                        kind, geometry, worksheet_filters, empty_set_calculations
                    ),
                )
                visuals.append(spec)
                if spec.query is not None:
                    page_datasources.add(spec.query.from_datasource)
                page_visuals.append(_to_presentation_visual(spec, display_names_for(spec)))

            control_visuals, control_interactions, conditions = _controls(
                db,
                db_name,
                sheet_visual_names,
                supported_measures,
                calculated_fields,
                param_names,
                normalized_layout,
                empty_set_calculations,
                empty_set_parameters,
                parameter_display_names,
                view=view,
                page_datasources=page_datasources,
            )
            if len(page_datasources) == 1:
                datasource = next(iter(page_datasources))
                control_visuals = [
                    control if control.query is not None else _materialize_control_query(control, datasource)
                    for control in control_visuals
                ]
            visuals.extend(control_visuals)
            interactions.extend(control_interactions)
            page_visuals.extend(
                _to_presentation_visual(v, display_names_for(v)) for v in control_visuals
            )
            action_cross_filters = _cross_filters(db, view.actions, sheet_visual_names)
            for rule in action_cross_filters:
                for target in rule.target_visual_ids:
                    interactions.append(
                        InteractionSpec(
                            name=f"{rule.rule_id}->{target}",
                            source=rule.source_visual_id,
                            target=target,
                            kind=rule.behavior.value,
                        )
                    )

            presentation_pages.append(
                PagePresentation(
                    page_id=db_name,
                    name=db_name,
                    display_name=db.name,
                    visuals=tuple(page_visuals),
                )
            )
            page_drills = _page_drills(
                db_name,
                [visual for visual in visuals if visual.page == db_name],
                declared_drill_paths,
            )
            interactions.extend(
                InteractionSpec(
                    name=drill.drill_id,
                    source=drill.target_visual_id,
                    target=drill.target_visual_id,
                    kind="drill",
                )
                for drill in page_drills
            )
            interaction_pages.append(
                PageInteractionSpec(
                    page_id=db_name,
                    filters=tuple(conditions),
                    cross_filters=tuple(action_cross_filters),
                    drills=tuple(page_drills),
                )
            )

        for worksheet in view.worksheets:
            if worksheet.name in placed_worksheets:
                continue
            worksheet_filters = [
                child for child in worksheet.children if child.kind is NodeKind.FILTER
            ]
            roles = _bindings(
                worksheet.attributes,
                supported_measures,
                calculated_fields,
                param_names,
                worksheet_filters,
            )
            query = _visual_query(
                worksheet.attributes,
                roles,
                default_datasource,
                supported_measures,
                worksheet_filters,
            )
            forecast = _forecast_spec(worksheet, roles)
            kind = (
                "line"
                if forecast is not None
                else _visual_kind(_mark_type(worksheet), worksheet.attributes, roles)
            )
            geometry = {
                "x": 0.0,
                "y": 0.0,
                "width": 1000.0,
                "height": 700.0,
                "z_index": 0,
                "layout_mode": "tiled",
            }
            visual = VisualSpec(
                name=worksheet.name,
                kind=kind,
                page=worksheet.name,
                title=_display_title(
                    worksheet.attributes.get("title"),
                    worksheet.name,
                    parameter_display_values,
                ),
                data_roles=roles,
                geometry=geometry,
                query=query,
                forecast=forecast,
                liveness_policy=_visual_liveness_policy(
                    kind, geometry, worksheet_filters, empty_set_calculations
                ),
            )
            visuals.append(visual)
            presentation_pages.append(
                PagePresentation(
                    page_id=worksheet.name,
                    name=worksheet.name,
                    display_name=worksheet.name,
                    visuals=(_to_presentation_visual(visual, display_names_for(visual)),),
                )
            )
            page_drills = _page_drills(worksheet.name, [visual], declared_drill_paths)
            interactions.extend(
                InteractionSpec(
                    name=drill.drill_id,
                    source=drill.target_visual_id,
                    target=drill.target_visual_id,
                    kind="drill",
                )
                for drill in page_drills
            )
            interaction_pages.append(
                PageInteractionSpec(page_id=worksheet.name, drills=tuple(page_drills))
            )

        render_plan = RenderPlan(
            schema_version=RENDER_PLAN_SCHEMA_VERSION,
            visuals=visuals,
            interactions=interactions,
            description="SourceAST-derived neutral DataVIZ render plan",
            metadata=_metadata(source_ast, capability_report, view),
        )
        destination = DestinationPlan(
            schema_version=PLAN_SCHEMA_VERSION,
            destination=self.destination,
            origin=self._origin,
            source_model="tableau_source_ast",
            source_schema_version=source_ast.schema_version,
            body=render_plan.to_dict(),
        )
        safe_meta = _safe_source_metadata(source_ast, self._origin)
        presentation_ir = PresentationIR(
            schema_version=PRESENTATION_SCHEMA_VERSION,
            doc_id=source_ast.root.name,
            title=source_ast.root.name,
            pages=tuple(presentation_pages),
            metadata=dict(safe_meta),
        )
        presentation_ir.validate()
        interaction_ir = InteractionIR(
            schema_version=INTERACTION_SCHEMA_VERSION,
            doc_id=source_ast.root.name,
            pages=tuple(interaction_pages),
            metadata=dict(safe_meta),
        )
        interaction_ir.validate()
        return SourceASTCompilation(
            semantic_model, capability_report, presentation_ir, interaction_ir, destination
        )



_HIERARCHY_AXIS_ROLE_RE = re.compile(r"^(x|y)_axis(?:_(\d+))?$")


def _declared_drill_paths(view: _ASTView) -> list[tuple[str, str, tuple[str, ...]]]:
    """Return normalized Tableau drill paths from SourceAST metadata."""
    paths: list[tuple[str, str, tuple[str, ...]]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    field_names, _ = _field_metadata_by_datasource(view)
    for datasource in view.datasources:
        raw_paths = datasource.attributes.get("drill_paths", [])
        if not isinstance(raw_paths, list):
            continue
        lookup = field_names.get(datasource.name, {})
        for raw_path in raw_paths:
            if not isinstance(raw_path, Mapping):
                continue
            levels = tuple(
                lookup.get(str(level), lookup.get(_logical_field_name(str(level)), str(level)))
                for level in raw_path.get("levels", [])
                if str(level)
            )
            if len(levels) < 2:
                continue
            item = (datasource.name, str(raw_path.get("name") or ""), levels)
            if item in seen:
                continue
            seen.add(item)
            paths.append(item)
    return paths


def _axis_hierarchy_levels(visual: VisualSpec) -> tuple[str, ...]:
    """Return the longest ordered x/y axis family containing physical fields."""
    families: dict[str, list[tuple[int, str]]] = {"x": [], "y": []}
    for role, reference in visual.data_roles.items():
        match = _HIERARCHY_AXIS_ROLE_RE.fullmatch(role)
        if match is None or not reference.startswith("field:"):
            continue
        families[match.group(1)].append((int(match.group(2) or 0), reference[6:]))
    chosen = families["y"] if len(families["y"]) >= len(families["x"]) else families["x"]
    if len(chosen) < 2:
        return ()
    ordered: list[str] = []
    for _index, field_name in sorted(chosen):
        if field_name not in ordered:
            ordered.append(field_name)
    return tuple(ordered) if len(ordered) >= 2 else ()


def _levels_belong_to_path(levels: tuple[str, ...], declared: tuple[str, ...]) -> bool:
    cursor = -1
    for level in levels:
        try:
            cursor = declared.index(level, cursor + 1)
        except ValueError:
            return False
    return True


def _page_drills(
    page_id: str,
    visuals: list[VisualSpec],
    declared_paths: list[tuple[str, str, tuple[str, ...]]],
) -> list[DrillSpec]:
    """Lower only source-declared, visual-bound hierarchy paths into InteractionIR."""
    drills: list[DrillSpec] = []
    for visual in visuals:
        levels = _axis_hierarchy_levels(visual)
        if not levels or visual.query is None:
            continue
        datasource = visual.query.from_datasource
        if not any(
            path_datasource == datasource and _levels_belong_to_path(levels, path_levels)
            for path_datasource, _path_name, path_levels in declared_paths
        ):
            continue
        drills.append(
            DrillSpec(
                drill_id=f"{page_id}:source-drill:{visual.name}",
                target_visual_id=visual.name,
                hierarchy_levels=levels,
            )
        )
    return drills


def _hierarchy_navigation_contracts(view: _ASTView) -> list[dict[str, Any]]:
    """Translate source extension metadata into neutral hierarchy navigation contracts."""
    field_names, _ = _field_metadata_by_datasource(view)
    parameter_names, _ = _unique_parameter_metadata(view)

    def resolve_parameter(value: object) -> str:
        raw = str(value or "")
        return parameter_names.get(raw, parameter_names.get(_logical_field_name(raw), raw))

    def resolve_field(value: object, datasource: str) -> str:
        raw = str(value or "")
        qualified = _QUALIFIED_TITLE_EXPR.fullmatch(raw)
        if qualified:
            datasource = _datasource_for_reference(view, qualified.group(1)) or datasource
            raw = qualified.group(2)
        lookup = field_names.get(datasource, {})
        return lookup.get(raw, lookup.get(_logical_field_name(raw), raw))

    navigators: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for dashboard in view.dashboards:
        normalized_layout = any(
            max(
                abs(float(zone.attributes.get("x", 0) or 0)),
                abs(float(zone.attributes.get("y", 0) or 0)),
                abs(float(zone.attributes.get("w", zone.attributes.get("width", 0)) or 0)),
                abs(float(zone.attributes.get("h", zone.attributes.get("height", 0)) or 0)),
            )
            > 5000
            for zone in _walk(dashboard)
            if zone.kind is NodeKind.LAYOUT_ZONE
        )
        raw_navigators = dashboard.attributes.get("hierarchy_navigators", [])
        if not isinstance(raw_navigators, list):
            continue
        for raw in raw_navigators:
            if not isinstance(raw, Mapping):
                continue
            instance_id = str(raw.get("instance_id") or "")
            config = raw.get("config", {})
            if not isinstance(config, Mapping):
                continue
            key = (dashboard.name, instance_id)
            if key in seen:
                continue
            seen.add(key)
            params = config.get("parameters", {})
            worksheet = config.get("worksheet", {})
            options = config.get("options", {})
            if not isinstance(params, Mapping) or not isinstance(worksheet, Mapping):
                continue
            raw_fields = params.get("fields", [])
            worksheet_fields = worksheet.get("fields", [])
            worksheet_datasource = _datasource_for_reference(
                view, worksheet.get("datasource")
            ) or _datasource_for_reference(view, worksheet.get("name"))
            mode = str(config.get("type") or "")
            if mode not in {"recursive", "flat"}:
                continue
            source_name = str(worksheet.get("name") or "")
            id_field = resolve_field(worksheet.get("childId"), worksheet_datasource)
            label_field = resolve_field(worksheet.get("childLabel"), worksheet_datasource)
            filter_field = resolve_field(worksheet.get("filter"), worksheet_datasource)
            selection_parameters: dict[str, Any] = {
                "path": [
                    resolve_parameter(value) for value in raw_fields if str(value)
                ]
                if isinstance(raw_fields, list)
                else [],
            }
            if params.get("childIdEnabled"):
                selection_parameters["id"] = resolve_parameter(params.get("childId"))
            if params.get("childLabelEnabled"):
                selection_parameters["label"] = resolve_parameter(params.get("childLabel"))
            depth_parameter = resolve_parameter(params.get("level"))
            if depth_parameter:
                selection_parameters["depth"] = depth_parameter
            contract: dict[str, Any] = {
                "kind": "hierarchy",
                "navigation_id": instance_id,
                "page_id": dashboard.name,
                "source_visual_id": f"{dashboard.name}::{source_name}",
                "mode": mode,
                "path_separator": str(config.get("separator") or "|"),
                "geometry": _geometry(raw.get("geometry", {}), normalized=normalized_layout)
                if isinstance(raw.get("geometry"), Mapping)
                else {},
                "data": {
                    "id_field": id_field,
                    "label_field": label_field,
                    "parent_field": resolve_field(
                        worksheet.get("parentId"), worksheet_datasource
                    ),
                    "path_fields": [
                        resolve_field(value, worksheet_datasource)
                        for value in worksheet_fields
                        if str(value)
                    ]
                    if isinstance(worksheet_fields, list)
                    else [],
                },
                "selection_parameters": selection_parameters,
                "source_selection": {
                    "listen": bool(
                        options.get("dashboardListenersEnabled", False)
                        if isinstance(options, Mapping)
                        else False
                    ),
                },
                "searchable": bool(
                    options.get("searchEnabled", False)
                    if isinstance(options, Mapping)
                    else False
                ),
            }
            if worksheet.get("enableMarkSelection") and id_field:
                contract["source_selection"]["emit_field"] = id_field
            if worksheet.get("filterEnabled") and filter_field:
                contract["filter"] = {
                    "target_field": filter_field,
                    "value_field": filter_field,
                }
            navigators.append(contract)
    return navigators


def _metadata(source_ast: SourceAST, semantic_ir: SemanticIR, view: _ASTView) -> dict[str, Any]:
    """Preserve neutral traceability without serializing raw source bytes."""
    calc_caps = semantic_ir.capabilities_for("calculated_field")
    calculated_fields = [
        {
            "name": calc.name,
            "formula": str(calc.attributes.get("formula") or ""),
            "datatype": str(calc.attributes.get("datatype") or ""),
            "status": calc_caps[idx].status.value if idx < len(calc_caps) else "unsupported",
        }
        for idx, calc in enumerate(view.calculations)
    ]
    drill_paths = [
        {"name": name, "levels": list(levels), "datasource": datasource}
        for datasource, name, levels in _declared_drill_paths(view)
    ]
    return {
        "source": _source_metadata(source_ast),
        "drill_paths": drill_paths,
        "navigation_contracts": _hierarchy_navigation_contracts(view),
        "semantic_ir": semantic_ir.to_dict(),
        "calculated_fields": calculated_fields,
        "diagnostics": [
            {
                "kind": capability.kind,
                "name": capability.name,
                "status": capability.status.value,
                "reason": capability.reason,
                "source_index": capability.source_index,
            }
            for capability in semantic_ir.capabilities
            if capability.status is not Support.SUPPORTED
        ],
        "source_counts": {
            "dashboards": len(view.dashboards),
            "worksheets": len(view.worksheets),
            "datasources": len(view.datasources),
            "calculations": len(view.calculations),
            "parameters": len(view.parameters),
            "filters": len(view.filters),
            "capabilities": len(semantic_ir.capabilities),
        },
        "limits": [
            "Tableau formula execution does not occur in the RenderPlan.",
            "Manual or unsupported capabilities require a downstream adapter.",
            "The plan carries no row data nor executable connections.",
        ],
    }


def _source_metadata(source_ast: SourceAST) -> dict[str, str]:
    """Return sanitized source identity shared by every compiled artifact."""
    provenance = source_ast.provenance
    artifact_path = provenance.artifact_path if provenance else ""
    return {
        "origin": source_ast.origin_kind.value,
        "artifact_path": artifact_path,
        "artifact_name": re.split(r"[\\/]", artifact_path)[-1] if artifact_path else "",
        "artifact_hash": provenance.artifact_hash if provenance else "",
        "source_ast_schema_version": source_ast.schema_version,
    }


def _safe_source_metadata(source_ast: SourceAST, origin: str) -> dict[str, Any]:
    """Sanitized source identity as safe scalar properties for presentation and interaction IR.

    Mirrors the powerbi compiler envelope: ``source`` is a nested structured
    block carrying the sanitized identity; the matched ``origin`` stays at the
    top level. Nested structured metadata is intentionally allowed by both IR
    contracts (only secrets and SQL carriers fail closed).
    """
    return {
        "origin": origin,
        "source": _source_metadata(source_ast),
    }


def compile_source_ast(source_ast: SourceAST) -> SourceASTCompilation:
    """Shortcut: compile a SourceAST with the default Tableau-origin compiler."""
    return SourceASTRenderPlanCompiler(origin=OriginKind.TABLEAU.value).compile(source_ast)


__all__ = [
    "SourceASTCompilation",
    "SourceASTRenderPlanCompiler",
    "compile_source_ast",
]
