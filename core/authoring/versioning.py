"""Immutable versioned authoring document with apply and rollback.

A :class:`Document` is the unit of authoring state. It holds the full, ordered,
immutable version history of a single neutral :class:`SemanticModel`. The head
is the latest version. Legacy published/superseded statuses remain readable, but
publication activation is owned by ``apps.dataviz_service.governance.publication``.

All mutators are pure: they validate and return a **new** :class:`Document`,
leaving the previous one intact. This makes preview, audit and rollback
trivially expressible.

Lifecycle:

* :func:`init_document` seeds version 1 as a ``draft``.
* :func:`apply_ops` validates a batch of operations against the head and
  appends a new ``draft`` version (parent = previous head number).
* :func:`rollback` appends a new ``draft`` whose model restores an earlier
  version's model; history is never deleted, and lineage is auditable via
  ``parent``.

Timestamps are UTC ISO-8601 produced by the standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from core.authoring.interaction_operations import InteractionOperation, materialize_interactions
from core.authoring.operations import Operation, materialize
from core.authoring.presentation_operations import PresentationOperation, materialize_presentation
from core.contracts.coherence import validate_cross_ir_coherence
from core.contracts.interaction_ir import InteractionIR
from core.contracts.presentation_ir import PresentationIR
from core.contracts.semantic_ir import SemanticModel

VersionStatus = Literal["draft", "published", "superseded"]


class AuthoringConflictError(ValueError):
    """Raised when a mutation was based on a stale document head."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"stale base version: expected {expected}, actual {actual}")


def _check_base_version(doc: Document, base_version: int | None) -> None:
    if base_version is None:
        return
    if not isinstance(base_version, int) or isinstance(base_version, bool):
        raise TypeError("base_version must be an int")
    if base_version < 1:
        raise ValueError("base_version must be >= 1")
    if base_version != doc.head.number:
        raise AuthoringConflictError(base_version, doc.head.number)


