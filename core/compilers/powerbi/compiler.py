"""Power BI Source AST -> neutral IRs + executable DataVIZ RenderPlan.

This is the maintained long-term compiler for the Power BI branch of the neutral
pipeline. It consumes a Power BI-origin :class:`core.contracts.source_ast.SourceAST`
(produced by :mod:`core.importers.powerbi.importer`) and emits the four neutral,
target-agnostic artifacts of the canonical DataVIZ pipeline:

* :class:`core.contracts.semantic_ir.SemanticModel` -- the accepted neutral
  semantic model (reused from :class:`core.importers.powerbi.PowerBIAstCompiler`
  so the validated DAX mini-parser is not duplicated).
* :class:`core.contracts.presentation_ir.PresentationIR` -- pages, visuals,
  geometry and data bindings.
* :class:`core.contracts.interaction_ir.InteractionIR` -- page filters, slicer
  cross-filtering and drill specs.
* :class:`core.compilers.render_plan.RenderPlan` -- the renderizable visual plan
  (visuals carry executable :class:`QuerySpec` where bindings resolve cleanly),
  wrapped in a :class:`DestinationPlan` envelope.

Design (Ponytail):

* Reuses the proven Power BI semantic compiler instead of re-implementing the
  DAX grammar; this module owns the presentation/interaction/render layer only.
* Maps PBIR ``visualType`` / PBIT visual types and PBIR filter shapes to the
  *closed neutral* kind sets. PBIR/DAX native identifiers never reach the
  neutral ``kind`` / ``intent`` / ``role`` fields; they survive only in the
  contracts' free-form ``properties`` / ``metadata`` escape hatches (provenance).
* Never fabricates bindings, filter values or cross-filter rules: anything that
  cannot be resolved against the neutral model is recorded as a note and
  preserved verbatim through the importer's ``raw_payload``.

Boundary (one-way dependency, same as :mod:`core.compilers.base`):

* depends on stdlib, ``core.contracts.*``, ``core.compilers.*`` and the sibling
  Power BI semantic compiler (``core.importers.powerbi``) -- which itself only
  depends on contracts + stdlib;
* never imports destination runtimes (``core.pbir_*`` / ``core.pbip_*`` /
  ``core.visual_*`` / DAX / connector engines);
* never re-parses PBIP/PBIT bytes -- it reads AST node kinds, attributes and
  ``raw_payload`` only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.compilers.base import SCHEMA_VERSION as PLAN_SCHEMA_VERSION
from core.compilers.base import DestinationPlan
from core.compilers.render_plan import SCHEMA_VERSION as RENDER_PLAN_SCHEMA_VERSION
from core.compilers.render_plan import InteractionSpec, RenderPlan, VisualSpec
from core.contracts.interaction_ir import (
    CrossFilterBehavior,
    CrossFilterRule,
    FilterCondition,
    FilterOperator,
    FilterScope,
    InteractionIR,
    PageInteractionSpec,
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
from core.contracts.query_ast import QuerySpec, SelectItem
from core.contracts.semantic_ir import Expression, SemanticModel
from core.contracts.source_ast import NodeKind, SourceAST, SourceNode
from core.importers.powerbi.ir_compiler import PowerBIAstCompiler

# --- Power BI visual type -> neutral RenderPlan kind -------------------------
#
# Closed mapping from PBIR ``visual.visualType`` and PBIT
# ``config.singleVisual.visualType`` tokens to the neutral
# :data:`core.compilers.render_plan.NATIVE_VISUAL_KINDS`. Known values use
# target-agnostic neutral kinds. Unknown values remain opaque intent strings so
# the RenderPlan/runtime can report them as unsupported without inventing a
# similar visual; the original type is also preserved in presentation metadata.
_PBIR_VISUAL_KIND: dict[str, str] = {
    "barChart": "bar",
    "clusteredBarChart": "bar",
    "stackedBarChart": "bar",
    "hundredPercentStackedBarChart": "bar",
    "columnChart": "column",
    "clusteredColumnChart": "column",
    "stackedColumnChart": "column",
    "hundredPercentStackedColumnChart": "column",
    "lineChart": "line",
    "stackedLineChart": "line",
    "ribbonChart": "line",
    "areaChart": "area",
    "stackedAreaChart": "area",
    "hundredPercentStackedAreaChart": "area",
    "pieChart": "pie",
    "donutChart": "donut",
    "treemap": "treemap",
    "scatterChart": "scatter",
    "card": "card",
    "cardVisual": "card",  # legacy PbixProj/pbi-tools token
    "kpi": "kpi",
    "gauge": "gauge",
    "tableEx": "table",
    "matrix": "matrix",
    "pivotTable": "matrix",
    "map": "map",
    "azureMap": "map",
    "shapeMap": "map",
    "textbox": "text_box",
    "text": "text_box",
    "image": "text_box",
    "slicer": "slicer",
}

# Neutral RenderPlan kind -> PresentationIR visual intent. Mirrors the mapping
# used by the Tableau SourceAST compiler so both origins share one neutral
# presentation vocabulary. RenderPlan kinds without a PresentationIR equivalent
# (``map``) fall back to ``custom_visual`` -- the honest neutral intent.
_KIND_TO_INTENT: dict[str, str] = {
    "bar": "bar",
    "column": "column",
    "line": "line",
    "area": "area",
    "pie": "pie",
    "donut": "donut",
    "scatter": "scatter",
    "kpi": "kpi_card",
    "card": "kpi_card",
    "gauge": "gauge",
    "table": "table",
    "matrix": "pivot_matrix",
    "text_box": "text_box",
    "slicer": "slicer_filter",
}

# PBIR projection role (``singleVisual.projections`` key) -> neutral RenderPlan
# role. Only roles with a clear neutral equivalent produce bindings; the rest
# are skipped (and noted) so no fabricated encoding reaches the neutral plan.
_PROJECTION_ROLE: dict[str, str] = {
    "category": "x_axis",
    "axis": "x_axis",
    "x": "x_axis",
    "y": "y_axis",
    "values": "value",
    "value": "value",
    "data": "value",  # legacy cardVisual projection bucket
    "series": "series",
    "legend": "color",
    "color": "color",
    "colorsaturation": "color",
    "saturation": "color",
    "tooltips": "tooltip",
    "tooltip": "tooltip",
    "size": "size",
    "radius": "size",
    "rows": "row",
    "columns": "column",
    "indicator": "value",
    "trendline": "x_axis",
}

# Neutral RenderPlan role -> PresentationIR FieldRole. Only mappable roles become
# DataBindings; the RenderPlan may carry additional roles (e.g. ``filter_target``)
# that have no FieldRole equivalent and stay presentation-free.
_ROLE_TO_FIELD_ROLE: dict[str, str] = {
    "x_axis": "x_axis",
    "y_axis": "y_axis",
    "value": "value",
    "series": "series",
    "color": "color",
    "size": "size",
    "label": "label",
    "tooltip": "tooltip",
    "row": "row",
    "column": "column",
    "filter_target": "filter_target",
}

# PBIR Advanced filter operator token -> neutral FilterOperator.
_ADVANCED_OP: dict[str, str] = {
    "gt": "greater_than",
    "lt": "less_than",
    "eq": "equals",
    "ne": "not_equals",
}


@dataclass(frozen=True)
class PowerBIRenderPlanCompilation:
    """Verifiable neutral artifacts compiled from a Power BI :class:`SourceAST`.

    Attributes:
        semantic_model: accepted neutral semantic model (tables/measures/
            relationships/parameters) reused from :class:`PowerBIAstCompiler`.
        presentation_ir: neutral pages/visuals/bindings presentation contract.
        interaction_ir: neutral page filters + slicer cross-filtering contract.
        render_plan: DataVIZ render plan wrapped in a destination envelope.
        notes: recorded approximations and preservation decisions (audit trail).
    """

    semantic_model: SemanticModel
    presentation_ir: PresentationIR
    interaction_ir: InteractionIR
    render_plan: DestinationPlan
    notes: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Pure helpers (operate on plain data; no AST mutation, no fabrication)
# ---------------------------------------------------------------------------


def _walk(node: SourceNode):
    """Yield ``node`` and every nested child (depth-first, pre-order)."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _as_float(value: Any, default: float = 0.0) -> float:
    """Coerce ``value`` to ``float`` defensively, falling back to ``default``."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    coerced = _as_float(value, float(default))
    return int(coerced)


def _visual_kind(visual_type: str) -> str:
    """Map a PBIR/PBIT visual type token to a neutral RenderPlan kind.

    Unknown tokens remain explicit intent strings for runtime graceful
    degradation; the caller also records the original type for provenance.
    """
    token = str(visual_type or "").strip()
    return _PBIR_VISUAL_KIND.get(token, token or "custom_visual")


def _intent_for(kind: str) -> str:
    """RenderPlan kind -> PresentationIR intent (``custom_visual`` when unmapped)."""
    return _KIND_TO_INTENT.get(kind, "custom_visual")


def _load_visual_config(raw_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the ``config`` block of a PBIR/PBIT visual as a dict.

    PBIR/PBIT store ``config`` either as an inline object or a JSON string; both
    are tolerated. Unparseable configs return an empty dict so the caller skips
    binding extraction instead of raising.
    """
    if not isinstance(raw_payload, Mapping):
        return {}
    config = raw_payload.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except (ValueError, TypeError):
            return {}
    return config if isinstance(config, dict) else {}


