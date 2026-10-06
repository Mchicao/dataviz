"""
Optimization Recommender - Genera recomendaciones de optimización.

Analiza los resultados de auditoría y sugiere:
- Creación de vistas/tablas intermedias
- Cambios a Custom SQL
- Mejoras de tipos de datos
"""

from dataclasses import dataclass, field
from enum import Enum

from core.calculation_extractor import WorkbookCalculations
from core.connection_analyzer import ConnectionType, WorkbookConnectionAnalysis
from core.materialization_advisor import analyze_materialization_opportunities
from core.twb_column_auditor import DatasourceAudit, WorkbookAudit


class RecommendationType(Enum):
    """Tipo de recomendación."""

    CREATE_VIEW = "create_view"  # Crear vista optimizada
    HIDE_COLUMNS = "hide_columns"  # Ocultar columnas en Tableau
    FIX_CUSTOM_SQL = "fix_custom_sql"  # Arreglar Custom SQL
    ADD_FILTER = "add_filter"  # Agregar filtro WHERE
    MATERIALIZE_CALC = "materialize_calc"  # Materializar cálculo costoso
    CHANGE_DATATYPE = "change_datatype"  # Cambiar tipo de dato


class Priority(Enum):
    """Prioridad de la recomendación."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Effort(Enum):
    """Esfuerzo estimado de implementación."""

    EASY = "easy"  # < 30 min
    MEDIUM = "medium"  # 30 min - 2 hrs
    HARD = "hard"  # > 2 hrs


@dataclass
class Recommendation:
    """Una recomendación de optimización."""

    rec_type: RecommendationType
    priority: Priority
    effort: Effort
    title: str
    description: str
    datasource: str | None = None
    estimated_savings: str | None = None
    sql_suggestion: str | None = None

    @property
    def priority_emoji(self) -> str:
        return {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(self.priority.value, "⚪")

    @property
    def effort_label(self) -> str:
        return {"easy": "⚡ Fácil", "medium": "🔧 Moderado", "hard": "🔨 Complejo"}.get(
            self.effort.value, "?"
        )


@dataclass
class OptimizationReport:
    """Reporte completo de recomendaciones."""

    workbook_name: str
    recommendations: list[Recommendation] = field(default_factory=list)

    @property
    def high_priority_count(self) -> int:
        return sum(1 for r in self.recommendations if r.priority == Priority.HIGH)

    @property
    def total_recommendations(self) -> int:
        return len(self.recommendations)

    def add(self, rec: Recommendation):
        self.recommendations.append(rec)


def generate_recommendations(
    audit: WorkbookAudit,
    conn_analysis: WorkbookConnectionAnalysis | None = None,
    calc_analysis: WorkbookCalculations | None = None,
    blended_sheets: list | None = None,  # List[BlendedWorksheet]
) -> OptimizationReport:
    """
    Genera recomendaciones de optimización basadas en el análisis.

    Args:
        audit: Resultado del column audit
        conn_analysis: Análisis de conexiones (opcional)
        calc_analysis: Análisis de cálculos (opcional)

    Returns:
        OptimizationReport con todas las recomendaciones
    """
    report = OptimizationReport(workbook_name=audit.workbook_name)

    # Analizar cada datasource
    for ds in audit.datasources:
        _analyze_datasource(ds, report)

    # Analizar Custom SQL si existe
    if conn_analysis:
        _analyze_connections(conn_analysis, report)

    # Analizar cálculos complejos
    if calc_analysis:
        _analyze_calculations(calc_analysis, report)

    # Analizar Blending
    if blended_sheets:
        _analyze_blending(blended_sheets, report)

    # Ordenar por prioridad
    priority_order = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
    report.recommendations.sort(key=lambda r: priority_order[r.priority])

    return report


def _analyze_datasource(ds: DatasourceAudit, report: OptimizationReport):
    """Analiza un datasource y genera recomendaciones."""

    # Recomendación: Ocultar columnas no usadas
    unused_not_hidden = ds.total_columns - ds.used_columns - ds.hidden_columns
    if unused_not_hidden > 5 and ds.usage_percent < 70:
        report.add(
            Recommendation(
                rec_type=RecommendationType.HIDE_COLUMNS,
                priority=Priority.HIGH if ds.usage_percent < 50 else Priority.MEDIUM,
                effort=Effort.EASY,
                title=f"Ocultar {unused_not_hidden} columnas en '{ds.caption}'",
                description=f"El datasource usa solo {ds.usage_percent:.0f}% de sus columnas. "
                f"Ocultar las {unused_not_hidden} columnas no usadas reducirá el extracto.",
                datasource=ds.caption,
                estimated_savings=f"{ds.estimated_savings_bytes_per_row / 1024:.1f} KB por fila",
            )
        )

    # Recomendación: Crear vista optimizada si ahorro > 30%
    if ds.usage_percent < 70 and ds.table and ds.schema:
        columns = [c.name for c in ds.columns if c.is_used and not c.is_calculated]
        if columns:
            columns_sql = ",\n    ".join(columns[:20])
            if len(columns) > 20:
                columns_sql += f",\n    -- ... {len(columns) - 20} más"

            view_name = f"vw_{ds.table}_optimized"
            sql = f"""CREATE OR REPLACE VIEW {ds.schema}.{view_name} AS
SELECT
    {columns_sql}
