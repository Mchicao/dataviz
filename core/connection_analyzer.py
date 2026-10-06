"""
Connection Analyzer - Detecta tipo de conexión y Custom SQL en Tableau.

Analiza datasources para identificar:
- Conexión nativa (tabla directa) → Tableau optimiza automáticamente
- Custom SQL → Potencial antipatrón SELECT *
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from pathlib import Path

ANALYZER_VERSION = "1.1.0"
_REDACTED = "[REDACTED]"


def _artifact_evidence(file_path: str, diagnostic: str) -> dict[str, object] | None:
    """Return a stable, non-sensitive reference for an analyzed artifact."""
    path = Path(file_path)
    try:
        artifact_hash = sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None
    return {
        "artifact_hash": artifact_hash,
        "locator": path.name,
        "diagnostic": diagnostic,
        "analyzer_version": ANALYZER_VERSION,
    }


def _private(value: str | None, *, local_only: bool) -> str | None:
    if value is None or local_only:
        return value
    return _REDACTED


class ConnectionType(Enum):
    """Tipo de conexión del datasource."""

    NATIVE_TABLE = "native"  # Conexión a tabla directa
    CUSTOM_SQL = "custom_sql"  # SQL personalizado
    JOIN = "join"  # Múltiples tablas con JOIN
    EXTRACT_ONLY = "extract"  # Solo extracto, sin conexión live
    UNKNOWN = "unknown"


@dataclass
class CustomSQLIssue:
    """Problema detectado en Custom SQL."""

    issue_type: str  # 'select_star', 'no_where', 'subquery_wrap'
    severity: str  # 'high', 'medium', 'low'
    description: str
    suggestion: str

    def to_dict(self) -> dict[str, str]:
        """Devuelve una representación estable para inventarios y evidencia."""
        return {
            "issue_type": self.issue_type,
            "severity": self.severity,
            "description": self.description,
            "suggestion": self.suggestion,
        }


@dataclass
class ConnectionAnalysis:
    """Resultado del análisis de conexión de un datasource."""

    datasource_name: str
    datasource_caption: str
    connection_type: ConnectionType
    table_name: str | None = None
    schema_name: str | None = None
    server: str | None = None
    database: str | None = None
    custom_sql: str | None = None  # SQL original si es Custom SQL
    issues: list[CustomSQLIssue] = field(default_factory=list)

    @property
    def table(self) -> str | None:
        """Alias para compatibilidad con vistas."""
        return self.table_name

    @property
    def schema(self) -> str | None:
        """Alias para compatibilidad con vistas."""
        return self.schema_name

    @property
    def has_issues(self) -> bool:
        return len(self.issues) > 0

    @property
    def optimization_badge(self) -> str:
        """Badge que indica si Tableau optimiza la conexión."""
        if self.connection_type == ConnectionType.NATIVE_TABLE:
            return "✅ Tableau Optimiza"
        elif self.connection_type == ConnectionType.CUSTOM_SQL:
            if any(i.issue_type == "select_star" for i in self.issues):
                return "🔴 SELECT * detectado"
            return "⚠️ Custom SQL"
        elif self.connection_type == ConnectionType.JOIN:
            return "🟡 JOIN (revisar)"
        else:
            return "⚪ N/A"

    def to_dict(self, *, local_only: bool = False) -> dict[str, object]:
        """Serialize the source, redacting connection details by default."""
        return {
            "name": self.datasource_name,
            "caption": self.datasource_caption,
            "type": self.connection_type.value,
            "table": _private(self.table_name, local_only=local_only),
            "schema": _private(self.schema_name, local_only=local_only),
            "server": _private(self.server, local_only=local_only),
            "database": _private(self.database, local_only=local_only),
            "custom_sql": _private(self.custom_sql, local_only=local_only),
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class WorkbookConnectionAnalysis:
    """Análisis completo de conexiones del workbook."""

    file_path: str
    datasources: list[ConnectionAnalysis] = field(default_factory=list)

    @property
    def native_count(self) -> int:
        return sum(1 for d in self.datasources if d.connection_type == ConnectionType.NATIVE_TABLE)

    @property
    def custom_sql_count(self) -> int:
        return sum(1 for d in self.datasources if d.connection_type == ConnectionType.CUSTOM_SQL)

    @property
    def issues_count(self) -> int:
        return sum(len(d.issues) for d in self.datasources)

    def to_dict(self, *, local_only: bool = False) -> dict[str, object]:
        """Serialize an analysis with safe defaults and reproducible evidence."""
        evidence = _artifact_evidence(self.file_path, "Tableau connections parsed")
        return {
            "file_path": self.file_path if local_only else None,
            "datasource_count": len(self.datasources),
            "native_count": self.native_count,
            "custom_sql_count": self.custom_sql_count,
            "issues_count": self.issues_count,
            "datasources": [
                datasource.to_dict(local_only=local_only) for datasource in self.datasources
            ],
            "coverage": {
                "status": "incomplete",
                "complete": False,
                "reason": "Connection analysis covers declared Tableau datasources only.",
            },
            "evidence": [evidence] if evidence else [],
        }


def analyze_custom_sql(sql: str) -> list[CustomSQLIssue]:
    """Analiza un Custom SQL y detecta antipatrones."""
    issues = []
    sql_upper = sql.upper().strip()

    # Detectar SELECT *
    if re.search(r"\bSELECT\s+\*\s+FROM\b", sql_upper):
        issues.append(
            CustomSQLIssue(
                issue_type="select_star",
                severity="high",
                description="SELECT * descarga TODAS las columnas de la tabla",
                suggestion="Reemplazar con SELECT columna1, columna2, ... solo las columnas necesarias",
            )
        )

    # Detectar falta de WHERE
    if "WHERE" not in sql_upper and "LIMIT" not in sql_upper:
        issues.append(
            CustomSQLIssue(
                issue_type="no_where",
                severity="medium",
                description="Sin cláusula WHERE - descarga toda la tabla",
                suggestion="Agregar WHERE con filtros de fecha u otros para limitar datos",
            )
        )

    # Detectar subquery (Tableau hace wrapping)
    if sql_upper.count("SELECT") > 1:
        issues.append(
            CustomSQLIssue(
                issue_type="subquery",
                severity="low",
                description="SQL contiene subqueries que pueden afectar performance",
                suggestion="Considerar crear una vista en la BD para simplificar",
            )
        )

    return issues


def analyze_connections(file_path: str) -> WorkbookConnectionAnalysis:
    """
    Analiza todas las conexiones de un workbook de Tableau.

    Args:
        file_path: Ruta al archivo .twb o .twbx

    Returns:
        WorkbookConnectionAnalysis con el análisis completo
    """
    # Extraer TWB si es TWBX
    if file_path.lower().endswith(".twbx"):
        from core.twbx_extractor import cleanup_temp_dir, extract_twb_from_twbx

        with open(file_path, "rb") as f:
            twb_path, temp_dir = extract_twb_from_twbx(f)
        try:
            return _analyze_twb_connections(twb_path, file_path)
        finally:
            cleanup_temp_dir(temp_dir)
    else:
        return _analyze_twb_connections(file_path, file_path)


def _analyze_twb_connections(twb_path: str, original_path: str) -> WorkbookConnectionAnalysis:
    """Analiza conexiones en un archivo TWB."""
    tree = ET.parse(twb_path)
    root = tree.getroot()

    result = WorkbookConnectionAnalysis(file_path=original_path)

    # Procesar cada datasource
    for ds in root.findall(".//datasources/datasource"):
        ds_name = ds.get("name", "")
        ds_caption = ds.get("caption", ds_name)

        # Ignorar Parameters
        if "Parameters" in ds_name:
            continue

        # Extraer información de conexión (server, database)
        conn = ds.find(".//connection")
        conn_server = conn.get("server") if conn is not None else None
        conn_database = conn.get("dbname") if conn is not None else None

        # Buscar relation para determinar tipo de conexión
        relation = ds.find(".//connection/relation")

        if relation is None:
            # Puede ser solo extracto
            extract = ds.find(".//extract")
            if extract is not None:
                result.datasources.append(
                    ConnectionAnalysis(
                        datasource_name=ds_name,
                        datasource_caption=ds_caption,
                        connection_type=ConnectionType.EXTRACT_ONLY,
                    )
                )
            continue

        rel_type = relation.get("type", "")

        if rel_type == "table":
            # Conexión nativa a tabla
            table = relation.get("table", "").strip("[]")
            # Separar schema.table
            parts = table.split("].[") if "].[" in table else table.split(".")
            schema = parts[0].strip("[]") if len(parts) > 1 else None
            table_name = parts[-1].strip("[]")

            result.datasources.append(
                ConnectionAnalysis(
                    datasource_name=ds_name,
                    datasource_caption=ds_caption,
                    connection_type=ConnectionType.NATIVE_TABLE,
                    table_name=table_name,
                    schema_name=schema,
                    server=conn_server,
                    database=conn_database,
                )
            )

        elif rel_type == "text":
            # Custom SQL - el contenido está en el texto del elemento
            custom_sql = relation.text or ""
            issues = analyze_custom_sql(custom_sql)

            result.datasources.append(
                ConnectionAnalysis(
                    datasource_name=ds_name,
                    datasource_caption=ds_caption,
                    connection_type=ConnectionType.CUSTOM_SQL,
                    custom_sql=custom_sql,
                    server=conn_server,
                    database=conn_database,
                    issues=issues,
                )
            )

        elif rel_type == "join":
            # JOIN entre tablas
            result.datasources.append(
                ConnectionAnalysis(
                    datasource_name=ds_name,
                    datasource_caption=ds_caption,
                    connection_type=ConnectionType.JOIN,
                )
            )
        else:
            result.datasources.append(
                ConnectionAnalysis(
                    datasource_name=ds_name,
                    datasource_caption=ds_caption,
                    connection_type=ConnectionType.UNKNOWN,
                )
            )

    return result


def generate_connection_report(analysis: WorkbookConnectionAnalysis) -> str:
    """Genera reporte de texto del análisis de conexiones."""
    lines = []
    lines.append("=" * 60)
    lines.append("ANÁLISIS DE CONEXIONES - BI Bridge Studio")
    lines.append("=" * 60)
    lines.append(f"Archivo: {analysis.file_path}")
    lines.append(f"Datasources: {len(analysis.datasources)}")
    lines.append(f"  - Nativos (Tableau optimiza): {analysis.native_count}")
    lines.append(f"  - Custom SQL: {analysis.custom_sql_count}")
    lines.append(f"  - Problemas detectados: {analysis.issues_count}")
    lines.append("")

    for ds in analysis.datasources:
        lines.append("-" * 40)
        lines.append(f"📦 {ds.datasource_caption}")
        lines.append(f"   Tipo: {ds.connection_type.value} {ds.optimization_badge}")

        if ds.table_name:
            table_full = f"{ds.schema_name}.{ds.table_name}" if ds.schema_name else ds.table_name
            lines.append(f"   Tabla: {table_full}")

        if ds.custom_sql:
            sql_preview = ds.custom_sql[:100].replace("\n", " ")
            if len(ds.custom_sql) > 100:
                sql_preview += "..."
            lines.append(f"   SQL: {sql_preview}")

        for issue in ds.issues:
            emoji = (
                "🔴" if issue.severity == "high" else "🟡" if issue.severity == "medium" else "🟢"
            )
            lines.append(f"   {emoji} {issue.description}")
            lines.append(f"      → {issue.suggestion}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# CLI
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python -m core.connection_analyzer <archivo.twb>")
        sys.exit(1)

    analysis = analyze_connections(sys.argv[1])
    print(generate_connection_report(analysis))
