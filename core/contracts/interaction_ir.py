"""Interaction Intermediate Representation (Interaction IR) contract.

Defines a neutral, target-agnostic interaction contract for selections,
filters, cross-filtering, hierarchy drilldown/drillup, tooltips, and navigation actions.

This layer decouples interactive behaviors from proprietary dashboard runtimes
(e.g., Tableau Actions vs Power BI Visual Interactions vs custom Web event loops).

Schema Versioning Policy (SemVer):
    * MAJOR: Breaking changes (field removals, type changes, semantic shifts).
    * MINOR: Backward-compatible extensions (optional fields with defaults).
    * PATCH: Internal fixes preserving serialized format.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION: str = "1.1.0"
"""Current schema version for Interaction IR."""


class SelectionMode(StrEnum):
    """Selection mode for interactive elements."""

    SINGLE = "single"
    MULTI = "multi"
    RANGE = "range"
    LITERAL_LIST = "literal_list"


class FilterOperator(StrEnum):
    """Filter logical operators."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    BETWEEN = "between"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    NOT_STARTS_WITH = "not_starts_with"
    ENDS_WITH = "ends_with"
    NOT_ENDS_WITH = "not_ends_with"
    TOP_N = "top_n"


class FilterScope(StrEnum):
    """Scope of influence for a filter rule."""

    VISUAL = "visual"
    PAGE = "page"
    WORKBOOK = "workbook"


class CrossFilterBehavior(StrEnum):
    """Behavior applied to target visuals on cross-filtering."""

    FILTER = "filter"
    HIGHLIGHT = "highlight"
    NONE = "none"


class TooltipKind(StrEnum):
    """Tooltip rendering behavior kind."""

    DEFAULT = "default"
    CUSTOM_FIELDS = "custom_fields"
    REPORT_PAGE = "report_page"


class NavigationKind(StrEnum):
    """Target destination or action for interactive navigation."""

    PAGE_NAV = "page_nav"
    URL_NAV = "url_nav"
    BACK = "back"
    BOOKMARK = "bookmark"
    CLEAR_FILTERS = "clear_filters"


_FORBIDDEN_CARRIER_KEYS: frozenset[str] = frozenset(
    {"raw_sql", "query", "secret", "password", "token", "connection_string", "dsn", "sql"}
)
_FORBIDDEN_CARRIER_KEYS_CF: frozenset[str] = frozenset(k.casefold() for k in _FORBIDDEN_CARRIER_KEYS)

_EXECUTABLE_SQL_VALUE: re.Pattern[str] = re.compile(
    r"\b(select|insert|update|delete|merge|create|drop|alter|truncate)\b"
    r"[^;\n]{0,60}?\b[a-zA-Z_][\w.]*\b[^;\n]{0,40}?\b(from|into|table|values|set)\b",
    re.IGNORECASE | re.DOTALL,
)
"""Executable SQL statement embedded in a single value (fail closed)."""

_USERINFO_VALUE: re.Pattern[str] = re.compile(r"://[^\s/@]+:[^\s/@]+@")
"""Embedded ``scheme://user:password@host`` credentials (fail closed)."""

_SECRET_ASSIGNMENT_VALUE: re.Pattern[str] = re.compile(
    r"\b(password|passwd|pwd|secret|token|api[_-]?key|connection[_-]?string|dsn)"
    r"\s*[:=]\s*\S+",
    re.IGNORECASE,
)
"""Inline ``<secret-name> = <value>`` assignments (fail closed)."""

_PRIVATE_KEY_VALUE: re.Pattern[str] = re.compile(r"-----BEGIN [^-]+ PRIVATE KEY-----")


