"""Inventario reproducible de cobertura estructural para el corpus Gate 0."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.calculation_extractor import extract_calculations
from core.tableau_parser import TableauReader
from core.twbx_extractor import cleanup_temp_dir, extract_twb_from_twbx
from core.validation.expected_sheets import normalizar


@contextmanager
def _readable_twb(source: Path) -> Iterator[Path]:
    """COV-CORE-01: entrega un TWB y limpia la extracción temporal del TWBX."""
    if source.suffix.lower() == ".twb":
        yield source
        return
    if source.suffix.lower() != ".twbx":
        raise ValueError(f"Fuente no soportada: {source.suffix}")

    with source.open("rb") as package:
        twb_path, temp_dir = extract_twb_from_twbx(package)
    try:
        yield Path(twb_path)
    finally:
        cleanup_temp_dir(temp_dir)


def _source_inventory(source: Path) -> dict[str, Any]:
    """COV-CORE-02: inventaría conceptos Tableau sin inferir su equivalencia."""
    with _readable_twb(source) as twb_path:
        reader = TableauReader(twb_path)
        workbook_ir = reader.get_typed_workbook_ir()
        calculations = extract_calculations(str(twb_path))
        worksheets = [
            worksheet
            for worksheet in reader.root.findall(".//worksheets/worksheet")
            if worksheet.get("name")
        ]
        filters = [
            {
                "worksheet": worksheet.get("name", ""),
                "field": filter_element.get("column", ""),
                "class": filter_element.get("class", ""),
            }
            for worksheet in worksheets
            for filter_element in worksheet.findall(".//filter")
        ]
        expected_pages = [
            item["Nombre"]
            for item in reader.get_detailed_worksheets()
            if isinstance(item.get("Nombre"), str)
        ]

    calculation_kinds = Counter(
        calculation.calc_type.value for calculation in calculations.calculated_fields
    )
    return {
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest().upper(),
        "worksheetCount": len(worksheets),
        "worksheetNames": [worksheet.get("name", "") for worksheet in worksheets],
        "dashboardCount": len(workbook_ir.dashboards),
        "dashboardNames": [dashboard.name for dashboard in workbook_ir.dashboards],
        "expectedPageCount": len(expected_pages),
        "expectedPageNames": expected_pages,
        "calculationCount": calculations.total_calculations,
        "calculationNames": sorted(
            calculation.name for calculation in calculations.calculated_fields
        ),
        "calculationKinds": dict(sorted(calculation_kinds.items())),
        "parameterCount": len(calculations.parameters),
        "parameterNames": sorted(parameter.name for parameter in calculations.parameters),
        "filterOccurrenceCount": len(filters),
        "filterOccurrences": filters,
    }


def _resolve_report_folder(migration_root: Path) -> Path:
    """COV-CORE-03: resuelve el Report activo declarado por el PBIP."""
    root = migration_root.resolve()
    for pbip_path in sorted(root.glob("*.pbip")):
        try:
            pbip = json.loads(pbip_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for artifact in pbip.get("artifacts", []):
            report_path = artifact.get("report", {}).get("path")
            if not isinstance(report_path, str) or not report_path:
                continue
            candidate = (root / report_path).resolve()
            if candidate.is_relative_to(root) and candidate.is_dir():
                return candidate

    report_folders = sorted(path for path in root.glob("*.Report") if path.is_dir())
    if len(report_folders) == 1:
        return report_folders[0]
    raise ValueError(f"No se pudo resolver un único Report en {migration_root}")


def _load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    """COV-CORE-04: lee evidencia JSON; un archivo inválido no se inventaría."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _target_semantic_object_names(migration_root: Path) -> list[str]:
    """COV-CORE-05: extrae columnas/medidas TMDL declaradas, no su resultado."""
    object_pattern = re.compile(
        r"^\s*(?:column|measure)\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=]+))",
        re.MULTILINE,
    )
    names: set[str] = set()
    for path in migration_root.glob("*.SemanticModel/definition/tables/*.tmdl"):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in object_pattern.finditer(content):
            names.add(next(value for value in match.groups() if value))
    return sorted(names)


