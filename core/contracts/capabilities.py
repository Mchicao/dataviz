"""Versioned destination capability profiles contract.

Defines how each source element maps onto a destination's support, with
explicit reason codes, confidence, manual-review flags and evidence references.
The contract is passive (data only) and depends on the standard library plus
the sibling provenance contract (``OriginKind`` and ``sanitize_relative_path``).

Four support levels form a strict quality ordering (best to worst)::

    NATIVE > EXTENDED > APPROXIMATE > UNSUPPORTED

* **NATIVE** -- a first-class destination feature exists; no fidelity loss.
* **EXTENDED** -- achievable by composing destination features.
* **APPROXIMATE** -- only an approximation is possible; known fidelity gap.
* **UNSUPPORTED** -- no viable destination representation.

Design (Ponytail): reuses ``OriginKind`` and the path sanitizer from
:mod:`core.contracts.provenance`; stdlib dataclasses only. No PBIR, DAX, React
or connector imports.

Schema Versioning Policy (SemVer):

* **MAJOR**: breaking change (field removal/rename, type or semantic shift).
* **MINOR**: backward-compatible extension (new optional field with default).
* **PATCH**: internal fix preserving the serialized form.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from core.contracts.provenance import OriginKind, sanitize_relative_path

SCHEMA_VERSION: str = "1.0.0"
"""Current schema version for the destination capability contract (SemVer)."""


class SupportLevel(StrEnum):
    """Support level for a single source element on a given destination.

    Values form a strict quality ordering (best to worst): NATIVE > EXTENDED >
    APPROXIMATE > UNSUPPORTED. See :func:`support_rank`.
    """

    NATIVE = "native"
    EXTENDED = "extended"
    APPROXIMATE = "approximate"
    UNSUPPORTED = "unsupported"


_SUPPORT_RANK: dict[SupportLevel, int] = {
    SupportLevel.NATIVE: 0,
    SupportLevel.EXTENDED: 1,
    SupportLevel.APPROXIMATE: 2,
    SupportLevel.UNSUPPORTED: 3,
}


def support_rank(level: SupportLevel) -> int:
    """Return the ordinal rank of ``level`` (0 = NATIVE best, 3 = UNSUPPORTED worst).

    Raises:
        TypeError: If ``level`` is not a :class:`SupportLevel`.
    """
    if not isinstance(level, SupportLevel):
        raise TypeError(f"support_rank expects SupportLevel, got {type(level).__name__}")
    return _SUPPORT_RANK[level]


class ReasonCode(StrEnum):
    """Closed set of explanation codes for non-NATIVE verdicts.

    ``NONE`` is reserved for NATIVE verdicts. Every non-NATIVE
    :class:`CapabilityVerdict` must carry a code other than ``NONE``.
    """

    NONE = "none"
    NO_NATIVE_EQUIVALENT = "no_native_equivalent"
    REQUIRES_COMPOSITION = "requires_composition"
    KNOWN_FIDELITY_GAP = "known_fidelity_gap"
    SEMANTIC_MISMATCH = "semantic_mismatch"
    PARTIAL_SUPPORT = "partial_support"
    TARGET_LIMITATION = "target_limitation"
    NEEDS_MANUAL_VERIFICATION = "needs_manual_verification"


#: Informational catalog of canonical source element kinds. Any non-empty
#: string is accepted (forward-compatible), but these are the expected values.
SUPPORTED_ELEMENT_KINDS: frozenset[str] = frozenset(
    {
        "dashboard",
        "worksheet",
        "visual",
        "calculation",
        "calculated_field",
        "parameter",
        "filter",
        "datasource",
        "relationship",
        "rls",
        "layout",
    }
)

#: Closed set of evidence kinds a verdict/report may reference.
EVIDENCE_KINDS: frozenset[str] = frozenset(
    {
        "screenshot",
        "structural_audit",
        "test_output",
        "log",
        "source_node",
        "calculation_trace",
        "other",
    }
)

#: Evidence kinds whose ``locator`` is a file path and must be sanitized.
_PATH_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {
        "screenshot",
        "structural_audit",
        "test_output",
        "log",
        "calculation_trace",
    }
)


def _coerce_support_level(value: Any) -> SupportLevel:
    """Normalize ``value`` to :class:`SupportLevel`, accepting the enum or its string."""
    if isinstance(value, SupportLevel):
        return value
    if isinstance(value, str):
        try:
            return SupportLevel(value)
        except ValueError as exc:
            raise ValueError(
                f"invalid support_level: {value!r} "
                f"(expected one of {[s.value for s in SupportLevel]})"
            ) from exc
    raise TypeError(f"support_level must be str or SupportLevel, not {type(value).__name__}")


def _coerce_reason_code(value: Any) -> ReasonCode:
    """Normalize ``value`` to :class:`ReasonCode`, accepting the enum or its string."""
    if isinstance(value, ReasonCode):
        return value
    if isinstance(value, str):
        try:
            return ReasonCode(value)
        except ValueError as exc:
            raise ValueError(
                f"invalid reason_code: {value!r} (expected one of {[r.value for r in ReasonCode]})"
            ) from exc
    raise TypeError(f"reason_code must be str or ReasonCode, not {type(value).__name__}")


@dataclass(frozen=True)
class SourceElementRef:
    """Neutral reference to a single source element under assessment.

    Attributes:
        element_kind: canonical kind (see :data:`SUPPORTED_ELEMENT_KINDS`).
        name: human-readable element name (may be empty).
        source_id: stable id (e.g. a ``stable_node_id`` from the Source AST).
    """

    element_kind: str
    name: str = ""
    source_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.element_kind, str) or not self.element_kind.strip():
            raise ValueError("SourceElementRef.element_kind is required")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "element_kind": self.element_kind,
            "name": self.name,
            "source_id": self.source_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceElementRef:
        """Reconstruct from a dict."""
        return cls(
            element_kind=str(data["element_kind"]),
            name=str(data.get("name", "")),
            source_id=str(data.get("source_id", "")),
        )


@dataclass(frozen=True)
class EvidenceRef:
    """A reference to a piece of evidence backing a verdict or report.

    Path-like kinds (see :data:`_PATH_EVIDENCE_KINDS`) have their ``locator``
    sanitized via :func:`sanitize_relative_path`, rejecting absolute / UNC /
    drive-letter / ``..``-escape / embedded-credential paths. Non-path kinds
    (e.g. ``source_node``) carry an opaque id and are not path-sanitized.

    Attributes:
        kind: one of :data:`EVIDENCE_KINDS`.
        locator: sanitized relative path or stable id locating the evidence.
        description: free-form note.
    """

    kind: str
    locator: str
    description: str = ""

    def __post_init__(self) -> None:
        if self.kind not in EVIDENCE_KINDS:
            raise ValueError(
                f"evidence kind not allowed: {self.kind!r}; allowlist={sorted(EVIDENCE_KINDS)}"
            )
        if not isinstance(self.locator, str) or not self.locator.strip():
            raise ValueError("EvidenceRef.locator is required")
        if self.kind in _PATH_EVIDENCE_KINDS:
            # Reuse the provenance sanitizer: never persist absolute/UNC/cred paths.
            object.__setattr__(self, "locator", sanitize_relative_path(self.locator))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "kind": self.kind,
            "locator": self.locator,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EvidenceRef:
        """Reconstruct from a dict."""
        return cls(
            kind=str(data["kind"]),
            locator=str(data["locator"]),
            description=str(data.get("description", "")),
        )


@dataclass(frozen=True)
class CapabilityVerdict:
    """Assessment of one source element against one destination.

    Invariants (enforced in ``__post_init__``):

    * NATIVE verdicts carry no reason (``reason_code == NONE``, ``reason == ""``).
    * Non-NATIVE verdicts must set a reason code other than ``NONE`` and a
      non-empty ``reason`` text.
    * ``confidence`` is a number in ``[0.0, 1.0]`` (booleans rejected).

    Attributes:
        element: the source element being assessed.
        destination: destination profile id (e.g. ``"power_bi_pbip"``).
        support_level: :class:`SupportLevel` assigned to this element.
        reason_code: :class:`ReasonCode` (required when not NATIVE).
        reason: free-form explanation (required when not NATIVE).
        confidence: confidence in the verdict, in ``[0.0, 1.0]``.
        manual_review: True if this element needs human verification.
        evidence: evidence references backing the verdict.
    """

    element: SourceElementRef
    destination: str
    support_level: SupportLevel
    reason_code: ReasonCode = ReasonCode.NONE
    reason: str = ""
    confidence: float = 1.0
    manual_review: bool = False
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.element, SourceElementRef):
            raise ValueError("CapabilityVerdict.element must be a SourceElementRef")
        if not isinstance(self.destination, str) or not self.destination.strip():
            raise ValueError("CapabilityVerdict.destination is required")
        if not isinstance(self.support_level, SupportLevel):
            object.__setattr__(self, "support_level", _coerce_support_level(self.support_level))
        if not isinstance(self.reason_code, ReasonCode):
            object.__setattr__(self, "reason_code", _coerce_reason_code(self.reason_code))
        if not isinstance(self.evidence, tuple):
            object.__setattr__(self, "evidence", tuple(self.evidence))
        for ev in self.evidence:
            if not isinstance(ev, EvidenceRef):
                raise TypeError("evidence entries must be EvidenceRef")
        # Confidence bounds (inclusive); booleans are not valid confidence values.
        c = self.confidence
        if isinstance(c, bool) or not isinstance(c, (int, float)):
            raise TypeError("confidence must be a number in [0.0, 1.0]")
        if not (0.0 <= float(c) <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {c!r}")
        object.__setattr__(self, "confidence", float(c))
        level = self.support_level
        if level is SupportLevel.NATIVE:
            if self.reason_code is not ReasonCode.NONE:
                raise ValueError("a NATIVE verdict must use reason_code NONE")
            if self.reason.strip():
                raise ValueError("a NATIVE verdict must not carry a reason")
        else:
            if self.reason_code is ReasonCode.NONE:
                raise ValueError(
                    f"a {level.value!r} verdict requires a reason_code other than NONE"
                )
            if not self.reason.strip():
                raise ValueError(f"a {level.value!r} verdict requires a reason text")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "element": self.element.to_dict(),
            "destination": self.destination,
            "support_level": self.support_level.value,
            "reason_code": self.reason_code.value,
            "reason": self.reason,
            "confidence": self.confidence,
            "manual_review": self.manual_review,
            "evidence": [ev.to_dict() for ev in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CapabilityVerdict:
        """Reconstruct from a dict, validating all verdict invariants."""
        return cls(
            element=SourceElementRef.from_dict(data["element"]),
            destination=str(data["destination"]),
            support_level=_coerce_support_level(data["support_level"]),
            reason_code=_coerce_reason_code(data.get("reason_code", ReasonCode.NONE.value)),
            reason=str(data.get("reason", "")),
            confidence=float(data.get("confidence", 1.0)),
            manual_review=bool(data.get("manual_review", False)),
            evidence=tuple(EvidenceRef.from_dict(ev) for ev in data.get("evidence", [])),
        )


@dataclass(frozen=True)
class DestinationProfile:
    """Capability profile: the per-element verdicts for one destination.

    Immutable. Validates schema version, destination, closed origin, and
    uniqueness of element ``source_id`` (when present). Round-trips through
    JSON via :meth:`to_json` / :meth:`from_json`.

    Attributes:
        schema_version: SemVer; must equal :data:`SCHEMA_VERSION`.
        destination: destination profile id (e.g. ``"power_bi_pbip"``).
        origin: :class:`OriginKind` value of the assessed source artifact.
        verdicts: ordered capability verdicts.
        description: free-form documentation.
    """

    schema_version: str
    destination: str
    origin: str = OriginKind.UNKNOWN.value
    verdicts: tuple[CapabilityVerdict, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, "
                f"received {self.schema_version!r}"
            )
        if not isinstance(self.destination, str) or not self.destination.strip():
            raise ValueError("DestinationProfile.destination is required")
        allowed = {k.value for k in OriginKind}
        if self.origin not in allowed:
            raise ValueError(f"origin must be one of {sorted(allowed)}, got {self.origin!r}")
        if not isinstance(self.verdicts, tuple):
            object.__setattr__(self, "verdicts", tuple(self.verdicts))
        seen: set[str] = set()
        for v in self.verdicts:
            if not isinstance(v, CapabilityVerdict):
                raise TypeError("verdicts entries must be CapabilityVerdict")
            sid = v.element.source_id
            if sid:
                if sid in seen:
                    raise ValueError(f"duplicate element source_id in profile: {sid!r}")
                seen.add(sid)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "schema_version": self.schema_version,
            "destination": self.destination,
            "origin": self.origin,
            "description": self.description,
            "verdicts": [v.to_dict() for v in self.verdicts],
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        """Serialize to canonical JSON (sorted keys, UTF-8 pure)."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DestinationProfile:
        """Rebuild a profile from a dict, enforcing ``schema_version`` (no migration)."""
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: expected {SCHEMA_VERSION!r}, received {received!r}"
            )
        return cls(
            schema_version=SCHEMA_VERSION,
            destination=str(data["destination"]),
            origin=str(data.get("origin", OriginKind.UNKNOWN.value)),
            description=str(data.get("description", "")),
            verdicts=tuple(CapabilityVerdict.from_dict(v) for v in data.get("verdicts", [])),
        )

    @classmethod
    def from_json(cls, text: str) -> DestinationProfile:
        """Rebuild from canonical JSON (``from_dict(json.loads(text))``)."""
        return cls.from_dict(json.loads(text))


__all__ = [
    "SCHEMA_VERSION",
    "CapabilityVerdict",
    "DestinationProfile",
    "EVIDENCE_KINDS",
    "EvidenceRef",
    "ReasonCode",
    "SUPPORTED_ELEMENT_KINDS",
    "SourceElementRef",
    "SupportLevel",
    "support_rank",
]
