"""Fidelity oracles: assess a compiled destination artifact against source IR.

The oracle compares the accepted **source neutral IR**
(:class:`~core.contracts.semantic_ir.SemanticModel`, optional
:class:`~core.contracts.presentation_ir.PresentationIR` and
:class:`~core.contracts.interaction_ir.InteractionIR`) against a compiled
:class:`~core.compilers.pbip.adapter.PBIPProject` and emits one
:class:`~core.contracts.capabilities.CapabilityVerdict` per assessed element,
grouped by four scopes:

* **source**   -- model structure (entities, fields, metrics, relationships,
  parameters, groups/grain) and model-level filters.
* **page**     -- report pages and page-level presentation contracts.
* **visual**   -- visual presence, layout geometry and data bindings.
* **context**  -- interaction/filter context (page filters, cross-filter rules).

Every non-native verdict carries a :class:`ReasonCode` and a tolerance-bound
explanation. Structural audits attach an :class:`EvidenceRef` pointing at the
PBIP file that was inspected (a sanitized relative path).

**Critical rule (Python-side semantics are never native DAX):** numeric
*values* and *aggregations* recomputed on the Python side can never earn a
``NATIVE`` verdict -- only the destination engine executing DAX can validate
runtime semantics. Such checks are always reported as ``APPROXIMATE`` with
``ReasonCode.NEEDS_MANUAL_VERIFICATION`` (or ``UNSUPPORTED`` when outside
tolerance), regardless of how closely the numbers match. Structural presence of
a measure/column/page/visual *can* be ``NATIVE`` because that is a structural
fact observable in the generated files, not an execution result.

**Forecast contract integration (no invention):** the
:meth:`assess_forecasts` oracle consumes the existing forecast contract
(:class:`ForecastAssessment` wrapping a serialized
:class:`~core.compilers.render_plan.ForecastSpec` plus the
:func:`~core.forecast.execute_forecast` metadata) and preserves its declared
``"approximate"`` fidelity verbatim. It never reads Tableau-side forecast
values and never promotes a forecast above ``APPROXIMATE`` until a
destination-side forecast oracle exists.

Design (Ponytail): stdlib + ``core.contracts`` (capabilities / fidelity) + the
``PBIPProject`` data type (pure data, one-way read). The oracle does not import
any destination runtime (PBIR / DAX engines / connectors) and does not re-run
the adapter; it only parses the already-generated PBIP files.

Schema Versioning Policy (SemVer): MAJOR breaking, MINOR additive, PATCH fix.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.contracts.capabilities import (
    SCHEMA_VERSION,
    CapabilityVerdict,
    DestinationProfile,
    EvidenceRef,
    ReasonCode,
    SourceElementRef,
    SupportLevel,
)
from core.contracts.fidelity import FidelityReport
from core.contracts.interaction_ir import InteractionIR
from core.contracts.presentation_ir import PresentationIR
from core.contracts.semantic_ir import SemanticModel

if TYPE_CHECKING:
    from core.compilers.pbip.adapter import PBIPProject

#: Neutral filter operators verified against PBIR filterConfiguration 1.2.0 /
#: SemanticQuery 1.3.0. Includes the InteractionIR spellings used by the adapter.
NATIVE_FILTER_OPS: frozenset[str] = frozenset(
    {
        "eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in",
        "between", "is_null", "is_not_null", "contains", "not_contains",
        "starts_with", "equals", "not_equals", "greater_than", "less_than",
        "not_starts_with",
    }
)

#: Default destination id assessed by the oracle (matches the PBIP adapter).
DEFAULT_DESTINATION: str = "power_bi_pbip"


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tolerances:
    """Numeric and geometric tolerance bands used by the oracles.

    A verdict is ``NATIVE`` only when the observed value is *within band*;
    deviations beyond the band downgrade the verdict and attach a reason.

    Attributes:
        value_abs: absolute tolerance for numeric value comparisons.
        value_rel: relative tolerance for numeric value comparisons
            (``|expected - actual| <= max(value_abs, value_rel * |expected|)``).
        geometry_abs: tolerance (logical pixels) for x/y/width/height when
            comparing a :class:`VisualGeometry` against a PBIR ``position``.
        confidence_native: confidence assigned to clean structural NATIVE
            verdicts.
        confidence_extended: confidence for EXTENDED verdicts.
        confidence_approximate: confidence for APPROXIMATE verdicts.
    """

    value_abs: float = 1e-9
    value_rel: float = 1e-6
    geometry_abs: float = 1.0
    confidence_native: float = 0.99
    confidence_extended: float = 0.85
    confidence_approximate: float = 0.6

    def __post_init__(self) -> None:
        for label, value in (
            ("value_abs", self.value_abs),
            ("value_rel", self.value_rel),
            ("geometry_abs", self.geometry_abs),
        ):
            if value < 0.0:
                raise ValueError(f"Tolerances.{label} must be non-negative")
        for label, value in (
            ("confidence_native", self.confidence_native),
            ("confidence_extended", self.confidence_extended),
            ("confidence_approximate", self.confidence_approximate),
        ):
            if not (0.0 <= float(value) <= 1.0):
                raise ValueError(f"Tolerances.{label} must be in [0.0, 1.0]")

    def value_within(self, expected: float, actual: float) -> bool:
        """True when ``actual`` is within the value tolerance band of ``expected``."""
        delta = abs(float(expected) - float(actual))
        return delta <= max(self.value_abs, self.value_rel * abs(float(expected)))

    def geometry_within(self, expected: float, actual: float) -> bool:
        """True when ``actual`` is within ``geometry_abs`` of ``expected``."""
        return abs(float(expected) - float(actual)) <= self.geometry_abs


# ---------------------------------------------------------------------------
# Value / aggregation checks (Python-side; never native)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValueCheck:
    """A single numeric value/aggregation check assessed on the Python side.

    The oracle compares ``expected`` against ``actual`` using the value
    tolerance band. Regardless of the outcome, the resulting verdict is never
    ``NATIVE``: Python recomputation is not destination DAX execution.

    Attributes:
        name: human-readable name of the checked value (e.g. a measure name).
        expected: reference value (typically from the source).
        actual: observed value (typically recomputed in Python or read back).
        source_id: stable id; defaults derived from ``name`` when empty.
        element_kind: capability element kind (default ``"calculation"``).
        description: free-form note explaining what the value represents.
    """

    name: str
    expected: float
    actual: float
    source_id: str = ""
    element_kind: str = "calculation"
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("ValueCheck.name is required")
        if not self.source_id:
            # Deterministic id keeps the profile's uniqueness invariant stable.
            object.__setattr__(self, "source_id", f"value:{self.name}")


# ---------------------------------------------------------------------------
# Forecast contract assessment (approximate until an external oracle exists)
# ---------------------------------------------------------------------------


#: Keys that identify a serialized ``ForecastSpec`` (the forecast contract).
#: Used only to confirm the caller passed the real contract, not to invent or
#: reinterpret source-side forecast values.
_FORECAST_SPEC_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"time_field", "value_field", "period", "horizon"}
)


@dataclass(frozen=True)
class ForecastAssessment:
    """Carry the existing forecast contract into the oracle without inventing values.

    Wraps a serialized :class:`~core.compilers.render_plan.ForecastSpec`
    (``spec``) and the metadata dict returned by
    :func:`~core.forecast.execute_forecast` (``metadata``). The oracle never
    reads Tableau-side forecast values and never recomputes them: it only
    preserves the contract's declared ``fidelity`` (``"approximate"``) and
    surfaces its declared ``limitations`` verbatim.

    Integrity rules (enforced in ``__post_init__``):

    * ``name`` is required (a forecast is identified by the visual/series it
      backs, never by a demo name).
    * ``spec`` must be a mapping carrying the forecast-contract keys
      (:data:`_FORECAST_SPEC_REQUIRED_KEYS`).
    * ``metadata['fidelity']`` must be exactly ``"approximate"`` (case/stripped).
      Any other value is refused: the oracle never invents or promotes a
      forecast fidelity. This is the explicit "no-invention" guard.

    Attributes:
        name: human-readable forecast name (e.g. the visual/series it backs).
        spec: serialized :class:`ForecastSpec` (the forecast contract input).
        metadata: execution metadata dict from :func:`execute_forecast`; must
            declare ``fidelity == "approximate"``. Carries ``algorithm``,
            ``limitations``, ``seasonal_periods``...
        source_id: stable id; derived from ``name`` when empty.
        trace_path: optional relative path to a calculation-trace artifact; when
            present the verdict evidence uses ``kind="calculation_trace"`` with
            this (path-sanitized) locator, otherwise an opaque metadata id.
    """

    name: str
    spec: Mapping[str, Any]
    metadata: Mapping[str, Any]
    source_id: str = ""
    trace_path: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.spec, Mapping):
            raise TypeError("ForecastAssessment.spec must be a mapping (serialized ForecastSpec)")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("ForecastAssessment.metadata must be a mapping")
        if not str(self.name).strip():
            raise ValueError("ForecastAssessment.name is required")
        missing = _FORECAST_SPEC_REQUIRED_KEYS - set(self.spec.keys())
        if missing:
            raise ValueError(
                f"ForecastAssessment.spec is missing forecast-contract keys: {sorted(missing)}"
            )
        fidelity = str(self.metadata.get("fidelity", "")).strip().lower()
        if fidelity != "approximate":
            raise ValueError(
                "ForecastAssessment.metadata['fidelity'] must be 'approximate'; "
                "the oracle refuses to invent or promote a forecast fidelity "
                f"(got {fidelity!r})"
            )
        if not self.source_id:
            # Deterministic id keeps the profile's uniqueness invariant stable.
            object.__setattr__(self, "source_id", f"forecast:{self.name}")


# ---------------------------------------------------------------------------
# Compiled-artifact index (parses PBIP files once)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _TableIndex:
    """Parsed view of one TMDL table file."""

    name: str
    columns: frozenset[str]
    file_path: str


@dataclass(frozen=True)
class _ArtifactIndex:
    """Structured, read-only view over a compiled :class:`PBIPProject`.

    Built once from the project's files; the oracles read it without touching
    the filesystem again. Parsing is intentionally minimal (regex + JSON): the
    oracle must not depend on the adapter or any TMDL/PBIR runtime to interpret
    the bytes -- it re-derives structure independently so it can catch drift.
    """

    tables: tuple[_TableIndex, ...] = ()
    measures: frozenset[str] = frozenset()
    measure_file: str = ""
    relationships: tuple[tuple[str, str], ...] = ()
    model_file: str = ""
    relationship_file: str = ""
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    visuals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    report_filters: list[dict[str, Any]] = field(default_factory=list)
    page_filters: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    visual_filters: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    page_visual_interactions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    report_file: str = ""

    def table(self, name: str) -> _TableIndex | None:
        """Locate a parsed table by exact name (case-sensitive)."""
        for table in self.tables:
            if table.name == name:
                return table
        return None

    def page_by_display(self, display_name: str) -> dict[str, Any] | None:
        """Locate a PBIR page by its ``displayName`` (independent correlation key)."""
        for page in self.pages.values():
            if str(page.get("displayName", "")) == display_name:
                return page
        return None


_TABLE_HEADER_RE = re.compile(r"^table\s+(.+)$", re.MULTILINE)
_COLUMN_RE = re.compile(r"^\tcolumn\s+(.+?)\s*$", re.MULTILINE)
_MEASURE_RE = re.compile(r"^\tmeasure\s+'([^']+)'", re.MULTILINE)
_RELATIONSHIP_RE = re.compile(
    r"^\trelationship\s+(?:'([^']+)'|\[([^\]]+)\])\s*\[([^\]]+)\]\s+to\s+"
    r"(?:'([^']+)'|\[([^\]]+)\])\s*\[([^\]]+)\]",
    re.MULTILINE,
)


def _strip_quotes(value: str) -> str:
    """Strip a single pair of surrounding single/double quotes from ``value``."""
    trimmed = value.strip()
    if len(trimmed) >= 2 and trimmed[0] in "\"'" and trimmed[-1] == trimmed[0]:
        return trimmed[1:-1]
    return trimmed


def build_index(project: PBIPProject) -> _ArtifactIndex:
    """Parse a compiled :class:`PBIPProject` into an :class:`_ArtifactIndex`.

    The index is the oracle's independent view of the destination artifact. It
    never imports the adapter's emitters: it re-derives table/column/measure/
    relationship structure and page/visual layout straight from the emitted
    text/JSON, so a regression in the adapter is caught rather than hidden.
    """
    tables: list[_TableIndex] = []
    measures: set[str] = set()
    measure_file = ""
    relationships: list[tuple[str, str]] = []
    model_file = ""
    relationship_file = ""
    pages: dict[str, dict[str, Any]] = {}
    visuals: dict[str, list[dict[str, Any]]] = {}
    report_filters: list[dict[str, Any]] = []
    page_filters: dict[str, list[dict[str, Any]]] = {}
    visual_filters: dict[str, list[dict[str, Any]]] = {}
    page_visual_interactions: dict[str, list[dict[str, Any]]] = {}
    report_file = ""

    for file in project.files:
        path = file.relative_path
        kind = file.kind
        if kind == "tmdl":
            if path.endswith("/model.tmdl") or path.endswith("model.tmdl"):
                model_file = path
                relationships.extend(_parse_relationships(file.content))
            elif path.endswith("/relationships.tmdl") or path.endswith("relationships.tmdl"):
                relationship_file = path
                relationships.extend(_parse_relationships_current(file.content))
            elif "/tables/" in path or path.endswith(".tmdl"):
                table = _parse_table_file(path, file.content)
                if table is not None:
                    tables.append(table)
                    file_measures = _parse_measures(file.content)
                    if file_measures:
                        measures.update(file_measures)
                        measure_file = path
        elif kind == "pbir_json":
            data = _safe_json(path, file.content)
            if data is None:
                continue
            if path.endswith("/report.json") or path.endswith("report.json"):
                report_file = path
                config = data.get("filterConfig", {})
                filters = config.get("filters", []) if isinstance(config, dict) else []
                if isinstance(filters, list):
                    report_filters = [f for f in filters if isinstance(f, dict)]
            elif path.endswith("/page.json") or path.endswith("page.json"):
                page_name = data.get("name", "")
                if isinstance(page_name, str) and page_name:
                    pages[page_name] = data
                    config = data.get("filterConfig", {})
                    filters = config.get("filters", []) if isinstance(config, dict) else []
                    if isinstance(filters, list):
                        page_filters[page_name] = [f for f in filters if isinstance(f, dict)]
                    interactions = data.get("visualInteractions", [])
                    if isinstance(interactions, list):
                        page_visual_interactions[page_name] = [
                            item for item in interactions if isinstance(item, dict)
                        ]
            elif path.endswith("/visual.json") or path.endswith("visual.json"):
                # Parent page folder is two levels up: .../<page>/visuals/<vid>/visual.json
                page_name = _parent_page_name(path)
                if page_name is not None:
                    visuals.setdefault(page_name, []).append(data)
                    config = data.get("filterConfig", {})
                    filters = config.get("filters", []) if isinstance(config, dict) else []
                    if isinstance(filters, list):
                        visual_filters.setdefault(page_name, []).extend(
                            item for item in filters if isinstance(item, dict)
                        )

    return _ArtifactIndex(
        tables=tuple(tables),
        measures=frozenset(measures),
        measure_file=measure_file,
        relationships=tuple(relationships),
        model_file=model_file,
        relationship_file=relationship_file,
        pages=pages,
        visuals=visuals,
        report_filters=report_filters,
        page_filters=page_filters,
        visual_filters=visual_filters,
        page_visual_interactions=page_visual_interactions,
        report_file=report_file,
    )


def _parent_page_name(visual_path: str) -> str | None:
    """Extract the PBIR page folder name from a ``visual.json`` relative path."""
    parts = visual_path.split("/")
    # Expect [..., <page>, "visuals", <vid>, "visual.json"]
    if len(parts) < 4:
        return None
    return parts[-4]


def _safe_json(path: str, content: str) -> dict[str, Any] | None:
    """Parse JSON content, returning ``None`` (never raising) on failure."""
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _parse_table_file(path: str, content: str) -> _TableIndex | None:
    """Parse one TMDL table file into a :class:`_TableIndex`."""
    header = _TABLE_HEADER_RE.search(content)
    if header is None:
        return None
    name = _strip_quotes(header.group(1))
    columns: set[str] = set()
    for match in _COLUMN_RE.finditer(content):
        columns.add(_strip_quotes(match.group(1).strip()))
    return _TableIndex(name=name, columns=frozenset(columns), file_path=path)


def _parse_measures(content: str) -> set[str]:
    """Extract measure names declared in a TMDL table file."""
    return {match.group(1) for match in _MEASURE_RE.finditer(content)}


def _parse_relationships(content: str) -> list[tuple[str, str]]:
    """Extract ``(from_entity, to_entity)`` pairs from a ``model.tmdl`` body."""
    pairs: list[tuple[str, str]] = []
    for match in _RELATIONSHIP_RE.finditer(content):
        from_entity = match.group(1) or _strip_quotes(match.group(2) or "")
        to_entity = match.group(4) or _strip_quotes(match.group(5) or "")
        if from_entity and to_entity:
            pairs.append((from_entity, to_entity))
    return pairs


def _parse_tmdl_column_ref(value: str) -> tuple[str, str]:
    """Parse current TMDL ``Table.Column`` / ``'Table Name'.Column`` refs."""
    text = value.strip()
    if not text or "." not in text:
        return "", ""
    table, column = text.rsplit(".", 1)
    return _strip_quotes(table), _strip_quotes(column)


def _parse_relationships_current(content: str) -> list[tuple[str, str]]:
    """Extract endpoint pairs from current Desktop ``relationships.tmdl``."""
    pairs: list[tuple[str, str]] = []
    from_ref = ""
    to_ref = ""
    for raw in content.splitlines():
        line = raw.strip()
        if line.startswith("relationship "):
            if from_ref and to_ref:
                from_table, _ = _parse_tmdl_column_ref(from_ref)
                to_table, _ = _parse_tmdl_column_ref(to_ref)
                if from_table and to_table:
                    pairs.append((from_table, to_table))
            from_ref = to_ref = ""
        elif line.startswith("fromColumn:"):
            from_ref = line.partition(":")[2].strip()
        elif line.startswith("toColumn:"):
            to_ref = line.partition(":")[2].strip()
    if from_ref and to_ref:
        from_table, _ = _parse_tmdl_column_ref(from_ref)
        to_table, _ = _parse_tmdl_column_ref(to_ref)
        if from_table and to_table:
            pairs.append((from_table, to_table))
    return pairs


# ---------------------------------------------------------------------------
# Verdict helpers
# ---------------------------------------------------------------------------


def _evidence(path: str, description: str) -> EvidenceRef:
    """Build a structural-audit evidence ref (path sanitized by EvidenceRef)."""
    return EvidenceRef(kind="structural_audit", locator=path, description=description)


def _native_verdict(
    *,
    element_kind: str,
    name: str,
    source_id: str,
    destination: str,
    confidence: float,
    evidence: tuple[EvidenceRef, ...],
    manual_review: bool = False,
) -> CapabilityVerdict:
    return CapabilityVerdict(
        element=SourceElementRef(element_kind=element_kind, name=name, source_id=source_id),
        destination=destination,
        support_level=SupportLevel.NATIVE,
        confidence=confidence,
        evidence=evidence,
        manual_review=manual_review,
    )


def _non_native_verdict(
    *,
    element_kind: str,
    name: str,
    source_id: str,
    destination: str,
    level: SupportLevel,
    reason_code: ReasonCode,
    reason: str,
    confidence: float,
    evidence: tuple[EvidenceRef, ...] = (),
    manual_review: bool = False,
) -> CapabilityVerdict:
    if level is SupportLevel.NATIVE:
        raise ValueError("use _native_verdict for NATIVE verdicts")
    return CapabilityVerdict(
        element=SourceElementRef(element_kind=element_kind, name=name, source_id=source_id),
        destination=destination,
        support_level=level,
        reason_code=reason_code,
        reason=reason,
        confidence=confidence,
        evidence=evidence,
        manual_review=manual_review,
    )


# ---------------------------------------------------------------------------
# Per-scope oracles
# ---------------------------------------------------------------------------


def assess_structure(
    model: SemanticModel,
    index: _ArtifactIndex,
    *,
    destination: str,
    tol: Tolerances,
) -> list[CapabilityVerdict]:
    """Source-scope oracle: model structure (entities, fields, metrics, groups).

    A first-class destination representation of an entity/field/metric is a
    structural fact observable in the generated files, so it may be ``NATIVE``.
    Missing elements are ``UNSUPPORTED``; partially-present entities (some
    fields missing) downgrade to ``PARTIAL_SUPPORT``.
    """
    verdicts: list[CapabilityVerdict] = []

    for entity in model.entities:
        table = index.table(entity.name)
        ev = (
            (_evidence(table.file_path, f"table '{entity.name}' structural audit"),)
            if table
            else ()
        )
        if table is None:
            verdicts.append(
                _non_native_verdict(
                    element_kind="datasource",
                    name=entity.name,
                    source_id=f"entity:{entity.name}",
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.NO_NATIVE_EQUIVALENT,
                    reason=f"entity {entity.name!r} has no table in the generated model",
                    confidence=0.2,
                    manual_review=True,
                )
            )
            continue
        missing = [f.name for f in entity.fields if f.name not in table.columns]
        if missing:
            verdicts.append(
                _non_native_verdict(
                    element_kind="datasource",
                    name=entity.name,
                    source_id=f"entity:{entity.name}",
                    destination=destination,
                    level=SupportLevel.EXTENDED,
                    reason_code=ReasonCode.PARTIAL_SUPPORT,
                    reason=(
                        f"entity {entity.name!r} present but {len(missing)} field(s) missing: "
                        f"{missing}"
                    ),
                    confidence=tol.confidence_extended,
                    evidence=ev,
                )
            )
        else:
            verdicts.append(
                _native_verdict(
                    element_kind="datasource",
                    name=entity.name,
                    source_id=f"entity:{entity.name}",
                    destination=destination,
                    confidence=tol.confidence_native,
                    evidence=ev,
                )
            )
        # Per-field verdicts (groups/grain): key fields define the entity grain.
        for fld in entity.fields:
            field_ev = (_evidence(table.file_path, f"column '{entity.name}.{fld.name}' present"),)
            if fld.name in table.columns:
                verdicts.append(
                    _native_verdict(
                        element_kind="layout",
                        name=f"{entity.name}.{fld.name}",
                        source_id=f"field:{entity.name}:{fld.name}",
                        destination=destination,
                        confidence=tol.confidence_native,
                        evidence=field_ev,
                    )
                )
            else:
                verdicts.append(
                    _non_native_verdict(
                        element_kind="layout",
                        name=f"{entity.name}.{fld.name}",
                        source_id=f"field:{entity.name}:{fld.name}",
                        destination=destination,
                        level=SupportLevel.UNSUPPORTED,
                        reason_code=ReasonCode.SEMANTIC_MISMATCH,
                        reason=f"column {entity.name}.{fld.name!r} not found in table",
                        confidence=0.2,
                        evidence=field_ev,
                        manual_review=True,
                    )
                )

    for metric in model.metrics:
        ev_file = index.measure_file or index.model_file
        ev = (_evidence(ev_file, f"measure '{metric.name}' structural audit"),) if ev_file else ()
        if metric.name in index.measures:
            verdicts.append(
                _native_verdict(
                    element_kind="calculated_field",
                    name=metric.name,
                    source_id=f"measure:{metric.name}",
                    destination=destination,
                    confidence=tol.confidence_native,
                    evidence=ev,
                )
            )
        else:
            verdicts.append(
                _non_native_verdict(
                    element_kind="calculated_field",
                    name=metric.name,
                    source_id=f"measure:{metric.name}",
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.NO_NATIVE_EQUIVALENT,
                    reason=f"measure {metric.name!r} not declared in the generated model",
                    confidence=0.2,
                    evidence=ev,
                    manual_review=True,
                )
            )

    for rel in model.relationships:
        ev_file = index.relationship_file or index.model_file
        ev = (_evidence(ev_file, f"relationship '{rel.name}' audit"),) if ev_file else ()
        pair = (rel.from_entity, rel.to_entity)
        if pair in index.relationships:
            verdicts.append(
                _native_verdict(
                    element_kind="relationship",
                    name=rel.name,
                    source_id=f"relationship:{rel.name}",
                    destination=destination,
                    confidence=tol.confidence_native,
                    evidence=ev,
                )
            )
        else:
            verdicts.append(
                _non_native_verdict(
                    element_kind="relationship",
                    name=rel.name,
                    source_id=f"relationship:{rel.name}",
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.SEMANTIC_MISMATCH,
                    reason=(
                        f"relationship {rel.name!r} ({rel.from_entity}->{rel.to_entity}) "
                        "not declared in relationships.tmdl/model.tmdl"
                    ),
                    confidence=0.2,
                    evidence=ev,
                    manual_review=True,
                )
            )

    for param in model.parameters:
        # Parameters have no first-class TMDL slot emitted by the adapter yet;
        # surface them honestly as a known target limitation needing review.
        verdicts.append(
            _non_native_verdict(
                element_kind="parameter",
                name=param.name,
                source_id=f"parameter:{param.name}",
                destination=destination,
                level=SupportLevel.APPROXIMATE,
                reason_code=ReasonCode.TARGET_LIMITATION,
                reason=(
                    f"parameter {param.name!r} has no native PBIP slot emitted; "
                    "requires manual authoring"
                ),
                confidence=tol.confidence_approximate,
                manual_review=True,
            )
        )

    return verdicts


def assess_filters(
    model: SemanticModel,
    index: _ArtifactIndex,
    *,
    destination: str,
    tol: Tolerances,
) -> list[CapabilityVerdict]:
    """Source/context-scope oracle: model filters emitted through native PBIR filterConfig.

    Filters may legitimately live at report, page or visual scope, so the oracle
    searches all three without importing the adapter. Scope correctness is pinned
    separately by compiler tests.
    """
    verdicts: list[CapabilityVerdict] = []
    all_filters = list(index.report_filters)
    all_filters.extend(item for values in index.page_filters.values() for item in values)
    all_filters.extend(item for values in index.visual_filters.values() for item in values)
    preserved = {
        str(f.get("displayName") or f.get("name") or ""): f
        for f in all_filters
        if f.get("displayName") or f.get("name")
    }
    ev_file = index.report_file

    for flt in model.filters:
        name = flt.name
        record = preserved.get(name)
        ev = (_evidence(ev_file, f"filter '{name}' preservation audit"),) if ev_file else ()
        is_native_op = flt.operator in NATIVE_FILTER_OPS
        if record is None:
            verdicts.append(
                _non_native_verdict(
                    element_kind="filter",
                    name=name,
                    source_id=f"filter:{name}",
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.SEMANTIC_MISMATCH,
                    reason=f"filter {name!r} not emitted in PBIR filterConfig",
                    confidence=0.2,
                    evidence=ev,
                    manual_review=True,
                )
            )
        elif is_native_op:
            verdicts.append(
                _native_verdict(
                    element_kind="filter",
                    name=name,
                    source_id=f"filter:{name}",
                    destination=destination,
                    confidence=tol.confidence_native,
                    evidence=ev,
                )
            )
        else:
            verdicts.append(
                _non_native_verdict(
                    element_kind="filter",
                    name=name,
                    source_id=f"filter:{name}",
                    destination=destination,
                    level=SupportLevel.APPROXIMATE,
                    reason_code=ReasonCode.TARGET_LIMITATION,
                    reason=(
                        f"filter {name!r} preserved but operator {flt.operator!r} has no "
                        "direct PBIR equivalent"
                    ),
                    confidence=tol.confidence_approximate,
                    evidence=ev,
                )
            )
    return verdicts


def assess_presentation(
    presentation: PresentationIR | None,
    index: _ArtifactIndex,
    *,
    destination: str,
    tol: Tolerances,
) -> list[CapabilityVerdict]:
    """Page/visual-scope oracle: pages, visual layout and data bindings.

    Correlates source pages/visuals to PBIR pages/visuals by ``displayName``
    (pages) and by title/intent/binding signature (visuals). Layout fidelity is
    judged within :attr:`Tolerances.geometry_abs`; binding resolution is judged
    from the ``biBridge.bindings`` block the adapter preserves verbatim.
    """
    verdicts: list[CapabilityVerdict] = []
    if presentation is None:
        return verdicts

    for page in presentation.pages:
        display = page.display_name or page.name
        pbir_page = index.page_by_display(display)
        if pbir_page is None:
            verdicts.append(
                _non_native_verdict(
                    element_kind="worksheet",
                    name=page.name,
                    source_id=f"page:{page.name}",
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.NO_NATIVE_EQUIVALENT,
                    reason=f"page {display!r} has no PBIR page.json",
                    confidence=0.2,
                    manual_review=True,
                )
            )
            continue
        ev = (
            _evidence(
                str(pbir_page.get("name", "")),
                f"page '{display}' layout audit (displayName lookup)",
            ),
        )
        width_ok = tol.geometry_within(float(page.width), float(pbir_page.get("width", 0)))
        height_ok = tol.geometry_within(float(page.height), float(pbir_page.get("height", 0)))
        if width_ok and height_ok:
            verdicts.append(
                _native_verdict(
                    element_kind="worksheet",
                    name=page.name,
                    source_id=f"page:{page.name}",
                    destination=destination,
                    confidence=tol.confidence_native,
                    evidence=ev,
                )
            )
        else:
            verdicts.append(
                _non_native_verdict(
                    element_kind="worksheet",
                    name=page.name,
                    source_id=f"page:{page.name}",
                    destination=destination,
                    level=SupportLevel.APPROXIMATE,
                    reason_code=ReasonCode.KNOWN_FIDELITY_GAP,
                    reason=(
                        f"page {display!r} present but dimensions differ "
                        f"(source {page.width}x{page.height} vs PBIR "
                        f"{pbir_page.get('width')}x{pbir_page.get('height')})"
                    ),
                    confidence=tol.confidence_approximate,
                    evidence=ev,
                )
            )
        verdicts.extend(_assess_visuals(page, index, destination=destination, tol=tol))
    return verdicts


_INTENT_TO_PBIR: dict[str, tuple[str, bool]] = {
    "bar": ("clusteredBarChart", True),
    "column": ("clusteredColumnChart", True),
    "line": ("lineChart", True),
    "area": ("areaChart", True),
    "scatter": ("scatterChart", True),
    "pie": ("pieChart", True),
    "donut": ("doughnutChart", True),
    "treemap": ("treemap", True),
    "map": ("azureMap", True),
    "table": ("tableEx", True),
    "pivot_matrix": ("pivotTable", True),
    "waterfall": ("waterfallChart", True),
    "slicer_filter": ("slicer", True),
    "text_box": ("textbox", True),
    "image": ("image", True),
    "kpi_card": ("card", False),
    "heatmap": ("tableEx", False),
    "gauge": ("card", False),
    "container": ("group", False),
    "custom_visual": ("textbox", False),
}


def _native_visual_fields(visual_json: dict[str, Any]) -> tuple[str, ...]:
    """Extract bound field/measure names from native PBIR queryState."""
    query = visual_json.get("visual", {}).get("query", {})
    query_state = query.get("queryState", {}) if isinstance(query, dict) else {}
    fields: list[str] = []
    for role in query_state.values() if isinstance(query_state, dict) else []:
        projections = role.get("projections", []) if isinstance(role, dict) else []
        for projection in projections if isinstance(projections, list) else []:
            field = projection.get("field", {}) if isinstance(projection, dict) else {}
            expression = field.get("Measure") or field.get("Column") or {}
            name = expression.get("Property") if isinstance(expression, dict) else None
            if name:
                fields.append(str(name))
    return tuple(sorted(fields))


def _native_visual_title(visual_json: dict[str, Any]) -> str:
    """Extract the title literal emitted in PBIR visual-container objects."""
    try:
        value = visual_json["visual"]["visualContainerObjects"]["title"][0]["properties"][
            "text"
        ]["expr"]["Literal"]["Value"]
    except (KeyError, IndexError, TypeError):
        return ""
    return str(value).strip("'")


def _visual_signature(visual_json: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    """Build a correlation signature exclusively from schema-valid PBIR."""
    visual_type = str(visual_json.get("visual", {}).get("visualType", ""))
    return (visual_type, _native_visual_fields(visual_json), _native_visual_title(visual_json))


def _source_visual_signature(visual: Any) -> tuple[str, tuple[str, ...], str]:
    """Build the same signature from a source :class:`VisualPresentation`."""
    intent = str(visual.intent.value) if hasattr(visual.intent, "value") else str(visual.intent)
    visual_type = _INTENT_TO_PBIR.get(intent, ("textbox", False))[0]
    fields = tuple(sorted(b.field_name for b in visual.bindings))
    return (visual_type, fields, visual.title)


def _assess_visuals(
    page: Any,
    index: _ArtifactIndex,
    *,
    destination: str,
    tol: Tolerances,
) -> list[CapabilityVerdict]:
    """Per-visual oracle: presence, layout geometry and binding resolution."""
    verdicts: list[CapabilityVerdict] = []
    # Index PBIR visuals of this page by both displayName-style title and signature.
    pbir_visuals = index.visuals.get(_find_pbir_page_name(index, page), [])
    by_title: dict[str, dict[str, Any]] = {}
    by_signature: dict[tuple[str, tuple[str, ...], str], dict[str, Any]] = {}
    for vj in pbir_visuals:
        title = _native_visual_title(vj)
        if title:
            by_title.setdefault(title, vj)
        sig = _visual_signature(vj)
        by_signature.setdefault(sig, vj)

    for visual in page.visuals:
        vev: tuple[EvidenceRef, ...] = ()
        correlated = by_title.get(visual.title) if visual.title else None
        if correlated is None:
            correlated = by_signature.get(_source_visual_signature(visual))
        if correlated is None:
            verdicts.append(
                _non_native_verdict(
                    element_kind="visual",
                    name=visual.visual_id,
                    source_id=f"visual:{page.page_id}:{visual.visual_id}",
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.NO_NATIVE_EQUIVALENT,
                    reason=(
                        f"visual {visual.visual_id!r} on page {page.name!r} has no "
                        "correlated PBIR visual.json"
                    ),
                    confidence=0.2,
                    manual_review=True,
                )
            )
            continue
        name = str(correlated.get("name", visual.visual_id))
        vev = (_evidence(name, f"visual '{visual.visual_id}' layout audit"),)
        intent = str(visual.intent.value) if hasattr(visual.intent, "value") else str(visual.intent)
        expected_type, is_native_intent = _INTENT_TO_PBIR.get(intent, ("textbox", False))
        actual_type = str(correlated.get("visual", {}).get("visualType", ""))
        is_native_intent = is_native_intent and actual_type == expected_type
        geometry_ok = _geometry_ok(correlated, visual, tol)
        bindings_ok, binding_reason = _bindings_ok(correlated, visual)
        if is_native_intent and geometry_ok and bindings_ok:
            verdicts.append(
                _native_verdict(
                    element_kind="visual",
                    name=visual.visual_id,
                    source_id=f"visual:{page.page_id}:{visual.visual_id}",
                    destination=destination,
                    confidence=tol.confidence_native,
                    evidence=vev,
                )
            )
            continue
        # Downgrade: pick the most informative reason.
        if not bindings_ok:
            level = SupportLevel.UNSUPPORTED
            reason_code = ReasonCode.SEMANTIC_MISMATCH
            reason = binding_reason
            confidence = 0.2
            manual = True
        elif not is_native_intent:
            level = SupportLevel.APPROXIMATE
            reason_code = ReasonCode.PARTIAL_SUPPORT
            reason = f"visual intent {intent!r} mapped to a non-native PBIR visualType"
            confidence = tol.confidence_approximate
            manual = False
        else:
            level = SupportLevel.APPROXIMATE
            reason_code = ReasonCode.KNOWN_FIDELITY_GAP
            reason = "visual present but layout geometry outside tolerance"
            confidence = tol.confidence_approximate
            manual = False
        verdicts.append(
            _non_native_verdict(
                element_kind="visual",
                name=visual.visual_id,
                source_id=f"visual:{page.page_id}:{visual.visual_id}",
                destination=destination,
                level=level,
                reason_code=reason_code,
                reason=reason,
                confidence=confidence,
                evidence=vev,
                manual_review=manual,
            )
        )
    return verdicts


def _find_pbir_page_name(index: _ArtifactIndex, page: Any) -> str:
    """Resolve the PBIR page folder name that holds ``page``'s visuals."""
    display = page.display_name or page.name
    pbir_page = index.page_by_display(display)
    if pbir_page is not None:
        return str(pbir_page.get("name", ""))
    return ""


