"""Durable human approval boundary for agent authoring proposals.

Approval is intentionally a thin domain facade. It does not own replay state,
locks, documents, or publication state. The repository must consume a pending
proposal and create its canonical draft atomically in durable storage.
"""

from __future__ import annotations

from typing import Protocol

from apps.dataviz_service.contracts import SanitizedServiceError, Version


class ApprovalError(SanitizedServiceError):
    """Raised when a proposal cannot be durably approved."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409, error_code="APPROVAL_REJECTED")


class AgentProposalApprovalRepository(Protocol):
    def approve_agent_proposal_to_draft(
        self,
        tenant_id: str,
        proposal_id: str,
        *,
        approver_id: str,
        confirm_destructive: bool = False,
        confirm_high_cost: bool = False,
    ) -> Version: ...


def approve_proposal(
    repository: AgentProposalApprovalRepository,
    proposal_id: str,
    *,
    tenant_id: str,
    approver_id: str,
    confirm_destructive: bool = False,
    confirm_high_cost: bool = False,
) -> Version:
    """Approve a persisted proposal into one durable draft version.

    This function never publishes. The repository implementation is responsible
    for tenant RLS, self-approval rejection, immutable proposal verification,
    stale-head CAS, replay idempotency, and committing the draft + decision in
    one transaction.

    ``confirm_high_cost`` is the explicit, independent confirmation for
    proposals whose stored operations declare high-cost custom visuals; it is
    deliberately not reused from ``confirm_destructive``.
    """
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ApprovalError("tenant_id is required")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ApprovalError("proposal_id is required")
    if not isinstance(approver_id, str) or not approver_id.strip():
        raise ApprovalError("approver_id is required")
    if not isinstance(confirm_destructive, bool):
        raise TypeError("confirm_destructive must be a bool")
    if not isinstance(confirm_high_cost, bool):
        raise TypeError("confirm_high_cost must be a bool")
    try:
        return repository.approve_agent_proposal_to_draft(
            tenant_id.strip(),
            proposal_id.strip(),
            approver_id=approver_id.strip(),
            confirm_destructive=confirm_destructive,
            confirm_high_cost=confirm_high_cost,
        )
    except SanitizedServiceError:
        raise
    except Exception as exc:
        raise ApprovalError("Agent proposal approval failed.") from exc


__all__ = ["AgentProposalApprovalRepository", "ApprovalError", "approve_proposal"]
