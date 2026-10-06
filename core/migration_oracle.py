"""Validación reproducible de los CSV materializados por una migración."""

from __future__ import annotations

import json
import math
from hashlib import sha256
from pathlib import Path
from typing import Any

import pandas as pd


def _semantic_data_directory(migration_root: Path) -> Path:
    """Encuentra la única carpeta Data de un modelo semántico migrado."""
    candidates = sorted(
        path
        for path in migration_root.glob("*.SemanticModel/Data")
        if path.is_dir()
    )
    if len(candidates) != 1:
        raise ValueError(
            "Se esperaba exactamente una carpeta *.SemanticModel/Data; "
            f"se encontraron {len(candidates)}"
        )
    return candidates[0]


def _compare_table(csv_path: Path, expected: dict[str, Any]) -> dict[str, Any]:
    """Compara contrato de filas, columnas y agregados con un CSV."""
    errors: list[str] = []
    if not csv_path.is_file():
        return {"errors": [f"archivo ausente: {csv_path.name}"]}

    frame = pd.read_csv(csv_path)
    expected_rows = int(expected["rowCount"])
    if len(frame) != expected_rows:
        errors.append(f"rowCount esperado={expected_rows} actual={len(frame)}")

    expected_columns = [str(column) for column in expected["columns"]]
    actual_columns = [str(column) for column in frame.columns]
    if actual_columns != expected_columns:
        errors.append(
            f"columns esperado={expected_columns!r} actual={actual_columns!r}"
        )

    for column, expected_sum in expected.get("numericSums", {}).items():
        if column not in frame:
            errors.append(f"numericSums columna ausente={column}")
            continue
        actual_sum = float(pd.to_numeric(frame[column], errors="coerce").sum())
        if not math.isclose(
            actual_sum,
            float(expected_sum),
            rel_tol=1e-9,
            abs_tol=1e-6,
        ):
            errors.append(
                f"numericSums.{column} esperado={expected_sum} actual={actual_sum}"
            )

    for metric_name, calculator in (
        ("nonNull", lambda series: int(series.notna().sum())),
        ("distinct", lambda series: int(series.nunique(dropna=True))),
    ):
        for column, expected_value in expected.get(metric_name, {}).items():
            if column not in frame:
                errors.append(f"{metric_name} columna ausente={column}")
                continue
            actual_value = calculator(frame[column])
            if actual_value != int(expected_value):
                errors.append(
                    f"{metric_name}.{column} esperado={expected_value} "
                    f"actual={actual_value}"
                )

    return {
        "path": str(csv_path),
        "rowCount": len(frame),
        "columns": actual_columns,
        "errors": errors,
    }


def validate_migration_oracle(
    manifest_path: str | Path,
    migration_root: str | Path,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Valida una salida contra métricas fijadas para una fuente con hash conocido."""
    manifest_path = Path(manifest_path).resolve()
    migration_root = Path(migration_root).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schemaVersion") != 1:
        raise ValueError("schemaVersion de oracle no soportada")

    data_directory = _semantic_data_directory(migration_root)
    table_reports = {
        str(filename): _compare_table(data_directory / str(filename), expected)
        for filename, expected in manifest.get("tables", {}).items()
    }
    source_report: dict[str, Any] | None = None
    if source_path is not None:
        resolved_source = Path(source_path).resolve()
        actual_hash = sha256(resolved_source.read_bytes()).hexdigest().upper()
        source_metadata = manifest.get("source", {})
        expected_hashes: set[str] = set()
        if source_metadata.get("sha256"):
            expected_hashes.add(str(source_metadata["sha256"]).upper())
        expected_hashes.update(
            str(workbook["sha256"]).upper()
            for workbook in source_metadata.get("workbooks", [])
            if workbook.get("sha256")
        )
        source_report = {
            "path": str(resolved_source),
            "sha256": actual_hash,
            "verified": actual_hash in expected_hashes,
        }

    # ORACLE-01: un solo error invalida la afirmación de fidelidad numérica.
    tables_match = bool(table_reports) and all(
        not table_report["errors"] for table_report in table_reports.values()
    )
    success = tables_match and (
        source_report is None or bool(source_report["verified"])
    )
    report = {
        "schemaVersion": 1,
        "manifest": str(manifest_path),
        "migrationRoot": str(migration_root),
        "success": success,
        "tables": table_reports,
    }
    if source_report is not None:
        # ORACLE-02: impide aplicar métricas correctas al TWBX equivocado.
        report["source"] = source_report
    return report
