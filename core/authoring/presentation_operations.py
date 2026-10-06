"""Typed authoring operations over the canonical PresentationIR."""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Literal, get_args

from core.contracts.presentation_ir import PagePresentation, PresentationIR, VisualPresentation

PresentationOpKind = Literal["add", "update", "remove"]
PresentationTarget = Literal["page", "visual"]

VALID_PRESENTATION_OP_KINDS = frozenset(get_args(PresentationOpKind))
VALID_PRESENTATION_TARGETS = frozenset(get_args(PresentationTarget))


@dataclass(frozen=True)
class PresentationOperation:
    kind: PresentationOpKind
    target: PresentationTarget
    name: str
    payload: PagePresentation | VisualPresentation | None = None
    page_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_PRESENTATION_OP_KINDS:
            raise ValueError(f"presentation op kind not allowed: {self.kind!r}")
        if self.target not in VALID_PRESENTATION_TARGETS:
            raise ValueError(f"presentation op target not allowed: {self.target!r}")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("presentation op name must be a non-empty string")
        if self.target == "visual" and (not isinstance(self.page_id, str) or not self.page_id):
            raise ValueError("visual presentation ops require page_id")
        if self.target == "page" and self.page_id is not None:
            raise ValueError("page presentation ops must not carry page_id")
        if self.kind == "remove":
            if self.payload is not None:
                raise ValueError("remove presentation ops must not carry payload")
            return
        expected = PagePresentation if self.target == "page" else VisualPresentation
        if not isinstance(self.payload, expected):
            raise TypeError(
                f"{self.target} {self.kind} payload must be {expected.__name__}, "
                f"got {type(self.payload).__name__}"
            )
        payload_name = self.payload.page_id if self.target == "page" else self.payload.visual_id
        if payload_name != self.name:
            raise ValueError(
                f"payload id {payload_name!r} must equal presentation op name {self.name!r}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "target": self.target,
            "name": self.name,
            "page_id": self.page_id,
            "payload": None if self.payload is None else self.payload.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> PresentationOperation:
        if not isinstance(data, dict):
            raise TypeError("presentation operation must be an object")
        unknown = set(data) - {"kind", "target", "name", "payload", "page_id"}
        if unknown:
            raise ValueError(f"unknown presentation operation field(s): {sorted(unknown)}")
        kind = data.get("kind")
        target = data.get("target")
        name = data.get("name")
        page_id = data.get("page_id")
        if not isinstance(kind, str) or not isinstance(target, str) or not isinstance(name, str):
            raise TypeError("presentation operation kind, target and name must be strings")
        if page_id is not None and not isinstance(page_id, str):
            raise TypeError("presentation operation page_id must be a string or null")
        raw_payload = data.get("payload")
        payload = None
        if kind == "remove":
            if raw_payload is not None:
                raise ValueError("remove presentation ops must not carry payload")
        else:
            if not isinstance(raw_payload, dict):
                raise TypeError("presentation add/update ops require an object payload")
            if target == "page":
                expected_type = PagePresentation
            elif target == "visual":
                expected_type = VisualPresentation
            else:
                raise ValueError(f"unknown presentation authoring target: {target!r}")
            if expected_type is not None:
                unknown_payload = set(raw_payload) - {
                    contract_field.name for contract_field in fields(expected_type)
                }
                if unknown_payload:
                    raise ValueError(
                        f"unknown {target} presentation payload field(s): {sorted(unknown_payload)}"
                    )
                payload = expected_type.from_dict(raw_payload)
        return cls(
            kind,  # type: ignore[arg-type]
            target,  # type: ignore[arg-type]
            name,
            payload,
            page_id,
        )


def add_page(page: PagePresentation) -> PresentationOperation:
    return PresentationOperation("add", "page", page.page_id, page)


def update_page(page: PagePresentation) -> PresentationOperation:
    return PresentationOperation("update", "page", page.page_id, page)


def remove_page(page_id: str) -> PresentationOperation:
    return PresentationOperation("remove", "page", page_id)


def add_visual(page_id: str, visual: VisualPresentation) -> PresentationOperation:
    return PresentationOperation("add", "visual", visual.visual_id, visual, page_id)


def update_visual(page_id: str, visual: VisualPresentation) -> PresentationOperation:
    return PresentationOperation("update", "visual", visual.visual_id, visual, page_id)


def remove_visual(page_id: str, visual_id: str) -> PresentationOperation:
    return PresentationOperation("remove", "visual", visual_id, None, page_id)


def materialize_presentation(
    presentation: PresentationIR, operations: list[PresentationOperation]
) -> PresentationIR:
    """Apply a batch atomically and return a validated PresentationIR snapshot."""
    pages = {page.page_id: page for page in presentation.pages}
    order = [page.page_id for page in presentation.pages]

    for operation in operations:
        if operation.target == "page":
            exists = operation.name in pages
            if operation.kind == "add":
                if exists:
                    raise ValueError(f"cannot add page {operation.name!r}: already exists")
                assert isinstance(operation.payload, PagePresentation)
                pages[operation.name] = operation.payload
                order.append(operation.name)
            elif operation.kind == "update":
                if not exists:
                    raise ValueError(f"cannot update page {operation.name!r}: not found")
                assert isinstance(operation.payload, PagePresentation)
                pages[operation.name] = operation.payload
            else:
                if not exists:
                    raise ValueError(f"cannot remove page {operation.name!r}: not found")
                del pages[operation.name]
                order.remove(operation.name)
            continue

        assert operation.page_id is not None
        page = pages.get(operation.page_id)
        if page is None:
            raise ValueError(f"cannot {operation.kind} visual: page {operation.page_id!r} not found")
        visuals = {visual.visual_id: visual for visual in page.visuals}
        visual_order = [visual.visual_id for visual in page.visuals]
        exists = operation.name in visuals
        if operation.kind == "add":
            if exists:
                raise ValueError(
                    f"cannot add visual {operation.name!r} on page {operation.page_id!r}: already exists"
                )
            assert isinstance(operation.payload, VisualPresentation)
            visuals[operation.name] = operation.payload
            visual_order.append(operation.name)
        elif operation.kind == "update":
            if not exists:
                raise ValueError(
                    f"cannot update visual {operation.name!r} on page {operation.page_id!r}: not found"
                )
            assert isinstance(operation.payload, VisualPresentation)
            visuals[operation.name] = operation.payload
        else:
            if not exists:
                raise ValueError(
                    f"cannot remove visual {operation.name!r} on page {operation.page_id!r}: not found"
                )
            del visuals[operation.name]
            visual_order.remove(operation.name)
        pages[operation.page_id] = replace(
            page, visuals=tuple(visuals[visual_id] for visual_id in visual_order)
        )

    result = replace(presentation, pages=tuple(pages[page_id] for page_id in order))
    result.validate()
    return result


__all__ = [
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
]
