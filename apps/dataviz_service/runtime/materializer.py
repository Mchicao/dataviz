"""RenderPlan + datasets neutrales -> resultados inmutables por visual."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from apps.dataviz_service.runtime.errors import (
    RuntimeMaterializationError,
    SemanticRLSUnavailableError,
)
from apps.dataviz_service.runtime.executor import QueryExecutor
from core.compilers.render_plan import RenderPlan, VisualSpec
from core.contracts.dataset import Dataset
from core.contracts.query_ast import QuerySpec
from core.contracts.semantic_ir import SemanticModel


def materialize_runtime_results(
    plan: RenderPlan,
    model: SemanticModel,
    datasets: Mapping[str, Dataset],
    *,
    parameters: Mapping[str, object] | None = None,
    query_runner: Callable[[QuerySpec], Dataset] | None = None,
) -> dict[str, Any]:
    """Ejecuta cada consulta y conserva resultados aislados por visual."""
    if model.rls_intents and query_runner is None:
        raise SemanticRLSUnavailableError("RLS materialization requires an authorized query runner.")
    executor = QueryExecutor(model, parameters)
    visual_results: dict[str, dict[str, object]] = {}
    forecast_models: dict[str, dict[str, object]] = {}
    for visual in plan.visuals:
        visual_results[visual.name], model_metadata = _materialize_visual(
            visual, executor, datasets, query_runner
        )
        if model_metadata is not None:
            forecast_models[visual.name] = model_metadata
    return {
        "schema_version": "2.0.0",
        "visuals": visual_results,
        "forecast_models": forecast_models,
    }


def _materialize_visual(
    visual: VisualSpec,
    executor: QueryExecutor,
    datasets: Mapping[str, Dataset],
    query_runner: Callable[[QuerySpec], Dataset] | None = None,
) -> tuple[dict[str, object], dict[str, object] | None]:
    resolved: dict[str, object] = {}
    query_result: Dataset | None = None
    if visual.query is not None:
        if query_runner is not None:
            query_result = query_runner(visual.query)
        else:
            dataset = datasets.get(visual.query.from_datasource)
            if dataset is None:
                raise RuntimeMaterializationError(
                    f"Datasource {visual.query.from_datasource!r} is not materialized"
                )
            query_result = executor.execute(visual.query, dataset)
    forecast_metadata: dict[str, object] | None = None
    if visual.forecast is not None:
        from core.forecast import execute_forecast

        if query_result is None:
            raise RuntimeMaterializationError(
                f"Visual {visual.name!r} has a forecast without an executable query"
            )
        query_result, forecast_metadata = execute_forecast(query_result, visual.forecast)
    for reference in visual.data_roles.values():
        kind, separator, name = reference.partition(":")
        if not separator or not name:
            raise RuntimeMaterializationError(f"Invalid neutral reference: {reference!r}")
        if kind == "parameter":
            if name not in executor.parameters:
                raise RuntimeMaterializationError(f"Parameter {name!r} is unavailable")
            resolved[reference] = executor.parameters[name]
            continue
        if query_result is None or name not in query_result.columns:
            raise RuntimeMaterializationError(
                f"Visual {visual.name!r} has no executable result for {reference!r}"
            )
        values = [row[name] for row in query_result.rows]
        resolved[reference] = (
            values[0] if visual.kind in {"card", "kpi", "text_box"} and len(values) == 1 else values
        )
    return resolved, forecast_metadata


__all__ = ["RuntimeMaterializationError", "materialize_runtime_results"]