def _target_inventory(migration_root: Path) -> dict[str, Any]:
    """COV-CORE-06: inventaría páginas, visuales y diagnósticos emitidos."""
    report_folder = _resolve_report_folder(migration_root)
    pages_folder = report_folder / "definition" / "pages"
    pages: list[dict[str, Any]] = []
    visual_types: Counter[str] = Counter()
    filter_entries = 0

    for page_folder in sorted(path for path in pages_folder.iterdir() if path.is_dir()):
        page_data = _load_json(page_folder / "page.json")
        if not isinstance(page_data, dict):
            continue
        page_visuals: list[str] = []
        visuals_folder = page_folder / "visuals"
        if visuals_folder.is_dir():
            for visual_path in sorted(visuals_folder.glob("*/visual.json")):
                visual_data = _load_json(visual_path)
                if not isinstance(visual_data, dict):
                    continue
                visual_type = visual_data.get("visual", {}).get("visualType")
                if not isinstance(visual_type, str) or not visual_type:
                    continue
                page_visuals.append(visual_type)
                visual_types[visual_type] += 1
                filters = visual_data.get("filterConfig", {}).get("filters", [])
                if isinstance(filters, list):
                    filter_entries += len(filters)
        pages.append(
            {
                "name": page_data.get("displayName", page_folder.name),
                "visualCount": len(page_visuals),
                "visualTypes": page_visuals,
            }
        )

    manifest_path = migration_root / "PythonMeasures" / "measures.manifest.json"
    manifest_data = _load_json(manifest_path)
    calculation_statuses = (
        [
            {
                "name": item.get("name", ""),
                "status": item.get("status", "unknown"),
                "reason": item.get("reason", ""),
            }
            for item in manifest_data
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]
        if isinstance(manifest_data, list)
        else []
    )
    return {
        "pageCount": len(pages),
        "pageNames": [page["name"] for page in pages],
        "pages": pages,
        "pagesWithoutVisuals": [
            page["name"] for page in pages if page["visualCount"] == 0
        ],
        "visualCount": sum(visual_types.values()),
        "visualTypes": dict(sorted(visual_types.items())),
        "slicerCount": visual_types.get("slicer", 0),
        "filterContractEntryCount": filter_entries,
        "semanticObjectNames": _target_semantic_object_names(migration_root),
        "calculationStatuses": calculation_statuses,
    }