FROM {ds.schema}.{ds.table};
-- Ahorro: {100 - ds.usage_percent:.0f}% menos columnas"""

            report.add(
                Recommendation(
                    rec_type=RecommendationType.CREATE_VIEW,
                    priority=Priority.MEDIUM,
                    effort=Effort.MEDIUM,
                    title=f"Crear vista optimizada para '{ds.caption}'",
                    description=f"Crear una vista SQL que solo incluya las {ds.used_columns} columnas usadas. "
                    f"Luego reapuntar el datasource a la vista.",
                    datasource=ds.caption,
                    estimated_savings=f"{100 - ds.usage_percent:.0f}% menos datos transferidos",
                    sql_suggestion=sql,
                )
            )


def _analyze_connections(conn_analysis: WorkbookConnectionAnalysis, report: OptimizationReport):
    """Analiza conexiones y genera recomendaciones de Custom SQL."""

    for conn in conn_analysis.datasources:
        if conn.connection_type == ConnectionType.CUSTOM_SQL and conn.has_issues:
            for issue in conn.issues:
                if issue.issue_type == "select_star":
                    report.add(
                        Recommendation(
                            rec_type=RecommendationType.FIX_CUSTOM_SQL,
                            priority=Priority.HIGH,
                            effort=Effort.EASY,
                            title=f"Eliminar SELECT * en '{conn.datasource_caption}'",
                            description="SELECT * descarga todas las columnas. "
                            "Reemplazar con lista explícita de columnas necesarias.",
                            datasource=conn.datasource_caption,
                            estimated_savings="Potencialmente 50%+ menos datos",
                        )
                    )

                elif issue.issue_type == "no_where":
                    report.add(
                        Recommendation(
                            rec_type=RecommendationType.ADD_FILTER,
                            priority=Priority.MEDIUM,
                            effort=Effort.MEDIUM,
                            title=f"Agregar filtro WHERE en '{conn.datasource_caption}'",
                            description="La query no tiene filtros y descarga toda la tabla. "
                            "Agregar WHERE con filtro de fecha o partición.",
                            datasource=conn.datasource_caption,
                        )
                    )


def _analyze_calculations(calc_analysis: WorkbookCalculations, report: OptimizationReport):
    """Analiza cálculos complejos y sugiere materialización."""

    # Buscar LOD muy complejos que podrían materializarse
    complex_lods = [
        c
        for c in calc_analysis.calculated_fields
        if c.calc_type.value == "lod" and c.complexity_score >= 7
    ]

    for calc in complex_lods[:3]:  # Máximo 3 recomendaciones
        report.add(
            Recommendation(
                rec_type=RecommendationType.MATERIALIZE_CALC,
                priority=Priority.LOW,
                effort=Effort.HARD,
                title=f"Materializar cálculo LOD '{calc.caption}'",
                description=f"Este cálculo LOD tiene complejidad {calc.complexity_score}/10. "
                "Considerar pre-calcular en la base de datos para mejor performance.",
                estimated_savings="Mejora en tiempo de refresco",
            )
        )

    # 2. Análisis de Materialización (IF/CASE complejos)
    mat_opportunities = analyze_materialization_opportunities(calc_analysis.calculated_fields)
    for opp in mat_opportunities:
        report.add(
            Recommendation(
                rec_type=RecommendationType.MATERIALIZE_CALC,
                priority=Priority.MEDIUM if opp.complexity < 10 else Priority.HIGH,
                effort=Effort.HARD,
                title=f"Materializar lógica: {opp.calc_name}",
                description=opp.description,
                estimated_savings=f"Simplificación de {opp.complexity} ramas condicionales",
            )
        )


def generate_report_markdown(report: OptimizationReport) -> str:
    """Genera reporte en formato Markdown."""
    lines = []
    lines.append("# Recomendaciones de Optimización")
    lines.append(f"**Workbook**: {report.workbook_name}")
    lines.append(f"**Total recomendaciones**: {report.total_recommendations}")
    lines.append(f"**Alta prioridad**: {report.high_priority_count}")
    lines.append("")

    for r in report.recommendations:
        lines.append(f"## {r.priority_emoji} {r.title}")
        lines.append(f"**Prioridad**: {r.priority.value} | **Esfuerzo**: {r.effort_label}")
        if r.datasource:
            lines.append(f"**Datasource**: {r.datasource}")
        lines.append("")
        lines.append(r.description)
        if r.estimated_savings:
            lines.append(f"\n**Ahorro estimado**: {r.estimated_savings}")
        if r.sql_suggestion:
            lines.append("\n```sql")
            lines.append(r.sql_suggestion)
            lines.append("```")
        lines.append("")

    return "\n".join(lines)


def _analyze_blending(blended_sheets: list, report: OptimizationReport):
    """Analiza hojas con Blending y sugiere Relationships."""
    for sheet in blended_sheets:
        report.add(
            Recommendation(
                rec_type=RecommendationType.CREATE_VIEW,  # Reusing type or add new one
                priority=Priority.MEDIUM,
                effort=Effort.MEDIUM,
                title=f"Evitar Blending en hoja '{sheet.worksheet_name}'",
                description=f"La hoja mezcla datos de {len(sheet.secondary_datasources) + 1} fuentes ({sheet.primary_datasource} + secundarios). "
                "El Data Blending en Tableau es menos eficiente que las Relationships/Joins. "
                "Crea un modelo de datos unificado en la fuente.",
                estimated_savings="Mejora de performance y flexibilidad",
            )
        )
