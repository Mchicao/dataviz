"""Immutable agent proposal: an approval-requested batch of typed operations.

A :class:`Proposal` is what an :class:`AgentToolkit` emits when an agent asks it
to ``propose_operations``. It bundles the typed operations, the structured diff
they would produce, and the base version they were validated against, plus a
``status`` that flows ``pending -> approved | rejected``.

Proposals are immutable value objects: they carry no behavior beyond a JSON-safe
:meth:`Proposal.summary` for display to a human approver. They deliberately do
NOT hold a reference to the :class:`~core.authoring.versioning.Document` and
cannot apply themselves. Applying a proposal requires the separate
:func:`apps.dataviz_service.agent_tools.approval.approve_proposal` entry point,
which the agent toolkit does not expose.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from core.authoring.diff import SemanticDiff
from core.authoring.interaction_operations import InteractionOperation
from core.authoring.operations import Operation
from core.authoring.presentation_operations import PresentationOperation
from core.contracts.interaction_ir import InteractionIR
from core.contracts.presentation_ir import PresentationIR
from core.contracts.semantic_ir import SemanticModel

ProposalStatus = Literal["pending", "approved", "rejected"]
ProposalType = Literal["authoring"]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _serialize_operation(operation: object) -> dict[str, Any]:
    if not isinstance(operation, (Operation, PresentationOperation, InteractionOperation)):
        raise TypeError("proposal operations must be typed authoring operations")
    return _jsonable(operation)


def _candidate_checksum(proposal: Proposal) -> str | None:
    if not all(
        value is not None
        for value in (
            proposal.candidate_semantic,
            proposal.candidate_presentation,
            proposal.candidate_interaction,
        )
    ):
        return None
    assert proposal.candidate_semantic is not None
    assert proposal.candidate_presentation is not None
    assert proposal.candidate_interaction is not None
    payload = {
        "semantic": proposal.candidate_semantic.to_dict(),
        "presentation": proposal.candidate_presentation.to_dict(),
        "interaction": proposal.candidate_interaction.to_dict(),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def proposal_identity_payload(proposal: Proposal) -> dict[str, Any]:
    """Return exactly the immutable identity covered by proposal SHA-256."""
    return {
        "proposal_id": proposal.proposal_id,
        "proposal_type": proposal.proposal_type,
        "tenant_id": proposal.tenant_id,
        "project_id": proposal.project_id,
        "doc_id": proposal.doc_id,
        "base_version": proposal.base_version,
        "base_checksum": proposal.base_checksum,
        "head_checksum": proposal.head_checksum,
        "candidate_checksum": _candidate_checksum(proposal),
        "author": proposal.author,
        "message": proposal.message,
        "semantic_ops": [_serialize_operation(op) for op in proposal.ops],
        "presentation_ops": [_serialize_operation(op) for op in proposal.presentation_ops],
        "interaction_ops": [_serialize_operation(op) for op in proposal.interaction_ops],
    }


def proposal_sha256(proposal: Proposal) -> str:
    encoded = json.dumps(
        proposal_identity_payload(proposal),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class Proposal:
    """An approval-requested batch of typed authoring operations.

    Attributes:
        proposal_id: unique id (uuid4 hex).
        doc_id: id of the document this proposal targets.
        base_version: head version number the ops were validated against.
        message: authoring message to attach on approval.
        ops: ordered tuple of typed :class:`Operation` instances.
        diff: structured :class:`SemanticDiff` the ops would produce.
        created_at: UTC ISO-8601 timestamp.
        status: lifecycle status; starts ``"pending"``.
        author: optional agent identifier that produced the proposal.
    """

    proposal_id: str
    doc_id: str
    tenant_id: str
    base_version: int
    message: str
    ops: tuple[Operation, ...]
    diff: SemanticDiff
    created_at: str
    status: ProposalStatus = "pending"
    author: str = ""
    project_id: str = ""
    proposal_type: ProposalType = "authoring"
    base_checksum: str = ""
    head_checksum: str = ""
    presentation_ops: tuple[PresentationOperation, ...] = ()
    interaction_ops: tuple[InteractionOperation, ...] = ()
    candidate_semantic: SemanticModel | None = None
    candidate_presentation: PresentationIR | None = None
    candidate_interaction: InteractionIR | None = None

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("proposal_id is required")
        if not self.doc_id:
            raise ValueError("doc_id is required")
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        object.__setattr__(self, "tenant_id", self.tenant_id.strip())
        if not self.project_id:
            # Compatibility for pre-CA06 in-process callers. Durable stores
            # verify this value against the authoritative project before write.
            object.__setattr__(self, "project_id", self.doc_id)
        if self.proposal_type != "authoring":
            raise ValueError("unsupported proposal_type")
        if not isinstance(self.base_version, int) or isinstance(self.base_version, bool):
            raise TypeError("base_version must be an int")
        if self.base_version < 1:
            raise ValueError("base_version must be >= 1")
        if not isinstance(self.ops, tuple):
            object.__setattr__(self, "ops", tuple(self.ops))
        for op in self.ops:
            if not isinstance(op, Operation):
                raise TypeError("proposal ops must be Operation instances")
        if not isinstance(self.presentation_ops, tuple):
            object.__setattr__(self, "presentation_ops", tuple(self.presentation_ops))
        if not isinstance(self.interaction_ops, tuple):
            object.__setattr__(self, "interaction_ops", tuple(self.interaction_ops))
        if any(not isinstance(op, PresentationOperation) for op in self.presentation_ops):
            raise TypeError("presentation_ops must contain PresentationOperation instances")
        if any(not isinstance(op, InteractionOperation) for op in self.interaction_ops):
            raise TypeError("interaction_ops must contain InteractionOperation instances")
        if not isinstance(self.diff, SemanticDiff):
            raise TypeError("proposal.diff must be a SemanticDiff")
        if self.status not in ("pending", "approved", "rejected"):
            raise ValueError(f"invalid proposal status: {self.status!r}")

    def summary(self) -> dict[str, object]:
        """Return a JSON-safe, redacted view for display to a human approver.

        The diff is summarized by stable id only; full element payloads are
        omitted so the summary never carries more than the agent already saw.
        """
        return {
            "proposal_id": self.proposal_id,
            "doc_id": self.doc_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "proposal_type": self.proposal_type,
            "base_version": self.base_version,
            "base_checksum": self.base_checksum,
            "head_checksum": self.head_checksum,
            "message": self.message,
            "author": self.author,
            "created_at": self.created_at,
            "status": self.status,
            "op_count": len(self.ops),
            "presentation_op_count": len(self.presentation_ops),
            "interaction_op_count": len(self.interaction_ops),
            "proposal_sha256": proposal_sha256(self),
            "diff": {
                "added": [(c.target, c.name) for c in self.diff.added],
                "removed": [(c.target, c.name) for c in self.diff.removed],
                "changed": [(c.target, c.name) for c in self.diff.changed],
            },
        }


def new_proposal_id() -> str:
    """Return a fresh proposal id (uuid4 hex)."""
    return uuid4().hex


__all__ = [
    "Proposal",
    "ProposalStatus",
    "ProposalType",
    "new_proposal_id",
    "proposal_identity_payload",
    "proposal_sha256",
]
