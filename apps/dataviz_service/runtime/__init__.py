"""Runtime de DataVIZ para ejecutar RenderPlan e IR neutrales."""

from __future__ import annotations

from apps.dataviz_service.runtime.errors import (
    DataVizRuntimeError,
    InvalidVisualSpecError,
    MissingDatasourceError,
    OpaqueDAXExecutionUnavailableError,
    UnknownFieldError,
    UnsupportedVisualError,
)
from apps.dataviz_service.runtime.executor import QueryExecutor
from apps.dataviz_service.runtime.materializer import (
    RuntimeMaterializationError,
    materialize_runtime_results,
)

__all__ = [
    "DataVizRuntimeError",
    "InvalidVisualSpecError",
    "MissingDatasourceError",
    "OpaqueDAXExecutionUnavailableError",
    "QueryExecutor",
    "RuntimeMaterializationError",
    "UnknownFieldError",
    "UnsupportedVisualError",
    "materialize_runtime_results",
]
