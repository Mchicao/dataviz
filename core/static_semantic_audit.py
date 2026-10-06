"""Auditoría estática del contrato semántico TMDL sin motor Power BI."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

_DECLARATION_RE = re.compile(
    r"^\s*(measure|column)\s+(?:'([^']+)'|\"([^\"]+)\"|([^\s=]+))(?:\s*=\s*(.*))?$"
)
_TABLE_RE = re.compile(r"^table\s+(?:'([^']+)'|\"([^\"]+)\"|(.+))$")
_QUALIFIED_REF_RE = re.compile(r"(?:'([^']+)'|([A-Za-z_]\w*))\[([^\]]+)\]")
_UNQUALIFIED_REF_RE = re.compile(r"(?<![\w'\]])\[([^\]]+)\]")
_RELATIONSHIP_RE = re.compile(r"^relationship\s+(.+)$")


def _parse_tables(tables_dir: Path) -> dict[str, dict[str, dict[str, str]]]:
    """SEM-AUDIT-01: inventaría objetos y expresiones declarados en TMDL."""
    tables: dict[str, dict[str, dict[str, str]]] = {}
    for path in sorted(tables_dir.glob("*.tmdl")):
        lines = path.read_text(encoding="utf-8").splitlines()
        if not lines:
            continue
        table_match = _TABLE_RE.match(lines[0].strip())
        if not table_match:
            continue
        table = next(value for value in table_match.groups() if value).strip()
        objects: dict[str, dict[str, str]] = {}
        index = 1
        while index < len(lines):
            match = _DECLARATION_RE.match(lines[index])
            if not match:
                index += 1
                continue
            kind = match.group(1)
            name = next(value for value in match.groups()[1:4] if value).strip()
            expression_lines = [match.group(5)] if match.group(5) is not None else []
            index += 1
            while index < len(lines):
                candidate = lines[index]
                if _DECLARATION_RE.match(candidate) or candidate.lstrip().startswith("partition "):
                    break
                stripped = candidate.strip()
                if stripped.startswith("///"):
                    index += 1
                    continue
                if stripped and not re.match(
                    r"^(dataType|formatString|sourceColumn|summarizeBy|lineageTag|displayFolder|description):",
                    stripped,
                ):
                    expression_lines.append(stripped)
                index += 1
            expression = "\n".join(line for line in expression_lines if line).strip()
            objects[name] = {"kind": kind, "expression": expression, "path": str(path)}
        tables[table] = objects
    return tables


def _parse_relationships(definition_dir: Path) -> list[dict[str, str]]:
    """SEM-AUDIT-02: lee extremos de relaciones declaradas en TMDL."""
    relationships: list[dict[str, str]] = []
    for path in sorted(definition_dir.rglob("*.tmdl")):
        current: dict[str, str] | None = None
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            match = _RELATIONSHIP_RE.match(line)
            if match:
                current = {"id": match.group(1).strip(" '\""), "path": str(path)}
                relationships.append(current)
                continue
            if current and ":" in line:
                key, value = line.split(":", 1)
                if key in {"fromTable", "fromColumn", "toTable", "toColumn"}:
                    current[key] = value.strip().strip("'\"")
    return relationships


def _find_cycle(graph: dict[str, set[str]]) -> list[str] | None:
    """SEM-AUDIT-03: devuelve un ciclo de dependencias si existe."""
    visited: set[str] = set()
    active: list[str] = []
    active_set: set[str] = set()

    def visit(node: str) -> list[str] | None:
        if node in active_set:
            start = active.index(node)
            return [*active[start:], node]
        if node in visited:
            return None
        visited.add(node)
        active.append(node)
        active_set.add(node)
        for dependency in sorted(graph.get(node, set())):
            cycle = visit(dependency)
            if cycle:
                return cycle
        active.pop()
        active_set.remove(node)
        return None

    for candidate in sorted(graph):
        cycle = visit(candidate)
        if cycle:
            return cycle
    return None


def _mask_dax_strings(expression: str) -> str:
    """SEM-AUDIT-03B: oculta strings DAX sin desplazar spans de referencias."""
    return re.sub(r'"(?:[^"]|"")*"', lambda match: " " * len(match.group(0)), expression)


def audit_static_semantic_model(migration_root: str | Path) -> dict[str, Any]:
    """SEM-AUDIT-04: valida referencias, relaciones y ciclos TMDL offline."""
    root = Path(migration_root).resolve()
    semantic_models = [path for path in root.glob("*.SemanticModel") if path.is_dir()]
    errors: list[dict[str, Any]] = []
    if len(semantic_models) != 1:
        return {
            "ok": False,
            "evidence": "static_tmdl_only",
            "errors": [{"code": "SEMANTIC_MODEL_COUNT", "actual": len(semantic_models)}],
            "limits": ["No ejecuta DAX ni abre el modelo en Analysis Services."],
        }

    definition_dir = semantic_models[0] / "definition"
    tables = _parse_tables(definition_dir / "tables")
    object_locations: dict[str, list[str]] = defaultdict(list)
    for table, objects in tables.items():
        for name in objects:
            object_locations[name].append(table)

    graph: dict[str, set[str]] = defaultdict(set)
    reference_count = 0
    for table, objects in tables.items():
        for name, item in objects.items():
            expression = item["expression"]
            if not expression:
                continue
            searchable_expression = _mask_dax_strings(expression)
            source = f"{table}.{name}"
            qualified_spans: list[tuple[int, int]] = []
            for match in _QUALIFIED_REF_RE.finditer(searchable_expression):
                reference_count += 1
                qualified_spans.append(match.span())
                target_table = match.group(1) or match.group(2)
                target_name = match.group(3)
                target = f"{target_table}.{target_name}"
                if target_table not in tables or target_name not in tables[target_table]:
                    errors.append({"code": "MISSING_QUALIFIED_REFERENCE", "source": source, "target": target})
                else:
                    graph[source].add(target)
            for match in _UNQUALIFIED_REF_RE.finditer(searchable_expression):
                if any(start <= match.start() < end for start, end in qualified_spans):
                    continue
                reference_count += 1
                target_name = match.group(1)
                candidates = []
                if target_name in objects:
                    candidates = [table]
                elif len(object_locations.get(target_name, [])) == 1:
                    candidates = object_locations[target_name]
                if not candidates:
                    errors.append({"code": "MISSING_UNQUALIFIED_REFERENCE", "source": source, "target": target_name})
                else:
                    graph[source].add(f"{candidates[0]}.{target_name}")

    relationships = _parse_relationships(definition_dir)
    for relationship in relationships:
        for table_key, column_key in (("fromTable", "fromColumn"), ("toTable", "toColumn")):
            table = relationship.get(table_key, "")
            column = relationship.get(column_key, "")
            if table not in tables or column not in tables.get(table, {}):
                errors.append(
                    {
                        "code": "INVALID_RELATIONSHIP_ENDPOINT",
                        "relationship": relationship.get("id"),
                        "target": f"{table}.{column}",
                    }
                )

    cycle = _find_cycle(graph)
    if cycle:
        errors.append({"code": "DEPENDENCY_CYCLE", "path": cycle})

    return {
        "ok": not errors,
        "evidence": "static_tmdl_only",
        "model": str(semantic_models[0]),
        "summary": {
            "tables": len(tables),
            "objects": sum(len(objects) for objects in tables.values()),
            "expressionReferences": reference_count,
            "relationships": len(relationships),
        },
        "errors": errors,
        "limits": [
            "No ejecuta DAX ni demuestra tipos, contexto de filtro o resultados.",
            "No valida carga/refresh del modelo ni fidelidad visual del reporte.",
            "Power BI Modeling MCP y SemanticOps requieren un modelo hospedado/conectado; no abren PBIP/TMDL offline.",
        ],
    }
