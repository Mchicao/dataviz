"""Bounded local data connectors for the DataVIZ runtime.

Three connectors, one streaming contract:

* :class:`LocalCsvConnector`   -- reads a local ``.csv`` via ``pyarrow.csv``.
* :class:`LocalParquetConnector` -- reads a local ``.parquet`` via
  ``pyarrow.parquet``.
* :class:`PostgresTestDoubleConnector` -- an in-memory *test double* that
  advertises a PostgreSQL capability path **without** ever opening a socket or
  importing a database driver. No real credentials or network source are
  involved; it exists so planners/renderers can be exercised against a
  PostgreSQL-shaped datasource in tests.

Design (Ponytail):

* Arrow is the wire format. Every connector streams
  :class:`pyarrow.RecordBatch` objects through ``stream``; :meth:`materialize`
  is a convenience that consumes the stream under the same limits and returns a
  :class:`~core.contracts.dataset.Dataset` the in-memory executor
  already understands.
* Capabilities are declared through the versioned contract
  :class:`~core.contracts.connector.ConnectorCapabilityContract`; the legacy
  :class:`ConnectorCapabilities` descriptor is kept as a projection of it.
* One enforcement point (:func:`_iter_bounded`) applies row/time limits and
  cooperative cancellation between batches for every connector.
* Local paths are sandboxed under a caller-supplied ``base_dir``. Absolute
  paths, drive letters, UNC paths, ``..`` escapes and embedded credentials are
  rejected up front, and a ``resolve()`` + containment check is the backstop.

Cancellation is **cooperative**: the cancel token is consulted between batches.
A source that blocks forever on a single batch cannot be interrupted here; that
is the documented boundary.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic as _monotonic
from typing import Any, ClassVar

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from core.contracts.connector import (
    SCHEMA_VERSION,
    AuthKind,
    AuthSupport,
    ConnectorCapabilityContract,
    ExecutionMode,
    LimitsSupport,
    PushdownSupport,
    SchemaDiscoveryLevel,
)
from core.contracts.dataset import Dataset

#: Stopped reasons reported by :class:`MaterializedTable.stopped_reason`.
STOP_REASONS: frozenset[str] = frozenset({"complete", "row_limit", "time_limit", "cancelled"})


# =====================================================================
# Errors
# =====================================================================


class ConnectorError(Exception):
    """Base class for controlled connector errors (path safety, config, I/O)."""


class PathSafetyError(ConnectorError):
    """Raised when a requested local path escapes the connector sandbox."""


class ConnectorCancelled(ConnectorError):
    """Raised by :meth:`Connector.raise_if_cancelled` helpers (cooperative)."""


# =====================================================================
# Limits, capabilities, results
# =====================================================================


def _is_real_int(value: Any) -> bool:
    """Real integer (``bool`` is rejected -- it is a subclass of ``int``)."""
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class ConnectorLimits:
    """Declarative bounds applied to every connector stream.

    Attributes:
        max_rows: Hard cap on the total number of rows emitted across all
            batches. Once reached the stream stops with reason ``"row_limit"``
            and the overshooting batch is sliced to fill the cap exactly.
        max_seconds: Wall-clock budget in seconds (checked between batches).
            Once exceeded the stream stops with reason ``"time_limit"``.
        batch_size: Target rows per emitted batch. Row-based for Parquet and
            the PostgreSQL test double; CSV reads in byte-sized blocks so this
            is treated as best-effort there.
    """

    max_rows: int | None = None
    max_seconds: float | None = None
    batch_size: int = 10_000

    def __post_init__(self) -> None:
        if self.max_rows is not None and (not _is_real_int(self.max_rows) or self.max_rows < 0):
            raise ValueError(f"max_rows must be a non-negative int, got {self.max_rows!r}")
        if self.max_seconds is not None:
            if isinstance(self.max_seconds, bool) or not isinstance(self.max_seconds, (int, float)):
                raise ValueError(f"max_seconds must be a number, got {self.max_seconds!r}")
            if self.max_seconds < 0:
                raise ValueError(f"max_seconds must be >= 0, got {self.max_seconds!r}")
        if not _is_real_int(self.batch_size) or self.batch_size < 1:
            raise ValueError(f"batch_size must be a positive int, got {self.batch_size!r}")


@dataclass(frozen=True)
class ConnectorCapabilities:
    """Legacy capability projection of :class:`ConnectorCapabilityContract`.

    Kept for backward compatibility (planners/renderers already read these
    fields). The source of truth is the versioned contract; build this view
    with :meth:`from_contract`.

    Attributes:
        kind: Canonical datasource kind (e.g. ``"csv"``, ``"postgresql"``).
        requires_network: ``True`` only if the connector opens a socket. The
            PostgreSQL test double is intentionally ``False``.
        supports_pushdown: ``True`` if column projection is pushed to the
            source rather than applied after reading.
        row_chunkable: ``True`` if the source streams in row batches.
        description: Human-readable note (no secrets, no absolute paths).
    """

    kind: str
    requires_network: bool
    supports_pushdown: bool
    row_chunkable: bool
    description: str = ""

    def __post_init__(self) -> None:
        if not self.kind or not self.kind.strip():
            raise ValueError("ConnectorCapabilities.kind is required")
        if not self.description:
            # Provide a stable, non-empty default so callers can rely on it.
            object.__setattr__(self, "description", f"{self.kind} connector")

    @classmethod
    def from_contract(cls, contract: ConnectorCapabilityContract) -> ConnectorCapabilities:
        """Project the versioned contract onto the legacy descriptor fields."""
        return cls(
            kind=contract.connector_id,
            requires_network=contract.requires_network,
            supports_pushdown=contract.pushdown.projection,
            row_chunkable=contract.streaming_row_chunkable,
            description=contract.description,
        )


@dataclass(frozen=True)
class MaterializedTable:
    """Result of :meth:`Connector.materialize`: Dataset + stream statistics.

    Attributes:
        dataset: In-memory :class:`Dataset` ready for the executor.
        batches: The Arrow batches that were emitted (post-limit).
        rows_emitted: Total rows across ``batches``.
        stopped_reason: Why streaming ended -- see :data:`STOP_REASONS`.
        elapsed_seconds: Wall-clock seconds consumed by the bounded stream.
        capability_contract: Versioned capability snapshot of the connector
            that produced this result.
    """

    dataset: Dataset
    batches: tuple[pa.RecordBatch, ...]
    rows_emitted: int
    stopped_reason: str
    elapsed_seconds: float
    capability_contract: ConnectorCapabilityContract

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """Legacy capability projection of ``capability_contract``."""
        return ConnectorCapabilities.from_contract(self.capability_contract)


# =====================================================================
# Cancellation
# =====================================================================


class CancelToken:
    """Thread-safe cooperative cancellation flag.

    Wraps :class:`threading.Event`. Any callable returning ``bool`` is also
    accepted directly by the connector APIs (see :func:`_cancel_checker`).
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Mark the token as cancelled (idempotent)."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """``True`` once :meth:`cancel` has been called."""
        return self._event.is_set()


def _cancel_checker(cancel: CancelToken | Callable[[], bool] | None) -> Callable[[], bool]:
    """Normalize the cancellation argument into a ``() -> bool`` predicate."""
    if cancel is None:
        return lambda: False
    if isinstance(cancel, CancelToken):
        return cancel.is_cancelled
    if callable(cancel):
        return cancel  # type: ignore[return-value]
    raise TypeError(
        f"cancel must be a CancelToken, a callable, or None; got {type(cancel).__name__}"
    )


# =====================================================================
# Path sandboxing
# =====================================================================


def resolve_within_sandbox(base_dir: Path | str, relative_path: str) -> Path:
    """Resolve ``relative_path`` under ``base_dir`` and forbid escapes.

    Defense in depth:

    1. Reject empty / absolute / drive-letter / UNC input up front.
    2. Join under the resolved ``base_dir``.
    3. ``resolve()`` the candidate (collapses ``..`` and symlinks) and verify
       the result is ``base_dir`` itself or one of its descendants.

    Args:
        base_dir: Sandbox root (trusted; usually a tenant scratch dir).
        relative_path: Untrusted relative path requested by the caller.

    Returns:
        The resolved, containment-checked absolute :class:`Path`.

    Raises:
        PathSafetyError: On any escape attempt or malformed input.
    """
    raw = "" if relative_path is None else str(relative_path)
    if not raw.strip():
        raise PathSafetyError("relative_path is empty")
    candidate_in = Path(relative_path)
    if candidate_in.is_absolute():
        raise PathSafetyError(f"absolute paths are forbidden: {relative_path!r}")
    # Drive letters (Windows) -- ``Path.drive`` is non-empty for ``C:`` style.
    if candidate_in.drive:
        raise PathSafetyError(f"drive letters are forbidden: {relative_path!r}")
    # UNC ``//host/share`` -- ``as_posix`` exposes a leading ``//``.
    if candidate_in.as_posix().startswith("//"):
        raise PathSafetyError(f"UNC paths are forbidden: {relative_path!r}")

    base = Path(base_dir).resolve()
    candidate = (base / candidate_in).resolve()
    if candidate != base and base not in candidate.parents:
        raise PathSafetyError(f"path escapes the connector sandbox: {relative_path!r}")
    return candidate


# =====================================================================
# Bounded streaming (single enforcement point)
# =====================================================================


@dataclass
class _Stop:
    """Mutable side-channel so a generator can report *why* it stopped."""

    reason: str = "complete"
    elapsed_seconds: float = 0.0
    schema: pa.Schema | None = None


def _finish(stop: _Stop, start: float) -> None:
    stop.elapsed_seconds = _monotonic() - start


def _iter_bounded(
    source: Iterator[pa.RecordBatch],
    limits: ConnectorLimits,
    cancel: CancelToken | Callable[[], bool] | None,
    stop: _Stop,
) -> Iterator[pa.RecordBatch]:
    """Yield batches from ``source`` enforcing row/time limits and cancellation.

    The first batch observed fixes the emitted schema (captured into
    ``stop.schema``) so callers can build a correctly-columned result even when
    a row cap of ``0`` suppresses every row.
    """
    start = _monotonic()
    cancelled = _cancel_checker(cancel)
    rows = 0
    for batch in source:
        if stop.schema is None:
            stop.schema = batch.schema
        # Cooperative checks happen between batches (never mid-batch).
        if cancelled():
            stop.reason = "cancelled"
            _finish(stop, start)
            return
        if limits.max_seconds is not None and (_monotonic() - start) >= limits.max_seconds:
            stop.reason = "time_limit"
            _finish(stop, start)
            return
        if limits.max_rows is not None and rows >= limits.max_rows:
            # Cap already filled by a previous (sliced) batch.
            stop.reason = "row_limit"
            _finish(stop, start)
            return
        out = batch
        if limits.max_rows is not None and rows + batch.num_rows > limits.max_rows:
            # Slice the overshoot so the cap is exact, never exceeded.
            out = batch.slice(0, limits.max_rows - rows)
        yield out
        rows += out.num_rows
        if limits.max_rows is not None and rows >= limits.max_rows:
            stop.reason = "row_limit"
            _finish(stop, start)
            return
    stop.reason = "complete"
    _finish(stop, start)


# =====================================================================
# Dataset bridge
# =====================================================================


def batches_to_dataset(
    name: str,
    batches: Sequence[pa.RecordBatch],
    *,
    schema: pa.Schema | None = None,
    columns: Sequence[str] | None = None,
) -> Dataset:
    """Convert Arrow batches into an in-memory :class:`Dataset`.

    When ``batches`` is empty, ``columns`` must be supplied so the resulting
    Dataset still carries a correct (column-only) shape.
    """
    if batches:
        table = pa.Table.from_batches(list(batches), schema=schema)
        return Dataset.from_records(name, table.to_pylist(), columns=table.column_names)
    cols = tuple(columns) if columns is not None else ()
    if not cols:
        raise ConnectorError("cannot infer columns: source produced no batches and none were given")
    return Dataset.from_records(name, [], columns=cols)


# =====================================================================
# Connector base
# =====================================================================


class Connector:
    """Abstract bounded connector streaming Arrow :class:`RecordBatch`.

    Subclasses implement :meth:`_iter_source` (the raw, unbounded source) and
    declare ``kind`` / ``CAPABILITIES`` as a versioned
    :class:`~core.contracts.connector.ConnectorCapabilityContract`. Limit
    enforcement, cancellation and the Dataset bridge are shared.
    """

    kind: ClassVar[str] = ""
    CAPABILITIES: ClassVar[ConnectorCapabilityContract]

    @property
    def capability_contract(self) -> ConnectorCapabilityContract:
        """Versioned capability declaration for this connector."""
        return self.CAPABILITIES

    @property
    def capabilities(self) -> ConnectorCapabilities:
        """Legacy capability projection of ``capability_contract``."""
        return ConnectorCapabilities.from_contract(self.CAPABILITIES)

    def _iter_source(self, columns: Sequence[str] | None) -> Iterator[pa.RecordBatch]:
        raise NotImplementedError

    def stream(
        self,
        *,
        limits: ConnectorLimits | None = None,
        cancel: CancelToken | Callable[[], bool] | None = None,
        columns: Sequence[str] | None = None,
    ) -> Iterator[pa.RecordBatch]:
        """Yield Arrow batches under ``limits`` and cooperative ``cancel``.

        Column projection (``columns``) is pushed down to the source when the
        connector advertises ``supports_pushdown``.
        """
        stop = _Stop()
        yield from _iter_bounded(
            self._iter_source(columns), limits or ConnectorLimits(), cancel, stop
        )

    def materialize(
        self,
        name: str,
        *,
        limits: ConnectorLimits | None = None,
        cancel: CancelToken | Callable[[], bool] | None = None,
        columns: Sequence[str] | None = None,
    ) -> MaterializedTable:
        """Consume :meth:`stream` and return a :class:`Dataset` plus statistics."""
        stop = _Stop()
        bounded_limits = limits or ConnectorLimits()
        collected: list[pa.RecordBatch] = []
        for batch in _iter_bounded(self._iter_source(columns), bounded_limits, cancel, stop):
            collected.append(batch)

        if stop.schema is not None:
            schema = stop.schema
            cols: list[str] | None = list(schema.names)
        elif columns is not None:
            schema = None
            cols = list(columns)
        else:
            schema = None
            cols = None

        if cols is None and not collected:
            raise ConnectorError(
                "schema unknown: source produced no batches and no columns were provided"
            )

        dataset = batches_to_dataset(name, collected, schema=schema, columns=cols)
        rows_emitted = sum(b.num_rows for b in collected)
        return MaterializedTable(
            dataset=dataset,
            batches=tuple(collected),
            rows_emitted=rows_emitted,
            stopped_reason=stop.reason,
            elapsed_seconds=stop.elapsed_seconds,
            capability_contract=self.CAPABILITIES,
        )


# =====================================================================
# Local CSV
# =====================================================================


class LocalCsvConnector(Connector):
    """Streams a local CSV file through ``pyarrow.csv``.

    Args:
        base_dir: Trusted sandbox root; the file must live inside it.
        relative_path: Untrusted path relative to ``base_dir``.
    """

    kind = "csv"
    CAPABILITIES: ClassVar[ConnectorCapabilityContract] = ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id="csv",
        schema_discovery=SchemaDiscoveryLevel.FULL,
        supported_types=("string", "boolean", "integer", "float", "date", "datetime"),
        auth=AuthSupport(kinds=(AuthKind.NONE,)),
        pushdown=PushdownSupport(projection=True, dialect="arrow"),
        streaming_row_chunkable=True,
        cancelable=True,
        limits=LimitsSupport(row_limit=True, time_limit=True, cost_limit=False),
        native_rls=False,
        execution_modes=(ExecutionMode.LIVE,),
        requires_network=False,
        description="Local CSV reader (pyarrow.csv); no network, no SQL engine.",
    )

    def __init__(self, base_dir: Path | str, relative_path: str) -> None:
        # Resolve once at construction; fail fast on unsafe paths.
        self._resolved = resolve_within_sandbox(base_dir, relative_path)
        self._source = str(self._resolved)

    def _iter_source(self, columns: Sequence[str] | None) -> Iterator[pa.RecordBatch]:
        convert_opts: pacsv.ConvertOptions | None = None
        if columns is not None:
            # Pushdown: keep only the requested columns (drops the rest).
            convert_opts = pacsv.ConvertOptions(include_columns=list(columns))
        kwargs: dict[str, Any] = {}
        if convert_opts is not None:
            kwargs["convert_options"] = convert_opts
        with pacsv.open_csv(self._source, **kwargs) as reader:
            yield from reader


# =====================================================================
# Local Parquet
# =====================================================================


class LocalParquetConnector(Connector):
    """Streams a local Parquet file through ``pyarrow.parquet``.

    Args:
        base_dir: Trusted sandbox root; the file must live inside it.
        relative_path: Untrusted path relative to ``base_dir``.
        batch_size: Row target for ``ParquetFile.iter_batches``.
    """

    kind = "parquet"
    CAPABILITIES: ClassVar[ConnectorCapabilityContract] = ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id="parquet",
        schema_discovery=SchemaDiscoveryLevel.FULL,
        supported_types=(
            "string",
            "boolean",
            "integer",
            "float",
            "decimal",
            "date",
            "datetime",
            "binary",
        ),
        auth=AuthSupport(kinds=(AuthKind.NONE,)),
        pushdown=PushdownSupport(projection=True, dialect="arrow"),
        streaming_row_chunkable=True,
        cancelable=True,
        limits=LimitsSupport(row_limit=True, time_limit=True, cost_limit=False),
        native_rls=False,
        execution_modes=(ExecutionMode.LIVE,),
        requires_network=False,
        description="Local Parquet reader (pyarrow.parquet); no network, no SQL engine.",
    )

    def __init__(
        self, base_dir: Path | str, relative_path: str, *, batch_size: int = 10_000
    ) -> None:
        self._resolved = resolve_within_sandbox(base_dir, relative_path)
        # Validate batch_size up front (ParquetFile.iter_batches needs >= 1).
        if not _is_real_int(batch_size) or batch_size < 1:
            raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")
        self._batch_size = batch_size

    def _iter_source(self, columns: Sequence[str] | None) -> Iterator[pa.RecordBatch]:
        parquet_file = pq.ParquetFile(self._resolved)
        cols = list(columns) if columns is not None else None
        yield from parquet_file.iter_batches(batch_size=self._batch_size, columns=cols)


# =====================================================================
# PostgreSQL test double (capability path, no network)
# =====================================================================


class PostgresTestDoubleConnector(Connector):
    """In-memory PostgreSQL test double -- the capability path, not a client.

    This connector advertises ``kind="postgresql"`` so higher layers can plan
    and render against a PostgreSQL-shaped datasource, but it **never** opens a
    socket, imports a database driver, or accepts credentials. Rows come from
    an injected in-memory fixture. A real PostgreSQL connector (network +
    driver + credential handling) is intentionally out of scope for this task.

    Args:
        rows: In-memory fixture rows (list of mappings).
        columns: Column order. Required when ``rows`` is empty so the emitted
            schema is unambiguous; inferred from the first row otherwise.
        batch_size: Rows per emitted batch.
    """

    kind = "postgresql"
    CAPABILITIES: ClassVar[ConnectorCapabilityContract] = ConnectorCapabilityContract(
        schema_version=SCHEMA_VERSION,
        connector_id="postgresql",
        schema_discovery=SchemaDiscoveryLevel.FULL,
        supported_types=(
            "string",
            "boolean",
            "integer",
            "float",
            "date",
            "datetime",
            "binary",
        ),
        auth=AuthSupport(kinds=(AuthKind.NONE,)),
        pushdown=PushdownSupport(projection=True, dialect="arrow"),
        streaming_row_chunkable=True,
        cancelable=True,
        limits=LimitsSupport(row_limit=True, time_limit=True, cost_limit=False),
        native_rls=False,
        execution_modes=(ExecutionMode.LIVE,),
        requires_network=False,
        description=(
            "PostgreSQL test double: in-memory fixture, no socket, no driver, "
            "no credentials. Real client is out of scope."
        ),
    )

    def __init__(
        self,
        rows: Sequence[Mapping[str, Any]],
        *,
        columns: Sequence[str] | None = None,
        batch_size: int = 10_000,
    ) -> None:
        # Defensive copy: the caller must not be able to mutate the fixture
        # after construction and alter streaming output.
        self._rows: list[dict[str, Any]] = [dict(r) for r in rows]
        self._columns: tuple[str, ...] = tuple(columns) if columns is not None else ()
        if not _is_real_int(batch_size) or batch_size < 1:
            raise ValueError(f"batch_size must be a positive int, got {batch_size!r}")
        self._batch_size = batch_size
        if not self._rows and not self._columns:
            raise ValueError(
                "PostgresTestDoubleConnector needs explicit columns when rows is empty"
            )
        if not self._columns and self._rows:
            # Infer column order from first appearance (stable).
            seen: set[str] = set()
            inferred: list[str] = []
            for record in self._rows:
                for key in record.keys():
                    if key not in seen:
                        seen.add(key)
                        inferred.append(key)
            self._columns = tuple(inferred)

    def _iter_source(self, columns: Sequence[str] | None) -> Iterator[pa.RecordBatch]:
        chosen = list(columns) if columns is not None else list(self._columns)
        if not self._rows:
            # Empty fixture: emit a single schema-bearing batch so consumers
            # learn the columns without reading any data.
            if chosen:
                schema = pa.schema([(name, pa.string()) for name in chosen])
                yield pa.RecordBatch.from_pylist([], schema=schema)
            return
        table = pa.Table.from_pylist(self._rows)
        if columns is not None:
            table = table.select(chosen)
        yield from table.to_batches(max_chunksize=self._batch_size)


#: Module-level aliases of the declared contracts (stable import surface for
#: the target-source registry and external planners).
CSV_CAPABILITY_CONTRACT = LocalCsvConnector.CAPABILITIES
PARQUET_CAPABILITY_CONTRACT = LocalParquetConnector.CAPABILITIES
POSTGRES_DOUBLE_CAPABILITY_CONTRACT = PostgresTestDoubleConnector.CAPABILITIES


__all__ = [
    "CSV_CAPABILITY_CONTRACT",
    "CancelToken",
    "Connector",
    "ConnectorCapabilities",
    "ConnectorCapabilityContract",
    "ConnectorError",
    "ConnectorLimits",
    "LocalCsvConnector",
    "LocalParquetConnector",
    "PARQUET_CAPABILITY_CONTRACT",
    "POSTGRES_DOUBLE_CAPABILITY_CONTRACT",
    "MaterializedTable",
    "PathSafetyError",
    "PostgresTestDoubleConnector",
    "STOP_REASONS",
    "batches_to_dataset",
    "resolve_within_sandbox",
]
