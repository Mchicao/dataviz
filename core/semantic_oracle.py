"""Evaluación programática de medidas Python generadas desde Tableau."""

from __future__ import annotations

import importlib.util
import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd

from core.migration_oracle import _semantic_data_directory


def _load_generated_module(module_path: Path) -> ModuleType:
    """Carga únicamente el módulo generado dentro de la migración indicada."""
    spec = importlib.util.spec_from_file_location(
        f"_generated_measures_{abs(hash(module_path))}", module_path
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"No se pudo cargar el módulo: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _values_match(actual: Any, expected: Any, tolerance: float) -> bool:
    """Compara escalares numéricos con tolerancia y otros valores exactamente."""
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(
            float(actual),
            float(expected),
            rel_tol=1e-9,
            abs_tol=tolerance,
        )
    return actual == expected


def _as_scalar(value: Any) -> Any:
    """Normaliza escalares NumPy sin convertir series en resultados válidos."""
    if hasattr(value, "item") and not isinstance(value, pd.Series):
        return value.item()
    return value


def _evaluate_context(
    function: Callable[[pd.DataFrame, dict[str, Any]], Any],
    frame: pd.DataFrame,
    parameters: dict[str, Any],
    context: dict[str, Any],
    tolerance: float,
) -> dict[str, Any]:
    """Evalúa una medida bajo filtros de igualdad y agrupaciones declaradas."""
    filtered = frame
    errors: list[str] = []
    for expected_filter in [*context.get("filters", []), *context.get("selections", [])]:
        column = str(expected_filter["column"])
        if column not in filtered.columns:
            errors.append(f"columna de filtro ausente: {column}")
            continue
        if "values" in expected_filter:
            values = expected_filter["values"]
            if values:
                filtered = filtered.loc[filtered[column].isin(values)]
        else:
            filtered = filtered.loc[filtered[column] == expected_filter["equals"]]

    group_by = [str(column) for column in context.get("groupBy", [])]
    for column in group_by:
        if column not in filtered.columns:
            errors.append(f"columna de agrupación ausente: {column}")

    if errors:
        return {"actual": None, "actualRows": [], "errors": errors}

    if not group_by:
        actual = _as_scalar(function(filtered, parameters))
        if isinstance(actual, pd.Series):
            errors.append("la medida produjo una serie y no un escalar")
        elif not _values_match(actual, context["expected"], tolerance):
            errors.append(f"esperado={context['expected']!r} actual={actual!r}")
        return {"actual": actual, "actualRows": [], "errors": errors}

    actual_rows: list[dict[str, Any]] = []
    for key, group in filtered.groupby(group_by, dropna=False, sort=True):
        keys = key if isinstance(key, tuple) else (key,)
        actual = _as_scalar(function(group, parameters))
        actual_rows.append(
            {
                "group": dict(zip(group_by, map(_as_scalar, keys), strict=True)),
                "actual": actual,
            }
        )

    # SEM-ORACLE-02: casamos filas por clave de grupo, no por posición, para que
    # el orden de declaración en el oracle no invalide un resultado correcto.
    expected_rows = context.get("expectedRows", [])
    expected_by_key = {
        _group_key(row["group"]): row for row in expected_rows
    }
    actual_by_key = {_group_key(row["group"]): row for row in actual_rows}
    for key, expected_row in expected_by_key.items():
        if key not in actual_by_key:
            errors.append(f"grupo esperado ausente={expected_row['group']!r}")
    for key, actual_row in actual_by_key.items():
        expected_row = expected_by_key.get(key)
        if expected_row is None:
            errors.append(f"grupo no declarado={actual_row['group']!r}")
        elif not _values_match(
            actual_row["actual"], expected_row["expected"], tolerance
        ):
            errors.append(
                f"grupo={actual_row['group']!r} esperado={expected_row['expected']!r} "
                f"actual={actual_row['actual']!r}"
            )
    return {"actual": None, "actualRows": actual_rows, "errors": errors}


def _group_key(group: dict[str, Any]) -> tuple[Any, ...]:
    """Clave hashable y estable para comparar grupos sin depender del orden."""
    return tuple(sorted(group.items()))


def _read_measure_dax(tables_directory: Path, table: str, measure: str) -> str | None:
    """Lee una expresión DAX escalar de una declaración TMDL de una línea."""
    candidates = [tables_directory / f"{table}.tmdl", *tables_directory.glob("*.tmdl")]
    seen: set[Path] = set()
    quoted_name = re.escape(measure)
    pattern = re.compile(
        rf"^\s*measure\s+(?:'{quoted_name}'|\"{quoted_name}\"|{quoted_name})\s*=\s*(.+)$",
        re.MULTILINE,
    )
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        match = pattern.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1).strip()
    return None


