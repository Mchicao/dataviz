"""Cross-IR coherence and referential integrity validation for canonical contracts.

Validates referential consistency across the canonical three IRs:
- PresentationIR <-> InteractionIR: page IDs, visual IDs, drill targets, tooltip targets, navigation targets.
- SemanticModel <-> PresentationIR: visual data bindings against available fields/metrics/parameters.
- SemanticModel <-> InteractionIR: filter targets, selection targets against available fields/metrics/parameters.
"""

from __future__ import annotations

from core.contracts.custom_visual import (
    CUSTOM_VISUAL_SPEC_PROPERTY,
    CustomVisualSpec,
    referenced_roles,
)
from core.contracts.interaction_ir import InteractionIR
from core.contracts.presentation_ir import (
    FieldRole,
    PresentationIR,
    VisualIntentKind,
)
from core.contracts.semantic_ir import SemanticModel


def _validate_custom_visual_contract(presentation: PresentationIR) -> None:
    """Valida el contrato de custom visuals contra los bindings del propio visual.

    Reglas (fail-closed):
    - Un ``custom_visual`` sin spec se conserva como fallback opaco para no
      perder intents de origen todavía no representables.
    - Cuando lleva ``properties['custom_visual_spec']``, la spec debe ser válida
      y cada rol ``x_role``/``y_role`` debe existir entre sus bindings.
    - Un visual con otro intent no puede llevar ``custom_visual_spec``.
    """
    for page in presentation.pages:
        for visual in page.visuals:
            raw_spec = visual.properties.get(CUSTOM_VISUAL_SPEC_PROPERTY)
            is_custom = visual.intent == VisualIntentKind.CUSTOM_VISUAL
            if raw_spec is None:
                continue
            if not is_custom:
                raise ValueError(
                    f"Visual {visual.visual_id!r} on page {page.page_id!r} is not a "
                    f"custom_visual intent but carries {CUSTOM_VISUAL_SPEC_PROPERTY}"
                )
            spec = CustomVisualSpec.from_dict(raw_spec)
            binding_roles = {
                binding.role.value if isinstance(binding.role, FieldRole) else str(binding.role)
                for binding in visual.bindings
            }
            missing_roles = referenced_roles(spec) - binding_roles
            if missing_roles:
                raise ValueError(
                    f"Custom visual {visual.visual_id!r} on page {page.page_id!r} "
                    f"references binding role(s) {sorted(missing_roles)} not present "
                    "in its bindings"
                )