def _compare(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """COV-CORE-07: compara sólo nombres y estados verificables."""
    expected = {normalizar(name): name for name in source["expectedPageNames"]}
    actual = {normalizar(name): name for name in target["pageNames"]}
    statuses = {
        item.get("name"): item
        for item in target["calculationStatuses"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    target_objects = set(target["semanticObjectNames"])
    source_calculations = set(source["calculationNames"])
    unsupported = sorted(
        name
        for name in source_calculations
        if statuses.get(name, {}).get("status") == "unsupported"
    )
    represented = sorted(
        name
        for name in source_calculations
        if statuses.get(name, {}).get("status") != "unsupported"
        and (name in target_objects or statuses.get(name, {}).get("status") == "supported")
    )
    unrepresented = sorted(source_calculations - set(unsupported) - set(represented))
    return {
        "matchedExpectedPages": sorted(
            expected[key] for key in expected.keys() & actual.keys()
        ),
        "missingExpectedPages": sorted(
            expected[key] for key in expected.keys() - actual.keys()
        ),
        "extraTargetPages": sorted(actual[key] for key in actual.keys() - expected.keys()),
        "representedCalculations": represented,
        "unsupportedCalculations": unsupported,
        "unrepresentedCalculations": unrepresented,
        "parameterStatusTraceable": sorted(
            name for name in source["parameterNames"] if name in statuses
        ),
    }


def _omissions(
    source: dict[str, Any], target: dict[str, Any], comparison: dict[str, Any]
) -> list[dict[str, str]]:
    """COV-CORE-08: conserva únicamente brechas respaldadas por un artefacto."""
    statuses = {
        item.get("name"): item
        for item in target["calculationStatuses"]
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    omissions = [
        {
            "category": "calculation",
            "sourceItem": name,
            "reason": str(statuses[name].get("reason") or "Marcado unsupported"),
            "evidence": "PythonMeasures/measures.manifest.json",
        }
        for name in comparison["unsupportedCalculations"]
    ]
    omissions.extend(
        {
            "category": "calculation",
            "sourceItem": name,
            "reason": "No tiene objeto TMDL ni estado en el manifiesto de cálculos",
            "evidence": "SemanticModel/definition/tables + PythonMeasures/measures.manifest.json",
        }
        for name in comparison["unrepresentedCalculations"]
    )
    omissions.extend(
        {
            "category": "page",
            "sourceItem": name,
            "reason": "No existe una página PBIR con el mismo nombre normalizado",
            "evidence": "Report/definition/pages",
        }
        for name in comparison["missingExpectedPages"]
    )
    expected_normalized = {
        normalizar(name) for name in source["expectedPageNames"] if isinstance(name, str)
    }
    omissions.extend(
        {
            "category": "page_content",
            "sourceItem": name,
            "reason": "La página PBIR existe pero no contiene visuales inventariables",
            "evidence": "Report/definition/pages/*/visuals",
        }
        for name in target["pagesWithoutVisuals"]
        if normalizar(name) in expected_normalized
    )
    return omissions


def analyze_gate0_case(source_path: Path, migration_root: Path) -> dict[str, Any]:
    """COV-CORE-09: genera una matriz por caso sin ejecutar Power BI Desktop."""
    source_path = source_path.resolve()
    migration_root = migration_root.resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if not migration_root.is_dir():
        raise NotADirectoryError(migration_root)

    source = _source_inventory(source_path)
    target = _target_inventory(migration_root)
    comparison = _compare(source, target)
    return {
        "sourceFile": source_path.name,
        "migrationRun": migration_root.name,
        "source": source,
        "target": target,
        "comparison": comparison,
        "omissions": _omissions(source, target, comparison),
        "assurance": {
            "structuralCoverage": "inventoried",
            "numericFidelity": "not_evaluated_by_this_report",
            "semanticFidelity": "not_evaluated_by_this_report",
            "visualFidelity": "not_evaluated",
            "interactionFidelity": "not_evaluated",
        },
    }


def _markdown_cell(value: object) -> str:
    """COV-CORE-10: evita romper tablas Markdown con nombres Tableau."""
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_gate0_markdown(cases: list[dict[str, Any]]) -> str:
    """COV-CORE-11: presenta la evidencia sin elevarla a validación visual."""
    lines = [
        "# Matriz reproducible de cobertura Gate 0",
        "",
        "Este reporte inventaría contratos Tableau y PBIR de forma estática. "
        "No evalúa fidelidad visual, resultados DAX ni interacciones; esas capas "
        "requieren sus oracles y Power BI Desktop normal.",
        "",
        "| Fuente | Worksheets | Dashboards | Páginas | Visuales | Cálculos "
        "representados | Unsupported | Sin representación | Parámetros con estado | "
        "Slicers | Filtros src/contratos PBIR |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in cases:
        source = case["source"]
        target = case["target"]
        comparison = case["comparison"]
        matched = len(comparison["matchedExpectedPages"])
        expected = source["expectedPageCount"]
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    case["sourceFile"],
                    source["worksheetCount"],
                    source["dashboardCount"],
                    f"{matched}/{expected}",
                    target["visualCount"],
                    f"{len(comparison['representedCalculations'])}/"
                    f"{source['calculationCount']}",
                    len(comparison["unsupportedCalculations"]),
                    len(comparison["unrepresentedCalculations"]),
                    f"{len(comparison['parameterStatusTraceable'])}/"
                    f"{source['parameterCount']}",
                    target["slicerCount"],
                    f"{source['filterOccurrenceCount']}/"
                    f"{target['filterContractEntryCount']}",
                )
            )
            + " |"
        )

    lines.extend(["", "## Brechas explícitas", ""])
    for case in cases:
        lines.append(f"### `{_markdown_cell(case['sourceFile'])}`")
        lines.append("")
        if not case["omissions"]:
            lines.append("- Sin omisiones explícitas en los artefactos inventariados.")
        for omission in case["omissions"]:
            lines.append(
                f"- `{_markdown_cell(omission['category'])}` — "
                f"`{_markdown_cell(omission['sourceItem'])}`: "
                f"{_markdown_cell(omission['reason'])} "
                f"(evidencia: `{_markdown_cell(omission['evidence'])}`)."
            )
        lines.append("")

    lines.extend(
        [
            "## Reproducción",
            "",
            "Repita `--case` para cada par fuente/salida y escriba ambos formatos en "
            "la misma ejecución:",
            "",
            "```powershell",
            "# COV-DOC-01: regenera la evidencia humana y machine-readable.",
            "uv run python scripts/generate_gate0_coverage.py --case "
            '"SOURCE.twbx=MIGRATION_ROOT" --json-output output/validation/gate0_coverage.json '
            "--markdown-output Docs/swarm/gate0_coverage_matrix.md",
            "```",
            "",
            "## Lectura correcta",
            "",
            "- Una página coincidente prueba trazabilidad nominal, no equivalencia del lienzo.",
            "- `filterContractEntryCount` cuenta contratos PBIR presentes; no prueba el estado "
            "de selección ni el resultado filtrado.",
            "- Un cálculo `supported` o una medida TMDL sólo prueba representación; su resultado "
            "debe validarse mediante un oracle semántico.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"
