"""Clasificación de visuales del corpus de fidelidad DataVIZ.

Módulo genérico que reconcilia el conteo de IDs visuales del manifest con las
fuentes sintéticas/públicas y clasifica cada visual en exactamente una
categoría de oráculo, exigiendo causa textual. No inventa visuales: si el
objetivo del wave no está evidenciado por el checkout, la discrepancia se
documenta y la aceptación permanece bloqueada.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

VISUAL_CLASSIFICATIONS = frozenset(
    {"oracle_owned", "structural_only", "unsupported", "requires_manual_review"}
)
ACCEPTANCE_VALUES = frozenset({"blocked", "not_evaluated", "accepted"})


def count_visual_ids(manifest: Mapping[str, object]) -> tuple[int, list[str]]:
    """Cuenta los IDs visuales efectivos y detecta duplicados globales."""
    ids: list[str] = []
    demos = manifest.get("demos")
    if not isinstance(demos, list):
        raise ValueError("manifest.demos must be a list")
    for demo in demos:
        if not isinstance(demo, Mapping):
            raise ValueError("each demo must be an object")
        visuals = demo.get("visuals")
        if not isinstance(visuals, list):
            raise ValueError(f"demo {demo.get('id')!r} must list visuals")
        for visual in visuals:
            if not isinstance(visual, Mapping) or not isinstance(visual.get("id"), str):
                raise ValueError("each visual must have a string id")
            ids.append(visual["id"])
    duplicates = sorted({vid for vid in ids if ids.count(vid) > 1})
    return len(ids), duplicates


def reconcile_classification(manifest: Mapping[str, object]) -> list[dict[str, object]]:
    """Valida que cada visual tenga exactamente una clasificación con causa."""
    problems: list[dict[str, object]] = []
    demos = manifest.get("demos")
    if not isinstance(demos, list):
        raise ValueError("manifest.demos must be a list")
    for demo in demos:
        if not isinstance(demo, Mapping):
            continue
        demo_id = demo.get("id")
        for visual in demo.get("visuals", []):
            if not isinstance(visual, Mapping):
                continue
            vid = visual.get("id")
            label = f"{demo_id}.{vid}"
            classification = visual.get("classification")
            if classification not in VISUAL_CLASSIFICATIONS:
                problems.append(
                    {
                        "visual": label,
                        "problem": f"classification must be one of {sorted(VISUAL_CLASSIFICATIONS)}",
                    }
                )
                continue
            has_oracle = isinstance(visual.get("oracle"), str) and bool(
                visual["oracle"].strip()
            )
            cause = visual.get("classification_cause")
            if not isinstance(cause, str) or not cause.strip():
                problems.append(
                    {"visual": label, "problem": "classification_cause must be a non-empty string"}
                )
            if not has_oracle:
                problems.append({"visual": label, "problem": "oracle identifier is required"})
    return problems


def check_acceptance_evidence(manifest: Mapping[str, object]) -> list[str]:
    """Exige capturas hashadas si la aceptación es ``accepted``."""
    acceptance = manifest.get("acceptance")
    if acceptance not in ACCEPTANCE_VALUES:
        raise ValueError(f"acceptance must be one of {sorted(ACCEPTANCE_VALUES)}")
    if acceptance != "accepted":
        return []
    reconciliation = manifest.get("reconciliation")
    captures = (
        reconciliation.get("capture_evidence", [])
        if isinstance(reconciliation, Mapping)
        else []
    )
    problems: list[str] = []
    if not isinstance(captures, list) or not captures:
        problems.append("accepted corpus requires non-empty reconciliation.capture_evidence")
        return problems
    for entry in captures:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("sha256"), str):
            problems.append("each capture_evidence entry requires a sha256 hash")
        if not isinstance(entry, Mapping) or not isinstance(entry.get("visual_id"), str):
            problems.append("each capture_evidence entry requires a visual_id")
    return problems


def load_manifest(root: Path) -> Mapping[str, object]:
    """Carga el manifest del corpus como diccionario inmutable-vista."""
    import json

    path = root / "tests" / "fixtures" / "dataviz_fidelity" / "manifest.json"
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, Mapping):
        raise ValueError("manifest must be a JSON object")
    return data


__all__ = [
    "VISUAL_CLASSIFICATIONS",
    "check_acceptance_evidence",
    "count_visual_ids",
    "load_manifest",
    "reconcile_classification",
]