def _geometry_ok(correlated: dict[str, Any], visual: Any, tol: Tolerances) -> bool:
    """True when the PBIR ``position`` matches the source geometry within band."""
    position = correlated.get("position", {})
    if not isinstance(position, dict) or not position:
        return False
    geom = visual.geometry
    checks = (
        tol.geometry_within(float(geom.x), float(position.get("x", 0.0))),
        tol.geometry_within(float(geom.y), float(position.get("y", 0.0))),
        tol.geometry_within(float(geom.width), float(position.get("width", 0.0))),
        tol.geometry_within(float(geom.height), float(position.get("height", 0.0))),
    )
    return all(checks)


def _bindings_ok(visual_json: dict[str, Any], visual: Any) -> tuple[bool, str]:
    """Compare source bindings with the native PBIR prototype query."""
    source = tuple(sorted(binding.field_name for binding in visual.bindings))
    emitted = _native_visual_fields(visual_json)
    if source != emitted:
        return False, f"native binding mismatch (source {source!r} vs PBIR {emitted!r})"
    return True, ""


def assess_context(
    interaction: InteractionIR | None,
    index: _ArtifactIndex,
    *,
    destination: str,
    tol: Tolerances,
    presentation: PresentationIR | None = None,
) -> list[CapabilityVerdict]:
    """Context-scope oracle: native PBIR filters and visual interaction edges.

    Interaction filters may be emitted at report/page/visual scope. Explicit
    cross-filter/highlight/none rules are emitted as page ``visualInteractions``.
    Unsupported interaction kinds remain outside this oracle's native claims.

    ``presentation`` provides the page_id -> displayName map used to correlate
    an interaction page to its PBIR page (the adapter correlates interaction to
    presentation by ``page_id``, and presentation to PBIR by ``displayName``).
    """
    verdicts: list[CapabilityVerdict] = []
    if interaction is None:
        return verdicts

    # Map interaction page_id -> PBIR displayName via the presentation, so page
    # filters can be located even though the index keys pages by PBIR hex name.
    page_display: dict[str, str] = {}
    if presentation is not None:
        for p in presentation.pages:
            page_display[p.page_id] = p.display_name or p.name

    # Global interaction filters (workbook scope) -> reported via report.json filter list.
    preserved_global = {
        str(f.get("displayName") or f.get("name") or ""): f
        for f in index.report_filters
        if f.get("displayName") or f.get("name")
    }
    ev_file = index.report_file
    for gf in interaction.global_filters:
        op_value = gf.operator.value if hasattr(gf.operator, "value") else str(gf.operator)
        record = preserved_global.get(gf.name)
        ev = (_evidence(ev_file, f"global filter '{gf.name}' audit"),) if ev_file else ()
        is_native_op = op_value in NATIVE_FILTER_OPS
        if record is None:
            verdicts.append(
                _non_native_verdict(
                    element_kind="filter",
                    name=gf.name,
                    source_id=f"global_filter:{gf.name}",
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.SEMANTIC_MISMATCH,
                    reason=f"global filter {gf.name!r} not emitted in report filterConfig",
                    confidence=0.2,
                    evidence=ev,
                    manual_review=True,
                )
            )
        elif is_native_op:
            verdicts.append(
                _native_verdict(
                    element_kind="filter",
                    name=gf.name,
                    source_id=f"global_filter:{gf.name}",
                    destination=destination,
                    confidence=tol.confidence_native,
                    evidence=ev,
                )
            )
        else:
            verdicts.append(
                _non_native_verdict(
                    element_kind="filter",
                    name=gf.name,
                    source_id=f"global_filter:{gf.name}",
                    destination=destination,
                    level=SupportLevel.APPROXIMATE,
                    reason_code=ReasonCode.TARGET_LIMITATION,
                    reason=(
                        f"global filter {gf.name!r} preserved but operator {op_value!r} "
                        "has no direct PBIR equivalent"
                    ),
                    confidence=tol.confidence_approximate,
                    evidence=ev,
                )
            )

    # Page/visual-level interaction filters -> native PBIR filterConfig.
    for page in interaction.pages:
        display = page_display.get(page.page_id, page.page_id)
        page_filters = _page_filters_for(index, display) + _visual_filters_for(index, display)
        preserved = {
            str(f.get("displayName") or f.get("name") or ""): f
            for f in page_filters
            if f.get("displayName") or f.get("name")
        }
        for flt in page.filters:
            op_value = flt.operator.value if hasattr(flt.operator, "value") else str(flt.operator)
            record = preserved.get(flt.name)
            is_native_op = op_value in NATIVE_FILTER_OPS
            if record is None:
                level = SupportLevel.UNSUPPORTED
                reason_code = ReasonCode.SEMANTIC_MISMATCH
                reason = f"page filter {flt.name!r} on {page.page_id!r} not preserved in page.json"
                confidence = 0.2
                manual = True
                level_final = level
            elif is_native_op:
                verdicts.append(
                    _native_verdict(
                        element_kind="filter",
                        name=flt.name,
                        source_id=f"page_filter:{page.page_id}:{flt.name}",
                        destination=destination,
                        confidence=tol.confidence_native,
                        evidence=(),
                    )
                )
                continue
            else:
                level_final = SupportLevel.APPROXIMATE
                reason_code = ReasonCode.TARGET_LIMITATION
                reason = (
                    f"page filter {flt.name!r} preserved but operator {op_value!r} has no "
                    "direct PBIR equivalent"
                )
                confidence = tol.confidence_approximate
                manual = False
            verdicts.append(
                _non_native_verdict(
                    element_kind="filter",
                    name=flt.name,
                    source_id=f"page_filter:{page.page_id}:{flt.name}",
                    destination=destination,
                    level=level_final,
                    reason_code=reason_code,
                    reason=reason,
                    confidence=confidence,
                    manual_review=manual,
                )
            )
        # Explicit cross-filter/highlight/none edges -> page.visualInteractions.
        page_json = _page_for_display(index, display)
        emitted_edges = page_json.get("visualInteractions", []) if page_json is not None else []
        emitted = {
            (str(edge.get("source", "")), str(edge.get("target", "")), str(edge.get("type", "")))
            for edge in emitted_edges
            if isinstance(edge, dict)
        }
        presentation_page = None
        if presentation is not None:
            presentation_page = next((p for p in presentation.pages if p.page_id == page.page_id), None)
        for cf in page.cross_filters:
            behavior = cf.behavior.value if hasattr(cf.behavior, "value") else str(cf.behavior)
            pbir_type = {"filter": "DataFilter", "highlight": "HighlightFilter", "none": "NoFilter"}.get(
                behavior
            )
            if presentation_page is None or pbir_type is None or not cf.target_visual_ids:
                verdicts.append(
                    _non_native_verdict(
                        element_kind="layout",
                        name=cf.rule_id,
                        source_id=f"cross_filter:{page.page_id}:{cf.rule_id}",
                        destination=destination,
                        level=SupportLevel.APPROXIMATE,
                        reason_code=ReasonCode.NEEDS_MANUAL_VERIFICATION,
                        reason=(
                            f"cross-filter rule {cf.rule_id!r} lacks an explicit PBIR edge "
                            "(page/targets/behavior incomplete); default interaction requires review"
                        ),
                        confidence=tol.confidence_approximate,
                        manual_review=True,
                    )
                )
                continue
            source = _pbir_visual_name(presentation_page.name, cf.source_visual_id)
            expected = {
                (source, _pbir_visual_name(presentation_page.name, target), pbir_type)
                for target in cf.target_visual_ids
            }
            if cf.bidirectional:
                expected |= {(target, source, pbir_type) for _, target, _ in tuple(expected)}
            if expected.issubset(emitted):
                verdicts.append(
                    _native_verdict(
                        element_kind="layout",
                        name=cf.rule_id,
                        source_id=f"cross_filter:{page.page_id}:{cf.rule_id}",
                        destination=destination,
                        confidence=tol.confidence_native,
                        evidence=(),
                    )
                )
            else:
                verdicts.append(
                    _non_native_verdict(
                        element_kind="layout",
                        name=cf.rule_id,
                        source_id=f"cross_filter:{page.page_id}:{cf.rule_id}",
                        destination=destination,
                        level=SupportLevel.UNSUPPORTED,
                        reason_code=ReasonCode.SEMANTIC_MISMATCH,
                        reason=f"cross-filter rule {cf.rule_id!r} missing native PBIR visualInteractions edge",
                        confidence=0.2,
                        manual_review=True,
                    )
                )
    return verdicts


