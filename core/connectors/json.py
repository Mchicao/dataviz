"""Local JSON connector: deterministic array-of-objects ingestion.

Capabilities (runtime-backed, truthfully declared):

* **schema discovery** -- the root must be a JSON array of objects; anything
  else fails closed. Column order is the first-appearance order of keys across
  objects (deterministic). Missing keys become nulls; later keys are appended.
* **deterministic typing** -- each column is assigned one neutral type from the
  whole payload: homogeneous ints stay integers, ints+floats widen to float,
  homogeneous bools stay boolean, and anything mixed with strings (or nested
  values) is canonicalized to strings (``json.dumps`` with sorted keys).
* **bounding** -- the file size is capped before open; row/time limits and
  cooperative cancellation are enforced through the shared bounded stream.

This connector reads only local files, never network URLs.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa

from apps.dataviz_service.runtime.connectors.local import (
    Connector,
    PathSafetyError,
    resolve_within_sandbox,
)
from core.contracts.connector import (
    SCHEMA_VERSION,
    AuthKind,
    AuthSupport,
    CapabilitySupport,
    ConnectorCapabilityContract,
    ExecutionMode,
    LimitsSupport,
    SchemaDiscoveryLevel,
)

#: Hard cap on the total file size accepted before open (configurable).
_DEFAULT_MAX_FILE_BYTES = 16 * 1024 * 1024

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


class JsonConnectorError(Exception):
    """Controlled connector failure (path safety, config, parse, schema)."""


@dataclass(frozen=True)
class JsonPayloadInfo:
    """Deterministic payload identity produced before data rows."""

    columns: tuple[str, ...]
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"columns": list(self.columns), "row_count": self.row_count}


class LocalJsonConnector(Connector):
    """Streams a local JSON array-of-objects file as Arrow batches.

    Args:
        base_dir: Trusted sandbox root; the file must live inside it.
        relative_path: Untrusted path relative to ``base_dir``.
        max_file_bytes: Hard file-size cap checked before open.
        batch_size: Rows per emitted batch.
    """

    kind = "json"
    CAPABILITIES: ClassVar[ConnectorCapabilityContract] = ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id="json",
        implementation=CapabilitySupport.SUPPORTED,
        schema_discovery=SchemaDiscoveryLevel.FULL,
        supported_types=("string", "boolean", "integer", "float"),
        auth=AuthSupport(kinds=(AuthKind.NONE,)),
        streaming_row_chunkable=True,
        cancelable=True,
        limits=LimitsSupport(row_limit=True, time_limit=True, cost_limit=False),
        native_rls=False,
        execution_modes=(ExecutionMode.LIVE,),
        requires_network=False,
        description=(
            "Local JSON array-of-objects reader: deterministic key order and typing, "
            "size-capped, sandboxed paths, no network access."
        ),
    )

    def __init__(
        self,
        base_dir: Path | str,
        relative_path: str,
        *,
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
        batch_size: int = 10_000,
    ) -> None:
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or max_file_bytes <= 0:
            raise ValueError(f"max_file_bytes must be a positive int, got {max_file_bytes!r}")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")
        self._batch_size = batch_size
        self._max_file_bytes = max_file_bytes
        try:
            self._resolved = resolve_within_sandbox(base_dir, relative_path)
        except PathSafetyError as error:
            raise JsonConnectorError(str(error)) from error
        ext = self._resolved.suffix.lower()
        if ext != ".json":
            raise JsonConnectorError(f"unsupported JSON extension {ext!r}; expected .json")
        if not self._resolved.is_file():
            raise JsonConnectorError(f"JSON file does not exist: {self._resolved.name}")
        size = self._resolved.stat().st_size
        if size > self._max_file_bytes:
            raise JsonConnectorError(
                f"JSON file size {size} bytes exceeds the configured maximum {self._max_file_bytes}"
            )
        self._file_size = size

    @property
    def payload_info(self) -> JsonPayloadInfo:
        """Payload identity: deterministic columns and row count."""
        records = _load_records(self._resolved)
        columns, rows = _canonicalize(records)
        return JsonPayloadInfo(columns=columns, row_count=len(rows))

    def _iter_source(self, columns: Sequence[str] | None) -> Iterator[pa.RecordBatch]:
        records = _load_records(self._resolved)
        all_columns, rows = _canonicalize(records)
        chosen = list(columns) if columns is not None else list(all_columns)
        missing = [name for name in chosen if name not in all_columns]
        if missing:
            raise JsonConnectorError(f"requested columns not present in payload: {missing!r}")

        neutral_types = _columns_neutral_types(all_columns, rows)
        positions = {name: position for position, name in enumerate(all_columns)}
        chosen_positions = [positions[name] for name in chosen]
        chosen_types = [neutral_types[name] for name in chosen]

        for start in range(0, len(rows), self._batch_size):
            chunk = rows[start : start + self._batch_size]
            arrays: list[pa.Array] = []
            for position, neutral in zip(chosen_positions, chosen_types, strict=True):
                values = [_convert_scalar(row[position], neutral) for row in chunk]
                arrays.append(pa.array(values, type=_ARROW_TYPE[neutral]))
            yield pa.RecordBatch.from_arrays(arrays, names=chosen)


#: Neutral column type -> arrow type for deterministic conversion.
_ARROW_TYPE: dict[str, pa.DataType] = {
    "string": pa.string(),
    "boolean": pa.bool_(),
    "integer": pa.int64(),
    "float": pa.float64(),
}


def _load_records(path: Path) -> list[dict[str, Any]]:
    """Load and validate the root JSON as an array of objects (fail-closed)."""
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as error:
        raise JsonConnectorError(f"invalid JSON: line {error.lineno} column {error.colno}: {error.msg}") from error
    except UnicodeDecodeError as error:
        raise JsonConnectorError(f"JSON file is not valid UTF-8: {error}") from error
    if not isinstance(raw, list):
        raise JsonConnectorError("root JSON must be an array of objects")
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise JsonConnectorError(f"element {index} is not a JSON object ({type(item).__name__})")
    return raw


def _canonicalize(records: list[dict[str, Any]]) -> tuple[tuple[str, ...], list[tuple[Any, ...]]]:
    """Return deterministic column order (first appearance) and tuple rows.

    Nested values are canonicalized to strings with sorted keys so the column
    type can never depend on insertion order.
    """
    columns: list[str] = []
    seen: set[str] = set()
    for record in records:
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                columns.append(key)
    rows: list[tuple[Any, ...]] = []
    for record in records:
        rows.append(tuple(_canonical_scalar(record.get(column)) for column in columns))
    return tuple(columns), rows


def _canonical_scalar(value: Any) -> Any:
    """Normalize one JSON scalar for deterministic typing.

    Nested containers become canonical JSON strings (sorted keys) so a column
    containing them is a string column. Integers beyond int64 become strings.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        if _INT64_MIN <= value <= _INT64_MAX:
            return value
        return str(value)
    if isinstance(value, float):
        return value
    if isinstance(value, (dict, list)):
        return _json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _columns_neutral_types(
    columns: tuple[str, ...], rows: list[tuple[Any, ...]]
) -> dict[str, str]:
    """Assign one deterministic neutral type per column across the payload."""
    result: dict[str, str] = {}
    for position, column in enumerate(columns):
        result[column] = _column_neutral_type([row[position] for row in rows])
    return result


def _column_neutral_type(values: Sequence[Any]) -> str:
    """Deterministic neutral type for a column's canonicalized values."""
    non_null = [value for value in values if value is not None]
    if not non_null:
        return "string"
    if all(isinstance(value, bool) for value in non_null):
        return "boolean"
    if all(isinstance(value, int) for value in non_null):
        return "integer"
    if all(isinstance(value, (int, float)) for value in non_null):
        return "float"
    return "string"


def _convert_scalar(value: Any, neutral: str) -> Any:
    """Coerce a canonicalized value to its column's neutral representation."""
    if value is None:
        return None
    if neutral == "string":
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)
    if neutral == "boolean":
        return bool(value)
    if neutral == "integer":
        return int(value)
    if neutral == "float":
        return float(value)
    return value


#: Module-level alias (stable import surface for consumers/registry).
JSON_CAPABILITY_CONTRACT = LocalJsonConnector.CAPABILITIES


__all__ = [
    "JSON_CAPABILITY_CONTRACT",
    "JsonConnectorError",
    "JsonPayloadInfo",
    "LocalJsonConnector",
]
