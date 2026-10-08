"""DataVIZ Render Plan contract and compiler.

The :class:`RenderPlan` is DataVIZ's destination-specific visual plan: a list
of :class:`VisualSpec` and the :class:`InteractionSpec`s between them. It is
the visual-centric counterpart to PBIP's table/measure-centric plan -- both
are produced from the **same** neutral
:class:`~core.contracts.semantic_ir.SemanticModel`.

Boundary rules (same as :mod:`core.compilers.base`):

* Depends on contracts (``core.contracts.*``) and the sibling compiler module
  ``core.compilers.base`` -- one-way, no cycles.
* Does **not** import React, recharts, MUI or any frontend runtime. The render
  plan is passive structural data; a future runtime consumes it.

:class:`DataVIZRenderPlanCompiler` derives the plan from the durable
three-IR snapshot: a :class:`~core.contracts.presentation_ir.PresentationIR`
(visuals, pages, bindings, geometry) plus a
:class:`~core.contracts.interaction_ir.InteractionIR` (cross-filters, drills,
navigations) over the canonical
:class:`~core.contracts.semantic_ir.SemanticModel`. Interactions the render
plan cannot represent (tooltips, selections, standalone filters, non-page
navigations) are surfaced as explicit ``metadata["diagnostics"]`` entries,
never silently dropped. When only the semantic model is passed, the compiler
keeps the legacy proof stub (one card per metric, one table per non-hidden
entity) for backward compatibility.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.compilers.base import DestinationCompiler, DestinationPlan
from core.contracts.custom_visual import CustomVisualSpec, spec_from_properties
from core.contracts.interaction_ir import (
    CrossFilterBehavior,
    InteractionIR,
    NavigationKind,
)
from core.contracts.presentation_ir import (
    ContainerKind,
    FieldRole,
    PresentationIR,
    VisualIntentKind,
)
from core.contracts.query_ast import QuerySpec
from core.contracts.semantic_ir import SemanticModel

SCHEMA_VERSION: str = "2.1.0"
"""Current schema version of the DataVIZ render plan contract (SemVer)."""

#: Native visual kinds supported by the current runtime. Other non-empty strings
#: remain valid visual intents and can degrade explicitly at runtime.
NATIVE_VISUAL_KINDS: frozenset[str] = frozenset(
    {
        "table",
        "card",
        "kpi",
        "bar",
        "stacked_bar",
        "percent_stacked_bar",
        "column",
        "stacked_column",
        "percent_stacked_column",
        "line",
        "area",
        "stacked_area",
        "ribbon",
        "packed_bubbles",
        "gantt",
        "pie",
        "donut",
        "scatter",
        "treemap",
        "heatmap",
        "funnel",
        "histogram",
        "box_plot",
        "bullet",
        "pareto",
        "lollipop",
        "combo",
        "matrix",
        "map",
        "waterfall",
        "gauge",
        "text_box",
        "slicer",
        "custom_visual",
    }
)

# Compatibilidad para consumidores que importaban el nombre anterior. Esta
# constante describe capacidad nativa, no una allowlist de intents válidos.
ALLOWED_VISUAL_KINDS: frozenset[str] = NATIVE_VISUAL_KINDS

#: Closed set of neutral interaction kinds between visuals.
ALLOWED_INTERACTION_KINDS: frozenset[str] = frozenset(
    {
        "filter",
        "highlight",
        "drill",
        "page_navigation",
    }
)

#: PresentationIR visual intent -> RenderPlan visual kind (the reverse of the
#: neutral mapping proven by the SourceAST compiler). Unknown intents degrade
#: explicitly to the neutral custom visual intent rather than being coerced.
_INTENT_TO_KIND: dict[str, str] = {
    "bar": "bar",
    "stacked_bar": "stacked_bar",
    "percent_stacked_bar": "percent_stacked_bar",
    "column": "column",
    "stacked_column": "stacked_column",
    "percent_stacked_column": "percent_stacked_column",
    "line": "line",
    "area": "area",
    "stacked_area": "stacked_area",
    "ribbon": "ribbon",
    "packed_bubbles": "packed_bubbles",
    "gantt": "gantt",
    "pie": "pie",
    "donut": "donut",
    "scatter": "scatter",
    "treemap": "treemap",
    "heatmap": "heatmap",
    "funnel": "funnel",
    "histogram": "histogram",
    "box_plot": "box_plot",
    "bullet": "bullet",
    "pareto": "pareto",
    "lollipop": "lollipop",
    "combo": "combo",
    "map": "map",
    "kpi_card": "kpi",
    "table": "table",
    "pivot_matrix": "matrix",
    "waterfall": "waterfall",
    "gauge": "gauge",
    "slicer_filter": "slicer",
    "text_box": "text_box",
    "container": "container",
    "image": "image",
    "custom_visual": "custom_visual",
}

#: PresentationIR field roles that map 1:1 onto RenderPlan data-role keys.
#: Every FieldRole value is already a neutral role key, so the mapping is the
#: identity restricted to the fields carried by :class:`DataBinding`.
_FIELD_ROLE_TO_ROLE: dict[str, str] = {role.value: role.value for role in FieldRole}


def _coerce_mapping(value: Any) -> dict[str, str]:
    """Normalize ``value`` to a ``dict[str, str]`` for visual data roles."""
    if not isinstance(value, Mapping):
        raise TypeError("data_roles must be a Mapping")
    return {str(k): str(v) for k, v in value.items()}


def _add_data_role(roles: dict[str, str], role: str, value: str) -> None:
    """Append a binding, suffixing repeated same-role bindings deterministically."""
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


def _neutral_binding_ref(
    field_name: str, measure_names: set[str], parameter_names: set[str]
) -> str:
    """Resolve a binding field name to a neutral DataVIZ reference."""
    if field_name in measure_names:
        return f"measure:{field_name}"
    if field_name in parameter_names:
        return f"parameter:{field_name}"
    return f"field:{field_name}"


def _geometry_dict(geometry: Any) -> dict[str, Any]:
    """Lift a :class:`VisualGeometry` into the render-plan geometry Mapping."""
    return {
        "x": geometry.x,
        "y": geometry.y,
        "width": geometry.width,
        "height": geometry.height,
        "z_index": geometry.z_index,
        "layout_mode": (
            geometry.layout_mode.value
            if isinstance(geometry.layout_mode, ContainerKind)
            else str(geometry.layout_mode)
        ),
    }


def _unsupported_diagnostic(
    kind: str, name: str, page_id: str, reason: str
) -> dict[str, str]:
    """One explicit diagnostic for an element the render plan cannot represent."""
    return {"kind": kind, "name": name, "page_id": page_id, "reason": reason}


@dataclass(frozen=True)
class ForecastSpec:
    """Transformación ETS neutral aplicada después de la consulta agregada."""

    time_field: str
    value_field: str
    period: str
    horizon: int
    confidence_level: float = 95.0
    ignore_last: int = 0
    fill_missing: bool = False
    seasonal: bool = True
    prediction_intervals: bool = True

    def __post_init__(self) -> None:
        if not self.time_field or not self.value_field:
            raise ValueError("forecast time_field and value_field are required")
        if self.period not in {"day", "week", "month", "quarter", "year"}:
            raise ValueError(f"forecast period not allowed: {self.period!r}")
        if self.horizon <= 0:
            raise ValueError("forecast horizon must be positive")
        if not 0 < self.confidence_level < 100:
            raise ValueError("forecast confidence_level must be between 0 and 100")
        if self.ignore_last < 0:
            raise ValueError("forecast ignore_last must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_field": self.time_field,
            "value_field": self.value_field,
            "period": self.period,
            "horizon": self.horizon,
            "confidence_level": self.confidence_level,
            "ignore_last": self.ignore_last,
            "fill_missing": self.fill_missing,
            "seasonal": self.seasonal,
            "prediction_intervals": self.prediction_intervals,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ForecastSpec:
        return cls(**dict(data))


@dataclass(frozen=True)
class VisualSpec:
    """A single visual in a :class:`RenderPlan`.

    Attributes:
        name: unique visual name within the plan.
        kind: non-empty neutral visual intent string.
        data_roles: mapping ``role -> neutral reference`` (e.g.
            ``{"value": "measure:total_amount"}`` or
            ``{"rows": "entity:sales.region"}``).
    """

    name: str
    kind: str
    data_roles: Mapping[str, str] = field(default_factory=dict)
    page: str = ""
    title: str = ""
    geometry: Mapping[str, Any] = field(default_factory=dict)
    query: QuerySpec | None = None
    forecast: ForecastSpec | None = None
    liveness_policy: str = ""
    custom_spec: CustomVisualSpec | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("VisualSpec.name is required")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("VisualSpec.kind is required")
        object.__setattr__(self, "data_roles", _coerce_mapping(self.data_roles))
        if not isinstance(self.geometry, Mapping):
            raise TypeError("geometry must be a Mapping")
        object.__setattr__(self, "geometry", dict(self.geometry))
        if self.query is not None and not isinstance(self.query, QuerySpec):
            raise TypeError("query must be QuerySpec or None")
        if self.forecast is not None and not isinstance(self.forecast, ForecastSpec):
            raise TypeError("forecast must be ForecastSpec or None")
        if self.custom_spec is not None and not isinstance(self.custom_spec, CustomVisualSpec):
            raise TypeError("custom_spec must be CustomVisualSpec or None")
        if self.liveness_policy not in {
            "",
            "requires_marks",
            "allows_empty_state",
            "decorative",
        }:
            raise ValueError("invalid liveness_policy")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        result = {
            "name": self.name,
            "kind": self.kind,
            "data_roles": dict(self.data_roles),
            "page": self.page,
            "title": self.title,
            "geometry": dict(self.geometry),
            "query": self.query.to_dict() if self.query is not None else None,
            "forecast": self.forecast.to_dict() if self.forecast is not None else None,
        }
        if self.liveness_policy:
            result["liveness_policy"] = self.liveness_policy
        if self.custom_spec is not None:
            result["custom_spec"] = self.custom_spec.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VisualSpec:
        """Reconstruct from a dict."""
        return cls(
            name=str(data["name"]),
            kind=data["kind"],
            data_roles=data.get("data_roles", {}),
            page=str(data.get("page", "")),
            title=str(data.get("title", "")),
            geometry=data.get("geometry", {}),
            query=(QuerySpec.from_dict(data["query"]) if data.get("query") is not None else None),
            forecast=(
                ForecastSpec.from_dict(data["forecast"])
                if data.get("forecast") is not None
                else None
            ),
            liveness_policy=str(data.get("liveness_policy", "")),
            custom_spec=(
                CustomVisualSpec.from_dict(data["custom_spec"])
                if data.get("custom_spec") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class InteractionSpec:
    """An interaction between two visuals in a :class:`RenderPlan`.

    Attributes:
        name: unique interaction name.
        source / target: names of the interacting visuals.
        kind: one of :data:`ALLOWED_INTERACTION_KINDS`.
    """

    name: str
    source: str
    target: str
    kind: str
    data_role: str = ""
    values: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("InteractionSpec.name is required")
        if not self.source or not self.target:
            raise ValueError("InteractionSpec.source and target are required")
        if self.kind not in ALLOWED_INTERACTION_KINDS:
            raise ValueError(
                f"interaction kind not allowed: {self.kind!r}; "
                f"allowlist={sorted(ALLOWED_INTERACTION_KINDS)}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "name": self.name,
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "data_role": self.data_role,
            "values": list(self.values),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InteractionSpec:
        """Reconstruct from a dict."""
        return cls(
            name=str(data["name"]),
            source=str(data["source"]),
            target=str(data["target"]),
            kind=str(data["kind"]),
            data_role=str(data.get("data_role", "")),
            values=tuple(data.get("values", ())),
        )


@dataclass(frozen=True)
class RenderPlan:
    """DataVIZ visual render plan: visuals plus their interactions.

    Attributes:
        schema_version: SemVer; must equal :data:`SCHEMA_VERSION`.
        visuals: ordered list of :class:`VisualSpec`.
        interactions: ordered list of :class:`InteractionSpec`.
        description: free-form documentation.
    """

    schema_version: str
    visuals: list[VisualSpec] = field(default_factory=list)
    interactions: list[InteractionSpec] = field(default_factory=list)
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, "
                f"received {self.schema_version!r}"
            )
        names: set[str] = set()
        for visual in self.visuals:
            if not isinstance(visual, VisualSpec):
                raise TypeError("visuals entries must be VisualSpec")
            if visual.name in names:
                raise ValueError(f"duplicate visual name: {visual.name!r}")
            names.add(visual.name)
        for inter in self.interactions:
            if not isinstance(inter, InteractionSpec):
                raise TypeError("interactions entries must be InteractionSpec")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("metadata must be a Mapping")
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        result = {
            "schema_version": self.schema_version,
            "visuals": [v.to_dict() for v in self.visuals],
            "interactions": [i.to_dict() for i in self.interactions],
            "description": self.description,
        }
        if self.metadata:
            result["metadata"] = dict(self.metadata)
        return result

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize to canonical JSON (sorted keys, UTF-8 pure)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RenderPlan:
        """Rebuild a render plan from a dict, enforcing ``schema_version``."""
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, received {received!r}"
            )
        return cls(
            schema_version=SCHEMA_VERSION,
            visuals=[VisualSpec.from_dict(v) for v in data.get("visuals", [])],
            interactions=[InteractionSpec.from_dict(i) for i in data.get("interactions", [])],
            description=str(data.get("description", "")),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> RenderPlan:
        """Rebuild from canonical JSON."""
        return cls.from_dict(json.loads(text))


class DataVIZRenderPlanCompiler(DestinationCompiler):
    """Compile a DataVIZ :class:`RenderPlan` from the durable three-IR snapshot.

    Canonical path: :meth:`compile` consumes the
    :class:`~core.contracts.presentation_ir.PresentationIR` (multipage pages,
    visual intents, bindings, geometry, titles) and the
    :class:`~core.contracts.interaction_ir.InteractionIR` (cross-filters,
    drills, page navigations) on top of the
    :class:`~core.contracts.semantic_ir.SemanticModel`. Inter-visual
    interactions the render plan supports become :class:`InteractionSpec`s;
    every other interaction element (tooltips, selections, standalone filters,
    non-page navigations) and authored-hidden visuals become explicit
    ``metadata["diagnostics"]`` entries -- never silently dropped.

    Backward-compatible fallback: with only the semantic model, it derives the
    legacy proof stub -- one ``card`` per metric and one ``table`` per
    non-hidden entity, no interactions (byte-identical to the original stub so
    persisted plans and fixture hashes stay stable).
    """

    @property
    def destination(self) -> str:
        return "dataviz_render"

    def compile(
        self,
        model: SemanticModel,
        presentation: PresentationIR | None = None,
        interaction: InteractionIR | None = None,
    ) -> DestinationPlan:
        """Compile a render plan from the canonical snapshot.

        Pass ``presentation`` and ``interaction`` to derive the plan from the
        full durable three-IR snapshot. Passing neither keeps the legacy
        semantic-only proof stub. Passing ``interaction`` without
        ``presentation`` is rejected loudly instead of silently ignoring it.
        """
        if presentation is None:
            if interaction is not None:
                raise TypeError(
                    "compile requires presentation when interaction is provided"
                )
            return self._compile_from_semantic(model)
        if not isinstance(presentation, PresentationIR):
            raise TypeError("presentation must be a PresentationIR or None")
        if interaction is not None and not isinstance(interaction, InteractionIR):
            raise TypeError("interaction must be an InteractionIR or None")
        return self._compile_from_snapshot(model, presentation, interaction)

    def _compile_from_semantic(self, model: SemanticModel) -> DestinationPlan:
        visuals: list[VisualSpec] = []
        # One card per metric: value role binds to the measure.
        for metric in model.metrics:
            if metric.hidden:
                continue
            visuals.append(
                VisualSpec(
                    name=f"card_{metric.name}",
                    kind="card",
                    data_roles={"value": f"measure:{metric.name}"},
                )
            )
        # One table per non-hidden entity: one role per non-hidden field.
        for entity in model.entities:
            if entity.hidden:
                continue
            roles = {
                field.name: f"entity:{entity.name}.{field.name}"
                for field in entity.fields
                if not field.hidden
            }
            visuals.append(
                VisualSpec(
                    name=f"table_{entity.name}",
                    kind="table",
                    data_roles=roles,
                )
            )
        render_plan = RenderPlan(
            schema_version=SCHEMA_VERSION,
            visuals=visuals,
            interactions=[],
        )
        return self._envelope(model, render_plan.to_dict())

    def _compile_from_snapshot(
        self,
        model: SemanticModel,
        presentation: PresentationIR,
        interaction: InteractionIR | None,
    ) -> DestinationPlan:
        measure_names = {metric.name for metric in model.metrics}
        parameter_names = {parameter.name for parameter in model.parameters}
        diagnostics: list[dict[str, str]] = []
        visuals: list[VisualSpec] = []

        for page in presentation.pages:
            for visual in page.visuals:
                visual_id = visual.visual_id
                if not visual.is_visible:
                    diagnostics.append(
                        _unsupported_diagnostic(
                            kind="visual",
                            name=visual_id,
                            page_id=page.page_id,
                            reason=(
                                "visual is authored hidden and cannot be "
                                "represented as a rendered visual"
                            ),
                        )
                    )
                    continue
                intent = (
                    visual.intent.value
                    if isinstance(visual.intent, VisualIntentKind)
                    else str(visual.intent)
                )
                kind = _INTENT_TO_KIND.get(intent, "custom_visual")
                custom_spec = (
                    spec_from_properties(visual.properties)
                    if kind == "custom_visual"
                    else None
                )
                roles: dict[str, str] = {}
                for binding in visual.bindings:
                    role = (
                        binding.role.value
                        if isinstance(binding.role, FieldRole)
                        else str(binding.role)
                    )
                    if role not in _FIELD_ROLE_TO_ROLE:
                        continue
                    _add_data_role(
                        roles,
                        role,
                        _neutral_binding_ref(
                            binding.field_name, measure_names, parameter_names
                        ),
                    )
                visuals.append(
                    VisualSpec(
                        name=visual_id,
                        kind=kind,
                        page=page.page_id,
                        title=visual.title,
                        data_roles=roles,
                        geometry=_geometry_dict(visual.geometry),
                        custom_spec=custom_spec,
                    )
                )

        interactions = _derived_interactions(interaction, diagnostics)

        derivation_parts = ["semantic", "presentation"]
        if interaction is not None:
            derivation_parts.append("interaction")
        metadata: dict[str, Any] = {"derivation": "+".join(derivation_parts)}
        if diagnostics:
            metadata["diagnostics"] = diagnostics
        render_plan = RenderPlan(
            schema_version=SCHEMA_VERSION,
            visuals=visuals,
            interactions=interactions,
            description="DataVIZ render plan derived from the durable three-IR snapshot",
            metadata=metadata,
        )
        return self._envelope(model, render_plan.to_dict())


def _derived_interactions(
    interaction: InteractionIR | None, diagnostics: list[dict[str, str]]
) -> list[InteractionSpec]:
    """Map :class:`InteractionIR` rules onto render-plan interactions.

    Cross-filters (filter/highlight), drills and page navigations are
    representable as inter-visual :class:`InteractionSpec`s. Every remaining
    interaction element is reported as an explicit diagnostic with a reason;
    nothing is silently dropped.
    """
    interactions: list[InteractionSpec] = []
    if interaction is None:
        return interactions

    for page in interaction.pages:
        for rule in page.cross_filters:
            if rule.behavior is CrossFilterBehavior.NONE:
                diagnostics.append(
                    _unsupported_diagnostic(
                        kind="cross_filter",
                        name=rule.rule_id,
                        page_id=page.page_id,
                        reason=(
                            "cross-filter behavior 'none' is not representable "
                            "as an interaction"
                        ),
                    )
                )
                continue
            kind = (
                rule.behavior.value
                if isinstance(rule.behavior, CrossFilterBehavior)
                else str(rule.behavior)
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
        for drill in page.drills:
            interactions.append(
                InteractionSpec(
                    name=drill.drill_id,
                    source=drill.target_visual_id,
                    target=drill.target_visual_id,
                    kind="drill",
                )
            )
        for navigation in page.navigations:
            if (
                navigation.kind is NavigationKind.PAGE_NAV
                and navigation.target_page_id
            ):
                interactions.append(
                    InteractionSpec(
                        name=navigation.action_id,
                        source=navigation.trigger_source_id,
                        target=navigation.target_page_id,
                        kind="page_navigation",
                    )
                )
                continue
            diagnostics.append(
                _unsupported_diagnostic(
                    kind="navigation",
                    name=navigation.action_id,
                    page_id=page.page_id,
                    reason=(
                        f"navigation kind {navigation.kind.value!r} is not "
                        "representable as page_navigation"
                    ),
                )
            )
        for selection in page.selections:
            diagnostics.append(
                _unsupported_diagnostic(
                    kind="selection",
                    name=selection.selection_id,
                    page_id=page.page_id,
                    reason=(
                        "selection contracts are not representable as "
                        "inter-visual render-plan interactions"
                    ),
                )
            )
        for tooltip in page.tooltips:
            diagnostics.append(
                _unsupported_diagnostic(
                    kind="tooltip",
                    name=tooltip.tooltip_id,
                    page_id=page.page_id,
                    reason="tooltip behaviors are not representable in the render plan",
                )
            )
        for filter_condition in page.filters:
            diagnostics.append(
                _unsupported_diagnostic(
                    kind="filter",
                    name=filter_condition.filter_id,
                    page_id=page.page_id,
                    reason=(
                        "filter condition has no source visual; render-plan "
                        "interactions require a source->target visual pair"
                    ),
                )
            )
    for filter_condition in interaction.global_filters:
        diagnostics.append(
            _unsupported_diagnostic(
                kind="filter",
                name=filter_condition.filter_id,
                page_id="",
                reason=(
                    "global filter condition has no source visual; render-plan "
                    "interactions require a source->target visual pair"
                ),
            )
        )
    return interactions


__all__ = [
    "ALLOWED_INTERACTION_KINDS",
    "ALLOWED_VISUAL_KINDS",
    "NATIVE_VISUAL_KINDS",
    "SCHEMA_VERSION",
    "DataVIZRenderPlanCompiler",
    "ForecastSpec",
    "InteractionSpec",
    "RenderPlan",
    "VisualSpec",
]
