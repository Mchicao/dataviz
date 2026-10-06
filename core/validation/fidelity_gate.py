"""Gate de fidelidad derivado desde evidencia, liveness y degradaciones."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from core.contracts.fidelity import FidelityReport
from core.validation.visual_similarity import ComparisonStatus, VisualComparison

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_HASHES = frozenset({"source", "dataset", "ir", "render_plan"})


class GateStatus(StrEnum):
    """Estados válidos del gate; sólo el evaluador produce ``accepted``."""

    PLANNED = "planned"
    READY = "ready"
    BLOCKED = "blocked"
    ACCEPTED = "accepted"


class LivenessPolicy(StrEnum):
    """Política de contenido visible por tipo de visual."""

    REQUIRES_MARKS = "requires_marks"
    ALLOWS_EMPTY_STATE = "allows_empty_state"
    DECORATIVE = "decorative"


class DegradationSeverity(StrEnum):
    """Severidad neutral de una degradación."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass(frozen=True)
class EvidenceEnvelope:
    """Identidad reproducible de una ejecución de validación."""

    hashes: Mapping[str, str]
    frontend_build_id: str
    captured_at: str
    viewport_width: int
    viewport_height: int
    device_pixel_ratio: float = 1.0
    locale: str = "en-US"
    timezone: str = "UTC"

    def __post_init__(self) -> None:
        normalized = {str(key): str(value).lower() for key, value in self.hashes.items()}
        missing = _REQUIRED_HASHES - normalized.keys()
        if missing:
            raise ValueError(f"missing evidence hashes: {sorted(missing)}")
        invalid = sorted(key for key, value in normalized.items() if not _HASH_RE.match(value))
        if invalid:
            raise ValueError(f"invalid SHA-256 evidence hashes: {invalid}")
        if not self.frontend_build_id.strip():
            raise ValueError("frontend_build_id is required")
        if self.viewport_width <= 0 or self.viewport_height <= 0:
            raise ValueError("viewport dimensions must be positive")
        if self.device_pixel_ratio <= 0:
            raise ValueError("device_pixel_ratio must be positive")
        if not self.locale.strip() or not self.timezone.strip():
            raise ValueError("locale and timezone are required")
        captured = _parse_timestamp(self.captured_at)
        if captured.tzinfo is None:
            raise ValueError("captured_at must include a timezone")
        object.__setattr__(self, "hashes", normalized)

    def problems(
        self,
        expected_hashes: Mapping[str, str],
        *,
        now: datetime | None = None,
        max_age: timedelta = timedelta(hours=24),
    ) -> tuple[str, ...]:
        """Devuelve contradicciones de identidad o frescura."""

        problems = [
            f"hash mismatch: {key}"
            for key, expected in expected_hashes.items()
            if self.hashes.get(key) != str(expected).lower()
        ]
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must include a timezone")
        captured = _parse_timestamp(self.captured_at)
        age = current - captured
        if age < timedelta(0):
            problems.append("evidence timestamp is in the future")
        elif age > max_age:
            problems.append("evidence is stale")
        return tuple(problems)

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> EvidenceEnvelope:
        return cls(
            hashes=dict(data.get("hashes", {})),  # type: ignore[arg-type]
            frontend_build_id=str(data.get("frontend_build_id", "")),
            captured_at=str(data.get("captured_at", "")),
            viewport_width=_as_int(data.get("viewport_width", 0)),
            viewport_height=_as_int(data.get("viewport_height", 0)),
            device_pixel_ratio=_as_float(data.get("device_pixel_ratio", 1.0)),
            locale=str(data.get("locale", "en-US")),
            timezone=str(data.get("timezone", "UTC")),
        )


@dataclass(frozen=True)
class VisualLiveness:
    """Filas y marcas observadas para un visual mostrado."""

    visual_id: str
    policy: LivenessPolicy
    row_count: int = 0
    mark_count: int = 0
    error: str = ""

    def __post_init__(self) -> None:
        if not self.visual_id.strip():
            raise ValueError("visual_id is required")
        if self.row_count < 0 or self.mark_count < 0:
            raise ValueError("row_count and mark_count must be non-negative")

    @property
    def blocker(self) -> str:
        if self.error.strip():
            return f"{self.visual_id}: {self.error.strip()}"
        if self.policy is LivenessPolicy.REQUIRES_MARKS and self.mark_count == 0:
            return f"{self.visual_id}: displayed visual has no marks"
        return ""

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> VisualLiveness:
        return cls(
            visual_id=str(data.get("visual_id", "")),
            policy=LivenessPolicy(str(data.get("policy", "requires_marks"))),
            row_count=_as_int(data.get("row_count", 0)),
            mark_count=_as_int(data.get("mark_count", 0)),
            error=str(data.get("error", "")),
        )


@dataclass(frozen=True)
class DegradationEntry:
    """Residual deduplicable; un waiver nunca borra el registro."""

    category: str
    element_id: str
    reason: str
    source_ref: str = ""
    severity: DegradationSeverity = DegradationSeverity.WARNING
    waiver_id: str = ""

    def __post_init__(self) -> None:
        if not self.category.strip() or not self.element_id.strip() or not self.reason.strip():
            raise ValueError("category, element_id and reason are required")

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.category, self.element_id, self.reason, self.source_ref

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> DegradationEntry:
        return cls(
            category=str(data.get("category", "")),
            element_id=str(data.get("element_id", "")),
            reason=str(data.get("reason", "")),
            source_ref=str(data.get("source_ref", "")),
            severity=DegradationSeverity(str(data.get("severity", "warning"))),
            waiver_id=str(data.get("waiver_id", "")),
        )


