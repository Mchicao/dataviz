"""Semantic diff and preview for authoring operations.

A :class:`SemanticDiff` describes the structured delta between two
:class:`SemanticModel` snapshots keyed by **stable IR id** (the per-section
``name``). ``added`` / ``removed`` are detected by id set difference; ``changed``
is detected by value equality of the contract dataclasses (which are frozen and
compare by value).

:func:`preview` computes the diff that *would* result from applying a batch of
operations to a model, without committing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from core.authoring.operations import Operation, materialize
from core.contracts.semantic_ir import SemanticModel

ChangeKind = Literal["added", "removed", "changed"]

# Authoring target kind -> SemanticModel attribute holding the section list.
_TARGET_SECTIONS: dict[str, str] = {
    "entity": "entities",
    "metric": "metrics",
    "parameter": "parameters",
    "filter": "filters",
    "relationship": "relationships",
    "rls_intent": "rls_intents",
}


def _index(model: SemanticModel, target: str) -> dict[str, object]:
    section = getattr(model, _TARGET_SECTIONS[target])
    return {item.name: item for item in section}


@dataclass(frozen=True)
class Change:
    """A single element-level change between two model snapshots.

    Attributes:
        target: IR section (``"entity"`` / ``"metric"`` / ...).
        name: stable id of the changed element.
        kind: ``"added"`` / ``"removed"`` / ``"changed"``.
        before: the previous instance for ``removed`` and ``changed``; ``None``
            for ``added``.
        after: the new instance for ``added`` and ``changed``; ``None`` for
            ``removed``.
    """

    target: str
    name: str
    kind: ChangeKind
    before: object | None = None
    after: object | None = None


@dataclass(frozen=True)
class SemanticDiff:
    """Structured, immutable diff between two :class:`SemanticModel` snapshots."""

    changes: tuple[Change, ...]

    @property
    def is_empty(self) -> bool:
        """True when there is no difference between the two snapshots."""
        return not self.changes

    @property
    def added(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.kind == "added")

    @property
    def removed(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.kind == "removed")

    @property
    def changed(self) -> tuple[Change, ...]:
        return tuple(c for c in self.changes if c.kind == "changed")


def diff(before: SemanticModel, after: SemanticModel) -> SemanticDiff:
    """Compute the structured semantic diff between two models by stable id."""
    changes: list[Change] = []
    for target in _TARGET_SECTIONS:
        a = _index(before, target)
        b = _index(after, target)
        for name in b:
            if name not in a:
                changes.append(Change(target, name, "added", after=b[name]))
        for name in a:
            if name not in b:
                changes.append(Change(target, name, "removed", before=a[name]))
        for name in a:
            if name in b and a[name] != b[name]:
                changes.append(Change(target, name, "changed", before=a[name], after=b[name]))
    return SemanticDiff(tuple(changes))


def preview(model: SemanticModel, ops: list[Operation]) -> SemanticDiff:
    """Return the diff that would result from applying ``ops`` to ``model``.

    Does not mutate ``model`` (it is immutable). Raises ``ValueError`` /
    ``TypeError`` if the ops are invalid, with the same validation as
    :func:`materialize`.
    """
    after = materialize(model, ops)
    return diff(model, after)


__all__ = [
    "Change",
    "ChangeKind",
    "SemanticDiff",
    "diff",
    "preview",
]