def _page_for_display(index: _ArtifactIndex, display_name: str) -> dict[str, Any] | None:
    """Locate the emitted PBIR page shown as ``display_name``."""
    return index.page_by_display(display_name)


def _page_filters_for(index: _ArtifactIndex, display_name: str) -> list[dict[str, Any]]:
    """Locate native page.filterConfig filters by PBIR display name."""
    for pname, filters in index.page_filters.items():
        page_json = index.pages.get(pname, {})
        if str(page_json.get("displayName", "")) == display_name:
            return filters
    return []


def _visual_filters_for(index: _ArtifactIndex, display_name: str) -> list[dict[str, Any]]:
    """Locate native visual.filterConfig filters belonging to a PBIR page."""
    for pname, filters in index.visual_filters.items():
        page_json = index.pages.get(pname, {})
        if str(page_json.get("displayName", "")) == display_name:
            return filters
    return []


def _pbir_visual_name(page_name: str, visual_id: str) -> str:
    """Independently reproduce PBIR's deterministic visual name for oracle comparison."""
    return hashlib.sha256(f"visual\x1f{page_name}\x1f{visual_id}".encode()).hexdigest()[:20]


def assess_values(
    checks: tuple[ValueCheck, ...],
    *,
    destination: str,
    tol: Tolerances,
) -> list[CapabilityVerdict]:
    """Value/aggregation oracle (Python-side; **never** native DAX).

    Compares each :class:`ValueCheck` within the value tolerance band. A value
    within band is still only ``APPROXIMATE`` with
    :attr:`ReasonCode.NEEDS_MANUAL_VERIFICATION`, because Python recomputation
    is not destination DAX execution. A value outside band is ``UNSUPPORTED``
    with :attr:`ReasonCode.SEMANTIC_MISMATCH` and forces manual review.
    """
    verdicts: list[CapabilityVerdict] = []
    for check in checks:
        within = tol.value_within(check.expected, check.actual)
        if within:
            verdicts.append(
                _non_native_verdict(
                    element_kind=check.element_kind,
                    name=check.name,
                    source_id=check.source_id,
                    destination=destination,
                    level=SupportLevel.APPROXIMATE,
                    reason_code=ReasonCode.NEEDS_MANUAL_VERIFICATION,
                    reason=(
                        f"value within tolerance but validated in Python, not via DAX "
                        f"execution (expected={check.expected!r}, actual={check.actual!r})"
                    ),
                    confidence=tol.confidence_approximate,
                    manual_review=True,
                )
            )
        else:
            verdicts.append(
                _non_native_verdict(
                    element_kind=check.element_kind,
                    name=check.name,
                    source_id=check.source_id,
                    destination=destination,
                    level=SupportLevel.UNSUPPORTED,
                    reason_code=ReasonCode.SEMANTIC_MISMATCH,
                    reason=(
                        f"value outside tolerance (expected={check.expected!r}, "
                        f"actual={check.actual!r})"
                    ),
                    confidence=0.1,
                    manual_review=True,
                )
            )
    return verdicts


