"""Contrato declarativo cerrado para visuales personalizados (custom visuals).

La especificación viaja serializada en
``VisualPresentation.properties['custom_visual_spec']`` sin bump de versión del
PresentationIR: es un contrato cerrado y estricto que sólo admite marcas neutras,
roles canónicos de bindings del PresentationIR y opciones de presentación.
Sin código, HTML, expresiones, URLs, SQL ni claves extra; cualquier desviación
falla cerrado en :meth:`CustomVisualSpec.from_dict`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.contracts.presentation_ir import FieldRole

CUSTOM_VISUAL_SPEC_SCHEMA_VERSION: str = "1.0.0"
"""Versión única admitida del contrato de custom visual."""

CUSTOM_VISUAL_SPEC_PROPERTY: str = "custom_visual_spec"
"""Clave de ``VisualPresentation.properties`` que transporta la especificación."""

MIN_DATA_POINTS: int = 1
MAX_DATA_POINTS: int = 50_000
"""Límite de puntos de datos declarados por custom visual."""

MIN_LAYERS: int = 1
MAX_LAYERS: int = 6
"""Número de capas declarables por custom visual."""

DEFAULT_RENDER_UNIT_BUDGET: int = 5_000
"""Presupuesto por defecto (por visual) de unidades de render estimadas."""

DEFAULT_QUERY_CELL_BUDGET: int = 20_000
"""Presupuesto por defecto (por visual) de celdas de consulta estimadas."""

COST_MARKS_WITH_POINT_OVERLAY: frozenset[str] = frozenset({"line", "area"})
"""Marcas donde ``show_points`` activa una capa adicional de render."""


class CustomVisualMark(StrEnum):
    """Marca neutra de una capa declarativa."""

    BAR = "bar"
    LINE = "line"
    AREA = "area"
    POINT = "point"


_HEX_COLOR: re.Pattern[str] = re.compile(r"^#[0-9A-Fa-f]{6}$")

_LAYER_REQUIRED_KEYS: frozenset[str] = frozenset({"mark", "x_role", "y_role"})
_LAYER_ALLOWED_KEYS: frozenset[str] = frozenset(
    {"mark", "x_role", "y_role", "color", "show_points"}
)
_SPEC_ALLOWED_KEYS: frozenset[str] = frozenset({"schema_version", "max_data_points", "layers"})

_MARK_VALUES: frozenset[str] = frozenset(mark.value for mark in CustomVisualMark)
_ROLE_VALUES: frozenset[str] = frozenset(role.value for role in FieldRole)


def _parse_role(raw: object, key: str) -> FieldRole:
    """Convierte un rol crudo a :class:`FieldRole` validando el conjunto canónico."""
    if not isinstance(raw, str):
        raise TypeError(f"custom visual layer {key} must be a string")
    if raw not in _ROLE_VALUES:
        raise ValueError(f"custom visual layer {key} {raw!r} is not a canonical binding role")
    return FieldRole(raw)


@dataclass(frozen=True)
class CustomVisualLayer:
    """Capa declarativa cerrada de un custom visual.

    Attributes:
        mark: marca neutra de la capa.
        x_role: rol canónico del binding usado como eje x de la capa.
        y_role: rol canónico del binding usado como eje y de la capa.
        color: color ``#RRGGBB`` opcional de la capa.
        show_points: opcional; sólo tiene efecto de costo en marcas line/area.
    """

    mark: CustomVisualMark
    x_role: FieldRole
    y_role: FieldRole
    color: str | None = None
    show_points: bool | None = None

    def __post_init__(self) -> None:
        mark = CustomVisualMark(self.mark) if isinstance(self.mark, str) else self.mark
        if not isinstance(mark, CustomVisualMark):
            raise TypeError("custom visual layer mark must be a CustomVisualMark")
        object.__setattr__(self, "mark", mark)
        object.__setattr__(self, "x_role", _parse_role(self.x_role, "x_role"))
        object.__setattr__(self, "y_role", _parse_role(self.y_role, "y_role"))
        if self.color is not None:
            if not isinstance(self.color, str) or not _HEX_COLOR.fullmatch(self.color):
                raise ValueError(
                    f"custom visual layer color must be '#RRGGBB', got {self.color!r}"
                )
        if self.show_points is not None and not isinstance(self.show_points, bool):
            raise TypeError("custom visual layer show_points must be a bool")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "mark": self.mark.value,
            "x_role": self.x_role.value,
            "y_role": self.y_role.value,
        }
        if self.color is not None:
            data["color"] = self.color
        if self.show_points is not None:
            data["show_points"] = self.show_points
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CustomVisualLayer:
        """Parsea una capa con claves exactas y tipos estrictos (fail-closed)."""
        if not isinstance(data, Mapping):
            raise TypeError("custom visual layer must be an object")
        unknown = set(data) - _LAYER_ALLOWED_KEYS
        if unknown:
            raise ValueError(f"custom visual layer contains unknown field(s): {sorted(unknown)}")
        missing = _LAYER_REQUIRED_KEYS - set(data)
        if missing:
            raise ValueError(f"custom visual layer is missing required field(s): {sorted(missing)}")
        raw_mark = data["mark"]
        if not isinstance(raw_mark, str):
            raise TypeError("custom visual layer mark must be a string")
        if raw_mark not in _MARK_VALUES:
            raise ValueError(
                f"custom visual layer mark must be one of {sorted(_MARK_VALUES)}, "
                f"got {raw_mark!r}"
            )
        raw_color = data.get("color")
        if raw_color is not None:
            if not isinstance(raw_color, str) or not _HEX_COLOR.fullmatch(raw_color):
                raise ValueError(
                    f"custom visual layer color must be '#RRGGBB', got {raw_color!r}"
                )
        raw_show_points = data.get("show_points")
        if raw_show_points is not None and not isinstance(raw_show_points, bool):
            raise TypeError("custom visual layer show_points must be a bool")
        return cls(
            mark=CustomVisualMark(raw_mark),
            x_role=_parse_role(data["x_role"], "x_role"),
            y_role=_parse_role(data["y_role"], "y_role"),
            color=raw_color,
            show_points=raw_show_points,
        )


@dataclass(frozen=True)
class CustomVisualSpec:
    """Especificación declarativa completa de un custom visual.

    Attributes:
        max_data_points: cardinalidad declarada de salida (1..50000).
        layers: 1..6 capas declarativas cerradas.
        schema_version: debe ser exactamente ``"1.0.0"``.
    """

    max_data_points: int
    layers: tuple[CustomVisualLayer, ...]
    schema_version: str = CUSTOM_VISUAL_SPEC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != CUSTOM_VISUAL_SPEC_SCHEMA_VERSION:
            raise ValueError(
                "custom_visual_spec schema_version must be "
                f"{CUSTOM_VISUAL_SPEC_SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if not isinstance(self.max_data_points, int) or isinstance(self.max_data_points, bool):
            raise TypeError("custom_visual_spec.max_data_points must be an int")
        if not MIN_DATA_POINTS <= self.max_data_points <= MAX_DATA_POINTS:
            raise ValueError(
                f"custom_visual_spec.max_data_points must be in "
                f"[{MIN_DATA_POINTS}, {MAX_DATA_POINTS}], got {self.max_data_points}"
            )
        layers = tuple(self.layers)
        object.__setattr__(self, "layers", layers)
        if not MIN_LAYERS <= len(layers) <= MAX_LAYERS:
            raise ValueError(
                f"custom_visual_spec.layers must contain between {MIN_LAYERS} and "
                f"{MAX_LAYERS} layers, got {len(layers)}"
            )
        for layer in layers:
            if not isinstance(layer, CustomVisualLayer):
                raise TypeError(
                    "custom_visual_spec.layers must contain CustomVisualLayer instances"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "max_data_points": self.max_data_points,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CustomVisualSpec:
        """Parsea la spec con claves exactas y tipos estrictos (fail-closed)."""
        if not isinstance(data, Mapping):
            raise TypeError("custom_visual_spec must be an object")
        unknown = set(data) - _SPEC_ALLOWED_KEYS
        if unknown:
            raise ValueError(f"custom_visual_spec contains unknown field(s): {sorted(unknown)}")
        missing = _SPEC_ALLOWED_KEYS - set(data)
        if missing:
            raise ValueError(
                f"custom_visual_spec is missing required field(s): {sorted(missing)}"
            )
        version = data["schema_version"]
        if version != CUSTOM_VISUAL_SPEC_SCHEMA_VERSION:
            raise ValueError(
                f"custom_visual_spec schema_version must be "
                f"{CUSTOM_VISUAL_SPEC_SCHEMA_VERSION!r}, got {version!r}"
            )
        points = data["max_data_points"]
        if not isinstance(points, int) or isinstance(points, bool):
            raise TypeError("custom_visual_spec.max_data_points must be an int")
        if not MIN_DATA_POINTS <= points <= MAX_DATA_POINTS:
            raise ValueError(
                f"custom_visual_spec.max_data_points must be in "
                f"[{MIN_DATA_POINTS}, {MAX_DATA_POINTS}], got {points}"
            )
        layers_raw = data["layers"]
        if not isinstance(layers_raw, list):
            raise TypeError("custom_visual_spec.layers must be a list")
        if not MIN_LAYERS <= len(layers_raw) <= MAX_LAYERS:
            raise ValueError(
                f"custom_visual_spec.layers must contain between {MIN_LAYERS} and "
                f"{MAX_LAYERS} layers, got {len(layers_raw)}"
            )
        layers = tuple(CustomVisualLayer.from_dict(layer) for layer in layers_raw)
        return cls(max_data_points=points, layers=layers, schema_version=version)


def referenced_roles(spec: CustomVisualSpec) -> frozenset[str]:
    """Roles canónicos únicos referenciados por las capas de la spec."""
    return frozenset(
        role for layer in spec.layers for role in (layer.x_role.value, layer.y_role.value)
    )


def layer_render_units(layer: CustomVisualLayer) -> int:
    """Unidades de render de una capa: 1, +1 sólo en line/area con ``show_points``.

    ``show_points`` en marcas ``bar``/``point`` no tiene efecto de costo.
    """
    has_overlay = (
        layer.mark.value in COST_MARKS_WITH_POINT_OVERLAY and layer.show_points is True
    )
    return 2 if has_overlay else 1


def spec_from_properties(properties: Mapping[str, Any]) -> CustomVisualSpec | None:
    """Extrae la spec custom de las propiedades del visual; ``None`` si no existe.

    Si la clave existe pero el valor no parsea, propaga el error del parser
    (fail-closed).
    """
    raw = properties.get(CUSTOM_VISUAL_SPEC_PROPERTY)
    if raw is None:
        return None
    return CustomVisualSpec.from_dict(raw)


__all__ = [
    "COST_MARKS_WITH_POINT_OVERLAY",
    "CUSTOM_VISUAL_SPEC_PROPERTY",
    "CUSTOM_VISUAL_SPEC_SCHEMA_VERSION",
    "CustomVisualLayer",
    "CustomVisualMark",
    "CustomVisualSpec",
    "DEFAULT_QUERY_CELL_BUDGET",
    "DEFAULT_RENDER_UNIT_BUDGET",
    "MAX_DATA_POINTS",
    "MAX_LAYERS",
    "MIN_DATA_POINTS",
    "MIN_LAYERS",
    "layer_render_units",
    "referenced_roles",
    "spec_from_properties",
]
