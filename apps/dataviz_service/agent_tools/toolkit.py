"""Allowlisted, read-only agent tools over an immutable authoring document.

The :class:`AgentToolkit` is the ONLY surface an autonomous agent is handed. It
exposes a closed, allowlisted set of tools that:

  * inspect the neutral :class:`~core.contracts.semantic_ir.SemanticModel` IR
    and the version history (read-only),
  * build typed :class:`~core.authoring.operations.Operation` proposals against
    stable IR ids, validated through the closed
    :class:`~core.contracts.semantic_ir.Expression` grammar (no arbitrary code),
  * request approval by emitting an immutable :class:`Proposal`.

It structurally CANNOT:

  * read secrets — it never imports :mod:`apps.dataviz_service.storage.credentials`
    and holds no credential-bearing object,
  * execute arbitrary SQL or Python — operations may only carry the typed
    :class:`~core.contracts.semantic_ir.Expression` grammar; raw strings, dicts
    and unknown expression kinds are rejected at the contract layer before
    reaching the toolkit,
  * publish or apply directly — it has no method that returns or mutates a
    :class:`~core.authoring.versioning.Document`. Approval + apply live in
    :mod:`apps.dataviz_service.agent_tools.approval`, which the agent is not
    given.

The toolkit is a frozen value object: ``document`` never changes through it.

Security note: every name added to :data:`ALLOWED_TOOLS` must correspond to a
read-only or proposal-emitting method that never returns a ``Document``. The
adversarial tests in ``apps/dataviz_service/tests/test_agent_tools.py`` pin this
invariant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, is_dataclass
from dataclasses import fields as dc_fields
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from apps.dataviz_service.agent_tools.proposal import Proposal
from apps.dataviz_service.contracts import SanitizedServiceError
from core.authoring.diff import SemanticDiff, preview
from core.authoring.operations import VALID_TARGET_KINDS, Operation
from core.authoring.versioning import Document
from core.contracts.semantic_ir import SemanticModel

# Closed allowlist of tools an agent may invoke through ``AgentToolkit.call``.
# Adding a name here is a security-sensitive change: every name must correspond
# to a read-only or proposal-emitting method that never returns a Document.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "inspect_model",
        "inspect_element",
        "list_versions",
        "preview_operations",
        "validate_operations",
        "propose_operations",
    }
)

# Safe identifier pattern for stable IR ids. Rejects path traversal, dunder
# access and arbitrary punctuation up front as defense in depth (the underlying
# lookups are dict-key based and would not match anyway).
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_ .&%()/\-]*$")
MAX_OPERATION_COUNT = 100
MAX_MESSAGE_LENGTH = 2000
MAX_AUTHOR_LENGTH = 200
# Element names are dictionary keys, not executable selectors.
MAX_ELEMENT_NAME_LENGTH = 512

# Field names that must never appear in any tool output. The neutral IR does not
# carry credentials, so this is a defense-in-depth canary: if any of these leak
# into a tool result something has gone very wrong upstream.
_FORBIDDEN_SECRET_TOKENS: frozenset[str] = frozenset(
    {"secret_access_key", "session_token", "password", "api_key", "access_token"}
)

# Maps an allowlisted target kind to the SemanticModel attribute holding its list.
_TARGET_TO_ATTR: dict[str, str] = {
    "entity": "entities",
    "metric": "metrics",
    "parameter": "parameters",
    "filter": "filters",
    "relationship": "relationships",
    "rls_intent": "rls_intents",
}


class ToolError(SanitizedServiceError):
    """Raised when an agent invokes an unknown/disallowed tool or passes bad args."""

    def __init__(self, message: str, error_code: str = "TOOL_ERROR") -> None:
        super().__init__(message, status_code=400, error_code=error_code)


def _now_iso() -> str:
    """UTC timestamp with second precision (stable, stdlib-only)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def _to_jsonable(obj: Any) -> Any:
    """Recursively convert contract dataclasses to JSON-compatible primitives.

    Mirrors the IR's private converter without depending on a private name, so
    agent-facing output stays JSON-safe and free of opaque objects crossing an
    RPC boundary.
    """
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in dc_fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    return obj