def _scan_property_value(value: Any, context: str) -> None:
    """Fail closed when a nested property value carries a secret or SQL carrier.

    Structured metadata (nested dicts/lists) is allowed: the scalar leaves are
    what get scanned. Any match raises instead of redacting, so carriers can
    never reach a consumer through the properties/metadata envelope.
    """
    if value is None:
        return
    if isinstance(value, str):
        if _USERINFO_VALUE.search(value):
            raise ValueError(f"{context} property value embeds userinfo credentials")
        if _SECRET_ASSIGNMENT_VALUE.search(value):
            raise ValueError(f"{context} property value embeds a secret assignment")
        if _PRIVATE_KEY_VALUE.search(value):
            raise ValueError(f"{context} property value embeds a private key")
        if _EXECUTABLE_SQL_VALUE.search(value):
            raise ValueError(f"{context} property value embeds executable SQL")
        return
    if isinstance(value, (bool, int, float)):
        return
    if isinstance(value, Mapping):
        for sub_key, sub_value in value.items():
            if not isinstance(sub_key, str):
                raise TypeError(
                    f"{context} property key must be a string, got {type(sub_key).__name__}"
                )
            if sub_key.casefold() in _FORBIDDEN_CARRIER_KEYS_CF:
                raise ValueError(
                    f"{context} properties contains forbidden carrier key {sub_key!r}"
                )
            _scan_property_value(sub_value, context)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _scan_property_value(item, context)
        return
    raise ValueError(
        f"{context} property must be a scalar, mapping, or list of scalars, got {type(value).__name__}"
    )

_ALLOWED_SELECTION_CONTRACT_KEYS: frozenset[str] = frozenset(
    {"selection_id", "source_visual_id", "target_field", "mode", "selected_values", "is_cleared_on_deselect"}
)
_ALLOWED_FILTER_CONDITION_KEYS: frozenset[str] = frozenset(
    {"filter_id", "name", "target_field", "operator", "values", "scope", "target_visual_ids", "is_interactive_slicer"}
)
_ALLOWED_CROSS_FILTER_RULE_KEYS: frozenset[str] = frozenset(
    {"rule_id", "source_visual_id", "target_visual_ids", "behavior", "bidirectional"}
)
_ALLOWED_DRILL_SPEC_KEYS: frozenset[str] = frozenset(
    {"drill_id", "target_visual_id", "hierarchy_levels", "current_level_index", "allow_drill_down", "allow_drill_up", "drill_through_page_id"}
)
_ALLOWED_TOOLTIP_SPEC_KEYS: frozenset[str] = frozenset(
    {"tooltip_id", "source_visual_id", "kind", "fields", "target_page_id", "trigger"}
)
_ALLOWED_NAVIGATION_ACTION_KEYS: frozenset[str] = frozenset(
    {"action_id", "trigger_source_id", "kind", "target_page_id", "target_url", "parameters"}
)
_ALLOWED_PAGE_INTERACTION_SPEC_KEYS: frozenset[str] = frozenset(
    {"page_id", "selections", "filters", "cross_filters", "drills", "tooltips", "navigations"}
)
_ALLOWED_INTERACTION_IR_KEYS: frozenset[str] = frozenset(
    {"schema_version", "doc_id", "global_filters", "pages", "metadata"}
)


def _validate_safe_properties(props: Mapping[str, Any], context: str) -> dict[str, Any]:
    """Validate free-form properties/metadata recursively and fail closed.

    Carrier keys (raw SQL, connection strings, secrets, tokens, queries) are
    banned at any nesting depth (case-insensitive). Values are scanned for
    embedded credentials, private keys, secret assignments, and executable SQL.
    Nested structured metadata is allowed; any violation raises.
    """
    if not isinstance(props, Mapping):
        raise TypeError(f"{context} properties must be an object")
    for k, v in props.items():
        if not isinstance(k, str):
            raise TypeError(f"{context} property key must be a string, got {type(k).__name__}")
        if k.casefold() in _FORBIDDEN_CARRIER_KEYS_CF:
            raise ValueError(f"{context} properties contains forbidden carrier key {k!r}")
        _scan_property_value(v, context)
    return dict(props)