def _context_kinds(declaration: dict[str, Any]) -> set[str]:
    """Clasifica un contexto en sus categorías semánticas declaradas."""
    kinds: set[str] = set()
    if declaration.get("groupBy"):
        kinds.add("grouping")
    if declaration.get("filters"):
        kinds.add("filter")
    if declaration.get("selections"):
        kinds.add("selection")
    return kinds


def _evaluate_visual_coverage(
    visuals: list[dict[str, Any]],
    measure_reports: dict[str, dict[str, Any]],
    context_index: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    """SEM-ORACLE-03: exige el conjunto mínimo (medida, filtro, agrupación) por visual.

    El oracle por visual es el contrato de cobertura semántica mínimo derivable
    desde la matriz Gate 0: cada visual debe tener al menos una medida calculada,
    un filtro y una agrupación, todos sin errores.
    """
    visual_coverage: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, str]] = []
    for visual in visuals:
        worksheet = str(visual.get("worksheet") or visual.get("name") or "")
        category_status: dict[str, dict[str, Any]] = {}
        for category, refs, kind in (
            ("calculatedMeasures", visual.get("calculatedMeasures", []), "measure"),
            ("filters", visual.get("filters", []), "filter"),
            ("groupings", visual.get("groupings", []), "grouping"),
        ):
            missing: list[str] = []
            ok = False
            for ref in refs:
                ref_name = str(ref)
                if kind == "measure":
                    report = measure_reports.get(ref_name)
                    if report is None or report["errors"]:
                        missing.append(ref_name)
                    else:
                        ok = True
                else:
                    entry = context_index.get(ref_name)
                    if entry is None or kind not in entry["kinds"] or entry["report"]["errors"]:
                        missing.append(ref_name)
                    else:
                        ok = True
            covered = bool(refs) and ok and not missing
            category_status[category] = {
                "covered": covered,
                "references": [str(ref) for ref in refs],
                "missing": missing,
            }
            if not covered:
                gaps.append({"worksheet": worksheet, "category": category})
        complete = all(status["covered"] for status in category_status.values())
        visual_coverage[worksheet] = {
            "worksheet": worksheet,
            **category_status,
            "complete": complete,
        }
    return visual_coverage, gaps


def validate_semantic_oracle(oracle_path: str | Path, migration_root: str | Path) -> dict[str, Any]:
    """Compara medidas escalares generadas con resultados públicos fijados.

    El módulo ejecutado debe ser una salida local del migrador sobre fixtures
    públicos confiables; no se debe usar con código aportado por clientes.
    """
    oracle_path = Path(oracle_path).resolve()
    migration_root = Path(migration_root).resolve()
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    if oracle.get("schemaVersion") not in {1, 2, 3}:
        raise ValueError("schemaVersion de oracle semántico no soportada")

    measures_directory = migration_root / "PythonMeasures"
    generated_manifest = json.loads(
        (measures_directory / "measures.manifest.json").read_text(encoding="utf-8")
    )
    generated_by_name = {str(item["name"]): item for item in generated_manifest}
    module = _load_generated_module(measures_directory / "measures.py")
    data_directory = _semantic_data_directory(migration_root)
    tables_directory = data_directory.parent / "definition" / "tables"

    measure_reports: dict[str, dict[str, Any]] = {}
    context_index: dict[str, dict[str, Any]] = {}
    for expected_measure in oracle.get("measures", []):
        name = str(expected_measure["name"])
        errors: list[str] = []
        generated = generated_by_name.get(name)
        actual: Any = None
        actual_dax: str | None = None
        context_reports: dict[str, dict[str, Any]] = {}
        if generated is None:
            errors.append("medida ausente en el manifiesto generado")
        elif generated.get("status") != "supported":
            errors.append(f"medida no ejecutable: {generated.get('reason', '')}")
        else:
            frame = pd.read_csv(data_directory / str(expected_measure["csv"]))
            function = getattr(module, str(generated["function"]))
            actual = function(frame, expected_measure.get("parameters", {}))
            actual = _as_scalar(actual)
            if isinstance(actual, pd.Series):
                errors.append("la medida produjo una serie y no un escalar")
            elif not _values_match(
                actual,
                expected_measure["expected"],
                float(expected_measure.get("absTolerance", 1e-9)),
            ):
                errors.append(f"esperado={expected_measure['expected']!r} actual={actual!r}")

            for context in expected_measure.get("contexts", []):
                context_name = str(context["name"])
                context_report = _evaluate_context(
                    function,
                    frame,
                    expected_measure.get("parameters", {}),
                    context,
                    float(expected_measure.get("absTolerance", 1e-9)),
                )
                context_reports[context_name] = context_report
                context_index[context_name] = {
                    "measure": name,
                    "kinds": _context_kinds(context),
                    "report": context_report,
                }
                errors.extend(
                    f"contexto {context_name}: {error}" for error in context_report["errors"]
                )

        if generated is not None and expected_measure.get("expectedDax"):
            actual_dax = _read_measure_dax(
                tables_directory,
                str(generated["table"]),
                name,
            )
            if actual_dax is None:
                errors.append("medida ausente en TMDL")
            elif re.sub(r"\s+", "", actual_dax) != re.sub(
                r"\s+", "", str(expected_measure["expectedDax"])
            ):
                errors.append(
                    f"DAX esperado={expected_measure['expectedDax']!r} actual={actual_dax!r}"
                )

        measure_reports[name] = {
            "actual": actual,
            "expected": expected_measure["expected"],
            "dax": actual_dax,
            "contexts": context_reports,
            "errors": errors,
        }

    # SEM-ORACLE-01: el escalar y todos sus contextos deben coincidir.
    visuals = oracle.get("visuals", [])
    if visuals:
        visual_coverage, visual_gaps = _evaluate_visual_coverage(
            visuals, measure_reports, context_index
        )
    else:
        visual_coverage, visual_gaps = {}, []

    success = bool(measure_reports) and all(
        not report["errors"] for report in measure_reports.values()
    )
    # SEM-ORACLE-04: un visual declarado debe cubrir el conjunto mínimo semántico.
    success = success and not visual_gaps
    return {
        "schemaVersion": int(oracle["schemaVersion"]),
        "oracle": str(oracle_path),
        "migrationRoot": str(migration_root),
        "success": success,
        "measures": measure_reports,
        "visualCoverage": visual_coverage,
        "visualCoverageGaps": visual_gaps,
    }


