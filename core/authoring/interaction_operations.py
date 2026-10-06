"""Typed authoring operations over the canonical InteractionIR."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Literal, get_args

from core.contracts.interaction_ir import FilterCondition, InteractionIR, PageInteractionSpec

InteractionOpKind = Literal["add", "update", "remove"]
InteractionTarget = Literal["page", "global_filter"]
VALID_INTERACTION_OP_KINDS = frozenset(get_args(InteractionOpKind))
VALID_INTERACTION_TARGETS = frozenset(get_args(InteractionTarget))


@dataclass(frozen=True)
class InteractionOperation:
    kind: InteractionOpKind
    target: InteractionTarget
    name: str
    payload: PageInteractionSpec | FilterCondition | None = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_INTERACTION_OP_KINDS:
            raise ValueError(f"interaction op kind not allowed: {self.kind!r}")
        if self.target not in VALID_INTERACTION_TARGETS:
            raise ValueError(f"interaction op target not allowed: {self.target!r}")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("interaction op name must be a non-empty string")
        if self.kind == "remove":
            if self.payload is not None:
                raise ValueError("remove interaction ops must not carry payload")
            return
        expected = PageInteractionSpec if self.target == "page" else FilterCondition
        if not isinstance(self.payload, expected):
            raise TypeError(
                f"{self.target} {self.kind} payload must be {expected.__name__}, "
                f"got {type(self.payload).__name__}"
            )
        payload_name = self.payload.page_id if self.target == "page" else self.payload.filter_id
        if payload_name != self.name:
            raise ValueError(
                f"payload id {payload_name!r} must equal interaction op name {self.name!r}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "target": self.target,
            "name": self.name,
            "payload": None if self.payload is None else self.payload.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> InteractionOperation:
        if not isinstance(data, dict):
            raise TypeError("interaction operation must be an object")
        unknown = set(data) - {"kind", "target", "name", "payload"}
        if unknown:
            raise ValueError(f"unknown interaction operation field(s): {sorted(unknown)}")
        kind = data.get("kind")
        target = data.get("target")
        name = data.get("name")
        if not isinstance(kind, str) or not isinstance(target, str) or not isinstance(name, str):
            raise TypeError("interaction operation kind, target and name must be strings")
        raw_payload = data.get("payload")
        payload = None
        if kind == "remove":
            if raw_payload is not None:
                raise ValueError("remove interaction ops must not carry payload")
        else:
            if not isinstance(raw_payload, dict):
                raise TypeError("interaction add/update ops require an object payload")
            if target == "page":
                expected_type = PageInteractionSpec
            elif target == "global_filter":
                expected_type = FilterCondition
            else:
                raise ValueError(f"unknown interaction authoring target: {target!r}")
            if expected_type is not None:
                unknown_payload = set(raw_payload) - {
                    contract_field.name for contract_field in fields(expected_type)
                }
                if unknown_payload:
                    raise ValueError(
                        f"unknown {target} interaction payload field(s): {sorted(unknown_payload)}"
                    )
                payload = expected_type.from_dict(raw_payload)
        return cls(
            kind,  # type: ignore[arg-type]
            target,  # type: ignore[arg-type]
            name,
            payload,
        )


def add_page_interactions(page: PageInteractionSpec) -> InteractionOperation:
    return InteractionOperation("add", "page", page.page_id, page)


def update_page_interactions(page: PageInteractionSpec) -> InteractionOperation:
    return InteractionOperation("update", "page", page.page_id, page)


def remove_page_interactions(page_id: str) -> InteractionOperation:
    return InteractionOperation("remove", "page", page_id)


def add_global_filter(filter_: FilterCondition) -> InteractionOperation:
    return InteractionOperation("add", "global_filter", filter_.filter_id, filter_)


def update_global_filter(filter_: FilterCondition) -> InteractionOperation:
    return InteractionOperation("update", "global_filter", filter_.filter_id, filter_)


def remove_global_filter(filter_id: str) -> InteractionOperation:
    return InteractionOperation("remove", "global_filter", filter_id)


def materialize_interactions(
    interaction: InteractionIR, operations: list[InteractionOperation]
) -> InteractionIR:
    """Apply a batch atomically and return a validated InteractionIR snapshot."""
    pages = {page.page_id: page for page in interaction.pages}
    page_order = [page.page_id for page in interaction.pages]
    filters = {item.filter_id: item for item in interaction.global_filters}
    filter_order = [item.filter_id for item in interaction.global_filters]

    for operation in operations:
        if operation.target == "page":
            section = pages
            order = page_order
            exists = operation.name in section
            if operation.kind == "add":
                if exists:
                    raise ValueError(f"cannot add interaction page {operation.name!r}: already exists")
                assert isinstance(operation.payload, PageInteractionSpec)
                section[operation.name] = operation.payload
                order.append(operation.name)
            elif operation.kind == "update":
                if not exists:
                    raise ValueError(f"cannot update interaction page {operation.name!r}: not found")
                assert isinstance(operation.payload, PageInteractionSpec)
                section[operation.name] = operation.payload
            else:
                if not exists:
                    raise ValueError(f"cannot remove interaction page {operation.name!r}: not found")
                del section[operation.name]
                order.remove(operation.name)
            continue

        exists = operation.name in filters
        if operation.kind == "add":
            if exists:
                raise ValueError(f"cannot add global filter {operation.name!r}: already exists")
            assert isinstance(operation.payload, FilterCondition)
            filters[operation.name] = operation.payload
            filter_order.append(operation.name)
        elif operation.kind == "update":
            if not exists:
                raise ValueError(f"cannot update global filter {operation.name!r}: not found")
            assert isinstance(operation.payload, FilterCondition)
            filters[operation.name] = operation.payload
        else:
            if not exists:
                raise ValueError(f"cannot remove global filter {operation.name!r}: not found")
            del filters[operation.name]
            filter_order.remove(operation.name)

    result = replace(
        interaction,
        global_filters=tuple(filters[filter_id] for filter_id in filter_order),
        pages=tuple(pages[page_id] for page_id in page_order),
    )
    result.validate()
    return result


__all__ = [
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
]