@dataclass(frozen=True)
class SelectionContract:
    """Active visual selection contract."""

    selection_id: str
    source_visual_id: str
    target_field: str
    mode: SelectionMode = SelectionMode.SINGLE
    selected_values: tuple[Any, ...] = ()
    is_cleared_on_deselect: bool = True

    def __post_init__(self) -> None:
        if not self.selection_id or not isinstance(self.selection_id, str):
            raise ValueError("selection_id must be a non-empty string")
        if not self.source_visual_id or not isinstance(self.source_visual_id, str):
            raise ValueError("source_visual_id must be a non-empty string")
        if not self.target_field or not isinstance(self.target_field, str):
            raise ValueError("target_field must be a non-empty string")
        for val in self.selected_values:
            if val is not None and not isinstance(val, (bool, int, float, str)):
                raise ValueError(
                    f"selection selected_values must contain scalars, got {type(val).__name__}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "source_visual_id": self.source_visual_id,
            "target_field": self.target_field,
            "mode": self.mode.value if isinstance(self.mode, SelectionMode) else str(self.mode),
            "selected_values": list(self.selected_values),
            "is_cleared_on_deselect": self.is_cleared_on_deselect,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SelectionContract:
        if not isinstance(data, Mapping):
            raise TypeError("SelectionContract must be an object")
        unknown = set(data) - _ALLOWED_SELECTION_CONTRACT_KEYS
        if unknown:
            raise ValueError(f"unknown selection contract field(s): {sorted(unknown)}")
        mode_raw = data.get("mode", SelectionMode.SINGLE.value)
        mode = SelectionMode(mode_raw) if isinstance(mode_raw, str) else mode_raw
        vals_raw = data.get("selected_values", ())
        if not isinstance(vals_raw, (list, tuple)):
            raise TypeError("selection selected_values must be a list")
        vals = tuple(vals_raw)
        return cls(
            selection_id=str(data["selection_id"]),
            source_visual_id=str(data["source_visual_id"]),
            target_field=str(data["target_field"]),
            mode=mode,
            selected_values=vals,
            is_cleared_on_deselect=bool(data.get("is_cleared_on_deselect", True)),
        )


@dataclass(frozen=True)
class FilterCondition:
    """Declarative filter condition rule."""

    filter_id: str
    name: str
    target_field: str
    operator: FilterOperator
    values: tuple[Any, ...] = ()
    scope: FilterScope = FilterScope.PAGE
    target_visual_ids: tuple[str, ...] = ()
    is_interactive_slicer: bool = False

    def __post_init__(self) -> None:
        if not self.filter_id or not isinstance(self.filter_id, str):
            raise ValueError("filter_id must be a non-empty string")
        if not self.target_field or not isinstance(self.target_field, str):
            raise ValueError("target_field must be a non-empty string")
        for val in self.values:
            if val is not None and not isinstance(val, (bool, int, float, str)):
                raise ValueError(
                    f"filter condition values must contain scalars, got {type(val).__name__}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "filter_id": self.filter_id,
            "name": self.name,
            "target_field": self.target_field,
            "operator": self.operator.value
            if isinstance(self.operator, FilterOperator)
            else str(self.operator),
            "values": list(self.values),
            "scope": self.scope.value if isinstance(self.scope, FilterScope) else str(self.scope),
            "target_visual_ids": list(self.target_visual_ids),
            "is_interactive_slicer": self.is_interactive_slicer,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FilterCondition:
        if not isinstance(data, Mapping):
            raise TypeError("FilterCondition must be an object")
        unknown = set(data) - _ALLOWED_FILTER_CONDITION_KEYS
        if unknown:
            raise ValueError(f"unknown filter condition field(s): {sorted(unknown)}")
        op_raw = data.get("operator")
        if not op_raw:
            raise ValueError("operator is required for FilterCondition")
        op = FilterOperator(op_raw) if isinstance(op_raw, str) else op_raw

        scope_raw = data.get("scope", FilterScope.PAGE.value)
        scope = FilterScope(scope_raw) if isinstance(scope_raw, str) else scope_raw

        vals_raw = data.get("values", ())
        if not isinstance(vals_raw, (list, tuple)):
            raise TypeError("filter condition values must be a list")
        vals = tuple(vals_raw)

        targets_raw = data.get("target_visual_ids", ())
        if not isinstance(targets_raw, (list, tuple)):
            raise TypeError("target_visual_ids must be a list")
        targets = tuple(str(t) for t in targets_raw)

        return cls(
            filter_id=str(data["filter_id"]),
            name=str(data.get("name", "")),
            target_field=str(data["target_field"]),
            operator=op,
            values=vals,
            scope=scope,
            target_visual_ids=targets,
            is_interactive_slicer=bool(data.get("is_interactive_slicer", False)),
        )



@dataclass(frozen=True)
class CrossFilterRule:
    """Inter-visual cross-filtering / cross-highlighting interaction rule."""

    rule_id: str
    source_visual_id: str
    target_visual_ids: tuple[str, ...] = ()
    behavior: CrossFilterBehavior = CrossFilterBehavior.FILTER
    bidirectional: bool = False

    def __post_init__(self) -> None:
        if not self.rule_id or not isinstance(self.rule_id, str):
            raise ValueError("rule_id must be a non-empty string")
        if not self.source_visual_id or not isinstance(self.source_visual_id, str):
            raise ValueError("source_visual_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "source_visual_id": self.source_visual_id,
            "target_visual_ids": list(self.target_visual_ids),
            "behavior": self.behavior.value
            if isinstance(self.behavior, CrossFilterBehavior)
            else str(self.behavior),
            "bidirectional": self.bidirectional,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CrossFilterRule:
        if not isinstance(data, Mapping):
            raise TypeError("CrossFilterRule must be an object")
        unknown = set(data) - _ALLOWED_CROSS_FILTER_RULE_KEYS
        if unknown:
            raise ValueError(f"unknown cross filter rule field(s): {sorted(unknown)}")
        beh_raw = data.get("behavior", CrossFilterBehavior.FILTER.value)
        behavior = CrossFilterBehavior(beh_raw) if isinstance(beh_raw, str) else beh_raw
        targets_raw = data.get("target_visual_ids", ())
        if not isinstance(targets_raw, (list, tuple)):
            raise TypeError("target_visual_ids must be a list")
        targets = tuple(str(t) for t in targets_raw)

        return cls(
            rule_id=str(data["rule_id"]),
            source_visual_id=str(data["source_visual_id"]),
            target_visual_ids=targets,
            behavior=behavior,
            bidirectional=bool(data.get("bidirectional", False)),
        )


@dataclass(frozen=True)
class DrillSpec:
    """Hierarchy drilldown, drillup, and drill-through specification."""

    drill_id: str
    target_visual_id: str
    hierarchy_levels: tuple[str, ...] = ()
    current_level_index: int = 0
    allow_drill_down: bool = True
    allow_drill_up: bool = True
    drill_through_page_id: str | None = None

    def __post_init__(self) -> None:
        if not self.drill_id or not isinstance(self.drill_id, str):
            raise ValueError("drill_id must be a non-empty string")
        if not self.target_visual_id or not isinstance(self.target_visual_id, str):
            raise ValueError("target_visual_id must be a non-empty string")
        if self.current_level_index < 0:
            raise ValueError(
                f"current_level_index must be non-negative, got {self.current_level_index}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "drill_id": self.drill_id,
            "target_visual_id": self.target_visual_id,
            "hierarchy_levels": list(self.hierarchy_levels),
            "current_level_index": self.current_level_index,
            "allow_drill_down": self.allow_drill_down,
            "allow_drill_up": self.allow_drill_up,
            "drill_through_page_id": self.drill_through_page_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DrillSpec:
        if not isinstance(data, Mapping):
            raise TypeError("DrillSpec must be an object")
        unknown = set(data) - _ALLOWED_DRILL_SPEC_KEYS
        if unknown:
            raise ValueError(f"unknown drill spec field(s): {sorted(unknown)}")
        levels_raw = data.get("hierarchy_levels", ())
        if not isinstance(levels_raw, (list, tuple)):
            raise TypeError("hierarchy_levels must be a list")
        levels = tuple(str(lvl) for lvl in levels_raw)

        return cls(
            drill_id=str(data["drill_id"]),
            target_visual_id=str(data["target_visual_id"]),
            hierarchy_levels=levels,
            current_level_index=int(data.get("current_level_index", 0)),
            allow_drill_down=bool(data.get("allow_drill_down", True)),
            allow_drill_up=bool(data.get("allow_drill_up", True)),
            drill_through_page_id=data.get("drill_through_page_id"),
        )


@dataclass(frozen=True)
class TooltipSpec:
    """Tooltip behavior specification."""

    tooltip_id: str
    source_visual_id: str
    kind: TooltipKind = TooltipKind.DEFAULT
    fields: tuple[str, ...] = ()
    target_page_id: str | None = None
    trigger: str = "HOVER"

    def __post_init__(self) -> None:
        if not self.tooltip_id or not isinstance(self.tooltip_id, str):
            raise ValueError("tooltip_id must be a non-empty string")
        if not self.source_visual_id or not isinstance(self.source_visual_id, str):
            raise ValueError("source_visual_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tooltip_id": self.tooltip_id,
            "source_visual_id": self.source_visual_id,
            "kind": self.kind.value if isinstance(self.kind, TooltipKind) else str(self.kind),
            "fields": list(self.fields),
            "target_page_id": self.target_page_id,
            "trigger": self.trigger,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TooltipSpec:
        if not isinstance(data, Mapping):
            raise TypeError("TooltipSpec must be an object")
        unknown = set(data) - _ALLOWED_TOOLTIP_SPEC_KEYS
        if unknown:
            raise ValueError(f"unknown tooltip spec field(s): {sorted(unknown)}")
        kind_raw = data.get("kind", TooltipKind.DEFAULT.value)
        kind = TooltipKind(kind_raw) if isinstance(kind_raw, str) else kind_raw

        fields_raw = data.get("fields", ())
        if not isinstance(fields_raw, (list, tuple)):
            raise TypeError("tooltip fields must be a list")
        fields = tuple(str(f) for f in fields_raw)

        return cls(
            tooltip_id=str(data["tooltip_id"]),
            source_visual_id=str(data["source_visual_id"]),
            kind=kind,
            fields=fields,
            target_page_id=data.get("target_page_id"),
            trigger=str(data.get("trigger", "HOVER")),
        )


@dataclass(frozen=True)
class NavigationAction:
    """Interactive navigation action contract."""

    action_id: str
    trigger_source_id: str
    kind: NavigationKind
    target_page_id: str | None = None
    target_url: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.action_id or not isinstance(self.action_id, str):
            raise ValueError("action_id must be a non-empty string")
        if not self.trigger_source_id or not isinstance(self.trigger_source_id, str):
            raise ValueError("trigger_source_id must be a non-empty string")
        _validate_safe_properties(self.parameters, "navigation parameters")

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "trigger_source_id": self.trigger_source_id,
            "kind": self.kind.value if isinstance(self.kind, NavigationKind) else str(self.kind),
            "target_page_id": self.target_page_id,
            "target_url": self.target_url,
            "parameters": dict(self.parameters),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> NavigationAction:
        if not isinstance(data, Mapping):
            raise TypeError("NavigationAction must be an object")
        unknown = set(data) - _ALLOWED_NAVIGATION_ACTION_KEYS
        if unknown:
            raise ValueError(f"unknown navigation action field(s): {sorted(unknown)}")
        kind_raw = data.get("kind")
        if not kind_raw:
            raise ValueError("kind is required for NavigationAction")
        kind = NavigationKind(kind_raw) if isinstance(kind_raw, str) else kind_raw

        parameters = _validate_safe_properties(
            data.get("parameters", {}), "navigation parameters"
        )

        return cls(
            action_id=str(data["action_id"]),
            trigger_source_id=str(data["trigger_source_id"]),
            kind=kind,
            target_page_id=data.get("target_page_id"),
            target_url=data.get("target_url"),
            parameters=parameters,
        )


@dataclass(frozen=True)
class PageInteractionSpec:
    """Interaction container for all rules active on a single page."""

    page_id: str
    selections: tuple[SelectionContract, ...] = ()
    filters: tuple[FilterCondition, ...] = ()
    cross_filters: tuple[CrossFilterRule, ...] = ()
    drills: tuple[DrillSpec, ...] = ()
    tooltips: tuple[TooltipSpec, ...] = ()
    navigations: tuple[NavigationAction, ...] = ()

    def __post_init__(self) -> None:
        if not self.page_id or not isinstance(self.page_id, str):
            raise ValueError("page_id must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "selections": [s.to_dict() for s in self.selections],
            "filters": [f.to_dict() for f in self.filters],
            "cross_filters": [cf.to_dict() for cf in self.cross_filters],
            "drills": [d.to_dict() for d in self.drills],
            "tooltips": [t.to_dict() for t in self.tooltips],
            "navigations": [n.to_dict() for n in self.navigations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PageInteractionSpec:
        if not isinstance(data, Mapping):
            raise TypeError("PageInteractionSpec must be an object")
        unknown = set(data) - _ALLOWED_PAGE_INTERACTION_SPEC_KEYS
        if unknown:
            raise ValueError(f"unknown page interaction field(s): {sorted(unknown)}")

        selections_raw = data.get("selections", ())
        if not isinstance(selections_raw, (list, tuple)):
            raise TypeError("page interaction selections must be a list")
        selections = tuple(
            SelectionContract.from_dict(s) if isinstance(s, Mapping) else s
            for s in selections_raw
        )

        filters_raw = data.get("filters", ())
        if not isinstance(filters_raw, (list, tuple)):
            raise TypeError("page interaction filters must be a list")
        filters = tuple(
            FilterCondition.from_dict(f) if isinstance(f, Mapping) else f
            for f in filters_raw
        )

        cross_filters_raw = data.get("cross_filters", ())
        if not isinstance(cross_filters_raw, (list, tuple)):
            raise TypeError("page interaction cross_filters must be a list")
        cross_filters = tuple(
            CrossFilterRule.from_dict(cf) if isinstance(cf, Mapping) else cf
            for cf in cross_filters_raw
        )

        drills_raw = data.get("drills", ())
        if not isinstance(drills_raw, (list, tuple)):
            raise TypeError("page interaction drills must be a list")
        drills = tuple(
            DrillSpec.from_dict(d) if isinstance(d, Mapping) else d for d in drills_raw
        )

        tooltips_raw = data.get("tooltips", ())
        if not isinstance(tooltips_raw, (list, tuple)):
            raise TypeError("page interaction tooltips must be a list")
        tooltips = tuple(
            TooltipSpec.from_dict(t) if isinstance(t, Mapping) else t
            for t in tooltips_raw
        )

        navigations_raw = data.get("navigations", ())
        if not isinstance(navigations_raw, (list, tuple)):
            raise TypeError("page interaction navigations must be a list")
        navigations = tuple(
            NavigationAction.from_dict(n) if isinstance(n, Mapping) else n
            for n in navigations_raw
        )

        return cls(
            page_id=str(data["page_id"]),
            selections=selections,
            filters=filters,
            cross_filters=cross_filters,
            drills=drills,
            tooltips=tooltips,
            navigations=navigations,
        )


@dataclass(frozen=True)
class InteractionIR:
    """Canonical versioned Interaction Intermediate Representation document."""

    schema_version: str = SCHEMA_VERSION
    doc_id: str = ""
    global_filters: tuple[FilterCondition, ...] = ()
    pages: tuple[PageInteractionSpec, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_safe_properties(self.metadata, "interaction metadata")

    def validate(self) -> None:
        """Validate schema version and page uniqueness."""
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Incompatible schema_version: expected {SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )
        _validate_safe_properties(self.metadata, "interaction metadata")
        page_ids: set[str] = set()
        for page in self.pages:
            if page.page_id in page_ids:
                raise ValueError(f"Duplicate page_id detected: {page.page_id!r}")
            page_ids.add(page.page_id)

        filter_ids: set[str] = set()
        for f in self.global_filters:
            if f.filter_id in filter_ids:
                raise ValueError(f"Duplicate global filter_id detected: {f.filter_id!r}")
            filter_ids.add(f.filter_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "doc_id": self.doc_id,
            "global_filters": [f.to_dict() for f in self.global_filters],
            "pages": [p.to_dict() for p in self.pages],
            "metadata": dict(self.metadata),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InteractionIR:
        if not isinstance(data, Mapping):
            raise TypeError("InteractionIR must be an object")
        unknown = set(data) - _ALLOWED_INTERACTION_IR_KEYS
        if unknown:
            raise ValueError(f"unknown interaction IR field(s): {sorted(unknown)}")
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"Incompatible schema_version: expected {SCHEMA_VERSION!r}, got {received!r}"
            )

        global_filters_raw = data.get("global_filters", ())
        if not isinstance(global_filters_raw, (list, tuple)):
            raise TypeError("interaction global_filters must be a list")
        global_filters = tuple(
            FilterCondition.from_dict(f) if isinstance(f, Mapping) else f
            for f in global_filters_raw
        )

        pages_raw = data.get("pages", ())
        if not isinstance(pages_raw, (list, tuple)):
            raise TypeError("interaction pages must be a list")
        pages = tuple(
            PageInteractionSpec.from_dict(p) if isinstance(p, Mapping) else p
            for p in pages_raw
        )

        metadata = _validate_safe_properties(
            data.get("metadata", {}), "interaction metadata"
        )

        doc = cls(
            schema_version=SCHEMA_VERSION,
            doc_id=str(data.get("doc_id", "")),
            global_filters=global_filters,
            pages=pages,
            metadata=metadata,
        )
        doc.validate()
        return doc

    @classmethod
    def from_json(cls, text: str) -> InteractionIR:
        return cls.from_dict(json.loads(text))