def derive_minimum_set_per_visual(gate0_case: dict[str, Any]) -> dict[str, Any]:
    """SEM-ORACLE-DERIVE: conjunto mínimo semántico por visual desde un caso Gate 0.

    La matriz Gate 0 provee, de forma genérica:
      - filtros por worksheet (``filterOccurrences``) -> derivable por visual,
      - cálculos por workbook con estado -> NO vinculados a un visual específico,
      - SIN inventario de agrupaciones (filas/jerarquías) -> brecha de derivación.

    Estas tres limitaciones son aproximaciones explícitas: no se afirma cobertura
    'por visual' que la matriz no pueda probar.
    """
    source = gate0_case["source"]
    target = gate0_case["target"]
    statuses = {
        item["name"]: item
        for item in target.get("calculationStatuses", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    supported_measures = sorted(
        name
        for name, status in statuses.items()
        if status.get("status") == "supported"
    )
    filters_by_worksheet: dict[str, list[str]] = {}
    for occurrence in source.get("filterOccurrences", []):
        worksheet = str(occurrence.get("worksheet", ""))
        filters_by_worksheet.setdefault(worksheet, []).append(
            str(occurrence.get("field", ""))
        )
    visuals = []
    for worksheet in source.get("worksheetNames", []):
        worksheet = str(worksheet)
        visuals.append(
            {
                "worksheet": worksheet,
                "calculatedMeasures": {
                    "derivableFromMatrix": False,
                    "workbookSupportedSet": supported_measures,
                    "approximation": (
                        "medidas inventariadas a nivel workbook; la matriz no las "
                        "vincula a un visual específico"
                    ),
                },
                "filters": {
                    "derivableFromMatrix": True,
                    "occurrences": filters_by_worksheet.get(worksheet, []),
                },
                "groupings": {
                    "derivableFromMatrix": False,
                    "approximation": (
                        "la matriz Gate 0 no inventaría filas/jerarquías; "
                        "brecha de derivación por visual"
                    ),
                },
            }
        )
    return {
        "sourceFile": gate0_case.get("sourceFile", ""),
        "minimumSetCategories": ["calculatedMeasures", "filters", "groupings"],
        "visuals": visuals,
    }


def build_semantic_oracle_per_visual(matrix: dict[str, Any]) -> dict[str, Any]:
    """SEM-ORACLE-BUILD: genera el artefacto por visual desde una matriz Gate 0.

    Generador versionable del contrato ``semantic_oracle_per_visual.json``: deriva
    cada caso con ``derive_minimum_set_per_visual`` y resume las aproximaciones
    estructurales que la matriz no puede probar por visual. Recibe la matriz (no la
    lee de disco) para mantener al caller dueño del I/O y permitir fixtures en tests.
    """
    cases = [derive_minimum_set_per_visual(case) for case in matrix["cases"]]
    approximations = [
        "medidas inventariadas a nivel workbook; la matriz no las vincula "
        "a un visual específico",
        "agrupaciones (filas/jerarquías) no inventariadas por la matriz Gate 0; "
        "brecha de derivación por visual",
    ]
    return {"cases": cases, "approximations": approximations}
