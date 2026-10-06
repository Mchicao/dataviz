"""Typed authoring operations over neutral Semantic, Presentation and Interaction IRs.

Public surface:

* **Operations** — :class:`Operation`, the ``add_*`` / ``update_*`` / ``remove_*``
  factories and :func:`materialize`.
* **Diff / preview** — :class:`Change`, :class:`SemanticDiff`, :func:`diff`,
  :func:`preview`.
* **Versioning** — :class:`Version`, :class:`Document`, :func:`init_document`,
  :func:`apply_ops`, :func:`rollback`. Publication is a service-governance action,
  not a core authoring mutation.

All operations target **stable IR ids** (the ``name`` of an entity / metric /
parameter / filter / relationship / rls_intent). Arbitrary code is structurally
rejected: logic may only enter the model through the closed
:class:`core.contracts.semantic_ir.Expression` grammar; raw dicts or source
strings are refused at construction time.
"""

from core.authoring.diff import Change, ChangeKind, SemanticDiff, diff, preview
from core.authoring.interaction_operations import (
    VALID_INTERACTION_OP_KINDS,
    VALID_INTERACTION_TARGETS,
    InteractionOperation,
    InteractionOpKind,
    InteractionTarget,
    add_global_filter,
    add_page_interactions,
    materialize_interactions,
    remove_global_filter,
    remove_page_interactions,
    update_global_filter,
    update_page_interactions,
)
from core.authoring.operations import (
    VALID_OP_KINDS,
    VALID_TARGET_KINDS,
    Operation,
    OpKind,
    TargetKind,
    add_entity,
    add_filter,
    add_metric,
    add_parameter,
    add_relationship,
    add_rls_intent,
    materialize,
    remove_entity,
    remove_filter,
    remove_metric,
    remove_parameter,
    remove_relationship,
    remove_rls_intent,
    update_entity,
    update_filter,
    update_metric,
    update_parameter,
    update_relationship,
    update_rls_intent,
)
from core.authoring.presentation_operations import (
    VALID_PRESENTATION_OP_KINDS,
    VALID_PRESENTATION_TARGETS,
    PresentationOperation,
    PresentationOpKind,
    PresentationTarget,
    add_page,
    add_visual,
    materialize_presentation,
    remove_page,
    remove_visual,
    update_page,
    update_visual,
)
from core.authoring.versioning import (
    AuthoringConflictError,
    Document,
    Version,
    VersionStatus,
    apply_ops,
    init_document,
    rollback,
)

__all__ = [
    # presentation operations
    "PresentationOpKind",
    "PresentationOperation",
    "PresentationTarget",
    "VALID_PRESENTATION_OP_KINDS",
    "VALID_PRESENTATION_TARGETS",
    "add_page",
    "add_visual",
    "materialize_presentation",
    "remove_page",
    "remove_visual",
    "update_page",
    "update_visual",
    # interaction operations
    "InteractionOpKind",
    "InteractionOperation",
    "InteractionTarget",
    "VALID_INTERACTION_OP_KINDS",
    "VALID_INTERACTION_TARGETS",
    "add_global_filter",
    "add_page_interactions",
    "materialize_interactions",
    "remove_global_filter",
    "remove_page_interactions",
    "update_global_filter",
    "update_page_interactions",
    # operations
    "OpKind",
    "Operation",
    "TargetKind",
    "VALID_OP_KINDS",
    "VALID_TARGET_KINDS",
    "add_entity",
    "add_filter",
    "add_metric",
    "add_parameter",
    "add_relationship",
    "add_rls_intent",
    "materialize",
    "remove_entity",
    "remove_filter",
    "remove_metric",
    "remove_parameter",
    "remove_relationship",
    "remove_rls_intent",
    "update_entity",
    "update_filter",
    "update_metric",
    "update_parameter",
    "update_relationship",
    "update_rls_intent",
    # diff
    "Change",
    "ChangeKind",
    "SemanticDiff",
    "diff",
    "preview",
    # versioning
    "Document",
    "AuthoringConflictError",
    "Version",
    "VersionStatus",
    "apply_ops",
    "init_document",
    "rollback",
]
