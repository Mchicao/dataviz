"""Allowlisted agent tools for safe authoring against the neutral Semantic IR.

Public surface:

* :class:`AgentToolkit` + :data:`ALLOWED_TOOLS` — the only object handed to an
  autonomous agent. Closed tool allowlist; read-only inspection + typed,
  approval-requested proposals. No secrets, no arbitrary code, no direct
  publish.
* :class:`Proposal` / :data:`ProposalStatus` — immutable approval request.
* :class:`ToolError` — controlled tool failure.
* :func:`approve_proposal` + :class:`ApprovalError` — the human-in-the-loop
  gate. NOT exposed by the toolkit; an agent holding only an
  :class:`AgentToolkit` cannot apply or publish.

See ``Docs/architecture/agentic_safety.md`` for the threat model and the
adversarial test coverage that pins these guarantees.
"""

from apps.dataviz_service.agent_tools.approval import ApprovalError, approve_proposal
from apps.dataviz_service.agent_tools.proposal import Proposal, ProposalStatus
from apps.dataviz_service.agent_tools.toolkit import (
    ALLOWED_TOOLS,
    AgentToolkit,
    ToolError,
    assert_no_secret_tokens,
)

__all__ = [
    "ALLOWED_TOOLS",
    "AgentToolkit",
    "ApprovalError",
    "Proposal",
    "ProposalStatus",
    "ToolError",
    "approve_proposal",
    "assert_no_secret_tokens",
]
