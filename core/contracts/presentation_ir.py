"""Presentation Intermediate Representation (Presentation IR) contract.

Defines a neutral, target-agnostic presentation contract for report pages,
visual intents, data bindings, visual geometry, theme/styling, and accessibility.

This layer sits between source AST models (e.g. Tableau TWB/TWBX, Power BI PBIP)
and target renders or native runtimes (e.g., DataVIZ web engine, PBIR generators).

Schema Versioning Policy (SemVer):
    * MAJOR: Breaking changes (field removals, type changes, semantic shifts).
    * MINOR: Backward-compatible extensions (optional fields with defaults).
    * PATCH: Internal fixes preserving serialized format.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

SCHEMA_VERSION: str = "1.0.0"
"""Current schema version for Presentation IR."""


class VisualIntentKind(StrEnum):
    """Neutral visual intent categories.

    Strictly target-agnostic and frontend-agnostic. PBIR visualType names
    (e.g., 'clusteredBarChart') and React component names (e.g., 'RechartsBar')
    are strictly prohibited as enum keys or values.
    """

    BAR = "bar"
    COLUMN = "column"
    LINE = "line"
    AREA = "area"
    SCATTER = "scatter"
    PIE = "pie"
    DONUT = "donut"
    TREEMAP = "treemap"
    HEATMAP = "heatmap"
    MAP = "map"
    KPI_CARD = "kpi_card"
    TABLE = "table"
    PIVOT_MATRIX = "pivot_matrix"
    WATERFALL = "waterfall"
    GAUGE = "gauge"
    SLICER_FILTER = "slicer_filter"
    CONTAINER = "container"
    TEXT_BOX = "text_box"
    IMAGE = "image"
    CUSTOM_VISUAL = "custom_visual"


class FieldRole(StrEnum):
    """Semantic field binding roles for visual encoding."""

    X_AXIS = "x_axis"
    Y_AXIS = "y_axis"
    COLOR = "color"
    SIZE = "size"
    LABEL = "label"
    TOOLTIP = "tooltip"
    ROW = "row"
    COLUMN = "column"
    VALUE = "value"
    SERIES = "series"
    FILTER_TARGET = "filter_target"
    TARGET_METRIC = "target_metric"
    COMPARISON_METRIC = "comparison_metric"


class ContainerKind(StrEnum):
    """Layout container positioning mode."""

    FLOATING = "floating"
    TILED = "tiled"
    FIXED = "fixed"
    RESPONSIVE = "responsive"


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

_ALLOWED_NESTED_SCALAR_TYPES = (bool, int, float, str)


def _scan_property_value(value: Any, context: str) -> None:
    """Fail closed when a nested property value carries a secret or SQL carrier.

    Structured metadata (nested dicts/lists) is allowed: the scalar leaves are
    what get scanned. Any match raises instead of redacting, so carriers can
    never reach a consumer through the metadata/properties envelope.
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

