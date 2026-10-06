"""Comparador de resultados DAX contra valores esperados del corpus."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ComparisonResult:
    """Resultado de comparar una medida DAX contra su valor esperado.

    Attributes:
        visual_id: Identificador del visual evaluado.
        measure: Nombre de la medida evaluada.
        status: Uno de ``'match'``, ``'mismatch'`` o ``'not_evaluable'``.
        detail: Detalle del resultado (vacío si hubo match).
    """

    visual_id: str
    measure: str
    status: str  # 'match' | 'mismatch' | 'not_evaluable'
    detail: str


def compare_visual(
    visual_id: str,
    measure: str,
    expected_value: float | None,
    tolerance: float,
    dax_value: float | None,
) -> ComparisonResult:
    """Compara el valor DAX de un visual contra el esperado con tolerancia relativa.

    Args:
        visual_id: Identificador del visual evaluado.
        measure: Nombre de la medida evaluada.
        expected_value: Valor esperado; None si falta el oráculo.
        tolerance: Tolerancia relativa permitida sobre el valor esperado.
        dax_value: Valor obtenido en Power BI; None si no fue evaluable.

    Returns:
        ComparisonResult con estado 'match', 'mismatch' o 'not_evaluable',
        indicando en detail cuál valor falta cuando corresponde.
    """
    if expected_value is None or dax_value is None:
        missing = "expected_value" if expected_value is None else "dax_value"
        return ComparisonResult(visual_id, measure, "not_evaluable", f"missing {missing}")
    if abs(dax_value - expected_value) <= tolerance * max(1.0, abs(expected_value)):
        return ComparisonResult(visual_id, measure, "match", "")
    return ComparisonResult(
        visual_id, measure, "mismatch", f"expected={expected_value} dax={dax_value}"
    )


def compare_corpus(
    oracles: list[dict],
    queries: list[dict],
    results: dict[str, float | None],
) -> list[ComparisonResult]:
    """Compara todas las entradas del corpus casando por visual_id.

    Args:
        oracles: Entradas con claves visual_id, measure y expected_value;
            tolerance es opcional (0.001 por defecto).
        queries: Consultas del corpus; aportan measure si el oráculo no la trae.
        results: Valores DAX por visual_id (None o ausente si no evaluable).

    Returns:
        Lista de ComparisonResult, una por entrada de oráculo.
    """
    query_measures = {q["visual_id"]: q.get("measure", "") for q in queries}
    return [
        compare_visual(
            oracle["visual_id"],
            oracle.get("measure") or query_measures.get(oracle["visual_id"], ""),
            oracle.get("expected_value"),
            oracle.get("tolerance", 0.001),
            results.get(oracle["visual_id"]),
        )
        for oracle in oracles
    ]