def validate_cross_ir_coherence(
    semantic: SemanticModel,
    presentation: PresentationIR | None = None,
    interaction: InteractionIR | None = None,
) -> None:
    """Validate referential integrity across the canonical three IRs.

    Raises:
        ValueError: If referential integrity or consistency is violated.
    """
    if presentation is not None and interaction is not None:
        if presentation.doc_id and interaction.doc_id and presentation.doc_id != interaction.doc_id:
            raise ValueError(
                f"PresentationIR doc_id {presentation.doc_id!r} and "
                f"InteractionIR doc_id {interaction.doc_id!r} do not match"
            )

        pres_page_map = {p.page_id: p for p in presentation.pages}
        inter_page_ids = {p.page_id for p in interaction.pages}
        orphan_pages = inter_page_ids - set(pres_page_map.keys())
        if orphan_pages:
            raise ValueError(
                f"InteractionIR references nonexistent presentation page(s): {sorted(orphan_pages)}"
            )

        for inter_page in interaction.pages:
            pres_page = pres_page_map[inter_page.page_id]
            page_visual_ids = {v.visual_id for v in pres_page.visuals}

            for f in inter_page.filters:
                orphan_v = set(f.target_visual_ids) - page_visual_ids
                if orphan_v:
                    raise ValueError(
                        f"Filter {f.filter_id!r} on page {inter_page.page_id!r} "
                        f"references nonexistent visual(s): {sorted(orphan_v)}"
                    )

            for cf in inter_page.cross_filters:
                if cf.source_visual_id not in page_visual_ids:
                    raise ValueError(
                        f"Cross filter {cf.rule_id!r} references nonexistent source visual "
                        f"{cf.source_visual_id!r} on page {inter_page.page_id!r}"
                    )
                orphan_cv = set(cf.target_visual_ids) - page_visual_ids
                if orphan_cv:
                    raise ValueError(
                        f"Cross filter {cf.rule_id!r} references nonexistent target visual(s): "
                        f"{sorted(orphan_cv)} on page {inter_page.page_id!r}"
                    )

            for d in inter_page.drills:
                if d.target_visual_id not in page_visual_ids:
                    raise ValueError(
                        f"Drill {d.drill_id!r} references nonexistent target visual "
                        f"{d.target_visual_id!r} on page {inter_page.page_id!r}"
                    )
                if d.drill_through_page_id and d.drill_through_page_id not in pres_page_map:
                    raise ValueError(
                        f"Drill {d.drill_id!r} references nonexistent drill_through_page_id "
                        f"{d.drill_through_page_id!r}"
                    )

            for t in inter_page.tooltips:
                if t.source_visual_id not in page_visual_ids:
                    raise ValueError(
                        f"Tooltip {t.tooltip_id!r} references nonexistent source visual "
                        f"{t.source_visual_id!r} on page {inter_page.page_id!r}"
                    )
                if t.target_page_id and t.target_page_id not in pres_page_map:
                    raise ValueError(
                        f"Tooltip {t.tooltip_id!r} references nonexistent target_page_id "
                        f"{t.target_page_id!r}"
                    )

            for n in inter_page.navigations:
                if n.target_page_id and n.target_page_id not in pres_page_map:
                    raise ValueError(
                        f"Navigation {n.action_id!r} references nonexistent target_page_id "
                        f"{n.target_page_id!r}"
                    )

            for s in inter_page.selections:
                if s.source_visual_id not in page_visual_ids:
                    raise ValueError(
                        f"Selection {s.selection_id!r} references nonexistent source visual "
                        f"{s.source_visual_id!r} on page {inter_page.page_id!r}"
                    )

        all_visual_ids = {v.visual_id for p in presentation.pages for v in p.visuals}
        for gf in interaction.global_filters:
            orphan_gv = set(gf.target_visual_ids) - all_visual_ids
            if orphan_gv:
                raise ValueError(
                    f"Global filter {gf.filter_id!r} references nonexistent visual(s): {sorted(orphan_gv)}"
                )

    if presentation is not None:
        _validate_custom_visual_contract(presentation)

    available_names = (
        {f.name for e in semantic.entities for f in e.fields}
        | {f"{e.name}.{f.name}" for e in semantic.entities for f in e.fields}
        | {m.name for m in semantic.metrics}
        | {p.name for p in semantic.parameters}
    )
    if available_names:
        if presentation is not None:
            for page in presentation.pages:
                for visual in page.visuals:
                    for binding in visual.bindings:
                        if binding.field_name and binding.field_name not in available_names:
                            raise ValueError(
                                f"Visual {visual.visual_id!r} references nonexistent "
                                f"semantic field or metric {binding.field_name!r}"
                            )

        if interaction is not None:
            for gf in interaction.global_filters:
                if gf.target_field and gf.target_field not in available_names:
                    raise ValueError(
                        f"Global filter {gf.filter_id!r} references nonexistent semantic field "
                        f"{gf.target_field!r}"
                    )

            for inter_page in interaction.pages:
                for f in inter_page.filters:
                    if f.target_field and f.target_field not in available_names:
                        raise ValueError(
                            f"Filter {f.filter_id!r} references nonexistent semantic field "
                            f"{f.target_field!r}"
                        )
                for s in inter_page.selections:
                    if s.target_field and s.target_field not in available_names:
                        raise ValueError(
                            f"Selection {s.selection_id!r} references nonexistent semantic field "
                            f"{s.target_field!r}"
                        )


__all__ = ["validate_cross_ir_coherence"]