def assess_forecasts(
    forecasts: tuple[ForecastAssessment, ...],
    *,
    destination: str,
    tol: Tolerances,
) -> list[CapabilityVerdict]:
    """Forecast-contract oracle: preserve ``approximate`` until an external oracle exists.

    The forecast contract (:class:`ForecastAssessment`, wrapping a serialized
    :class:`~core.compilers.render_plan.ForecastSpec` plus the
    :func:`~core.forecast.execute_forecast` metadata) recomputes ETS forecasts
    in Python. Tableau's proprietary forecasting model values are **never**
    read, and no destination-side (DAX) forecast oracle exists yet. Therefore
    every forecast is reported :attr:`SupportLevel.APPROXIMATE` with
    :attr:`ReasonCode.NEEDS_MANUAL_VERIFICATION`, regardless of any recomputed
    number -- exactly mirroring the critical rule that Python-side semantics
    are never native DAX.

    The contract's declared ``limitations`` are surfaced verbatim in the reason
    text so reviewers see the known gaps (e.g. proprietary model selection).
    """
    verdicts: list[CapabilityVerdict] = []
    for fc in forecasts:
        algorithm = str(fc.metadata.get("algorithm", "unknown"))
        raw_limits = fc.metadata.get("limitations") or []
        limitations = [str(line).strip() for line in raw_limits if str(line).strip()]
        reason = "; ".join(
            [
                f"forecast {fc.name!r} backed by {algorithm}",
                "fidelity preserved as 'approximate'",
                "no destination forecast oracle exists; source model values not read",
            ]
            + [f"limitation: {line}" for line in limitations]
        )
        if fc.trace_path.strip():
            evidence: tuple[EvidenceRef, ...] = (
                EvidenceRef(
                    kind="calculation_trace",
                    locator=fc.trace_path,
                    description=f"forecast {fc.name!r} execution metadata trace",
                ),
            )
        else:
            # Opaque metadata id (kind 'other' is not path-sanitized); never
            # fabricates a file path, only identifies the backing metadata.
            evidence = (
                EvidenceRef(
                    kind="other",
                    locator=f"forecast_metadata:{fc.name}",
                    description=f"forecast {fc.name!r} execution metadata ({algorithm})",
                ),
            )
        verdicts.append(
            _non_native_verdict(
                element_kind="calculation",
                name=fc.name,
                source_id=fc.source_id,
                destination=destination,
                level=SupportLevel.APPROXIMATE,
                reason_code=ReasonCode.NEEDS_MANUAL_VERIFICATION,
                reason=reason,
                confidence=tol.confidence_approximate,
                evidence=evidence,
                manual_review=True,
            )
        )
    return verdicts