def _validate_element_name(name: str) -> None:
    if not isinstance(name, str):
        raise ToolError("name must be a string", "TOOL_BAD_NAME")
    if name == "":
        raise ToolError("name must not be empty", "TOOL_BAD_NAME")
    if len(name) > MAX_ELEMENT_NAME_LENGTH:
        raise ToolError("name exceeds maximum length", "TOOL_BAD_NAME")
    if not _SAFE_NAME.fullmatch(name):
        raise ToolError(f"name contains disallowed characters: {name!r}", "TOOL_BAD_NAME")
    if name.startswith("__"):
        raise ToolError(f"dunder names are not allowed: {name!r}", "TOOL_BAD_NAME")


def _validate_target(target: str) -> None:
    if target not in VALID_TARGET_KINDS:
        raise ToolError(
            f"target not allowed: {target!r}; allowlist={sorted(VALID_TARGET_KINDS)}",
            "TOOL_BAD_TARGET",
        )


def _validate_ops(ops: object) -> None:
    """Reject anything that is not a list of typed :class:`Operation` instances.

    This is the toolkit-side enforcement that raw dicts / strings / arbitrary
    objects cannot enter the proposal pipeline. The contract layer additionally
    rejects payloads that are not the exact typed contract instance, and the
    closed Expression grammar rejects arbitrary code.
    """
    if not isinstance(ops, list):
        raise ToolError("ops must be a list", "TOOL_BAD_OPS")
    if not ops:
        raise ToolError("ops must not be empty", "TOOL_BAD_OPS")
    if len(ops) > MAX_OPERATION_COUNT:
        raise ToolError(
            f"ops exceeds the maximum of {MAX_OPERATION_COUNT}", "TOOL_OPS_TOO_LARGE"
        )
    for op in ops:
        if not isinstance(op, Operation):
            raise ToolError(
                "ops must contain only Operation instances; raw dicts/strings are rejected",
                "TOOL_BAD_OPS",
            )


def _section_index(model: SemanticModel, target: str) -> dict[str, object]:
    """``{name: instance}`` index for one section of ``model``."""
    return {item.name: item for item in getattr(model, _TARGET_TO_ATTR[target])}


def _diff_view(base_version: int, diff: SemanticDiff) -> dict[str, Any]:
    return {
        "base_version": base_version,
        "is_empty": diff.is_empty,
        "added": [(c.target, c.name) for c in diff.added],
        "removed": [(c.target, c.name) for c in diff.removed],
        "changed": [(c.target, c.name) for c in diff.changed],
    }


