"""Estimación de costo declarativo para operaciones de authoring custom visual.

Toda la estimación deriva exclusivamente de la cardinalidad declarada en el
contrato ``custom_visual_spec`` (``basis='declared_output_cardinality'``):

- ``estimated_query_cells = max_data_points * número de roles únicos referenciados``
- ``estimated_render_units = max_data_points * suma por capa de
  (1 + 1 si mark es line/area y show_points es true)``

No se escanean orígenes ni se inventan valores monetarios: el reporte advierte
de forma honesta que el costo real del origen (scan/conector) puede ser mayor y
depende del conector. Los presupuestos se aplican por visual; el nivel agregado
``high`` exige confirmación humana explícita (``confirm_high_cost``) en la
aprobación durable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.authoring.presentation_operations import PresentationOperation
from core.contracts.custom_visual import (
    DEFAULT_QUERY_CELL_BUDGET,
    DEFAULT_RENDER_UNIT_BUDGET,
    CustomVisualSpec,
    layer_render_units,
    referenced_roles,
    spec_from_properties,
)
from core.contracts.presentation_ir import PagePresentation, VisualIntentKind, VisualPresentation

COST_BASIS: str = "declared_output_cardinality"
"""Base declarada de la estimación; nunca un escaneo del origen."""

SOURCE_SCAN_COST_WARNING: str = (
    "Estimated from declared output cardinality only; the source scan/connector "
    "cost can be higher and depends on the connector."
)
"""Advertencia honesta incluida en cada reporte: el costo real puede ser mayor."""


@dataclass(frozen=True)
class CustomVisualCostEstimate:
    """Estimación de costo por visual custom declarado en una operación.

    Attributes:
        page_id: página destino de la operación.
        visual_id: identificador del visual.
        operation: kind de la operación (``add``/``update``).
        max_data_points: cardinalidad declarada en la spec.
        layer_count: número de capas declaradas.
        referenced_roles: roles únicos referenciados por las capas (ordenados).
        estimated_query_cells: ``max_data_points * roles únicos``.
        estimated_render_units: ``max_data_points * unidades por capa``.
        render_unit_budget: presupuesto de unidades de render aplicado.
        query_cell_budget: presupuesto de celdas de consulta aplicado.
    """

    page_id: str
    visual_id: str
    operation: str
    max_data_points: int
    layer_count: int
    referenced_roles: tuple[str, ...]
    estimated_query_cells: int
    estimated_render_units: int
    render_unit_budget: int
    query_cell_budget: int

    @property
    def level(self) -> str:
        """Nivel del visual: ``high`` si alguna estimación supera su presupuesto."""
        if (
            self.estimated_render_units > self.render_unit_budget
            or self.estimated_query_cells > self.query_cell_budget
        ):
            return "high"
        return "normal"

    @property
    def requires_user_confirmation(self) -> bool:
        return self.level == "high"

    def to_dict(self) -> dict[str, object]:
        return {
            "page_id": self.page_id,
            "visual_id": self.visual_id,
            "operation": self.operation,
            "max_data_points": self.max_data_points,
            "layer_count": self.layer_count,
            "referenced_roles": list(self.referenced_roles),
            "estimated_query_cells": self.estimated_query_cells,
            "estimated_render_units": self.estimated_render_units,
            "render_unit_budget": self.render_unit_budget,
            "query_cell_budget": self.query_cell_budget,
            "level": self.level,
            "requires_user_confirmation": self.requires_user_confirmation,
        }


@dataclass(frozen=True)
class CustomVisualCostReport:
    """Reporte agregado serializable del costo de las operaciones custom.

    Attributes:
        estimates: estimaciones por visual (sólo add/update custom con spec).
        render_unit_budget: presupuesto por visual de unidades de render.
        query_cell_budget: presupuesto por visual de celdas de consulta.
    """

    estimates: tuple[CustomVisualCostEstimate, ...]
    render_unit_budget: int = DEFAULT_RENDER_UNIT_BUDGET
    query_cell_budget: int = DEFAULT_QUERY_CELL_BUDGET

    @property
    def level(self) -> str:
        """Nivel agregado: ``high`` si algún visual requiere confirmación."""
        if any(estimate.level == "high" for estimate in self.estimates):
            return "high"
        return "normal"

    @property
    def requires_user_confirmation(self) -> bool:
        return self.level == "high"

    def to_dict(self) -> dict[str, object]:
        return {
            "basis": COST_BASIS,
            "level": self.level,
            "requires_user_confirmation": self.requires_user_confirmation,
            "budgets": {
                "render_units": self.render_unit_budget,
                "query_cells": self.query_cell_budget,
            },
            "totals": {
                "custom_visual_count": len(self.estimates),
                "estimated_query_cells": sum(
                    estimate.estimated_query_cells for estimate in self.estimates
                ),
                "estimated_render_units": sum(
                    estimate.estimated_render_units for estimate in self.estimates
                ),
            },
            "visuals": [estimate.to_dict() for estimate in self.estimates],
            "warnings": [SOURCE_SCAN_COST_WARNING] if self.estimates else [],
        }


def estimate_custom_visual(
    spec: CustomVisualSpec,
    *,
    page_id: str,
    visual_id: str,
    operation: str,
    render_unit_budget: int = DEFAULT_RENDER_UNIT_BUDGET,
    query_cell_budget: int = DEFAULT_QUERY_CELL_BUDGET,
) -> CustomVisualCostEstimate:
    """Estima el costo de un visual custom desde su spec declarada.

    Los roles se cuentan únicos entre capas y el sobrecosto de ``show_points``
    sólo aplica a marcas ``line``/``area`` (ver :func:`layer_render_units`).
    """
    roles = referenced_roles(spec)
    return CustomVisualCostEstimate(
        page_id=page_id,
        visual_id=visual_id,
        operation=operation,
        max_data_points=spec.max_data_points,
        layer_count=len(spec.layers),
        referenced_roles=tuple(sorted(roles)),
        estimated_query_cells=spec.max_data_points * len(roles),
        estimated_render_units=spec.max_data_points
        * sum(layer_render_units(layer) for layer in spec.layers),
        render_unit_budget=render_unit_budget,
        query_cell_budget=query_cell_budget,
    )


def estimate_presentation_operations(
    operations: Iterable[PresentationOperation],
    *,
    render_unit_budget: int = DEFAULT_RENDER_UNIT_BUDGET,
    query_cell_budget: int = DEFAULT_QUERY_CELL_BUDGET,
) -> CustomVisualCostReport:
    """Estima el costo de las ``PresentationOperation`` add/update de customs.

    Considera sólo operaciones ``add``/``update`` sobre visuales con intent
    ``custom_visual`` y ``custom_visual_spec`` presente. Si una spec presente no
    parsea, falla cerrado: la validación de coherencia previa debe haberla
    rechazado antes de llegar aquí.
    """
    estimates: list[CustomVisualCostEstimate] = []
    for operation in operations:
        if operation.kind == "remove":
            continue
        payload = operation.payload
        candidates: tuple[tuple[str, VisualPresentation], ...]
        if operation.target == "visual" and isinstance(payload, VisualPresentation):
            candidates = ((operation.page_id or "", payload),)
        elif operation.target == "page" and isinstance(payload, PagePresentation):
            candidates = tuple((payload.page_id, visual) for visual in payload.visuals)
        else:
            continue
        for page_id, visual in candidates:
            if visual.intent != VisualIntentKind.CUSTOM_VISUAL:
                continue
            spec = spec_from_properties(visual.properties)
            if spec is None:
                continue
            estimates.append(
                estimate_custom_visual(
                    spec,
                    page_id=page_id,
                    visual_id=visual.visual_id,
                    operation=operation.kind,
                    render_unit_budget=render_unit_budget,
                    query_cell_budget=query_cell_budget,
                )
            )
    return CustomVisualCostReport(
        tuple(estimates),
        render_unit_budget=render_unit_budget,
        query_cell_budget=query_cell_budget,
    )


__all__ = [
    "COST_BASIS",
    "SOURCE_SCAN_COST_WARNING",
    "CustomVisualCostEstimate",
    "CustomVisualCostReport",
    "estimate_custom_visual",
    "estimate_presentation_operations",
]