# ---------------------------------------------------------------------------
# Top-level oracle
# ---------------------------------------------------------------------------


class FidelityOracle:
    """Orchestrates the per-scope oracles into a :class:`FidelityReport`.

    The oracle is the bridge between the compiled destination artifact
    (:class:`PBIPProject`) and the capability/fidelity contract. It produces a
    versioned :class:`DestinationProfile` (one verdict per assessed element) and
    wraps it in a :class:`FidelityReport` whose summary is computed from the
    verdicts (the contract's source of truth).

    Acceptance follows the contract's golden rule: ``accepted`` is an explicit
    input (default ``False``) and an accepted report requires at least one
    evidence reference (report-level or on any verdict). The oracle always
    attaches structural-audit evidence to presence verdicts, so an accepted
    report is admissible as soon as at least one element was assessed.

    Attributes:
        destination: destination id (default :data:`DEFAULT_DESTINATION`).
        origin: :class:`~core.contracts.provenance.OriginKind` value of the
            assessed source.
        tolerances: tolerance band used by all per-scope oracles.
    """

    def __init__(
        self,
        *,
        destination: str = DEFAULT_DESTINATION,
        origin: str = "unknown",
        tolerances: Tolerances | None = None,
    ) -> None:
        if not destination or not isinstance(destination, str):
            raise ValueError("destination is required")
        self.destination = destination
        self.origin = origin
        self.tolerances = tolerances or Tolerances()

    def assess(
        self,
        model: SemanticModel,
        project: PBIPProject,
        *,
        presentation: PresentationIR | None = None,
        interaction: InteractionIR | None = None,
        value_checks: tuple[ValueCheck, ...] = (),
        forecasts: tuple[ForecastAssessment, ...] = (),
        accepted: bool = False,
        evidence: tuple[EvidenceRef, ...] = (),
        reviewed_by: str = "",
        notes: str = "",
    ) -> FidelityReport:
        """Assess ``model`` against ``project`` and return a :class:`FidelityReport`.

        Runs the source/page/visual/context, value and forecast oracles,
        assembles the verdicts into a :class:`DestinationProfile`, and builds
        the report via :meth:`FidelityReport.from_profile` (summary computed
        from verdicts).
        """
        index = build_index(project)
        verdicts: list[CapabilityVerdict] = []
        verdicts.extend(
            assess_structure(model, index, destination=self.destination, tol=self.tolerances)
        )
        verdicts.extend(
            assess_filters(model, index, destination=self.destination, tol=self.tolerances)
        )
        verdicts.extend(
            assess_presentation(
                presentation, index, destination=self.destination, tol=self.tolerances
            )
        )
        verdicts.extend(
            assess_context(
                interaction,
                index,
                destination=self.destination,
                tol=self.tolerances,
                presentation=presentation,
            )
        )
        verdicts.extend(
            assess_values(value_checks, destination=self.destination, tol=self.tolerances)
        )
        verdicts.extend(
            assess_forecasts(forecasts, destination=self.destination, tol=self.tolerances)
        )

        profile = DestinationProfile(
            schema_version=SCHEMA_VERSION,
            destination=self.destination,
            origin=self.origin,
            verdicts=tuple(verdicts),
            description=(
                f"Fidelity oracle assessment of {model.name!r} against destination "
                f"{self.destination!r}"
            ),
        )
        return FidelityReport.from_profile(
            profile,
            accepted=accepted,
            evidence=evidence,
            reviewed_by=reviewed_by,
            notes=notes,
        )


__all__ = [
    "DEFAULT_DESTINATION",
    "NATIVE_FILTER_OPS",
    "FidelityOracle",
    "ForecastAssessment",
    "Tolerances",
    "ValueCheck",
    "assess_context",
    "assess_filters",
    "assess_forecasts",
    "assess_presentation",
    "assess_structure",
    "assess_values",
    "build_index",
]