def _normalize_queryref(ref: str) -> str:
    """Reduce a PBIR ``queryRef`` to its trailing field/measure identifier.

    Handles the common shapes: ``Sales.Amount`` -> ``Amount``,
    ``'Sales'[Amount]`` -> ``Amount``, ``Sales[Amount]`` -> ``Amount``,
    ``Measure.TotalAmount`` -> ``TotalAmount``. The result is unresolved -- the
    caller resolves it against the neutral :class:`SemanticModel`.
    """
    text = str(ref or "").strip()
    if not text:
        return ""
    wrapper, separator, body = text.partition("(")
    if separator and text.endswith(")") and wrapper.strip().casefold() in {
        "avg",
        "average",
        "count",
        "countnonull",
        "distinctcount",
        "max",
        "min",
        "sum",
    }:
        text = body[:-1].strip()
    if "[" in text and text.endswith("]"):
        return text[text.rindex("[") + 1 : -1].strip().strip("'")
    if "." in text:
        return text.rsplit(".", 1)[1].strip().strip("'")
    return text.strip("'")


def _resolve_ref(name: str, metrics: frozenset[str], field_names: frozenset[str]) -> str | None:
    """Resolve a normalized identifier to a neutral ``measure:``/``field:`` ref.

    Returns ``None`` when the identifier matches neither a metric nor a field --
    the caller then skips the binding instead of inventing a reference.
    """
    clean = name.strip()
    if not clean:
        return None
    if clean in metrics:
        return f"measure:{clean}"
    if clean in field_names:
        return f"field:{clean}"
    return None


