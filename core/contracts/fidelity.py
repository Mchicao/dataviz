"""Versioned fidelity report contract.

Aggregates a :class:`~core.contracts.capabilities.DestinationProfile` into a
self-contained fidelity report with denominators (per support level), reason
distribution, confidence statistics, the manual-review roster and evidence
references.

**Golden rule: acceptance is never inferred from JSON validity.** A
:class:`FidelityReport` carries an explicit ``accepted`` flag that defaults to
``False`` and is read verbatim from the source data; successful deserialization
never implies acceptance. Accepting a report additionally requires at least one
evidence reference (report-level or on any verdict), so acceptance is tied to
explicit evidence rather than to parse success.

Design (Ponytail): stdlib dataclasses; reuses the capability and provenance
contracts. No PBIR / DAX / React / connector imports.

Schema Versioning Policy (SemVer): see :mod:`core.contracts.capabilities`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.contracts.capabilities import (
    CapabilityVerdict,
    DestinationProfile,
    EvidenceRef,
    SupportLevel,
)

SCHEMA_VERSION: str = "1.0.0"
"""Current schema version for the fidelity report contract (SemVer)."""


@dataclass(frozen=True)
class FidelitySummary:
    """Aggregate counts over a profile's verdicts (the report's denominators).

    Attributes:
        total: denominator -- number of verdicts assessed.
        native / extended / approximate / unsupported: counts per support level
            (their sum must equal :attr:`total`).
        manual_review: count of verdicts flagged for manual review.
        reason_code_counts: mapping ``reason_code value -> count``.
        min_confidence / mean_confidence: confidence statistics in ``[0, 1]``.
    """

    total: int = 0
    native: int = 0
    extended: int = 0
    approximate: int = 0
    unsupported: int = 0
    manual_review: int = 0
    reason_code_counts: dict[str, int] = field(default_factory=dict)
    min_confidence: float = 0.0
    mean_confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.total < 0:
            raise ValueError("total must be non-negative")
        for label, value in (
            ("native", self.native),
            ("extended", self.extended),
            ("approximate", self.approximate),
            ("unsupported", self.unsupported),
            ("manual_review", self.manual_review),
        ):
            if value < 0:
                raise ValueError(f"{label} must be non-negative")
        if self.native + self.extended + self.approximate + self.unsupported != self.total:
            raise ValueError("native + extended + approximate + unsupported must equal total")
        if self.manual_review > self.total:
            raise ValueError("manual_review cannot exceed total")
        for c in (self.min_confidence, self.mean_confidence):
            if not (0.0 <= c <= 1.0):
                raise ValueError(f"confidence stats must be in [0.0, 1.0], got {c!r}")

    def fraction(self, level: SupportLevel) -> float:
        """Return ``count(level) / total`` (0.0 when total == 0)."""
        count = {
            SupportLevel.NATIVE: self.native,
            SupportLevel.EXTENDED: self.extended,
            SupportLevel.APPROXIMATE: self.approximate,
            SupportLevel.UNSUPPORTED: self.unsupported,
        }[level]
        return count / self.total if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "total": self.total,
            "native": self.native,
            "extended": self.extended,
            "approximate": self.approximate,
            "unsupported": self.unsupported,
            "manual_review": self.manual_review,
            "reason_code_counts": dict(self.reason_code_counts),
            "min_confidence": self.min_confidence,
            "mean_confidence": self.mean_confidence,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FidelitySummary:
        """Reconstruct from a dict."""
        return cls(
            total=int(data.get("total", 0)),
            native=int(data.get("native", 0)),
            extended=int(data.get("extended", 0)),
            approximate=int(data.get("approximate", 0)),
            unsupported=int(data.get("unsupported", 0)),
            manual_review=int(data.get("manual_review", 0)),
            reason_code_counts={
                str(k): int(v) for k, v in data.get("reason_code_counts", {}).items()
            },
            min_confidence=float(data.get("min_confidence", 0.0)),
            mean_confidence=float(data.get("mean_confidence", 0.0)),
        )


def compute_summary(
    verdicts: list[CapabilityVerdict] | tuple[CapabilityVerdict, ...],
) -> FidelitySummary:
    """Compute a :class:`FidelitySummary` from verdicts (the source of truth).

    Counts each :class:`SupportLevel`, tallies reason codes, manual-review
    flags and confidence statistics. An empty verdict set yields a zero-total
    summary with ``min_confidence = mean_confidence = 0.0``.
    """
    counts = {
        SupportLevel.NATIVE: 0,
        SupportLevel.EXTENDED: 0,
        SupportLevel.APPROXIMATE: 0,
        SupportLevel.UNSUPPORTED: 0,
    }
    reason: dict[str, int] = {}
    manual = 0
    confs: list[float] = []
    for v in verdicts:
        counts[v.support_level] += 1
        reason[v.reason_code.value] = reason.get(v.reason_code.value, 0) + 1
        if v.manual_review:
            manual += 1
        confs.append(v.confidence)
    total = len(verdicts)
    if confs:
        mn = min(confs)
        mean = sum(confs) / total
    else:
        mn = 0.0
        mean = 0.0
    return FidelitySummary(
        total=total,
        native=counts[SupportLevel.NATIVE],
        extended=counts[SupportLevel.EXTENDED],
        approximate=counts[SupportLevel.APPROXIMATE],
        unsupported=counts[SupportLevel.UNSUPPORTED],
        manual_review=manual,
        reason_code_counts=reason,
        min_confidence=mn,
        mean_confidence=mean,
    )


@dataclass(frozen=True)
class FidelityReport:
    """Self-contained fidelity report for one destination migration.

    Golden rule: **acceptance is never inferred from JSON validity.**
    :attr:`accepted` defaults to ``False`` and is read verbatim from source
    data. Constructing an accepted report (``accepted=True``) requires at least
    one evidence reference (report-level or on any verdict); otherwise
    construction raises ``ValueError``. Acceptance is thus tied to explicit
    evidence, never to parse success.

    The stored :attr:`summary` must be consistent with :attr:`profile`; a
    mismatch raises ``ValueError`` on construction (tamper / drift detection).

    Attributes:
        schema_version: SemVer; must equal :data:`SCHEMA_VERSION`.
        profile: the assessed :class:`DestinationProfile`.
        summary: precomputed :class:`FidelitySummary` (validated for consistency).
        accepted: explicit acceptance flag; never derived from JSON validity.
        evidence: report-level evidence references (beyond per-verdict evidence).
        reviewed_by: identifier of the human/operator who reviewed (optional).
        notes: free-form notes.
    """

    schema_version: str
    profile: DestinationProfile
    summary: FidelitySummary = field(default_factory=FidelitySummary)
    accepted: bool = False
    evidence: tuple[EvidenceRef, ...] = ()
    reviewed_by: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, "
                f"received {self.schema_version!r}"
            )
        if not isinstance(self.profile, DestinationProfile):
            raise ValueError("FidelityReport.profile must be a DestinationProfile")
        if not isinstance(self.summary, FidelitySummary):
            raise ValueError("FidelityReport.summary must be a FidelitySummary")
        if not isinstance(self.evidence, tuple):
            object.__setattr__(self, "evidence", tuple(self.evidence))
        for ev in self.evidence:
            if not isinstance(ev, EvidenceRef):
                raise TypeError("evidence entries must be EvidenceRef")
        # Integrity: the stored summary must match a recompute from the profile.
        expected = compute_summary(self.profile.verdicts)
        if self.summary != expected:
            raise ValueError("summary does not match the profile's verdicts")
        # Golden rule: acceptance requires evidence (report-level or any verdict).
        if self.accepted and not self.has_evidence():
            raise ValueError(
                "an accepted report requires at least one evidence reference "
                "(report-level or on a verdict)"
            )

    @property
    def destination(self) -> str:
        """Destination id (delegated to the profile; no duplicated field)."""
        return self.profile.destination

    @property
    def origin(self) -> str:
        """Origin of the assessed source (delegated to the profile)."""
        return self.profile.origin

    def has_evidence(self) -> bool:
        """True if there is any report-level or per-verdict evidence reference."""
        if self.evidence:
            return True
        return any(v.evidence for v in self.profile.verdicts)

    @classmethod
    def from_profile(
        cls,
        profile: DestinationProfile,
        *,
        accepted: bool = False,
        evidence: tuple[EvidenceRef, ...] = (),
        reviewed_by: str = "",
        notes: str = "",
    ) -> FidelityReport:
        """Build a report from a profile, computing the summary automatically."""
        return cls(
            schema_version=SCHEMA_VERSION,
            profile=profile,
            summary=compute_summary(profile.verdicts),
            accepted=accepted,
            evidence=evidence,
            reviewed_by=reviewed_by,
            notes=notes,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "schema_version": self.schema_version,
            "profile": self.profile.to_dict(),
            "summary": self.summary.to_dict(),
            "accepted": self.accepted,
            "evidence": [ev.to_dict() for ev in self.evidence],
            "reviewed_by": self.reviewed_by,
            "notes": self.notes,
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize to canonical JSON (sorted keys, UTF-8 pure)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FidelityReport:
        """Rebuild a report from a dict.

        Enforces ``schema_version`` (no migration) and requires an explicit
        ``summary``. The ``accepted`` flag is read verbatim from the data and
        defaults to ``False`` when absent -- it is **never** inferred from the
        fact that parsing succeeded (golden rule).
        """
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, received {received!r}"
            )
        profile_raw = data.get("profile")
        if not isinstance(profile_raw, Mapping):
            raise ValueError("FidelityReport.profile must be a mapping")
        profile = DestinationProfile.from_dict(profile_raw)
        summary_raw = data.get("summary")
        if not isinstance(summary_raw, Mapping):
            raise ValueError("FidelityReport.summary is required and must be a mapping")
        summary = FidelitySummary.from_dict(summary_raw)
        return cls(
            schema_version=SCHEMA_VERSION,
            profile=profile,
            summary=summary,
            accepted=bool(data.get("accepted", False)),
            evidence=tuple(EvidenceRef.from_dict(ev) for ev in data.get("evidence", [])),
            reviewed_by=str(data.get("reviewed_by", "")),
            notes=str(data.get("notes", "")),
        )

    @classmethod
    def from_json(cls, text: str) -> FidelityReport:
        """Rebuild from canonical JSON (``from_dict(json.loads(text))``)."""
        return cls.from_dict(json.loads(text))


__all__ = [
    "SCHEMA_VERSION",
    "FidelityReport",
    "FidelitySummary",
    "compute_summary",
]
