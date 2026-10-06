"""Local Excel connector: deterministic, sandboxed, formula-safe ingestion.

Capabilities (runtime-backed, truthfully declared):

* **schema discovery** -- the header row is read before data rows; the column
  order and names are deterministic (empty or duplicated headers fail closed).
* **ingestion** -- sheets are read through ``openpyxl`` in ``read_only`` +
  ``data_only`` mode: formula cells are returned from their *cached* values and
  **never evaluated**, and VBA/macros (``.xlsm``) are rejected outright. Legacy
  ``.xls`` (BIFF) is refused with a clear message.
* **bounding** -- the file size is capped before open and row/time limits plus
  cooperative cancellation are enforced through the shared
  :func:`~apps.dataviz_service.runtime.connectors.local._iter_bounded` stream,
  the same single enforcement point as the CSV/Parquet connectors.
* **sandbox** -- the path must resolve inside a caller-supplied base dir
  (:func:`resolve_within_sandbox`), same policy as the existing local
  connectors.

Date-like cells are normalized to ``datetime.datetime`` before arrow
construction so the batch schema stays identical across chunks.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from functools import cached_property
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

#: Hard cap on the total workbook size accepted before open (configurable).
_DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024

#: Openpyxl supports modern XML formats; ``.xlsm`` and ``.xltm`` are refused
#: fail-closed because macro-capable workbooks/templates must not be ingested (no executable macros).
_ALLOWED_EXTENSIONS = {".xlsx", ".xltx"}
_REFUSED_EXTENSIONS = {
    ".xls": "legacy .xls (BIFF) is not supported; save the workbook as .xlsx",
    ".xlsm": "macro-enabled .xlsm workbooks are not ingested (no executable macros)",
    ".xltm": "macro-enabled .xltm templates are not ingested (no executable macros)",
}


class ExcelConnectorError(Exception):
    """Controlled connector failure (path safety, config, parse, schema)."""


@dataclass(frozen=True)
class ExcelSheetInfo:
    """Deterministic header/sheet identity produced before data rows."""

    sheet_name: str
    columns: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"sheet_name": self.sheet_name, "columns": list(self.columns)}


class LocalExcelConnector(Connector):
    """Streams a local ``.xlsx`` sheet as Arrow batches.

    Args:
        base_dir: Trusted sandbox root; the file must live inside it.
        relative_path: Untrusted path relative to ``base_dir``.
        sheet_name: Worksheet to read. ``None`` uses the first worksheet.
        max_file_bytes: Hard file-size cap checked before open.
        batch_size: Rows per emitted batch (also the fetch step for the header).
    """

    kind = "excel"
    CAPABILITIES: ClassVar[ConnectorCapabilityContract] = ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id="excel",
        implementation=CapabilitySupport.SUPPORTED,
        schema_discovery=SchemaDiscoveryLevel.FULL,
        supported_types=("string", "boolean", "integer", "float", "decimal", "date", "datetime"),
        auth=AuthSupport(kinds=(AuthKind.NONE,)),
        streaming_row_chunkable=True,
        cancelable=True,
        limits=LimitsSupport(row_limit=True, time_limit=True, cost_limit=False),
        native_rls=False,
        execution_modes=(ExecutionMode.LIVE,),
        requires_network=False,
        description=(
            "Local Excel reader (openpyxl read_only/data_only): no formula or macro "
            "execution, deterministic header schema, sandboxed paths."
        ),
    )

    def __init__(
        self,
        base_dir: Path | str,
        relative_path: str,
        *,
        sheet_name: str | None = None,
        max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES,
        batch_size: int = 10_000,
    ) -> None:
        if isinstance(max_file_bytes, bool) or not isinstance(max_file_bytes, int) or max_file_bytes <= 0:
            raise ValueError(f"max_file_bytes must be a positive int, got {max_file_bytes!r}")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")
        self._batch_size = batch_size
        self._max_file_bytes = max_file_bytes
        self._sheet_name = sheet_name
        try:
            self._resolved = resolve_within_sandbox(base_dir, relative_path)
        except PathSafetyError as error:
            raise ExcelConnectorError(str(error)) from error
        ext = self._resolved.suffix.lower()
        if ext in _REFUSED_EXTENSIONS:
            raise ExcelConnectorError(_REFUSED_EXTENSIONS[ext])
        if ext not in _ALLOWED_EXTENSIONS:
            raise ExcelConnectorError(f"unsupported workbook extension {ext!r}; expected .xlsx or .xltx")
        if not self._resolved.is_file():
            raise ExcelConnectorError(f"workbook does not exist: {self._resolved.name}")
        size = self._resolved.stat().st_size
        if size > self._max_file_bytes:
            raise ExcelConnectorError(
                f"workbook size {size} bytes exceeds the configured maximum {self._max_file_bytes}"
            )
        self._file_size = size

    @cached_property
    def sheet_info(self) -> ExcelSheetInfo:
        """Header/sheet identity (read without materializing data rows)."""
        import openpyxl

        workbook = openpyxl.load_workbook(self._resolved, read_only=True, data_only=True)
        try:
            if self._sheet_name is not None:
                if self._sheet_name not in workbook.sheetnames:
                    raise ExcelConnectorError(
                        f"sheet {self._sheet_name!r} not found; available: {workbook.sheetnames!r}"
                    )
                worksheet = workbook[self._sheet_name]
            else:
                worksheet = workbook[workbook.sheetnames[0]]
            return ExcelSheetInfo(sheet_name=worksheet.title, columns=self._read_headers(worksheet))
        finally:
            workbook.close()

    @staticmethod
    def _read_headers(worksheet: Any) -> tuple[str, ...]:
        """Read and validate the first row as deterministic column names."""
        header_row = next(worksheet.iter_rows(values_only=True), None)
        if header_row is None:
            raise ExcelConnectorError("workbook sheet has no header row")
        columns: list[str] = []
        seen: set[str] = set()
        for index, cell in enumerate(header_row):
            if cell is None or (isinstance(cell, str) and not cell.strip()):
                raise ExcelConnectorError(f"empty header at column {index + 1}; headers are required")
            name = str(cell).strip()
            if name in seen:
                raise ExcelConnectorError(f"duplicated header {name!r}; column names must be unique")
            seen.add(name)
            columns.append(name)
        return tuple(columns)

    def _iter_source(self, columns: Sequence[str] | None) -> Iterator[pa.RecordBatch]:
        headers = self.sheet_info.columns
        if columns is not None:
            requested = list(columns)
            missing = [name for name in requested if name not in headers]
            if missing:
                raise ExcelConnectorError(f"requested columns not present in sheet: {missing!r}")
            chosen = requested
        else:
            chosen = list(headers)

        import openpyxl

        workbook = openpyxl.load_workbook(self._resolved, read_only=True, data_only=True)
        try:
            worksheet = workbook[self.sheet_info.sheet_name]
            rows_iter = worksheet.iter_rows(values_only=True)
            header = next(rows_iter, None)  # already validated by sheet_info
            index_of = {name: position for position, name in enumerate(header)}
            positions = [index_of[name] for name in chosen]

            schema: pa.Schema | None = None
            chunk: list[dict[str, Any]] = []
            for raw_row in rows_iter:
                row: dict[str, Any] = {}
                for name, position in zip(chosen, positions, strict=True):
                    row[name] = _normalize_cell(raw_row[position])
                chunk.append(row)
                if len(chunk) >= self._batch_size:
                    batch, schema = _batch_for(chunk, schema)
                    yield batch
                    chunk = []
            if chunk:
                batch, schema = _batch_for(chunk, schema)
                yield batch
        finally:
            workbook.close()


def _normalize_cell(value: Any) -> Any:
    """Normalize a cell value for stable arrow typing (date-like -> datetime)."""
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day)
    return value


def _batch_for(rows: list[dict[str, Any]], schema: pa.Schema | None) -> tuple[pa.RecordBatch, pa.Schema]:
    """Build one RecordBatch with a schema consistent across chunks.

    The first chunk infers the schema; later chunks convert against it.
    Conversion failures (heterogeneous column values) fail closed with a
    controlled error instead of leaking an arrow exception.
    """
    try:
        if schema is None:
            table = pa.Table.from_pylist(rows)
            schema = table.schema
        else:
            table = pa.Table.from_pylist(rows, schema=schema)
    except (pa.ArrowInvalid, pa.ArrowTypeError, TypeError, ValueError) as error:
        raise ExcelConnectorError(f"cannot cast sheet values to a stable schema: {error}") from error
    return table.to_batches(max_chunksize=len(rows))[0], schema


#: Module-level alias (stable import surface for consumers/registry).
EXCEL_CAPABILITY_CONTRACT = LocalExcelConnector.CAPABILITIES


__all__ = [
    "EXCEL_CAPABILITY_CONTRACT",
    "ExcelConnectorError",
    "ExcelSheetInfo",
    "LocalExcelConnector",
]