def _projections(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Collect ``(role, queryRef)`` pairs from a visual's ``singleVisual`` block."""
    single = config.get("singleVisual")
    if not isinstance(single, dict):
        return []
    raw_proj = single.get("projections")
    if not isinstance(raw_proj, dict):
        return []
    pairs: list[tuple[str, str]] = []
    for role, items in raw_proj.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                ref = item.get("queryRef") or item.get("ref")
                if isinstance(ref, str) and ref.strip():
                    pairs.append((str(role), ref))
    return pairs


def _bindings(
    config: Mapping[str, Any],
    metrics: frozenset[str],
    field_names: frozenset[str],
    notes: list[str],
    visual_label: str,
) -> dict[str, str]:
    """Derive neutral RenderPlan data_roles from a visual's projections.

    Only projections whose ``queryRef`` resolves against the neutral model
    produce a role; everything else is noted and skipped (no fabrication).
    Repeated channels are disambiguated (``value``, ``value_2`` ...).
    """
    roles: dict[str, str] = {}
    for role, ref in _projections(config):
        neutral_role = _PROJECTION_ROLE.get(str(role).lower())
        if neutral_role is None:
            notes.append(
                f"{visual_label}: projection role {role!r} has no neutral mapping; skipped"
            )
            continue
        resolved = _resolve_ref(_normalize_queryref(ref), metrics, field_names)
        if resolved is None:
            notes.append(
                f"{visual_label}: projection {role!r} -> {ref!r} not resolvable against model; skipped"
            )
            continue
        _add_role(roles, neutral_role, resolved)
    return roles


def _add_role(roles: dict[str, str], role: str, value: str) -> None:
    """Insert ``value`` under ``role``, disambiguating repeated channels."""
    if not value or value in roles.values():
        return
    key = role
    index = 1
    while key in roles:
        index += 1
        key = f"{role}_{index}"
    roles[key] = value


def _geometry(raw_payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build a neutral geometry dict from a PBIR/PBIT visual raw payload.

    PBIR stores position under ``position``; PBIT visual containers expose
    ``x``/``y``/``width``/``height`` at the top level. Missing dimensions
    default to ``0`` (honest: "unspecified") and the raw layout is preserved by
    the caller in :attr:`VisualPresentation.properties`.
    """
    src: Mapping[str, Any] = {}
    if isinstance(raw_payload, Mapping):
        visual_container = raw_payload.get("visual_container")
        payload = visual_container if isinstance(visual_container, dict) else raw_payload
        position = payload.get("position")
        src = position if isinstance(position, dict) else payload
    width = _as_float(src.get("width"), 0.0)
    height = _as_float(src.get("height"), 0.0)
    return {
        "x": _as_float(src.get("x"), 0.0),
        "y": _as_float(src.get("y"), 0.0),
        "width": width,
        "height": height,
        "z_index": max(0, _as_int(src.get("z", src.get("zIndex", src.get("tabOrder", 0))), 0)),
        "layout_mode": "tiled",
    }


def _to_visual_geometry(geometry: Mapping[str, Any]) -> VisualGeometry:
    """Lift a neutral geometry dict into a :class:`VisualGeometry` value."""
    mode_raw = str(geometry.get("layout_mode", "tiled"))
    try:
        layout_mode = ContainerKind(mode_raw)
    except ValueError:
        layout_mode = ContainerKind.TILED
    width = _as_float(geometry.get("width"), 0.0)
    height = _as_float(geometry.get("height"), 0.0)
    return VisualGeometry(
        x=_as_float(geometry.get("x"), 0.0),
        y=_as_float(geometry.get("y"), 0.0),
        width=width,
        height=height,
        z_index=max(0, _as_int(geometry.get("z_index"), 0)),
        layout_mode=layout_mode,
    )


def _build_query(roles: Mapping[str, str], model_name: str) -> QuerySpec | None:
    """Compile resolved data_roles into an executable :class:`QuerySpec`.

    Measures become ``measure_ref`` select items; fields become ``field_ref``
    select items and group-by keys (unqualified, matching the Power BI semantic
    compiler convention). Returns ``None`` when no role resolves, so the visual
    stays in the plan without a fabricated query.
    """
    if not roles:
        return None
    select: list[SelectItem] = []
    group_by: list[Expression] = []
    has_measure = False
    seen: set[str] = set()
    for ref in roles.values():
        if ref in seen or ":" not in ref:
            continue
        seen.add(ref)
        kind, name = ref.split(":", 1)
        if kind == "measure":
            select.append(SelectItem(Expression(kind="measure_ref", name=name), alias=name))
            has_measure = True
        elif kind == "field":
            expr = Expression(kind="field_ref", name=name)
            select.append(SelectItem(expr, alias=name))
            group_by.append(expr)
    if not select:
        return None
    return QuerySpec(
        from_datasource=model_name,
        select=select,
        group_by=group_by if has_measure else [],
    )


def _to_presentation_visual(visual: VisualSpec, pbir_type: str) -> VisualPresentation:
    """Lift a RenderPlan :class:`VisualSpec` into a :class:`VisualPresentation`.

    The native PBIR/PBIT ``visualType`` is preserved in ``properties`` so the
    neutral intent stays target-agnostic while full provenance is retained.
    """
    intent = VisualIntentKind(_intent_for(visual.kind))
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
            )
        )
    return VisualPresentation(
        visual_id=visual.name,
        intent=intent,
        title=visual.title,
        geometry=_to_visual_geometry(visual.geometry),
        bindings=tuple(bindings),
        properties={"pbir_visual_type": pbir_type} if pbir_type else {},
    )


# --- Filter extraction (best-effort, non-fabricating) ------------------------


_FILTER_MISSING = object()


def _ci_get(mapping: Mapping[str, Any], key: str) -> Any:
    """Case-insensitive mapping lookup for PBIR/PBIT JSON dialects."""
    if key in mapping:
        return mapping[key]
    folded = key.casefold()
    for candidate, value in mapping.items():
        if isinstance(candidate, str) and candidate.casefold() == folded:
            return value
    return None


def _filter_query_payload(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the SemanticQuery filter body from a PBIR filter container."""
    nested = _ci_get(raw, "filter")
    return nested if isinstance(nested, Mapping) else raw


def _expression_property(value: Any) -> str:
    """Find the first Column/Measure ``Property`` in one query expression."""
    if not isinstance(value, Mapping):
        return ""
    for kind in ("Column", "Measure"):
        body = _ci_get(value, kind)
        if isinstance(body, Mapping):
            prop = _ci_get(body, "Property")
            if isinstance(prop, str) and prop.strip():
                return prop.strip()
    for nested in value.values():
        if isinstance(nested, Mapping):
            found = _expression_property(nested)
            if found:
                return found
        elif isinstance(nested, list):
            for item in nested:
                found = _expression_property(item)
                if found:
                    return found
    return ""


def _filter_target(raw: Mapping[str, Any], fallback: str) -> str:
    """Resolve the neutral target field from current PBIR or legacy filter JSON."""
    field = _ci_get(raw, "field")
    found = _expression_property(field)
    if found:
        return found

    payload = _filter_query_payload(raw)
    where = _ci_get(payload, "Where")
    if isinstance(where, list):
        for clause in where:
            if not isinstance(clause, Mapping):
                continue
            condition = _ci_get(clause, "Condition")
            found = _expression_property(condition)
            if found:
                return found

    frm = _ci_get(raw, "from")
    if isinstance(frm, str):
        normalized = _normalize_queryref(frm)
        if normalized:
            return normalized
    if isinstance(frm, list) and frm:
        first = frm[0]
        if isinstance(first, Mapping):
            name = _ci_get(first, "name")
            if isinstance(name, str) and name.strip():
                return name.strip()
            expr = _ci_get(first, "expr")
            if isinstance(expr, Mapping):
                source = _ci_get(expr, "source")
                if isinstance(source, Mapping) and isinstance(_ci_get(source, "ref"), str):
                    return str(_ci_get(source, "ref"))
                column = _ci_get(expr, "column")
                if isinstance(column, str) and column.strip():
                    return column.strip()
    return fallback or "unknown_filter_target"


def _decode_pbir_literal(value: Any) -> Any:
    """Decode the closed SemanticQuery literal token subset emitted by DataVIZ."""
    if not isinstance(value, str):
        return value
    token = value.strip()
    lowered = token.casefold()
    if lowered == "null":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if len(token) >= 2 and token[0] == "'" and token[-1] == "'":
        return token[1:-1].replace("''", "'")
    if len(token) > 1 and token[-1] in {"L", "l"}:
        try:
            return int(token[:-1])
        except ValueError:
            return token
    if len(token) > 1 and token[-1] in {"D", "d", "M", "m"}:
        try:
            return float(token[:-1])
        except ValueError:
            return token
    return token


def _literal_of(entry: Any) -> Any:
    """Extract one scalar from current PBIR or legacy literal containers."""
    if isinstance(entry, (str, int, float, bool)) or entry is None:
        return _decode_pbir_literal(entry)
    if isinstance(entry, list):
        if len(entry) != 1:
            return _FILTER_MISSING
        return _literal_of(entry[0])
    if not isinstance(entry, Mapping):
        return _FILTER_MISSING
    literal = _ci_get(entry, "Literal")
    if isinstance(literal, Mapping):
        value = _ci_get(literal, "Value")
        if value is not None or any(
            isinstance(k, str) and k.casefold() == "value" for k in literal
        ):
            return _decode_pbir_literal(value)
    expr = _ci_get(entry, "expr")
    if isinstance(expr, Mapping):
        literal = _ci_get(expr, "literal")
        if isinstance(literal, Mapping):
            value = _ci_get(literal, "value")
            if value is not None:
                return _decode_pbir_literal(value)
    return _FILTER_MISSING


def _first_filter_condition(raw: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload = _filter_query_payload(raw)
    where = _ci_get(payload, "Where")
    if not isinstance(where, list):
        return None
    for clause in where:
        if isinstance(clause, Mapping):
            condition = _ci_get(clause, "Condition")
            if isinstance(condition, Mapping):
                return condition
    return None


def _native_filter_semantics(raw: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]] | None:
    """Lower the supported SemanticQuery predicate subset without approximation."""
    condition = _first_filter_condition(raw)
    if condition is None:
        return None
    negated = False
    not_body = _ci_get(condition, "Not")
    if isinstance(not_body, Mapping):
        expression = _ci_get(not_body, "Expression")
        if not isinstance(expression, Mapping):
            return None
        condition = expression
        negated = True

    in_body = _ci_get(condition, "In")
    if isinstance(in_body, Mapping):
        raw_values = _ci_get(in_body, "Values")
        if not isinstance(raw_values, list):
            return None
        values: list[Any] = []
        for entry in raw_values:
            literal = _literal_of(entry)
            if literal is _FILTER_MISSING:
                return None
            values.append(literal)
        return ("not_in" if negated else "in", tuple(values))

    between = _ci_get(condition, "Between")
    if isinstance(between, Mapping):
        if negated:
            return None
        low = _literal_of(_ci_get(between, "LowerBound"))
        high = _literal_of(_ci_get(between, "UpperBound"))
        if low is _FILTER_MISSING or high is _FILTER_MISSING:
            return None
        return "between", (low, high)

    comparison = _ci_get(condition, "Comparison")
    if isinstance(comparison, Mapping):
        try:
            kind = int(_ci_get(comparison, "ComparisonKind"))
        except (TypeError, ValueError):
            return None
        right = _literal_of(_ci_get(comparison, "Right"))
        if right is _FILTER_MISSING or right is None:
            # InteractionIR has no null / inclusive comparison operators today.
            return None
        if kind == 0:
            return ("not_equals" if negated else "equals", (right,))
        if negated:
            return None
        if kind == 1:
            return "greater_than", (right,)
        if kind == 3:
            return "less_than", (right,)
        return None

    for expression_key, positive, negative in (
        ("Contains", "contains", "not_contains"),
        ("StartsWith", "starts_with", "not_starts_with"),
    ):
        body = _ci_get(condition, expression_key)
        if not isinstance(body, Mapping):
            continue
        right = _literal_of(_ci_get(body, "Right"))
        if right is _FILTER_MISSING or right is None:
            return None
        return (negative if negated else positive, (right,))
    return None


def _legacy_filter_semantics(raw: Mapping[str, Any]) -> tuple[str, tuple[Any, ...]] | None:
    """Decode the older simplified filter shape used by PBIT/pbi-tools fixtures."""
    filter_type = str(_ci_get(raw, "filterType") or "").lower()
    if filter_type == "advanced":
        token = str(_ci_get(raw, "operator") or "").lower()
        operator = _ADVANCED_OP.get(token)
        if operator is None:
            return None
    elif filter_type == "topn":
        operator = "top_n"
    else:
        operator = "in"

    where = _ci_get(raw, "where")
    values: list[Any] = []
    if isinstance(where, list):
        for clause in where:
            if not isinstance(clause, Mapping):
                continue
            condition = _ci_get(clause, "condition")
            if not isinstance(condition, Mapping):
                continue
            in_block = _ci_get(condition, "in")
            if not isinstance(in_block, Mapping):
                continue
            raw_values = _ci_get(in_block, "values")
            if not isinstance(raw_values, list):
                continue
            for entry in raw_values:
                literal = _literal_of(entry)
                if literal is not _FILTER_MISSING:
                    values.append(literal)
    return operator, tuple(values)


def _filter_condition(
    node: SourceNode,
    notes: list[str],
    *,
    scope: FilterScope,
    target_visual_ids: tuple[str, ...] = (),
    is_interactive_slicer: bool = False,
    filter_id: str | None = None,
) -> FilterCondition | None:
    """Lower one Power BI filter only when its semantics fit InteractionIR exactly."""
    raw = node.raw_payload if isinstance(node.raw_payload, dict) else {}
    name = (
        _ci_get(raw, "displayName")
        or _ci_get(raw, "name")
        or _ci_get(raw, "filterId")
        or node.name
        or node.node_id
    )
    target = _filter_target(raw, str(name))
    semantics = _native_filter_semantics(raw)
    # Current PBIR containers carry an explicit nested SemanticQuery ``filter``
    # body. If that body cannot lower exactly, never reinterpret it through the
    # legacy simplified dialect because that can change inclusive/null meaning.
    if semantics is None and not isinstance(_ci_get(raw, "filter"), Mapping):
        semantics = _legacy_filter_semantics(raw)
    if semantics is None:
        notes.append(f"filter {name!r}: raw predicate preserved; no exact InteractionIR operator")
        return None
    operator, values = semantics
    try:
        neutral_operator = FilterOperator(operator)
    except ValueError:
        notes.append(f"filter {name!r}: operator {operator!r} not representable in InteractionIR")
        return None
    if neutral_operator in {FilterOperator.IN, FilterOperator.NOT_IN} and not values:
        notes.append(f"filter {name!r}: categorical values not extractable; raw payload preserved")
        return None
    return FilterCondition(
        filter_id=filter_id or node.node_id,
        name=str(name),
        target_field=target,
        operator=neutral_operator,
        values=values,
        scope=scope,
        target_visual_ids=target_visual_ids,
        is_interactive_slicer=is_interactive_slicer,
    )


# ---------------------------------------------------------------------------
# AST view: structural projection of a Power BI SourceAST
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PowerBIView:
    """Structural projection of a Power BI SourceAST used by the compiler."""

    pages: list[SourceNode] = field(default_factory=list)
    filters: list[SourceNode] = field(default_factory=list)
    security_roles: list[SourceNode] = field(default_factory=list)


def _build_view(ast: SourceAST) -> _PowerBIView:
    """Project PAGE (origin_tag=page) and FILTER nodes from the AST."""
    nodes = list(_walk(ast.root))
    return _PowerBIView(
        pages=[n for n in nodes if n.kind is NodeKind.PAGE and n.origin_tag == "page"],
        filters=[
            n for n in nodes if n.kind is NodeKind.FILTER and n.origin_tag == "filter"
        ],
        security_roles=[n for n in nodes if n.origin_tag == "security_role"],
    )


def _page_filters(page: SourceNode) -> list[SourceNode]:
    """Return the direct FILTER children of a page node."""
    return [child for child in page.children if child.kind is NodeKind.FILTER]


def _page_visuals(page: SourceNode) -> list[SourceNode]:
    """Return the direct VISUAL children of a page node."""
    return [child for child in page.children if child.kind is NodeKind.VISUAL]


def _visual_filters(visual: SourceNode) -> list[SourceNode]:
    """Return FILTER children nested under one PBIR visual."""
    return [child for child in visual.children if child.kind is NodeKind.FILTER]


def _page_cross_filter_rules(
    page: SourceNode,
    native_to_neutral: Mapping[str, str],
    notes: list[str],
) -> tuple[list[CrossFilterRule], set[str]]:
    """Lower explicit PBIR ``visualInteractions`` into canonical rules.

    ``Default`` is intentionally not guessed because PBIR delegates its exact
    effect to the target visual type. Its source is still returned in the
    configured-source set so later slicer-default synthesis cannot override an
    authored interaction matrix.
    """
    raw = page.raw_payload if isinstance(page.raw_payload, Mapping) else {}
    items = _ci_get(raw, "visualInteractions")
    if not isinstance(items, list):
        return [], set()

    rules: list[CrossFilterRule] = []
    configured_sources: set[str] = set()
    behavior_map = {
        "datafilter": CrossFilterBehavior.FILTER,
        "highlightfilter": CrossFilterBehavior.HIGHLIGHT,
        "nofilter": CrossFilterBehavior.NONE,
    }
    page_id = _page_id(page)
    for idx, item in enumerate(items):
        if not isinstance(item, Mapping):
            notes.append(f"page {page_id!r}: visualInteraction[{idx}] is not an object; preserved raw")
            continue
        source_native = str(_ci_get(item, "source") or "").strip()
        target_native = str(_ci_get(item, "target") or "").strip()
        type_token = str(_ci_get(item, "type") or "").strip()
        if source_native:
            configured_sources.add(source_native)
        if not source_native or not target_native:
            notes.append(
                f"page {page_id!r}: visualInteraction[{idx}] lacks source/target; preserved raw"
            )
            continue
        source = native_to_neutral.get(source_native)
        target = native_to_neutral.get(target_native)
        if source is None or target is None:
            notes.append(
                f"page {page_id!r}: visualInteraction[{idx}] references unknown visual; preserved raw"
            )
            continue
        if type_token.casefold() == "default":
            notes.append(
                f"page {page_id!r}: visualInteraction[{idx}] uses PBIR Default semantics; preserved raw"
            )
            continue
        behavior = behavior_map.get(type_token.casefold())
        if behavior is None:
            notes.append(
                f"page {page_id!r}: visualInteraction[{idx}] type {type_token!r} unsupported; preserved raw"
            )
            continue
        rules.append(
            CrossFilterRule(
                rule_id=f"{page_id}::pbir_interaction_{idx}",
                source_visual_id=source,
                target_visual_ids=(target,),
                behavior=behavior,
            )
        )
    return rules, configured_sources


def _visual_type(visual: SourceNode) -> str:
    """Return the native visualType token preserved by the importer."""
    token = visual.attributes.get("visual_type")
    return str(token) if isinstance(token, str) else ""


def _page_width(page: SourceNode) -> float:
    width = _as_float(page.attributes.get("width"), 0.0)
    return width if width > 0.0 else 1280.0


def _page_height(page: SourceNode) -> float:
    height = _as_float(page.attributes.get("height"), 0.0)
    return height if height > 0.0 else 720.0


def _page_id(page: SourceNode) -> str:
    """Stable page id: the importer's ``page_id`` GUID, else the node name."""
    raw_id = page.attributes.get("page_id")
    return str(raw_id) if isinstance(raw_id, str) and raw_id else page.name


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


class PowerBIRenderPlanCompiler:
    """Compile a Power BI :class:`SourceAST` into the four neutral artifacts.

    The compiler reuses :class:`PowerBIAstCompiler` for the accepted
    :class:`SemanticModel` and adds the presentation/interaction/render layers
    that turn a Power BI artifact into a renderizable, origin-agnostic plan.

    Boundary: reads AST node kinds/attributes/``raw_payload`` only -- it never
    re-parses PBIP/PBIT bytes nor imports a destination runtime.
    """

    def __init__(self) -> None:
        self._semantic_compiler = PowerBIAstCompiler()

    @property
    def destination(self) -> str:
        """Stable destination id this compiler emits."""
        return "dataviz_render"

    def compile(self, source_ast: SourceAST) -> PowerBIRenderPlanCompilation:
        """Compile a Power BI :class:`SourceAST` into the neutral DataVIZ stack."""
        if not isinstance(source_ast, SourceAST):
            raise TypeError("source_ast must be a SourceAST")
        if source_ast.origin_kind is not OriginKind.POWER_BI:
            raise ValueError(
                f"PowerBIRenderPlanCompiler expects a power_bi SourceAST, "
                f"got {source_ast.origin_kind!r}"
            )
        source_ast.validate()

        notes: list[str] = []
        model, semantic_notes = self._semantic_compiler.compile_with_notes(source_ast)
        notes.extend(semantic_notes)

        metrics = frozenset(m.name for m in model.metrics)
        field_names = frozenset(f.name for e in model.entities for f in e.fields)

        view = _build_view(source_ast)
        for role in view.security_roles:
            notes.append(
                f"source security role {role.name!r} requires explicit RLS translation review; "
                "it is preserved in SourceAST and not lowered as a report filter"
            )

        visuals: list[VisualSpec] = []
        interactions: list[InteractionSpec] = []
        presentation_pages: list[PagePresentation] = []
        interaction_pages: list[PageInteractionSpec] = []
        raw_filters_preserved: list[dict[str, Any]] = []

        page_subtree_filter_ids: set[str] = set()
        for page in view.pages:
            page_id = _page_id(page)
            page_visual_names: list[str] = []
            page_presentation_visuals: list[VisualPresentation] = []
            page_visual_nodes: list[tuple[SourceNode, str, str, VisualSpec]] = []
            native_to_neutral: dict[str, str] = {}

            for visual_node in _page_visuals(page):
                pbir_type = _visual_type(visual_node)
                native_visual_id = str(
                    visual_node.attributes.get("visual_id", visual_node.name)
                )
                visual_name = f"{page_id}::{native_visual_id}"
                native_to_neutral[native_visual_id] = visual_name
                kind = _visual_kind(pbir_type)
                if pbir_type and pbir_type not in _PBIR_VISUAL_KIND:
                    notes.append(
                        f"{visual_name}: PBIR visualType {pbir_type!r} has no direct "
                        f"neutral kind; preserved as an unsupported intent"
                    )
                config = _load_visual_config(visual_node.raw_payload)
                roles = _bindings(config, metrics, field_names, notes, visual_name)
                query = _build_query(roles, model.name)
                spec = VisualSpec(
                    name=visual_name,
                    kind=kind,
                    page=page_id,
                    title=str(visual_node.name or ""),
                    data_roles=roles,
                    geometry=_geometry(visual_node.raw_payload),
                    query=query,
                )
                visuals.append(spec)
                page_visual_names.append(visual_name)
                page_visual_nodes.append((visual_node, native_visual_id, visual_name, spec))
                page_presentation_visuals.append(_to_presentation_visual(spec, pbir_type))

            explicit_cross, configured_sources = _page_cross_filter_rules(
                page, native_to_neutral, notes
            )
            page_cross: list[CrossFilterRule] = list(explicit_cross)
            for rule in explicit_cross:
                if rule.behavior not in {CrossFilterBehavior.FILTER, CrossFilterBehavior.HIGHLIGHT}:
                    continue
                kind = (
                    "highlight"
                    if rule.behavior is CrossFilterBehavior.HIGHLIGHT
                    else "filter"
                )
                for target in rule.target_visual_ids:
                    interactions.append(
                        InteractionSpec(
                            name=f"{rule.rule_id}->{target}",
                            source=rule.source_visual_id,
                            target=target,
                            kind=kind,
                        )
                    )

            # Preserve Power BI's default slicer behavior only when the PBIR page
            # does not contain an explicit interaction matrix for that source.
            for _node, native_id, visual_name, slicer in page_visual_nodes:
                if slicer.kind != "slicer" or native_id in configured_sources:
                    continue
                targets = [name for name in page_visual_names if name != visual_name]
                if not targets:
                    continue
                filter_target = slicer.data_roles.get("filter_target") or next(
                    (r for r in slicer.data_roles.values() if r.startswith("field:")), ""
                )
                for target in targets:
                    interactions.append(
                        InteractionSpec(
                            name=f"{visual_name}->{target}",
                            source=visual_name,
                            target=target,
                            kind="filter",
                            data_role=filter_target,
                        )
                    )
                page_cross.append(
                    CrossFilterRule(
                        rule_id=f"{visual_name}::cross",
                        source_visual_id=visual_name,
                        target_visual_ids=tuple(targets),
                        behavior=CrossFilterBehavior.FILTER,
                    )
                )
                notes.append(
                    f"page {page_id!r}: default slicer->visual cross-filtering applied "
                    f"for {visual_name!r}; no explicit PBIR source rule exists"
                )

            page_filter_conditions: list[FilterCondition] = []
            for filter_node in _page_filters(page):
                page_subtree_filter_ids.add(filter_node.node_id)
                condition = _filter_condition(
                    filter_node, notes, scope=FilterScope.PAGE
                )
                if condition is not None:
                    page_filter_conditions.append(condition)
                if isinstance(filter_node.raw_payload, dict):
                    raw_filters_preserved.append(
                        {"page_id": page_id, "raw": filter_node.raw_payload}
                    )

            for visual_node, _native_id, visual_name, visual_spec in page_visual_nodes:
                visual_filter_nodes = _visual_filters(visual_node)
                if not visual_filter_nodes:
                    continue
                filter_targets = tuple(
                    target
                    for rule in page_cross
                    if rule.source_visual_id == visual_name
                    and rule.behavior is CrossFilterBehavior.FILTER
                    for target in rule.target_visual_ids
                )
                for filter_node in visual_filter_nodes:
                    page_subtree_filter_ids.add(filter_node.node_id)
                    if visual_spec.kind == "slicer":
                        condition = _filter_condition(
                            filter_node,
                            notes,
                            scope=FilterScope.PAGE,
                            target_visual_ids=filter_targets,
                            is_interactive_slicer=True,
                            filter_id=visual_name,
                        )
                    else:
                        condition = _filter_condition(
                            filter_node,
                            notes,
                            scope=FilterScope.VISUAL,
                            target_visual_ids=(visual_name,),
                        )
                    if condition is not None:
                        page_filter_conditions.append(condition)
                    if isinstance(filter_node.raw_payload, dict):
                        raw_filters_preserved.append(
                            {
                                "page_id": page_id,
                                "visual_id": visual_name,
                                "raw": filter_node.raw_payload,
                            }
                        )

            presentation_pages.append(
                PagePresentation(
                    page_id=page_id,
                    name=page.name or page_id,
                    display_name=page.name,
                    width=_page_width(page),
                    height=_page_height(page),
                    visuals=tuple(page_presentation_visuals),
                    properties={"page_id": page_id},
                )
            )
            interaction_pages.append(
                PageInteractionSpec(
                    page_id=page_id,
                    filters=tuple(page_filter_conditions),
                    cross_filters=tuple(page_cross),
                )
            )

        # Only FILTER nodes outside every page subtree are workbook/report filters.
        # Unsupported page/visual predicates remain scoped raw evidence and must
        # never be reinterpreted as workbook-wide simply because lowering failed.
        global_filters: list[FilterCondition] = []
        for filter_node in view.filters:
            if filter_node.node_id in page_subtree_filter_ids:
                continue
            condition = _filter_condition(
                filter_node, notes, scope=FilterScope.WORKBOOK
            )
            if condition is not None:
                global_filters.append(condition)
            if isinstance(filter_node.raw_payload, dict):
                raw_filters_preserved.append({"page_id": "", "raw": filter_node.raw_payload})

        render_plan = RenderPlan(
            schema_version=RENDER_PLAN_SCHEMA_VERSION,
            visuals=visuals,
            interactions=interactions,
            description="Power BI SourceAST-derived neutral DataVIZ render plan",
            metadata=_metadata(source_ast, model, view, raw_filters_preserved, notes),
        )
        destination = DestinationPlan(
            schema_version=PLAN_SCHEMA_VERSION,
            destination=self.destination,
            origin=OriginKind.POWER_BI.value,
            source_model=model.name,
            source_schema_version=model.schema_version,
            body=render_plan.to_dict(),
        )
        presentation_ir = PresentationIR(
            doc_id=source_ast.root.name,
            title=source_ast.root.name,
            pages=tuple(presentation_pages),
            metadata={"origin": OriginKind.POWER_BI.value, "source": _source_metadata(source_ast)},
        )
        presentation_ir.validate()
        interaction_ir = InteractionIR(
            doc_id=source_ast.root.name,
            global_filters=tuple(global_filters),
            pages=tuple(interaction_pages),
            metadata={
                "origin": OriginKind.POWER_BI.value,
                "source": _source_metadata(source_ast),
                "raw_filters_preserved": len(raw_filters_preserved),
            },
        )
        interaction_ir.validate()
        return PowerBIRenderPlanCompilation(
            semantic_model=model,
            presentation_ir=presentation_ir,
            interaction_ir=interaction_ir,
            render_plan=destination,
            notes=tuple(notes),
        )


def _metadata(
    source_ast: SourceAST,
    model: SemanticModel,
    view: _PowerBIView,
    raw_filters_preserved: list[dict[str, Any]],
    diagnostics: list[str],
) -> dict[str, Any]:
    """Preserve neutral traceability without serializing raw source bytes."""
    visual_count = sum(len(_page_visuals(p)) for p in view.pages)
    return {
        "source": _source_metadata(source_ast),
        "model": {
            "name": model.name,
            "schema_version": model.schema_version,
            "entities": len(model.entities),
            "metrics": len(model.metrics),
            "parameters": len(model.parameters),
            "relationships": len(model.relationships),
        },
        "source_counts": {
            "pages": len(view.pages),
            "visuals": visual_count,
            "filters": len(view.filters),
        },
        "raw_filters_preserved": raw_filters_preserved,
        "diagnostics": list(diagnostics),
        "limits": [
            "Power BI DAX is not executed in the RenderPlan; out-of-subset DAX is "
            "preserved verbatim in the semantic model metric descriptions.",
            "Slicer cross-filtering reflects the Power BI default page scope; edited "
            "visual interactions inside raw_payload are not yet interpreted.",
            "Categorical filter literal values are extracted best-effort; the raw "
            "filter object is always preserved in 'raw_filters_preserved'.",
            "The plan carries no row data nor executable connections.",
        ],
    }


def _source_metadata(source_ast: SourceAST) -> dict[str, str]:
    """Return sanitized source identity shared by every compiled artifact."""
    provenance = source_ast.provenance
    return {
        "origin": source_ast.origin_kind.value,
        "artifact_path": provenance.artifact_path if provenance else "",
        "artifact_hash": provenance.artifact_hash if provenance else "",
        "source_ast_schema_version": source_ast.schema_version,
    }


def compile_powerbi_render_plan(source_ast: SourceAST) -> PowerBIRenderPlanCompilation:
    """Compile a Power BI :class:`SourceAST` into the neutral DataVIZ stack.

    Args:
        source_ast: a :class:`SourceAST` with ``origin_kind == power_bi``
            (typically produced by :func:`core.importers.powerbi.import_powerbi`).

    Returns:
        A :class:`PowerBIRenderPlanCompilation` carrying the accepted neutral
        :class:`SemanticModel`, the :class:`PresentationIR`, the
        :class:`InteractionIR` and the DataVIZ :class:`RenderPlan` envelope.
    """
    return PowerBIRenderPlanCompiler().compile(source_ast)


__all__ = [
    "PowerBIRenderPlanCompilation",
    "PowerBIRenderPlanCompiler",
    "compile_powerbi_render_plan",
]
