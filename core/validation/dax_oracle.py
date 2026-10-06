"""Oráculo numérico por visual del RenderPlan.

Lee el plan de render (:class:`~core.compilers.render_plan.RenderPlan`) y la IR
semántica neutra (:class:`~core.contracts.semantic_ir.SemanticModel`) desde sus
JSON canónicos y deriva, por visual, el valor numérico esperado de su medida
cuando la IR lo permite: sólo cuando la expresión de la medida es una constante
plegable (literales y aritmética entre literales).

Las medidas que dependen de datos (agregaciones, campos, parámetros u otras
medidas) se marcan ``not_evaluable`` con una razón explícita: este módulo nunca
inventa números esperados. La tolerancia por defecto es relativa (0.001), con
la misma semántica que :func:`core.validation.dax_result_comparator.compare`.
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.compilers.render_plan import RenderPlan
from core.contracts.semantic_ir import Expression, SemanticModel

#: Tolerancia relativa por defecto (fracción del valor esperado).
_DEFAULT_TOLERANCE: float = 0.001

#: Aritmética ente literales plegable en tiempo de extracción.
_BINARY_ARITHMETIC: dict[str, Callable[[float, float], float]] = {
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "div": operator.truediv,
}

#: Razones explícitas por clase de nodo no constante.
_KIND_REASONS: dict[str, str] = {
    "field_ref": "referencia a campo sin datos o fixtures para evaluarla",
    "agg": "agregación dependiente de datos sin fixtures",
    "measure_ref": "depende del valor de otra medida",
    "parameter_ref": "depende de un parámetro",
}


@dataclass(frozen=True)
class VisualOracle:
    """Oráculo numérico esperado para un visual individual del RenderPlan."""

    visual_id: str
    measure: str
    expected_value: float | None
    tolerance: float
    status: Literal["expected", "not_evaluable"]
    reason: str | None


def _fold_constant(expr: Expression) -> tuple[float | None, str | None]:
    """Intenta plegar una expresión neutra a un valor numérico constante.

    Returns:
        Tupla ``(valor, razón)`` mutuamente excluyente: valor numérico cuando
        la expresión es constante plegable, o razón explícita cuando no.
    """
    kind = expr.kind
    if kind == "literal":
        value = expr.value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None, f"constante no numérica: {value!r}"
        return float(value), None
    if kind == "unary":
        operand, why = _fold_constant(expr.children[0])
        if operand is None:
            return None, why
        if expr.op != "neg":
            return None, f"operador unario no aritmético: {expr.op!r}"
        return -operand, None
    if kind == "binary":
        left, left_why = _fold_constant(expr.children[0])
        if left is None:
            return None, left_why
        right, right_why = _fold_constant(expr.children[1])
        if right is None:
            return None, right_why
        operation = _BINARY_ARITHMETIC.get(expr.op)
        if operation is None:
            return None, f"operador binario no aritmético: {expr.op!r}"
        if expr.op == "div" and right == 0:
            return None, "división por cero en expresión constante"
        return operation(left, right), None
    return None, _KIND_REASONS.get(kind, f"expresión no constante: kind={kind!r}")


def _visual_oracle(
    visual_id: str, measure: str, value: float | None, reason: str | None
) -> VisualOracle:
    """Construye el oráculo deduciendo el estado del valor obtenido."""
    return VisualOracle(
        visual_id=visual_id,
        measure=measure,
        expected_value=value,
        tolerance=_DEFAULT_TOLERANCE,
        status="expected" if value is not None else "not_evaluable",
        reason=reason,
    )


def extract_visual_oracles(render_plan_path: Path, semantic_ir_path: Path) -> list[VisualOracle]:
    """Deriva el valor numérico esperado de la medida de cada visual con medida.

    Args:
        render_plan_path: Ruta al JSON canónico del :class:`RenderPlan`.
        semantic_ir_path: Ruta al JSON canónico del :class:`SemanticModel`.

    Returns:
        Oráculos en el orden del plan, uno por visual cuyo rol ``value``
        referencia una medida (prefijo ``measure:``). El valor esperado sólo se
        emite si la expresión de la medida es una constante plegable; en otro
        caso el oráculo queda ``not_evaluable`` con razón explícita.
    """
    plan = RenderPlan.from_json(render_plan_path.read_text(encoding="utf-8"))
    model = SemanticModel.from_json(semantic_ir_path.read_text(encoding="utf-8"))
    expressions = {metric.name: metric.expression for metric in model.metrics}
    oracles: list[VisualOracle] = []
    for visual in plan.visuals:
        reference = visual.data_roles.get("value", "")
        if not reference.startswith("measure:"):
            continue
        measure = reference.split(":", 1)[1]
        if measure not in expressions:
            oracles.append(
                _visual_oracle(
                    visual.name, measure, None, "medida no declarada en la IR semántica"
                )
            )
            continue
        value, reason = _fold_constant(expressions[measure])
        oracles.append(_visual_oracle(visual.name, measure, value, reason))
    return oracles


__all__ = ["VisualOracle", "extract_visual_oracles"]