@dataclass(frozen=True)
class AgentToolkit:
    """Allowlisted, read-only tool surface over an immutable :class:`Document`.

    The toolkit is constructed from a single :class:`Document` and never holds
    or derives any other state. All tools return JSON-safe ``dict`` / ``list``
    values or an immutable :class:`Proposal`; none returns or mutates a
    :class:`Document`.

    Attributes:
        document: the immutable authoring document the tools read from.
    """

    document: Document
    tenant_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.document, Document):
            raise TypeError("AgentToolkit.document must be a core.authoring Document")
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ToolError("tenant_id is required", "TOOL_TENANT_REQUIRED")
        object.__setattr__(self, "tenant_id", self.tenant_id.strip())

    # --- dispatcher -----------------------------------------------------

    def call(self, name: str, /, **kwargs: Any) -> Any:
        """Invoke tool ``name`` iff it is in :data:`ALLOWED_TOOLS`.

        Raises :class:`ToolError` for any name outside the allowlist, so the
        toolkit cannot be coerced into calling an arbitrary attribute (e.g.
        ``__class__``, a private helper, or a future non-read-only method).
        """
        if not isinstance(name, str) or name not in ALLOWED_TOOLS:
            raise ToolError(
                f"tool not allowed: {name!r}; allowlist={sorted(ALLOWED_TOOLS)}",
                "TOOL_NOT_ALLOWED",
            )
        return getattr(self, name)(**kwargs)

    # --- read-only inspection ------------------------------------------

    def inspect_model(self) -> dict[str, Any]:
        """Return a JSON-safe summary of the head :class:`SemanticModel`.

        Includes per-section stable ids (names only) and counts, plus the head
        and published version numbers. No element payloads are returned here;
        use :meth:`inspect_element` for a single element's full detail.
        """
        doc = self.document
        model = doc.head.model
        sections = {
            attr: [item.name for item in getattr(model, attr)] for attr in _TARGET_TO_ATTR.values()
        }
        return {
            "doc_id": doc.doc_id,
            "tenant_id": self.tenant_id,
            "head_version": doc.head.number,
            "head_status": doc.head.status,
            "published_version": doc.published.number if doc.published else None,
            "model_name": model.name,
            "schema_version": model.schema_version,
            "counts": {k: len(v) for k, v in sections.items()},
            **{k: sorted(v) for k, v in sections.items()},
        }

    def inspect_element(self, *, target: str, name: str) -> dict[str, Any]:
        """Return the full JSON-safe detail of one element by stable id.

        Raises :class:`ToolError` for an unknown ``target`` or ``name``.
        """
        _validate_target(target)
        _validate_element_name(name)
        element = _section_index(self.document.head.model, target).get(name)
        if element is None:
            raise ToolError(
                f"{target} {name!r} not found",
                f"TOOL_{target.upper()}_NOT_FOUND",
            )
        return {"target": target, "name": name, "data": _to_jsonable(element)}

    def list_versions(self) -> list[dict[str, Any]]:
        """Return a JSON-safe summary of the full version history.

        Only metadata is returned (number, status, parent, message,
        ``created_at``); model contents are not duplicated here.
        """
        return [
            {
                "number": v.number,
                "status": v.status,
                "parent": v.parent,
                "message": v.message,
                "created_at": v.created_at,
            }
            for v in self.document.versions
        ]

    # --- validation + proposal (no apply, no publish) ------------------

    def preview_operations(self, *, ops: list[Operation]) -> dict[str, Any]:
        """Return the structured diff ``ops`` would produce, without committing.

        Runs the full validation pipeline (structural + existence + semantic)
        via :func:`core.authoring.preview`, which calls
        :func:`core.authoring.materialize`. Invalid ops raise ``ValueError`` /
        ``TypeError`` unchanged from the authoring layer.
        """
        _validate_ops(ops)
        diff = preview(self.document.head.model, ops)
        return _diff_view(self.document.head.number, diff)

    def validate_operations(self, *, ops: list[Operation]) -> dict[str, Any]:
        """Validate ``ops`` against the head model; return acceptance metadata.

        Raises on any invalid op (same validation as :meth:`preview_operations`
        but returns only ``ok`` / counts, no diff payload).
        """
        _validate_ops(ops)
        preview(self.document.head.model, ops)
        return {
            "ok": True,
            "op_count": len(ops),
            "base_version": self.document.head.number,
        }

    def propose_operations(
        self, *, ops: list[Operation], message: str = "", author: str = ""
    ) -> Proposal:
        """Build an immutable, pending :class:`Proposal` from validated ``ops``.

        The proposal records the head version it was validated against; a later
        :func:`~apps.dataviz_service.agent_tools.approval.approve_proposal` call
        rejects it if the document has moved on (stale base). This method NEVER
        applies, publishes or mutates the document.
        """
        _validate_ops(ops)
        if not isinstance(message, str):
            raise ToolError("message must be a string", "TOOL_BAD_MESSAGE")
        if not isinstance(author, str):
            raise ToolError("author must be a string", "TOOL_BAD_AUTHOR")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise ToolError(
                f"message exceeds the maximum of {MAX_MESSAGE_LENGTH} characters",
                "TOOL_MESSAGE_TOO_LARGE",
            )
        if len(author) > MAX_AUTHOR_LENGTH:
            raise ToolError(
                f"author exceeds the maximum of {MAX_AUTHOR_LENGTH} characters",
                "TOOL_AUTHOR_TOO_LARGE",
            )
        diff = preview(self.document.head.model, ops)
        return Proposal(
            proposal_id=uuid4().hex,
            doc_id=self.document.doc_id,
            tenant_id=self.tenant_id,
            base_version=self.document.head.number,
            message=message,
            ops=tuple(ops),
            diff=diff,
            created_at=_now_iso(),
            author=author,
        )


def assert_no_secret_tokens(payload: Any) -> None:
    """Canary helper: raise :class:`ToolError` if ``payload`` (recursively)
    contains a key whose name is a known secret-bearing token.

    The neutral IR never carries credentials, so this should never fire for
    toolkit output. It exists as defense-in-depth and is exercised by the
    adversarial test suite.
    """
    blob = repr(payload).lower()
    leaked = {tok for tok in _FORBIDDEN_SECRET_TOKENS if tok in blob}
    if leaked:
        raise ToolError(
            f"tool output leaked forbidden secret token(s): {sorted(leaked)}",
            "TOOL_SECRET_LEAK",
        )


__all__ = [
    "ALLOWED_TOOLS",
    "AgentToolkit",
    "ToolError",
    "assert_no_secret_tokens",
]
