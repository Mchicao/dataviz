"""
Migration Planner - Genera un plan estratégico de migración.

Agrega los análisis de columnas, conexiones, cálculos y optimización
para generar un documento completo de estrategia de migración.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any

from core.calculation_extractor import WorkbookCalculations
from core.connection_analyzer import WorkbookConnectionAnalysis
from core.optimization_recommender import OptimizationReport
from core.twb_column_auditor import WorkbookAudit

ANALYZER_VERSION = "1.1.0"
_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "server",
    "database",
    "schema",
    "table",
    "custom_sql",
    "raw_sql",
    "sql",
    "file_path",
    "path",
    "artifact_path",
}


def _json_safe(value: Any, *, local_only: bool = False, key: str | None = None) -> Any:
    if not local_only and key in _SENSITIVE_KEYS and value is not None:
        return _REDACTED
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, dict):
        return {
            str(item_key): _json_safe(item_value, local_only=local_only, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, local_only=local_only) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item, local_only=local_only) for item in value)
    if isinstance(value, str) and not local_only:
        path = Path(value)
        if path.is_absolute():
            return _REDACTED
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("migration plan contains a non-finite number")
        return value
    raise TypeError(f"migration plan contains unsupported value: {type(value).__name__}")


def _evidence_reference(value: object) -> dict[str, object]:
    locator = str(value)
    path = Path(locator)
    artifact_hash = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    return {
        "artifact_hash": artifact_hash,
        "locator": path.name,
        "diagnostic": "Caller-supplied migration evidence reference",
        "analyzer_version": ANALYZER_VERSION,
    }


@dataclass
class MigrationEffort:
    """Estimación de esfuerzo de migración."""

    complexity_level: str  # Low, Medium, High, Critical
    estimated_hours: int
    rationale: list[str] = field(default_factory=list)


@dataclass
class MigrationPlan:
    """Plan completo de migración."""

    workbook_name: str
    generated_at: str
    summary: str
    effort: MigrationEffort
    risks: list[str] = field(default_factory=list)
    strategy_steps: list[str] = field(default_factory=list)
    coverage: dict[str, object] = field(default_factory=dict)
    sources: list[dict[str, object]] = field(default_factory=list)
    visuals: list[dict[str, object]] = field(default_factory=list)
    calculations: list[dict[str, object]] = field(default_factory=list)
    filters: list[dict[str, object]] = field(default_factory=list)
    interactions: list[dict[str, object]] = field(default_factory=list)
    dependencies: list[dict[str, object]] = field(default_factory=list)
    manual_work: list[str] = field(default_factory=list)
    recommendations: list[dict[str, object]] = field(default_factory=list)
    expected_result: str = (
        "Inventario y plan acotados a los artefactos autorizados; no implica migración universal."
    )
    evidence: list[object] = field(default_factory=list)
    universal_migration_claim: bool = False

    def to_markdown(self) -> str:
        """Genera el reporte en formato Markdown."""
        lines = []
        lines.append(f"# 🚀 Plan de Migración: {self.workbook_name}")
        lines.append(f"*Generado el: {self.generated_at} por BI Bridge Studio*")
        lines.append("")

        lines.append("## 📊 Resumen Ejecutivo")
        lines.append(self.summary)
        lines.append(f"**Alcance**: {self.expected_result}")
        lines.append("")

        lines.append("## ⏱️ Estimación de Esfuerzo")
        lines.append(f"**Nivel de Complejidad**: {self.effort.complexity_level}")
        lines.append(f"**Esfuerzo Estimado**: {self.effort.estimated_hours} horas")
        lines.append("\n**Justificación**:")
        for r in self.effort.rationale:
            lines.append(f"- {r}")
        lines.append("")

        if self.risks:
            lines.append("## ⚠️ Riesgos Identificados")
            for risk in self.risks:
                lines.append(f"- 🔴 {risk}")
            lines.append("")

        if self.manual_work:
            lines.append("## 🛠️ Trabajo Manual Requerido")
            for item in self.manual_work:
                lines.append(f"- {item}")
            lines.append("")

        lines.append("## 👣 Estrategia de Migración")
        for i, step in enumerate(self.strategy_steps, 1):
            lines.append(f"{i}. {step}")

        return "\n".join(lines)

    def to_dict(self, *, local_only: bool = False) -> dict[str, object]:
        """Serialize a JSON-safe plan with privacy-safe defaults."""
        payload = {
            "workbook_name": self.workbook_name,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "effort": {
                "complexity_level": self.effort.complexity_level,
                "estimated_hours": self.effort.estimated_hours,
                "rationale": list(self.effort.rationale),
            },
            "scope": self.expected_result,
            "universal_migration_claim": self.universal_migration_claim,
            "coverage": {
                **self.coverage,
                "status": self.coverage.get("status", "incomplete"),
                "complete": self.coverage.get("complete", False),
            },
            "sources": self.sources,
            "visuals": self.visuals,
            "calculations": self.calculations,
            "filters": self.filters,
            "interactions": self.interactions,
            "dependencies": self.dependencies,
            "risks": list(self.risks),
            "manual_work": list(self.manual_work),
            "recommendations": list(self.recommendations),
            "expected_result": self.expected_result,
            "strategy_steps": list(self.strategy_steps),
            "evidence": list(self.evidence),
        }
        return _json_safe(payload, local_only=local_only)


def calculate_effort(
    audit: WorkbookAudit,
    conn: WorkbookConnectionAnalysis | None,
    calc: WorkbookCalculations | None,
    opt: OptimizationReport | None,
) -> MigrationEffort:
    """Calcula el esfuerzo de migración basado en métricas."""
    base_hours = 4  # Setup inicial + validación básica
    rationale = ["Tiempo base de configuración y validación (4h)"]
    complexity = "Low"

    # 1. Volumetría
    cols_score = len(audit.datasources) + (audit.total_columns / 50)
    if cols_score > 10:
        base_hours += 4
        rationale.append("Alta cantidad de columnas/datasources (+4h)")
        complexity = "Medium"

    # 2. Conexiones
    if conn:
        custom_sql_count = sum(
            1 for ds in conn.datasources if ds.connection_type.value == "custom_sql"
        )
        if custom_sql_count > 0:
            base_hours += 4 * custom_sql_count
            rationale.append(
                f"{custom_sql_count} Custom SQL detectados - requiere reescritura (+{4 * custom_sql_count}h)"
            )
            complexity = "Medium"

    # 3. Cálculos
    if calc:
        # LODs son complejos
        if calc.lod_count > 0:
            hours = calc.lod_count * 2
            base_hours += hours
            rationale.append(f"{calc.lod_count} cálculos LOD complejos (+{hours}h)")
            complexity = "High" if calc.lod_count > 5 else complexity

        # Table Calcs son muy complejos
        if calc.table_calc_count > 0:
            hours = calc.table_calc_count * 3
            base_hours += hours
            rationale.append(
                f"{calc.table_calc_count} Table Calculations (RANK, RUNNING) (+{hours}h)"
            )
            complexity = "High"

    # 4. Recomendaciones
    if opt and opt.high_priority_count > 0:
        base_hours += 2 * opt.high_priority_count
        rationale.append(
            f"{opt.high_priority_count} optimizaciones de alta prioridad requeridas (+{2 * opt.high_priority_count}h)"
        )

    # Definir nivel final
    if base_hours > 40:
        complexity = "Critical"
    elif base_hours > 20 and complexity != "High":
        complexity = "Medium"

    return MigrationEffort(complexity, int(base_hours), rationale)


def generate_migration_plan(
    audit: WorkbookAudit,
    conn: WorkbookConnectionAnalysis | None,
    calc: WorkbookCalculations | None,
    opt: OptimizationReport | None,
    *,
    filters: list[dict[str, object]] | None = None,
    interactions: list[dict[str, object]] | None = None,
    dependencies: list[dict[str, object]] | None = None,
    evidence: list[object] | None = None,
) -> MigrationPlan:
    """Genera el plan de migración completo."""

    effort = calculate_effort(audit, conn, calc, opt)

    risks = []
    manual_work = []
    if conn and any(ds.connection_type.value == "custom_sql" for ds in conn.datasources):
        risks.append(
            "Uso de Custom SQL puede degradar performance en Power BI (impedir Query Folding)."
        )
        manual_work.append("Revisar y validar cada Custom SQL antes de reemplazarlo por una vista o consulta nativa.")
    if calc and calc.table_calc_count > 5:
        risks.append("Alto uso de Table Calculations: difícil replicación exacta en DAX.")
    if calc and (calc.lod_count or calc.table_calc_count):
        manual_work.append("Validar manualmente el contexto de cada cálculo LOD o Table Calculation en DAX.")
    if audit.total_columns > 100:
        risks.append("Modelo muy ancho (muchas columnas): posible impacto en memoria.")

    strategy = [
        "**Preparación**: Crear vistas en base de datos para eliminar Custom SQL.",
        "**Modelado**: Importar tablas limpias a Power BI y establecer relaciones.",
    ]

    if calc and (calc.lod_count > 0 or calc.table_calc_count > 0):
        strategy.append(
            "**Cálculos**: Migrar cálculos LOD a medidas DAX usando `CALCULATE`. Reemplazar Table Calcs por medidas con `RANKX` o ventanas temporales."
        )
    else:
        strategy.append(
            "**Cálculos**: Migrar cálculos simples a columnas calculadas o medidas básicas."
        )

    strategy.append(
        "**Visualización**: Recrear los dashboards usando los visuales nativos de Power BI."
    )
    strategy.append("**Validación**: Comparar cifras totales entre Tableau y Power BI.")

    filter_items = list(filters or [])
    interaction_items = list(interactions or [])
    dependency_items = list(dependencies or [])
    source_items = [ds.to_dict(local_only=True) for ds in conn.datasources] if conn else []
    calculation_items = []
    if calc:
        calculation_items = [
            {
                "name": item.name,
                "type": item.calc_type.value,
                "complexity": item.complexity_score,
                "referenced_columns": sorted(item.referenced_columns),
                "used_by_worksheets": sorted(item.used_by_worksheets),
            }
            for item in calc.calculated_fields
        ]
    visual_items = [
        {"kind": "worksheet", "count": audit.worksheets_count},
        {"kind": "dashboard", "count": audit.dashboards_count},
    ]
    coverage = {
        "status": "incomplete",
        "complete": False,
        "reason": "Structural planning coverage does not prove runtime or visual equivalence.",
        "sources": len(source_items),
        "visuals": audit.worksheets_count,
        "calculations": len(calculation_items),
        "filters": len(filter_items),
        "interactions": len(interaction_items),
        "dependencies": len(dependency_items),
    }
    if not manual_work:
        manual_work.append("Revisar visuales, filtros, interacciones y cifras con evidencia del artefacto autorizado.")

    evidence_items = list(evidence or [])
    if not evidence_items:
        evidence_items.append(conn.file_path if conn else audit.file_path)
    evidence_refs = [
        item if isinstance(item, dict) else _evidence_reference(item) for item in evidence_items
    ]
    recommendations = [
        {"kind": "risk", "message": risk, "evidence": list(evidence_refs)} for risk in risks
    ]
    recommendations.extend(
        {"kind": "manual_work", "message": item, "evidence": list(evidence_refs)}
        for item in manual_work
    )

    summary = (
        f"Workbook con {len(audit.datasources)} fuentes de datos y {audit.total_columns} columnas total. "
        f"Se han detectado {calc.total_calculations if calc else 0} campos calculados. "
        f"La migración se clasifica como de complejidad **{effort.complexity_level}**."
    )

    return MigrationPlan(
        workbook_name=audit.workbook_name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        summary=summary,
        effort=effort,
        risks=risks,
        strategy_steps=strategy,
        coverage=coverage,
        sources=source_items,
        visuals=visual_items,
        calculations=calculation_items,
        filters=filter_items,
        interactions=interaction_items,
        dependencies=dependency_items,
        manual_work=manual_work,
        recommendations=recommendations,
        evidence=evidence_refs,
    )
