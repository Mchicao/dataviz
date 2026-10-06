"""Rúbrica visual versionada y scoring determinista (paquete G0-VISUAL, Gate 0).

Este módulo define ``visual_rubric`` v2 y calcula scores por dimensión a
partir de metadatos de payload (geometría, títulos, roles de color, marcas,
interacciones), nunca desde percepción de píxeles. Misma entrada -> mismo score.

Criterio D040 (fidelidad visual total): la equivalencia de diseño
(``design_equivalence``) exige score de fidelidad >= 95 **Y** cero brechas P0
(visual faltante, kind distinto, geometría con IoU bajo el umbral declarado o
títulos distintos contra el oráculo del origen) **Y** cero reducciones de
marcas sin aceptar. Cualquier otro caso queda ``partial`` (o
``requires_manual_review``) con las brechas enumeradas; sólo la aceptación
explícita del usuario por demo puede cerrarlas. El umbral 80 legacy
(``fidelity_threshold_met``) fue eliminado y ya no es ejecutable.

Separación obligatoria (lección DVZ-CORPUS-015): la calidad intrínseca del
dashboard y la fidelidad al origen se puntúan por separado; el score intrínseco
es informativo y no promociona. Lo no computable desde metadatos queda
``not_evaluable`` con causa; nunca un 0 silencioso ni una equivalencia
inventada. Los atributos de diseño que el oráculo del origen no declara
(paleta efectiva, tipografía) quedan ``not_evaluable`` con causa: se comparan
títulos y roles color/series sólo cuando la referencia los declara.

La comparación de píxeles por región sigue viviendo en
``core/validation/visual_similarity.py``; el flujo del operador se documenta en
``output/validation/dataviz_g0_20260829/visual/capture_plan_luna.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

RUBRIC_ID = "visual_rubric"
RUBRIC_VERSION = "2"

#: Umbral de equivalencia de diseño (0-100): score mínimo para que una demo
#: alcance ``design_equivalence``; junto con cero brechas P0 (D040).
DESIGN_EQUIVALENCE_THRESHOLD = 95.0
#: IoU mínimo por visual emparejado; por debajo es brecha P0 de geometría.
P0_GEOMETRY_IOU_THRESHOLD = 0.5
#: Umbral informativo de calidad intrínseca; NO promociona la dimensión visual.
INTRINSIC_THRESHOLD = 70.0

#: Orden canónico de dimensiones de la rúbrica.
DIMENSION_ORDER: tuple[str, ...] = (
    "layout",
    "densidad",
    "color",
    "titulos",
    "interaccion",
    "fidelidad_al_origen",
)

#: Dimensiones que componen el score intrínseco (informativo).
INTRINSIC_DIMENSIONS: tuple[str, ...] = ("layout", "densidad", "titulos", "interaccion")

#: Kinds que no portan marcas de datos ni exigen título propio.
EXEMPT_KINDS: frozenset[str] = frozenset({"slicer", "text_box"})

#: Marcas por visual a partir de las cuales se registra observación de densidad
#: alta (no penaliza el score; su corrección es decisión estética del usuario).
HIGH_MARK_COUNT = 5000

#: Tipos canónicos de brecha P0 (D040): diferencias que impiden por sí mismas
#: la equivalencia de diseño.
P0_BREACH_TYPES: tuple[str, ...] = (
    "missing_visual",
    "kind_mismatch",
    "geometry_iou_below_threshold",
    "title_mismatch",
)

# Pesos de la fidelidad por componente (se renormalizan según disponibilidad).
# Estructurales: cobertura, kind, IoU, marcas. De diseño: títulos y roles
# color/series declarados por el oráculo del origen.
_FIDELITY_WEIGHTS: dict[str, float] = {
    "visual_coverage_ratio": 0.3,
    "kind_match_ratio": 0.15,
    "geometry_iou_mean": 0.15,
    "marks_ratio_median": 0.1,
    "title_match_ratio": 0.2,
    "color_role_match_ratio": 0.1,
}

#: Componentes del score de fidelidad, en orden canónico para enumerar causas.
_FIDELITY_COMPONENT_KEYS: tuple[str, ...] = tuple(_FIDELITY_WEIGHTS)

_SCORED = "scored"
_NOT_EVALUABLE = "not_evaluable"
_REQUIRES_MANUAL_REVIEW = "requires_manual_review"


def _round(value: float, digits: int = 4) -> float:
    """Redondeo estable para evitar ruido de coma flotante entre corridas."""
    return round(value, digits)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _render_text(value: Any) -> str:
    """Normalize only whitespace that browser/Tableau text rendering collapses."""
    return " ".join(str(value or "").split())


def _rect(visual: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """Extrae (x, y, width, height) sólo si la geometría es válida."""
    geometry = visual.get("geometry")
    if not isinstance(geometry, dict):
        return None
    try:
        x = float(geometry["x"])
        y = float(geometry["y"])
        width = float(geometry["width"])
        height = float(geometry["height"])
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


def _intersection(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[0] + a[2], b[0] + b[2])
    y2 = min(a[1] + a[3], b[1] + b[3])
    return max(0.0, x2 - x1) * max(0.0, y2 - y1)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    intersection = _intersection(a, b)
    union = a[2] * a[3] + b[2] * b[3] - intersection
    return intersection / union if union > 0 else 0.0


def _marks_count(results_visuals: dict[str, Any], name: str) -> int:
    """Número de filas (marcas) de un visual según ``results.visuals``."""
    columns = results_visuals.get(name)
    if not isinstance(columns, dict):
        return 0
    return max((len(values) for values in columns.values() if isinstance(values, list)), default=0)


@dataclass(frozen=True)
class DimensionScore:
    """Resultado de una dimensión: score 0-100 o causa explícita."""

    dimension: str
    status: str  # scored | not_evaluable | requires_manual_review
    score: float | None = None
    cause: str | None = None
    observations: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "status": self.status,
            "score": self.score,
            "cause": self.cause,
            "observations": self.observations,
        }


@dataclass(frozen=True)
class RubricEvaluation:
    """Evaluación completa de una demo según la rúbrica (D040)."""

    case_id: str
    dimensions: dict[str, DimensionScore]
    score_intrinseco: float | None
    score_fidelidad: float | None
    verdict: str
    causes: tuple[str, ...]
    p0_breaches: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "rubric_id": RUBRIC_ID,
            "rubric_version": RUBRIC_VERSION,
            "dimensions": {name: score.to_dict() for name, score in self.dimensions.items()},
            "score_intrinseco": self.score_intrinseco,
            "score_fidelidad": self.score_fidelidad,
            "verdict": self.verdict,
            "causes": list(self.causes),
            "p0_breaches": list(self.p0_breaches),
            "no_promocion": (
                "score_intrinseco es informativo; la dimensión visual sólo aprueba "
                "por equivalencia de diseño (score >= "
                f"{DESIGN_EQUIVALENCE_THRESHOLD} y cero brechas P0) o por brecha "
                "aceptada explícitamente por el usuario por demo (D040)"
            ),
        }


# ---------------------------------------------------------------------------
# Dimensiones intrínsecas (metadatos del payload DataVIZ)
# ---------------------------------------------------------------------------


def score_layout(visuals: list[dict[str, Any]]) -> DimensionScore:
    """Layout: geometría válida y solapamientos, desde ``plan.visuals[].geometry``.

    Métricas: ``valid_geometry_ratio`` (visuales con rectángulo positivo) y
    ``overlap_index`` (suma de intersecciones par-a-par / suma de áreas).
    Score = 100 * valid_ratio * (1 - overlap_index).
    """
    if not visuals:
        return DimensionScore("layout", _NOT_EVALUABLE, cause="payload sin visuales: no hay geometría que evaluar")
    rects = [_rect(visual) for visual in visuals]
    valid = [rect for rect in rects if rect is not None]
    valid_ratio = len(valid) / len(visuals)
    if not valid:
        return DimensionScore(
            "layout",
            _NOT_EVALUABLE,
            cause="ningún visual declara geometría válida (x, y, width, height positivos)",
            observations={"valid_geometry_ratio": 0.0},
        )
    total_area = sum(w * h for _, _, w, h in valid)
    overlap_area = 0.0
    for i in range(len(valid)):
        for j in range(i + 1, len(valid)):
            overlap_area += _intersection(valid[i], valid[j])
    overlap_index = min(1.0, overlap_area / total_area) if total_area > 0 else 0.0
    score = 100.0 * valid_ratio * (1.0 - overlap_index)
    return DimensionScore(
        "layout",
        _SCORED,
        score=_round(score, 2),
        observations={
            "valid_geometry_ratio": _round(valid_ratio),
            "overlap_index": _round(overlap_index),
            "visuals_total": len(visuals),
        },
    )


def score_density(visuals: list[dict[str, Any]], results_visuals: dict[str, Any] | None) -> DimensionScore:
    """Densidad: marcas por visual desde ``results.visuals`` (filas por columna).

    Los visuales ``slicer``/``text_box`` están exentos. Score = 100 * (1 -
    vacíos/mark_bearing). La densidad excesiva se observa sin penalizar: su
    corrección es estética y queda para revisión manual.
    """
    if results_visuals is None:
        return DimensionScore(
            "densidad",
            _NOT_EVALUABLE,
            cause="payload sin results.visuals: no hay conteo de marcas disponible",
        )
    bearing = [visual for visual in visuals if _text(visual.get("kind")) not in EXEMPT_KINDS]
    if not bearing:
        return DimensionScore(
            "densidad",
            _NOT_EVALUABLE,
            cause="sin visuales portadores de marcas (sólo slicers/text_box): densidad no aplicable",
        )
    marks_by_visual = {visual.get("name", ""): _marks_count(results_visuals, visual.get("name", "")) for visual in bearing}
    empty = [name for name, count in marks_by_visual.items() if count == 0]
    dense = {name: count for name, count in sorted(marks_by_visual.items()) if count > HIGH_MARK_COUNT}
    score = 100.0 * (1.0 - len(empty) / len(bearing))
    return DimensionScore(
        "densidad",
        _SCORED,
        score=_round(score, 2),
        observations={
            "mark_bearing_visuals": len(bearing),
            "empty_mark_visuals": len(empty),
            "empty_visual_names": sorted(empty),
            "marks_por_visual": dict(sorted(marks_by_visual.items())),
            "max_marks": max(marks_by_visual.values(), default=0),
            "high_density_visuals": dense,
            "high_density_threshold": HIGH_MARK_COUNT,
        },
    )


def score_titles(visuals: list[dict[str, Any]]) -> DimensionScore:
    """Títulos: ``plan.visuals[].title`` no vacío para visuales con marcas.

    Los ``slicer``/``text_box`` están exentos. Score = 100 * titulados/requeridos.
    """
    required = [visual for visual in visuals if _text(visual.get("kind")) not in EXEMPT_KINDS]
    if not required:
        return DimensionScore(
            "titulos",
            _NOT_EVALUABLE,
            cause="sin visuales que exijan título (sólo slicers/text_box)",
        )
    untitled = [visual.get("name", "") for visual in required if not _text(visual.get("title"))]
    score = 100.0 * (1.0 - len(untitled) / len(required))
    return DimensionScore(
        "titulos",
        _SCORED,
        score=_round(score, 2),
        observations={
            "required_visuals": len(required),
            "untitled_visuals": sorted(untitled),
        },
    )


def score_color(visuals: list[dict[str, Any]]) -> DimensionScore:
    """Color: sólo la cobertura de roles ``color``/``series`` es computable.

    La paleta efectiva renderizada requiere comparación de píxeles contra el
    oráculo (operador Luna), por eso la dimensión queda
    ``requires_manual_review`` con la observación metadata adjunta.
    """
    required = [visual for visual in visuals if _text(visual.get("kind")) not in EXEMPT_KINDS]
    if not required:
        return DimensionScore(
            "color",
            _NOT_EVALUABLE,
            cause="sin visuales que porten color (sólo slicers/text_box)",
        )
    with_role = sum(
        1
        for visual in required
        if isinstance(visual.get("data_roles"), dict)
        and (visual["data_roles"].get("color") or visual["data_roles"].get("series"))
    )
    coverage = with_role / len(required)
    return DimensionScore(
        "color",
        _REQUIRES_MANUAL_REVIEW,
        cause=(
            "la paleta efectiva requiere captura renderizada contra el oráculo "
            "(operador Luna); desde metadatos sólo es computable la cobertura de "
            "roles color/series, que no mide calidad cromática"
        ),
        observations={
            "color_role_coverage": _round(coverage),
            "visuals_with_color_role": with_role,
            "required_visuals": len(required),
        },
    )


def score_interaction(visuals: list[dict[str, Any]], interactions: list[dict[str, Any]] | None) -> DimensionScore:
    """Interacción: resolvibilidad de ``plan.interactions`` (source/target existen).

    Sin interacciones ni slicers declarados queda ``not_evaluable``: la ausencia
    puede ser fidelidad legítima al origen o pérdida, y los metadatos no
    permiten distinguirlo.
    """
    names = {_text(visual.get("name")) for visual in visuals}
    slicers = sum(1 for visual in visuals if _text(visual.get("kind")) == "slicer")
    declared = interactions or []
    if not declared and slicers == 0:
        return DimensionScore(
            "interaccion",
            _NOT_EVALUABLE,
            cause="payload sin interacciones ni slicers declarados: no distinguible entre ausencia legítima y pérdida",
        )
    if not declared:
        return DimensionScore(
            "interaccion",
            _SCORED,
            score=100.0,
            observations={"declared_interactions": 0, "slicers": slicers, "unresolved_interactions": 0},
        )
    unresolved = [
        _text(item.get("name")) or f"index_{index}"
        for index, item in enumerate(declared)
        if _text(item.get("source")) not in names or _text(item.get("target")) not in names
    ]
    score = 100.0 * (1.0 - len(unresolved) / len(declared))
    return DimensionScore(
        "interaccion",
        _SCORED,
        score=_round(score, 2),
        observations={
            "declared_interactions": len(declared),
            "slicers": slicers,
            "unresolved_interactions": len(unresolved),
            "unresolved_names": sorted(unresolved),
        },
    )


# ---------------------------------------------------------------------------
# Fidelidad al origen (requiere oráculo comparable)
# ---------------------------------------------------------------------------


def _color_role(visual: dict[str, Any]) -> str | None:
    """Rol de color/serie declarado para un visual, o None si no lo declara."""
    roles = visual.get("data_roles")
    if not isinstance(roles, dict):
        return None
    for key in ("color", "series"):
        value = _text(roles.get(key))
        if value:
            return f"{key}:{value}"
    return None


def score_fidelity(
    reference_payload: dict[str, Any] | None,
    actual_payload: dict[str, Any] | None,
) -> DimensionScore:
    """Fidelidad al origen: compara metadatos del oráculo contra el payload DataVIZ.

    Componentes estructurales: cobertura de visuales, match de kind, IoU de
    geometría de visuales emparejados y mediana de razón de marcas. Componentes
    de diseño (D040): ratio de títulos declarados idénticos y ratio de roles
    color/series declarados idénticos; sólo se comparan cuando la referencia
    los declara, y quedan ``not_evaluable`` con causa si no. Los pesos se
    renormalizan según componentes disponibles. Además enumera las brechas P0
    (``missing_visual``, ``kind_mismatch``,
    ``geometry_iou_below_threshold``, ``title_mismatch``) que impiden la
    equivalencia de diseño aunque el score sea alto. Sin payload de referencia
    (Tableau/Power BI) queda ``requires_manual_review``: nunca se infiere.
    """
    if reference_payload is None:
        return DimensionScore(
            "fidelidad_al_origen",
            _REQUIRES_MANUAL_REVIEW,
            cause=(
                "sin oráculo del origen (metadatos Tableau/Power BI o captura del "
                "operador) comparable: la fidelidad no se infiere ni se da por buena"
            ),
        )
    reference_plan = reference_payload.get("plan") or {}
    actual_plan = (actual_payload or {}).get("plan") or {}
    reference_visuals = reference_plan.get("visuals") or []
    actual_visuals = actual_plan.get("visuals") or []
    if not reference_visuals:
        return DimensionScore(
            "fidelidad_al_origen",
            _NOT_EVALUABLE,
            cause="el oráculo de referencia no declara visuales",
        )

    actual_by_name = {_text(visual.get("name")): visual for visual in actual_visuals}
    reference_by_name = {_text(visual.get("name")): visual for visual in reference_visuals}
    reference_rects: dict[str, tuple[float, float, float, float]] = {}
    for visual in reference_visuals:
        rect = _rect(visual)
        if rect is not None:
            reference_rects[_text(visual.get("name"))] = rect

    reference_names = set(reference_by_name)
    matched = sorted(reference_names & set(actual_by_name))
    missing = sorted(reference_names - set(actual_by_name))

    coverage = len(matched) / len(reference_names)
    kind_paired = [name for name in matched if _text(reference_by_name[name].get("kind"))]
    kind_matches = sum(
        1
        for name in kind_paired
        if _text(actual_by_name[name].get("kind")) == _text(reference_by_name[name].get("kind"))
    )
    kind_ratio = kind_matches / len(kind_paired) if kind_paired else None

    ious: list[float] = []
    for name in matched:
        actual_rect = _rect(actual_by_name[name])
        if name in reference_rects and actual_rect is not None:
            ious.append(_iou(reference_rects[name], actual_rect))
    iou_mean = sum(ious) / len(ious) if ious else None

    reference_results = (reference_payload.get("results") or {}).get("visuals") or {}
    actual_results = ((actual_payload or {}).get("results") or {}).get("visuals") or {}
    mark_density_by_visual: list[dict[str, Any]] = []
    if reference_results and actual_results:
        for name in matched:
            reference_marks = _marks_count(reference_results, name)
            actual_marks = _marks_count(actual_results, name)
            if reference_marks > 0:
                ratio = min(1.0, actual_marks / reference_marks)
                reduced = actual_marks < reference_marks
                mark_density_by_visual.append(
                    {
                        "visual": name,
                        "source_mark_count": reference_marks,
                        "rendered_mark_count": actual_marks,
                        "ratio": _round(ratio),
                        "classification": "approximation" if reduced else "scored",
                        "review_status": "requires_manual_review" if reduced else None,
                    }
                )
    mark_ratios = [item["ratio"] for item in mark_density_by_visual]
    mark_density_reductions = [
        item for item in mark_density_by_visual if item["classification"] == "approximation"
    ]
    marks_median = sorted(mark_ratios)[len(mark_ratios) // 2] if mark_ratios else None

    # Componentes de diseño: sólo contra atributos que la referencia declara.
    title_pairs = 0
    title_matches = 0
    title_mismatches: list[dict[str, Any]] = []
    for name in matched:
        reference_title_raw = _text(reference_by_name[name].get("title"))
        if not reference_title_raw:
            continue
        title_pairs += 1
        actual_title_raw = _text(actual_by_name[name].get("title"))
        reference_title = _render_text(reference_title_raw)
        actual_title = _render_text(actual_title_raw)
        if actual_title.casefold() == reference_title.casefold():
            title_matches += 1
        else:
            title_mismatches.append({"visual": name, "reference": reference_title_raw, "actual": actual_title_raw})
    title_ratio = title_matches / title_pairs if title_pairs else None

    color_pairs = 0
    color_matches = 0
    color_mismatches: list[dict[str, Any]] = []
    for name in matched:
        reference_role = _color_role(reference_by_name[name])
        if reference_role is None:
            continue
        color_pairs += 1
        actual_role = _color_role(actual_by_name[name])
        if actual_role == reference_role:
            color_matches += 1
        else:
            color_mismatches.append({"visual": name, "reference": reference_role, "actual": actual_role or ""})
    color_ratio = color_matches / color_pairs if color_pairs else None

    # Brechas P0 (D040): diferencias que por sí mismas impiden la equivalencia
    # de diseño, independientemente del score agregado.
    p0_breaches: list[dict[str, Any]] = []
    for name in missing:
        p0_breaches.append({"type": "missing_visual", "visual": name})
    for name in matched:
        reference_kind = _text(reference_by_name[name].get("kind"))
        actual_kind = _text(actual_by_name[name].get("kind"))
        if reference_kind and reference_kind != actual_kind:
            p0_breaches.append(
                {"type": "kind_mismatch", "visual": name, "reference": reference_kind, "actual": actual_kind}
            )
        reference_rect = reference_rects.get(name)
        if reference_rect is None:
            continue
        actual_rect = _rect(actual_by_name[name])
        iou = _iou(reference_rect, actual_rect) if actual_rect is not None else 0.0
        if iou < P0_GEOMETRY_IOU_THRESHOLD:
            p0_breaches.append(
                {
                    "type": "geometry_iou_below_threshold",
                    "visual": name,
                    "iou": _round(iou),
                    "threshold": P0_GEOMETRY_IOU_THRESHOLD,
                }
            )
    for mismatch in title_mismatches:
        p0_breaches.append({"type": "title_mismatch", **mismatch})

    available: dict[str, float] = {"visual_coverage_ratio": coverage}
    if kind_ratio is not None:
        available["kind_match_ratio"] = kind_ratio
    if iou_mean is not None:
        available["geometry_iou_mean"] = iou_mean
    if marks_median is not None:
        available["marks_ratio_median"] = marks_median
    if title_ratio is not None:
        available["title_match_ratio"] = title_ratio
    if color_ratio is not None:
        available["color_role_match_ratio"] = color_ratio
    total_weight = sum(_FIDELITY_WEIGHTS[key] for key in available)
    weighted = sum(_FIDELITY_WEIGHTS[key] * value for key, value in available.items()) / total_weight

    observations: dict[str, Any] = {
        "reference_visuals": len(reference_names),
        "actual_visuals": len(actual_by_name),
        "matched_visuals": len(matched),
        "missing_visuals": len(missing),
        "missing_visual_names": missing,
        "extra_visuals": len(set(actual_by_name) - reference_names),
        "visual_coverage_ratio": _round(coverage),
        "kind_match_ratio": None if kind_ratio is None else _round(kind_ratio),
        "kind_paired_visuals": len(kind_paired),
        "geometry_iou_mean": None if iou_mean is None else _round(iou_mean),
        "geometry_paired_visuals": len(ious),
        "marks_ratio_median": None if marks_median is None else _round(marks_median),
        "marks_paired_visuals": len(mark_ratios),
        "mark_density_by_visual": mark_density_by_visual,
        "mark_density_reductions": mark_density_reductions,
        "mark_density_reduction_count": len(mark_density_reductions),
        "title_match_ratio": None if title_ratio is None else _round(title_ratio),
        "title_paired_visuals": title_pairs,
        "title_mismatches": title_mismatches,
        "color_role_match_ratio": None if color_ratio is None else _round(color_ratio),
        "color_role_paired_visuals": color_pairs,
        "color_role_mismatches": color_mismatches,
        "p0_geometry_iou_threshold": P0_GEOMETRY_IOU_THRESHOLD,
        "p0_breaches": p0_breaches,
        "p0_breach_count": len(p0_breaches),
    }
    if title_ratio is None:
        observations["title_not_evaluable_cause"] = (
            "el oráculo del origen no declara títulos comparables: el componente "
            "de diseño de títulos queda not_evaluable"
        )
    if color_ratio is None:
        observations["color_role_not_evaluable_cause"] = (
            "el oráculo del origen no declara roles color/series: el componente "
            "de diseño de codificación cromática queda not_evaluable"
        )
    return DimensionScore(
        "fidelidad_al_origen",
        _SCORED,
        score=_round(100.0 * weighted, 2),
        observations=observations,
    )


# ---------------------------------------------------------------------------
# Rúbrica versionada y evaluación de caso
# ---------------------------------------------------------------------------


def rubric_v1() -> dict[str, Any]:
    """Documento histórico de la rúbrica v1 (criterio 80, obsoleto desde D040).

    Se conserva congelado para trazabilidad de la evidencia
    ``rubric_v1.json``; NO ejecuta veredictos (ver ``rubric_v2`` y
    ``evaluate_case``).
    """
    return {
        "id": RUBRIC_ID,
        "version": "1",
        "created_at": "2026-08-29",
        "package": "G0-VISUAL",
        "gate": "Gate 0",
        "principio": (
            "scoring determinista sobre metadatos/payloads; misma entrada produce "
            "mismo score; nada se puntúa desde percepción subjetiva; lo no "
            "computable queda not_evaluable o requires_manual_review con causa"
        ),
        "escala": "0-100 por dimensión (scores redondeados a 2 decimales)",
        "dimensions": {
            "layout": {
                "grupo": "intrinseco",
                "definicion": "sanidad geométrica del dashboard: visuales con rectángulo válido y sin solapamientos",
                "fuente_computable": "payload.plan.visuals[].geometry (x, y, width, height)",
                "metrica": "valid_geometry_ratio y overlap_index (suma de intersecciones par-a-par / suma de áreas)",
                "formula": "score = 100 * valid_geometry_ratio * (1 - overlap_index)",
                "escala": "0-100",
                "umbral_informativo": INTRINSIC_THRESHOLD,
                "umbral_papel": "informativo; no promociona",
            },
            "densidad": {
                "grupo": "intrinseco",
                "definicion": "visuales portadores de marcas con datos renderizables y densidad observada",
                "fuente_computable": "payload.results.visuals (filas por columna) + plan.visuals[].kind",
                "metrica": "empty_mark_ratio sobre visuales con marcas (slicer/text_box exentos); high_density_visuals > 5000 marcas observado",
                "formula": "score = 100 * (1 - vacios / mark_bearing)",
                "escala": "0-100",
                "umbral_informativo": INTRINSIC_THRESHOLD,
                "umbral_papel": "informativo; la densidad excesiva se registra y su corrección es decisión del usuario",
            },
            "color": {
                "grupo": "intrinseco",
                "definicion": "calidad cromática del dashboard; sólo parcialmente computable",
                "fuente_computable": "plan.visuals[].data_roles.color|series (cobertura); paleta efectiva requiere píxeles",
                "metrica": "color_role_coverage (observación); la dimensión queda requires_manual_review",
                "formula": None,
                "escala": "0-100",
                "escala_nota": "reservada: sin score hasta contar captura del oráculo",
                "umbral_informativo": None,
                "umbral_papel": "requires_manual_review hasta comparación visual del operador",
            },
            "titulos": {
                "grupo": "intrinseco",
                "definicion": "visuales portadores de marcas con título humano no vacío",
                "fuente_computable": "plan.visuals[].title (slicer/text_box exentos)",
                "metrica": "titled_ratio = titulados / requeridos",
                "formula": "score = 100 * titled_ratio",
                "escala": "0-100",
                "umbral_informativo": 80.0,
                "umbral_papel": "informativo; no promociona",
            },
            "interaccion": {
                "grupo": "intrinseco",
                "definicion": "interacciones declaradas con extremos resolubles y presencia de slicers",
                "fuente_computable": "plan.interactions[] (source/target) + visuales kind=slicer",
                "metrica": "resolved_interaction_ratio; sin interacciones ni slicers -> not_evaluable",
                "formula": "score = 100 * resueltas / declaradas",
                "escala": "0-100",
                "umbral_informativo": INTRINSIC_THRESHOLD,
                "umbral_papel": "informativo; la fidelidad de interacciones contra el origen es parte del paquete G0-NAV",
            },
            "fidelidad_al_origen": {
                "grupo": "fidelidad",
                "definicion": "qué tanto el dashboard DataVIZ preserva el origen Tableau/Power BI comparable",
                "fuente_computable": (
                    "score_fidelity(reference_payload, actual_payload): metadatos del "
                    "oráculo (mismo esquema de payload) contra el payload DataVIZ"
                ),
                "metrica": (
                    "visual_coverage_ratio (peso 0.4), kind_match_ratio (0.2), "
                    "geometry_iou_mean de emparejados (0.2), marks_ratio_median (0.2); "
                    "pesos renormalizados según componentes disponibles"
                ),
                "formula": "score = 100 * suma(peso * componente) / suma(pesos disponibles)",
                "escala": "0-100",
                "umbral": 80.0,
                "umbral_papel": (
                    "umbral de aprobación: >= 80 alcanza fidelity_threshold_met; por "
                    "debajo queda below_fidelity_threshold y sólo el usuario puede "
                    "aceptar la brecha; sin oráculo -> requires_manual_review"
                ),
            },
        },
        "grupos": {
            "intrinseco": list(INTRINSIC_DIMENSIONS),
            "fidelidad": ["fidelidad_al_origen"],
        },
        "score_intrinseco": {
            "definicion": "media de las dimensiones intrínsecas con status scored (None si ninguna)",
            "papel": "informativo: describe calidad del dashboard, nunca aprueba la dimensión visual",
        },
        "score_fidelidad": {
            "definicion": "score de fidelidad_al_origen cuando existe oráculo comparable",
            "papel": "única vía de aprobación automática de la dimensión visual",
        },
        "no_promocion": {
            "regla": (
                "un dashboard aprueba visual sólo por UMBRAL DE FIDELIDAD (>= 80) o "
                "por brecha aceptada explícitamente por el usuario; el score "
                "intrínseco perfecto no promociona"
            ),
            "umbral_fidelidad": 80.0,
            "aprobador": "usuario",
        },
        "estados_por_dimension": {
            "scored": "score 0-100 computado desde metadatos",
            "not_evaluable": "sin datos suficientes; con causa obligatoria",
            "requires_manual_review": "requiere oráculo visual o decisión estética; con causa obligatoria",
        },
        "estado": "obsoleta: sustituida por v2 (D040); no ejecuta veredictos",
    }


def rubric_v2() -> dict[str, Any]:
    """Devuelve la rúbrica ``visual_rubric`` v2 completa (serializable a JSON).

    Implementa el criterio D040: la aprobación visual (``design_equivalence``)
    es conjuntiva (score >= 95, cero brechas P0 y cero reducciones de marcas sin
    aceptar) e incorpora componentes de diseño (títulos y roles color/series
    declarados por el oráculo).
    """
    return {
        "id": RUBRIC_ID,
        "version": RUBRIC_VERSION,
        "created_at": "2026-08-30",
        "package": "G0-VISUAL",
        "gate": "Gate 0",
        "decision": "D040",
        "sustituye_a": "visual_rubric v1 (umbral 80 obsoleto, sustituido por el criterio D040)",
        "principio": (
            "scoring determinista sobre metadatos/payloads; misma entrada produce "
            "mismo score; nada se puntúa desde percepción subjetiva; lo no "
            "computable queda not_evaluable o requires_manual_review con causa; "
            "fidelidad visual total: reproducir el diseño del origen, sin ser "
            "pixel-perfect estricto"
        ),
        "escala": "0-100 por dimensión (scores redondeados a 2 decimales)",
        "dimensions": {
            "layout": {
                "grupo": "intrinseco",
                "definicion": "sanidad geométrica del dashboard: visuales con rectángulo válido y sin solapamientos",
                "fuente_computable": "payload.plan.visuals[].geometry (x, y, width, height)",
                "metrica": "valid_geometry_ratio y overlap_index (suma de intersecciones par-a-par / suma de áreas)",
                "formula": "score = 100 * valid_geometry_ratio * (1 - overlap_index)",
                "escala": "0-100",
                "umbral_informativo": INTRINSIC_THRESHOLD,
                "umbral_papel": "informativo; no promociona",
            },
            "densidad": {
                "grupo": "intrinseco",
                "definicion": "visuales portadores de marcas con datos renderizables y densidad observada",
                "fuente_computable": "payload.results.visuals (filas por columna) + plan.visuals[].kind",
                "metrica": "empty_mark_ratio sobre visuales con marcas (slicer/text_box exentos); high_density_visuals > 5000 marcas observado",
                "formula": "score = 100 * (1 - vacios / mark_bearing)",
                "escala": "0-100",
                "umbral_informativo": INTRINSIC_THRESHOLD,
                "umbral_papel": "informativo; la densidad excesiva se registra y su corrección es decisión del usuario",
            },
            "color": {
                "grupo": "intrinseco",
                "definicion": "calidad cromática del dashboard; sólo parcialmente computable",
                "fuente_computable": "plan.visuals[].data_roles.color|series (cobertura); paleta efectiva requiere píxeles",
                "metrica": "color_role_coverage (observación); la dimensión queda requires_manual_review",
                "formula": None,
                "escala": "0-100",
                "escala_nota": "reservada: sin score hasta contar captura del oráculo",
                "umbral_informativo": None,
                "umbral_papel": "requires_manual_review hasta comparación visual del operador",
            },
            "titulos": {
                "grupo": "intrinseco",
                "definicion": "visuales portadores de marcas con título humano no vacío",
                "fuente_computable": "plan.visuals[].title (slicer/text_box exentos)",
                "metrica": "titled_ratio = titulados / requeridos",
                "formula": "score = 100 * titled_ratio",
                "escala": "0-100",
                "umbral_informativo": 80.0,
                "umbral_papel": "informativo; no promociona",
            },
            "interaccion": {
                "grupo": "intrinseco",
                "definicion": "interacciones declaradas con extremos resolubles y presencia de slicers",
                "fuente_computable": "plan.interactions[] (source/target) + visuales kind=slicer",
                "metrica": "resolved_interaction_ratio; sin interacciones ni slicers -> not_evaluable",
                "formula": "score = 100 * resueltas / declaradas",
                "escala": "0-100",
                "umbral_informativo": INTRINSIC_THRESHOLD,
                "umbral_papel": "informativo; la fidelidad de interacciones contra el origen es parte del paquete G0-NAV",
            },
            "fidelidad_al_origen": {
                "grupo": "fidelidad",
                "definicion": (
                    "qué tanto el dashboard DataVIZ reproduce el diseño del origen "
                    "Tableau/Power BI comparable (D040): estructura + diseño declarado"
                ),
                "fuente_computable": (
                    "score_fidelity(reference_payload, actual_payload): metadatos del "
                    "oráculo (mismo esquema de payload) contra el payload DataVIZ"
                ),
                "metrica": (
                    "estructurales: visual_coverage_ratio (peso 0.30), kind_match_ratio "
                    "(0.15), geometry_iou_mean de emparejados (0.15), marks_ratio_median "
                    "(0.10) y ratios/clasificación por visual para impedir que una reducción "
                    "quede oculta por la mediana; de diseño: title_match_ratio contra títulos declarados por la "
                    "referencia (0.20) y color_role_match_ratio contra roles color/series "
                    "declarados (0.10); pesos renormalizados según componentes disponibles; "
                    "los atributos de diseño no declarados por la referencia quedan "
                    "not_evaluable con causa"
                ),
                "formula": "score = 100 * suma(peso * componente) / suma(pesos disponibles)",
                "escala": "0-100",
                "umbral": DESIGN_EQUIVALENCE_THRESHOLD,
                "regla_aprobacion": {
                    "conjuntivo": True,
                    "score_minimo": DESIGN_EQUIVALENCE_THRESHOLD,
                    "cero_brechas_p0": True,
                    "cero_reducciones_de_marcas_sin_aceptar": True,
                    "definicion": (
                        "design_equivalence sólo si score >= 95 Y p0_breach_count == 0 "
                        "Y mark_density_reduction_count == 0; cualquier otro caso scored "
                        "queda partial con brechas enumeradas"
                    ),
                },
                "brechas_p0": {
                    "tipos": list(P0_BREACH_TYPES),
                    "geometry_iou_threshold": P0_GEOMETRY_IOU_THRESHOLD,
                    "definicion": (
                        "missing_visual: visual del origen ausente; kind_mismatch: kind "
                        "distinto; geometry_iou_below_threshold: IoU del visual emparejado "
                        "bajo el umbral declarado; title_mismatch: título declarado por el "
                        "origen distinto en el actual"
                    ),
                    "salida": "observations.p0_breaches y RubricEvaluation.p0_breaches",
                },
                "umbral_papel": (
                    "umbral de equivalencia de diseño (D040): el score por sí solo NO "
                    "aprueba; se requiere además cero brechas P0; sin oráculo -> "
                    "requires_manual_review"
                ),
            },
        },
        "grupos": {
            "intrinseco": list(INTRINSIC_DIMENSIONS),
            "fidelidad": ["fidelidad_al_origen"],
        },
        "score_intrinseco": {
            "definicion": "media de las dimensiones intrínsecas con status scored (None si ninguna)",
            "papel": "informativo: describe calidad del dashboard, nunca aprueba la dimensión visual",
        },
        "score_fidelidad": {
            "definicion": "score de fidelidad_al_origen cuando existe oráculo comparable",
            "papel": "condición necesaria (no suficiente) de design_equivalence",
        },
        "veredictos": [
            "design_equivalence",
            "partial",
            "requires_manual_review",
            "blocked",
        ],
        "veredictos_definicion": {
            "design_equivalence": (
                f"score_fidelidad >= {DESIGN_EQUIVALENCE_THRESHOLD} y cero brechas P0 "
                "y cero reducciones de marcas sin aceptar (equivalencia de diseño D040)"
            ),
            "partial": (
                "fidelidad scored que no alcanza el conjuntivo (score < 95 o brechas "
                "P0 presentes): brechas y componentes débiles enumerados en causes"
            ),
            "requires_manual_review": (
                "sin oráculo comparable o fidelidad no evaluable: no se infiere ni se "
                "da por buena"
            ),
            "blocked": "sin payload ejecutable",
        },
        "no_promocion": {
            "regla": (
                "un dashboard aprueba visual sólo por EQUIVALENCIA DE DISEÑO "
                f"(score >= {DESIGN_EQUIVALENCE_THRESHOLD} y cero brechas P0) o por "
                "aceptación explícita del usuario por demo; el score intrínseco "
                "perfecto no promociona"
            ),
            "umbral_fidelidad": DESIGN_EQUIVALENCE_THRESHOLD,
            "cero_brechas_p0": True,
            "aprobador": "usuario",
        },
        "estados_por_dimension": {
            "scored": "score 0-100 computado desde metadatos",
            "not_evaluable": "sin datos suficientes; con causa obligatoria",
            "requires_manual_review": "requiere oráculo visual o decisión estética; con causa obligatoria",
        },
    }


def evaluate_case(
    case_id: str,
    payload: dict[str, Any] | None,
    reference_payload: dict[str, Any] | None = None,
) -> RubricEvaluation:
    """Puntúa una demo completa. Determinista: misma entrada -> mismo resultado."""
    dimensions: dict[str, DimensionScore] = {}
    causes: list[str] = []
    plan = (payload or {}).get("plan") or {}
    visuals = plan.get("visuals") or []

    if not payload or not isinstance(plan, dict):
        causa = "sin payload ejecutable (caso blocked en el corpus)"
        for name in DIMENSION_ORDER:
            dimensions[name] = DimensionScore(name, _NOT_EVALUABLE, cause=causa)
        return RubricEvaluation(case_id, dimensions, None, None, "blocked", (causa,))

    results_visuals = ((payload or {}).get("results") or {}).get("visuals")
    dimensions["layout"] = score_layout(visuals)
    dimensions["densidad"] = score_density(visuals, results_visuals)
    dimensions["color"] = score_color(visuals)
    dimensions["titulos"] = score_titles(visuals)
    dimensions["interaccion"] = score_interaction(visuals, plan.get("interactions"))
    dimensions["fidelidad_al_origen"] = score_fidelity(reference_payload, payload)

    intrinsic_scores = [
        dimensions[name].score
        for name in INTRINSIC_DIMENSIONS
        if dimensions[name].status == _SCORED and dimensions[name].score is not None
    ]
    score_intrinseco = (
        _round(sum(intrinsic_scores) / len(intrinsic_scores), 2) if intrinsic_scores else None
    )
    fidelity = dimensions["fidelidad_al_origen"]
    score_fidelidad = fidelity.score if fidelity.status == _SCORED else None
    p0_breaches: tuple[dict[str, Any], ...] = ()

    if fidelity.status != _SCORED:
        verdict = "requires_manual_review"
        causes.append(fidelity.cause or "fidelidad no evaluable")
    else:
        p0_breaches = tuple(fidelity.observations.get("p0_breaches") or ())
        mark_density_reductions = fidelity.observations.get("mark_density_reductions") or []
        debiles = [
            f"{key}={fidelity.observations.get(key)}"
            for key in _FIDELITY_COMPONENT_KEYS
            if fidelity.observations.get(key) is not None and fidelity.observations[key] < 1.0
        ]
        if p0_breaches:
            verdict = "partial"
            breach_types = sorted({breach["type"] for breach in p0_breaches})
            causes.append(
                f"brechas P0 ({len(p0_breaches)}) que impiden la equivalencia de diseño: "
                f"{', '.join(breach_types)}; sólo el usuario puede aceptarlas por demo (D040)"
            )
        elif mark_density_reductions:
            verdict = "partial"
            reductions = ", ".join(
                f"{item['visual']}={item['ratio']}" for item in mark_density_reductions
            )
            causes.append(
                f"marks_ratio_median={fidelity.observations.get('marks_ratio_median')}; "
                f"reducciones de marcas clasificadas como approximation ({reductions}) "
                "requieren revisión manual o aceptación explícita por demo (D040)"
            )
        elif score_fidelidad is not None and score_fidelidad >= DESIGN_EQUIVALENCE_THRESHOLD:
            verdict = "design_equivalence"
        else:
            verdict = "partial"
            causes.append(
                f"fidelidad {score_fidelidad} por debajo del umbral de equivalencia de "
                f"diseño {DESIGN_EQUIVALENCE_THRESHOLD}; componentes débiles: "
                f"{', '.join(debiles) or 'ninguno identificado'}; sólo el usuario puede "
                "aceptar la brecha por demo (D040)"
            )
    for name in INTRINSIC_DIMENSIONS:
        if dimensions[name].status == _NOT_EVALUABLE:
            causes.append(f"{name}: {dimensions[name].cause}")

    return RubricEvaluation(
        case_id, dimensions, score_intrinseco, score_fidelidad, verdict, tuple(causes), p0_breaches
    )


__all__ = [
    "DESIGN_EQUIVALENCE_THRESHOLD",
    "DIMENSION_ORDER",
    "DimensionScore",
    "EXEMPT_KINDS",
    "HIGH_MARK_COUNT",
    "INTRINSIC_DIMENSIONS",
    "INTRINSIC_THRESHOLD",
    "P0_BREACH_TYPES",
    "P0_GEOMETRY_IOU_THRESHOLD",
    "RubricEvaluation",
    "RUBRIC_ID",
    "RUBRIC_VERSION",
    "evaluate_case",
    "rubric_v1",
    "rubric_v2",
    "score_color",
    "score_density",
    "score_fidelity",
    "score_interaction",
    "score_layout",
    "score_titles",
]
