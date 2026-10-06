"""Registro de consultas DAX pendientes de ejecución por el operador.

Genera, por demo del corpus G0, las consultas DAX ``EVALUATE`` desde el plan de
render y la IR semántica de cada ``payload.json``/``query_context.json``, y las
marca ``blocked_pending_operator`` con su causa. Este módulo JAMÁS ejecuta DAX:
la ejecución contra Power BI Desktop es responsabilidad exclusiva del operador
UI (Luna); registrar resultados no ejecutados está prohibido.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core.compilers.render_plan import RenderPlan
from core.contracts.semantic_ir import SemanticModel
from core.validation.dax_query_generator import generate_dax_queries_from_objects
from core.validation.pbip_case_surrogates import discover_case_surrogates

#: Estado obligatorio de toda consulta no ejecutada.
PENDING_OPERATOR_STATUS = "blocked_pending_operator"

#: Causa documentada del bloqueo (el operador UI ejecuta Power BI Desktop).
PENDING_OPERATOR_CAUSE = "requiere Power BI Desktop (operador Luna)"


def _sha256(path: Path) -> str | None:
    """SHA-256 hex del archivo, o None si no existe."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PendingQueries:
    """Consultas generadas para una demo, sin ejecutar y marcadas como pendientes."""

    case_id: str
    queries: list[dict[str, str]]
    payload_sha256: str | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        """Serializa el registro con el estado de bloqueo y su causa."""
        return {
            "status": PENDING_OPERATOR_STATUS,
            "cause": PENDING_OPERATOR_CAUSE,
            "query_count": len(self.queries),
            "queries": self.queries,
            "payload_sha256": self.payload_sha256,
            "error": self.error,
        }


def _pending_for_case(
    case_id: str,
    case_dir: Path,
    case_surrogates: dict[tuple[str, str], str] | None = None,
) -> PendingQueries:
    """Genera las consultas de un caso o registra el error con causa explícita."""
    payload_path = case_dir / "payload.json"
    context_path = case_dir / "query_context.json"
    payload_hash = _sha256(payload_path)
    if not payload_path.is_file() or not context_path.is_file():
        return PendingQueries(case_id, [], payload_hash, "falta payload.json o query_context.json")
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        context = json.loads(context_path.read_text(encoding="utf-8"))
        plan = RenderPlan.from_json(json.dumps(payload.get("plan", {})))
        model = SemanticModel.from_json(json.dumps(context.get("semantic_model", {})))
        queries = generate_dax_queries_from_objects(plan, model, case_surrogates=case_surrogates)
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        return PendingQueries(case_id, [], payload_hash, f"error generando: {exc}")
    return PendingQueries(
        case_id,
        [{"visual_id": q.visual_id, "measure": q.measure, "dax": q.dax} for q in queries],
        payload_hash,
        None,
    )


def build_pending_dax_report(
    case_paths: Mapping[str, Path],
    *,
    pbip_roots: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Genera el reporte de consultas DAX pendientes para cada demo del corpus.

    Args:
        case_paths: mapa ``case_id`` → directorio del caso con ``payload.json``
            y ``query_context.json``.

    Returns:
        Reporte con ``operator_status`` global ``blocked_pending_operator`` y un
        registro por demo con las consultas generadas. Ningún resultado DAX es
        ejecutado ni inventado aquí.
    """
    pbip_roots = pbip_roots or {}
    demos = {}
    for case_id, case_dir in sorted(case_paths.items()):
        pbip_root = pbip_roots.get(case_id)
        case_surrogates = (
            discover_case_surrogates(Path(pbip_root)) if pbip_root is not None else {}
        )
        demos[case_id] = _pending_for_case(
            case_id,
            Path(case_dir),
            case_surrogates=case_surrogates,
        ).to_dict()
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "operator_status": PENDING_OPERATOR_STATUS,
        "cause": PENDING_OPERATOR_CAUSE,
        "note": (
            "Consultas generadas pero NO ejecutadas; la ejecución DAX contra "
            "Power BI Desktop normal corresponde al operador UI (Luna)."
        ),
        "demos": demos,
    }


__all__ = [
    "PENDING_OPERATOR_CAUSE",
    "PENDING_OPERATOR_STATUS",
    "PendingQueries",
    "build_pending_dax_report",
]