@dataclass(frozen=True)
class FidelityGateDecision:
    """Resultado inmutable y explicable del gate."""

    status: GateStatus
    blockers: tuple[str, ...] = ()
    pending: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    degradation_count: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.status is GateStatus.ACCEPTED

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "accepted": self.accepted,
            "blockers": list(self.blockers),
            "pending": list(self.pending),
            "warnings": list(self.warnings),
            "degradation_count": self.degradation_count,
            "metadata": dict(self.metadata),
        }


def derive_fidelity_gate(
    report: FidelityReport,
    *,
    evidence: EvidenceEnvelope | None = None,
    expected_hashes: Mapping[str, str] | None = None,
    liveness: tuple[VisualLiveness, ...] = (),
    degradations: tuple[DegradationEntry, ...] = (),
    comparisons: tuple[VisualComparison, ...] = (),
    now: datetime | None = None,
    max_evidence_age: timedelta = timedelta(hours=24),
) -> FidelityGateDecision:
    """Deriva el gate; ignora cualquier ``accepted`` autorreportado del informe."""

    if report.summary.total == 0:
        return FidelityGateDecision(
            status=GateStatus.PLANNED,
            pending=("no assessed capability verdicts",),
        )

    blockers: list[str] = []
    pending: list[str] = []
    warnings: list[str] = []
    if evidence is None:
        blockers.append("evidence envelope is missing")
    else:
        blockers.extend(
            evidence.problems(expected_hashes or {}, now=now, max_age=max_evidence_age)
        )

    if report.summary.unsupported:
        blockers.append(f"{report.summary.unsupported} unsupported capability verdict(s)")
    if report.summary.manual_review:
        blockers.append(f"{report.summary.manual_review} verdict(s) require manual review")

    if not liveness:
        pending.append("visual liveness capture pending")
    blockers.extend(record.blocker for record in liveness if record.blocker)

    unique_degradations = {item.key: item for item in degradations}.values()
    for item in unique_degradations:
        message = f"{item.element_id}: {item.reason}"
        if item.severity is DegradationSeverity.BLOCKING and not item.waiver_id:
            blockers.append(message)
        elif item.severity is not DegradationSeverity.INFO:
            warnings.append(message)

    if not comparisons:
        pending.append("visual comparison pending")
    for comparison in comparisons:
        if comparison.status is ComparisonStatus.FAILED:
            blockers.append(f"{comparison.page_id or '<page>'}: visual comparison failed")
        elif comparison.status is ComparisonStatus.PENDING:
            pending.append(f"{comparison.page_id or '<page>'}: visual comparison pending")
        elif comparison.status is ComparisonStatus.WARNING:
            warnings.append(f"{comparison.page_id or '<page>'}: visual comparison warning")

    if report.accepted and (blockers or pending):
        warnings.append("self-reported accepted flag was not trusted")

    if blockers:
        status = GateStatus.BLOCKED
    elif pending:
        status = GateStatus.READY
    else:
        status = GateStatus.ACCEPTED
    return FidelityGateDecision(
        status=status,
        blockers=tuple(dict.fromkeys(blockers)),
        pending=tuple(dict.fromkeys(pending)),
        warnings=tuple(dict.fromkeys(warnings)),
        degradation_count=len(tuple(unique_degradations)),
        metadata={"source_report_accepted": report.accepted},
    )


def derive_fidelity_gate_from_manifest(
    report: FidelityReport,
    manifest: Mapping[str, object],
    *,
    now: datetime | None = None,
) -> FidelityGateDecision:
    """Deserializa un manifest pequeño y ejecuta el gate derivado."""

    evidence_raw = manifest.get("evidence")
    evidence = (
        EvidenceEnvelope.from_dict(evidence_raw) if isinstance(evidence_raw, Mapping) else None
    )
    liveness_raw = manifest.get("liveness", [])
    degradations_raw = manifest.get("degradations", [])
    comparisons_raw = manifest.get("comparisons", [])
    return derive_fidelity_gate(
        report,
        evidence=evidence,
        expected_hashes=dict(manifest.get("expected_hashes", {})),  # type: ignore[arg-type]
        liveness=tuple(
            VisualLiveness.from_dict(item)
            for item in liveness_raw  # type: ignore[union-attr]
            if isinstance(item, Mapping)
        ),
        degradations=tuple(
            DegradationEntry.from_dict(item)
            for item in degradations_raw  # type: ignore[union-attr]
            if isinstance(item, Mapping)
        ),
        comparisons=tuple(
            VisualComparison.from_dict(dict(item))
            for item in comparisons_raw  # type: ignore[union-attr]
            if isinstance(item, Mapping)
        ),
        now=now,
    )


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("captured_at must be an ISO-8601 timestamp") from exc


def _as_int(value: object) -> int:
    return int(str(value))


def _as_float(value: object) -> float:
    return float(str(value))


__all__ = [
    "DegradationEntry",
    "DegradationSeverity",
    "EvidenceEnvelope",
    "FidelityGateDecision",
    "GateStatus",
    "LivenessPolicy",
    "VisualLiveness",
    "derive_fidelity_gate",
    "derive_fidelity_gate_from_manifest",
]
