"""Excepciones controladas del runtime de DataVIZ.

Todas heredan de :class:`DataVizRuntimeError` para que la frontera HTTP pueda
convertirlas en errores sanitizados sin filtrar trazas internas al exterior.
"""

from __future__ import annotations


class DataVizRuntimeError(Exception):
    """Clase base de los errores controlados del runtime."""


class UnknownFieldError(DataVizRuntimeError):
    """Campo referenciado fuera del esquema del dataset."""


class UnsupportedVisualError(DataVizRuntimeError):
    """Familia visual fuera de la lista blanca."""


class InvalidVisualSpecError(DataVizRuntimeError):
    """Spec visual con canales inválidos para su familia."""


class MissingDatasourceError(DataVizRuntimeError):
    """Datasource no registrado en el runtime."""


class QueryInputTooLargeError(DataVizRuntimeError):
    """Persisted dataset rows exceed the configured query input cap."""


class RuntimeMaterializationError(DataVizRuntimeError):
    """Persisted dataset payload is not a valid materialization."""


class OpaqueDAXExecutionUnavailableError(RuntimeMaterializationError):
    """The neutral runtime cannot evaluate source-preserved opaque DAX."""


class SemanticRLSUnavailableError(DataVizRuntimeError):
    """Persisted semantic RLS exists but no trusted evaluator is available."""