def _now_iso() -> str:
    """UTC timestamp with second precision (stable, stdlib-only)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Version:
    """An immutable snapshot of the authored model at a point in time.

    Attributes:
        number: monotonic version number (starts at 1).
        model: the :class:`SemanticModel` snapshot.
        status: ``"draft"``, ``"published"`` or ``"superseded"``.
        parent: the version number this one was derived from, or ``None`` for
            the seed version.
        message: free-form authoring message (commit-like).
        created_at: UTC ISO-8601 timestamp.
    """

    number: int
    model: SemanticModel
    status: VersionStatus
    parent: int | None
    message: str
    created_at: str
    presentation: PresentationIR | None = None
    interaction: InteractionIR | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.number, int) or isinstance(self.number, bool):
            raise TypeError("version number must be an int")
        if self.number < 1:
            raise ValueError("version number must be >= 1")
        if not isinstance(self.model, SemanticModel):
            raise TypeError("version.model must be a SemanticModel")
        if self.presentation is not None:
            if not isinstance(self.presentation, PresentationIR):
                raise TypeError("version.presentation must be a PresentationIR")
            self.presentation.validate()
        if self.interaction is not None:
            if not isinstance(self.interaction, InteractionIR):
                raise TypeError("version.interaction must be an InteractionIR")
            self.interaction.validate()
        if self.presentation is not None or self.interaction is not None:
            validate_cross_ir_coherence(self.model, self.presentation, self.interaction)


@dataclass(frozen=True)
class Document:
    """Immutable versioned authoring document with full history.

    Invariants enforced on construction:

    * ``doc_id`` is a non-empty string.
    * ``versions`` is non-empty, unique and strictly increasing by ``number``.
    * At most one version has ``status == "published"``.

    Attributes:
        doc_id: stable document identifier.
        versions: ordered tuple of :class:`Version` (index 0 is version 1).
    """

    doc_id: str
    versions: tuple[Version, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.doc_id, str) or not self.doc_id:
            raise ValueError("doc_id is required")
        if not self.versions:
            raise ValueError("Document must have at least one version")
        numbers = [v.number for v in self.versions]
        if numbers != sorted(numbers) or len(set(numbers)) != len(numbers):
            raise ValueError("versions must be unique and ordered by number")
        if len([v for v in self.versions if v.status == "published"]) > 1:
            raise ValueError("at most one published version is allowed")

    @property
    def head(self) -> Version:
        """The latest version (highest number)."""
        return self.versions[-1]

    @property
    def published(self) -> Version | None:
        """The current published version, or ``None`` if none is published."""
        for v in reversed(self.versions):
            if v.status == "published":
                return v
        return None

    def version(self, number: int) -> Version:
        """Look up a version by number; raise :class:`KeyError` if absent."""
        for v in self.versions:
            if v.number == number:
                return v
        raise KeyError(f"version {number} not found in document {self.doc_id!r}")


def init_document(
    model: SemanticModel,
    *,
    doc_id: str,
    message: str = "initial",
    presentation: PresentationIR | None = None,
    interaction: InteractionIR | None = None,
) -> Document:
    """Create a new authoring document seeded with ``model`` as version 1 draft."""
    v1 = Version(
        number=1,
        model=model,
        status="draft",
        parent=None,
        message=message,
        created_at=_now_iso(),
        presentation=presentation,
        interaction=interaction,
    )
    return Document(doc_id=doc_id, versions=(v1,))


def apply_ops(
    doc: Document,
    ops: list[Operation],
    *,
    presentation_ops: list[PresentationOperation] | None = None,
    interaction_ops: list[InteractionOperation] | None = None,
    message: str = "",
    base_version: int | None = None,
    can_manage_rls: bool = True,
) -> Document:
    """Validate ``ops`` against ``doc.head`` and append a new draft version.

    Atomic: if any operation fails structural, existence or semantic
    validation, ``doc`` is unchanged and the underlying ``ValueError`` /
    ``TypeError`` propagates. The new version's ``parent`` is the previous
    head's number, preserving lineage.
    """
    _check_base_version(doc, base_version)
    if not can_manage_rls and any(op.target == "rls_intent" for op in ops):
        raise ValueError(
            "General authoring cannot mutate semantic RLS policy without a dedicated security capability."
        )
    new_model = materialize(doc.head.model, ops)
    new_presentation = doc.head.presentation
    if presentation_ops:
        if new_presentation is None:
            raise ValueError("presentation_ops require a PresentationIR on the document head")
        new_presentation = materialize_presentation(new_presentation, presentation_ops)
    new_interaction = doc.head.interaction
    if interaction_ops:
        if new_interaction is None:
            raise ValueError("interaction_ops require an InteractionIR on the document head")
        new_interaction = materialize_interactions(new_interaction, interaction_ops)
    new_version = Version(
        number=doc.head.number + 1,
        model=new_model,
        status="draft",
        parent=doc.head.number,
        message=message,
        created_at=_now_iso(),
        presentation=new_presentation,
        interaction=new_interaction,
    )
    return Document(doc_id=doc.doc_id, versions=doc.versions + (new_version,))



def rollback(
    doc: Document,
    to_number: int,
    *,
    message: str = "",
    base_version: int | None = None,
    can_manage_rls: bool = True,
) -> Document:
    """Append a new draft version restoring the model of version ``to_number``.

    History is preserved: rollback never deletes versions. The new head is a
    ``draft`` whose ``parent`` points at ``to_number``, so lineage is fully
    auditable. The caller typically follows up with :func:`publish` to make the
    restored state eligible for a separately governed publication request.
    """
    _check_base_version(doc, base_version)
    target = doc.version(to_number)
    if not can_manage_rls and doc.head.model.rls_intents != target.model.rls_intents:
        raise ValueError(
            "General authoring cannot alter semantic RLS policy via rollback without a dedicated security capability."
        )
    restored = Version(
        number=doc.head.number + 1,
        model=target.model,
        status="draft",
        parent=to_number,
        message=message or f"rollback to v{to_number}",
        created_at=_now_iso(),
        presentation=target.presentation,
        interaction=target.interaction,
    )
    return Document(doc_id=doc.doc_id, versions=doc.versions + (restored,))



__all__ = [
    "Document",
    "AuthoringConflictError",
    "Version",
    "VersionStatus",
    "apply_ops",
    "init_document",
    "rollback",
]