_ALLOWED_GEOMETRY_KEYS: frozenset[str] = frozenset(
    {"x", "y", "width", "height", "z_index", "layout_mode", "padding"}
)
_ALLOWED_ACCESSIBILITY_KEYS: frozenset[str] = frozenset(
    {"alt_text", "aria_label", "tab_index", "screen_reader_summary", "high_contrast_aware"}
)
_ALLOWED_THEME_KEYS: frozenset[str] = frozenset(
    {
        "font_family",
        "font_size_pt",
        "primary_color",
        "background_color",
        "card_background_color",
        "text_color",
        "color_palette",
        "border_color",
        "border_width_px",
        "border_radius_px",
        "shadow_enabled",
    }
)
_ALLOWED_DATA_BINDING_KEYS: frozenset[str] = frozenset(
    {
        "binding_id",
        "field_name",
        "role",
        "aggregation",
        "format_string",
        "display_name",
        "sort_order",
    }
)
_ALLOWED_VISUAL_KEYS: frozenset[str] = frozenset(
    {
        "visual_id",
        "intent",
        "title",
        "subtitle",
        "geometry",
        "bindings",
        "style",
        "accessibility",
        "is_visible",
        "properties",
    }
)
_ALLOWED_PAGE_PRESENTATION_KEYS: frozenset[str] = frozenset(
    {
        "page_id",
        "name",
        "display_name",
        "is_hidden",
        "width",
        "height",
        "theme",
        "accessibility",
        "visuals",
        "properties",
    }
)
_ALLOWED_PRESENTATION_IR_KEYS: frozenset[str] = frozenset(
    {"schema_version", "doc_id", "title", "theme", "pages", "metadata"}
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
class VisualGeometry:
    """Bounding box geometry and layout properties in logical canvas pixels.

    Attributes:
        x: X-coordinate of top-left corner.
        y: Y-coordinate of top-left corner.
        width: Visual width (must be >= 0).
        height: Visual height (must be >= 0).
        z_index: Stacking order (must be >= 0).
        layout_mode: Container positioning mode.
        padding: Logical padding (top, right, bottom, left).
    """

    x: float = 0.0
    y: float = 0.0
    width: float = 400.0
    height: float = 300.0
    z_index: int = 0
    layout_mode: ContainerKind = ContainerKind.TILED
    padding: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        if self.width < 0.0:
            raise ValueError(f"width must be non-negative, got {self.width}")
        if self.height < 0.0:
            raise ValueError(f"height must be non-negative, got {self.height}")
        if self.z_index < 0:
            raise ValueError(f"z_index must be non-negative, got {self.z_index}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "z_index": self.z_index,
            "layout_mode": self.layout_mode.value
            if isinstance(self.layout_mode, ContainerKind)
            else str(self.layout_mode),
            "padding": list(self.padding),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VisualGeometry:
        if not isinstance(data, Mapping):
            raise TypeError("VisualGeometry must be an object")
        unknown = set(data) - _ALLOWED_GEOMETRY_KEYS
        if unknown:
            raise ValueError(f"unknown geometry field(s): {sorted(unknown)}")
        mode_raw = data.get("layout_mode", ContainerKind.TILED.value)
        layout_mode = ContainerKind(mode_raw) if isinstance(mode_raw, str) else mode_raw
        padding_raw = data.get("padding", (0.0, 0.0, 0.0, 0.0))
        padding = (
            tuple(float(p) for p in padding_raw)
            if isinstance(padding_raw, (list, tuple))
            else (0.0, 0.0, 0.0, 0.0)
        )
        return cls(
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            width=float(data.get("width", 400.0)),
            height=float(data.get("height", 300.0)),
            z_index=int(data.get("z_index", 0)),
            layout_mode=layout_mode,
            padding=padding,
        )



@dataclass(frozen=True)
class AccessibilityConfig:
    """Accessibility and screen reader metadata."""

    alt_text: str = ""
    aria_label: str = ""
    tab_index: int = 0
    screen_reader_summary: str = ""
    high_contrast_aware: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AccessibilityConfig:
        if not isinstance(data, Mapping):
            raise TypeError("AccessibilityConfig must be an object")
        unknown = set(data) - _ALLOWED_ACCESSIBILITY_KEYS
        if unknown:
            raise ValueError(f"unknown accessibility field(s): {sorted(unknown)}")
        return cls(
            alt_text=str(data.get("alt_text", "")),
            aria_label=str(data.get("aria_label", "")),
            tab_index=int(data.get("tab_index", 0)),
            screen_reader_summary=str(data.get("screen_reader_summary", "")),
            high_contrast_aware=bool(data.get("high_contrast_aware", True)),
        )


@dataclass(frozen=True)
class ThemeConfig:
    """Visual styling, color palette, and theme tokens."""

    font_family: str = "Segoe UI, sans-serif"
    font_size_pt: float = 10.0
    primary_color: str = "#111827"
    background_color: str = "#FFFFFF"
    card_background_color: str = "#FFFFFF"
    text_color: str = "#1F2937"
    color_palette: tuple[str, ...] = ("#2563EB", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6")
    border_color: str = "#E5E7EB"
    border_width_px: float = 0.0
    border_radius_px: float = 0.0
    shadow_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["color_palette"] = list(self.color_palette)
        return res

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ThemeConfig:
        if not isinstance(data, Mapping):
            raise TypeError("ThemeConfig must be an object")
        unknown = set(data) - _ALLOWED_THEME_KEYS
        if unknown:
            raise ValueError(f"unknown theme field(s): {sorted(unknown)}")
        palette_raw = data.get(
            "color_palette", ("#2563EB", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6")
        )
        palette = (
            tuple(str(c) for c in palette_raw) if isinstance(palette_raw, (list, tuple)) else ()
        )
        return cls(
            font_family=str(data.get("font_family", "Segoe UI, sans-serif")),
            font_size_pt=float(data.get("font_size_pt", 10.0)),
            primary_color=str(data.get("primary_color", "#111827")),
            background_color=str(data.get("background_color", "#FFFFFF")),
            card_background_color=str(data.get("card_background_color", "#FFFFFF")),
            text_color=str(data.get("text_color", "#1F2937")),
            color_palette=palette,
            border_color=str(data.get("border_color", "#E5E7EB")),
            border_width_px=float(data.get("border_width_px", 0.0)),
            border_radius_px=float(data.get("border_radius_px", 0.0)),
            shadow_enabled=bool(data.get("shadow_enabled", False)),
        )


@dataclass(frozen=True)
class DataBinding:
    """Field binding configuring how data channels map to visual encodings."""

    binding_id: str
    field_name: str
    role: FieldRole
    aggregation: str | None = None
    format_string: str | None = None
    display_name: str | None = None
    sort_order: str | None = None

    def __post_init__(self) -> None:
        if not self.binding_id or not isinstance(self.binding_id, str):
            raise ValueError("binding_id must be a non-empty string")
        if not self.field_name or not isinstance(self.field_name, str):
            raise ValueError("field_name must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "field_name": self.field_name,
            "role": self.role.value if isinstance(self.role, FieldRole) else str(self.role),
            "aggregation": self.aggregation,
            "format_string": self.format_string,
            "display_name": self.display_name,
            "sort_order": self.sort_order,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DataBinding:
        if not isinstance(data, Mapping):
            raise TypeError("DataBinding must be an object")
        unknown = set(data) - _ALLOWED_DATA_BINDING_KEYS
        if unknown:
            raise ValueError(f"unknown data binding field(s): {sorted(unknown)}")
        role_raw = data.get("role")
        if not role_raw:
            raise ValueError("role is required for DataBinding")
        role = FieldRole(role_raw) if isinstance(role_raw, str) else role_raw
        return cls(
            binding_id=str(data["binding_id"]),
            field_name=str(data["field_name"]),
            role=role,
            aggregation=data.get("aggregation"),
            format_string=data.get("format_string"),
            display_name=data.get("display_name"),
            sort_order=data.get("sort_order"),
        )



@dataclass(frozen=True)
class VisualPresentation:
    """Presentation model for an individual visual element within a report page."""

    visual_id: str
    intent: VisualIntentKind
    title: str = ""
    subtitle: str = ""
    geometry: VisualGeometry = field(default_factory=VisualGeometry)
    bindings: tuple[DataBinding, ...] = ()
    style: ThemeConfig = field(default_factory=ThemeConfig)
    accessibility: AccessibilityConfig = field(default_factory=AccessibilityConfig)
    is_visible: bool = True
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.visual_id or not isinstance(self.visual_id, str):
            raise ValueError("visual_id must be a non-empty string")
        _validate_safe_properties(self.properties, "visual")

    def to_dict(self) -> dict[str, Any]:
        return {
            "visual_id": self.visual_id,
            "intent": self.intent.value
            if isinstance(self.intent, VisualIntentKind)
            else str(self.intent),
            "title": self.title,
            "subtitle": self.subtitle,
            "geometry": self.geometry.to_dict(),
            "bindings": [b.to_dict() for b in self.bindings],
            "style": self.style.to_dict(),
            "accessibility": self.accessibility.to_dict(),
            "is_visible": self.is_visible,
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> VisualPresentation:
        if not isinstance(data, Mapping):
            raise TypeError("VisualPresentation must be an object")
        unknown = set(data) - _ALLOWED_VISUAL_KEYS
        if unknown:
            raise ValueError(f"unknown visual field(s): {sorted(unknown)}")
        intent_raw = data.get("intent")
        if not intent_raw:
            raise ValueError("intent is required for VisualPresentation")
        intent = VisualIntentKind(intent_raw) if isinstance(intent_raw, str) else intent_raw

        bindings_raw = data.get("bindings", ())
        if not isinstance(bindings_raw, (list, tuple)):
            raise TypeError("visual bindings must be a list")
        bindings = tuple(
            DataBinding.from_dict(b) if isinstance(b, Mapping) else b for b in bindings_raw
        )

        geometry_raw = data.get("geometry")
        if geometry_raw is not None and not isinstance(geometry_raw, Mapping):
            raise TypeError("visual geometry must be an object or null")
        geometry = (
            VisualGeometry.from_dict(geometry_raw)
            if isinstance(geometry_raw, Mapping)
            else VisualGeometry()
        )

        style_raw = data.get("style")
        if style_raw is not None and not isinstance(style_raw, Mapping):
            raise TypeError("visual style must be an object or null")
        style = (
            ThemeConfig.from_dict(style_raw) if isinstance(style_raw, Mapping) else ThemeConfig()
        )

        acc_raw = data.get("accessibility")
        if acc_raw is not None and not isinstance(acc_raw, Mapping):
            raise TypeError("visual accessibility must be an object or null")
        acc = (
            AccessibilityConfig.from_dict(acc_raw)
            if isinstance(acc_raw, Mapping)
            else AccessibilityConfig()
        )

        properties = _validate_safe_properties(data.get("properties", {}), "visual")

        return cls(
            visual_id=str(data["visual_id"]),
            intent=intent,
            title=str(data.get("title", "")),
            subtitle=str(data.get("subtitle", "")),
            geometry=geometry,
            bindings=bindings,
            style=style,
            accessibility=acc,
            is_visible=bool(data.get("is_visible", True)),
            properties=properties,
        )


@dataclass(frozen=True)
class PagePresentation:
    """Presentation model for a report canvas page."""

    page_id: str
    name: str
    display_name: str = ""
    is_hidden: bool = False
    width: float = 1280.0
    height: float = 720.0
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    accessibility: AccessibilityConfig = field(default_factory=AccessibilityConfig)
    visuals: tuple[VisualPresentation, ...] = ()
    properties: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.page_id or not isinstance(self.page_id, str):
            raise ValueError("page_id must be a non-empty string")
        if not self.name or not isinstance(self.name, str):
            raise ValueError("name must be a non-empty string")
        if self.width <= 0.0:
            raise ValueError(f"width must be positive, got {self.width}")
        if self.height <= 0.0:
            raise ValueError(f"height must be positive, got {self.height}")
        _validate_safe_properties(self.properties, "page")

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "name": self.name,
            "display_name": self.display_name,
            "is_hidden": self.is_hidden,
            "width": self.width,
            "height": self.height,
            "theme": self.theme.to_dict(),
            "accessibility": self.accessibility.to_dict(),
            "visuals": [v.to_dict() for v in self.visuals],
            "properties": dict(self.properties),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PagePresentation:
        if not isinstance(data, Mapping):
            raise TypeError("PagePresentation must be an object")
        unknown = set(data) - _ALLOWED_PAGE_PRESENTATION_KEYS
        if unknown:
            raise ValueError(f"unknown page field(s): {sorted(unknown)}")
        visuals_raw = data.get("visuals", ())
        if not isinstance(visuals_raw, (list, tuple)):
            raise TypeError("page visuals must be a list")
        visuals = tuple(
            VisualPresentation.from_dict(v) if isinstance(v, Mapping) else v for v in visuals_raw
        )

        theme_raw = data.get("theme")
        if theme_raw is not None and not isinstance(theme_raw, Mapping):
            raise TypeError("page theme must be an object or null")
        theme = (
            ThemeConfig.from_dict(theme_raw) if isinstance(theme_raw, Mapping) else ThemeConfig()
        )

        acc_raw = data.get("accessibility")
        if acc_raw is not None and not isinstance(acc_raw, Mapping):
            raise TypeError("page accessibility must be an object or null")
        acc = (
            AccessibilityConfig.from_dict(acc_raw)
            if isinstance(acc_raw, Mapping)
            else AccessibilityConfig()
        )

        properties = _validate_safe_properties(data.get("properties", {}), "page")

        return cls(
            page_id=str(data["page_id"]),
            name=str(data["name"]),
            display_name=str(data.get("display_name", "")),
            is_hidden=bool(data.get("is_hidden", False)),
            width=float(data.get("width", 1280.0)),
            height=float(data.get("height", 720.0)),
            theme=theme,
            accessibility=acc,
            visuals=visuals,
            properties=properties,
        )


@dataclass(frozen=True)
class PresentationIR:
    """Canonical versioned Presentation Intermediate Representation document."""

    schema_version: str = SCHEMA_VERSION
    doc_id: str = ""
    title: str = ""
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    pages: tuple[PagePresentation, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_safe_properties(self.metadata, "presentation metadata")

    def validate(self) -> None:
        """Validate integrity and uniqueness constraints across pages and visuals."""
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"Incompatible schema_version: expected {SCHEMA_VERSION!r}, "
                f"got {self.schema_version!r}"
            )
        _validate_safe_properties(self.metadata, "presentation metadata")
        page_ids: set[str] = set()
        for page in self.pages:
            if page.page_id in page_ids:
                raise ValueError(f"Duplicate page_id detected: {page.page_id!r}")
            page_ids.add(page.page_id)

            visual_ids: set[str] = set()
            for visual in page.visuals:
                if visual.visual_id in visual_ids:
                    raise ValueError(
                        f"Duplicate visual_id {visual.visual_id!r} in page {page.page_id!r}"
                    )
                visual_ids.add(visual.visual_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "doc_id": self.doc_id,
            "title": self.title,
            "theme": self.theme.to_dict(),
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
    def from_dict(cls, data: Mapping[str, Any]) -> PresentationIR:
        if not isinstance(data, Mapping):
            raise TypeError("PresentationIR must be an object")
        unknown = set(data) - _ALLOWED_PRESENTATION_IR_KEYS
        if unknown:
            raise ValueError(f"unknown presentation IR field(s): {sorted(unknown)}")
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"Incompatible schema_version: expected {SCHEMA_VERSION!r}, got {received!r}"
            )

        pages_raw = data.get("pages", ())
        if not isinstance(pages_raw, (list, tuple)):
            raise TypeError("presentation pages must be a list")
        pages = tuple(
            PagePresentation.from_dict(p) if isinstance(p, Mapping) else p for p in pages_raw
        )

        theme_raw = data.get("theme")
        if theme_raw is not None and not isinstance(theme_raw, Mapping):
            raise TypeError("presentation theme must be an object or null")
        theme = (
            ThemeConfig.from_dict(theme_raw) if isinstance(theme_raw, Mapping) else ThemeConfig()
        )

        metadata = _validate_safe_properties(data.get("metadata", {}), "presentation metadata")

        doc = cls(
            schema_version=SCHEMA_VERSION,
            doc_id=str(data.get("doc_id", "")),
            title=str(data.get("title", "")),
            theme=theme,
            pages=pages,
            metadata=metadata,
        )
        doc.validate()
        return doc

    @classmethod
    def from_json(cls, text: str) -> PresentationIR:
        return cls.from_dict(json.loads(text))
