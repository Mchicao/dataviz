"""
Migrador de esquemas para archivos Tableau TWB.
Valida exhaustivamente antes de modificar y genera archivo nuevo.

Validaciones previas:
- Tabla existe en esquema destino
- Tabla tiene datos
- Estructura coincide (columnas)
- Nivel de datos similar (diferencia de filas)

Uso:
    python -m core.twb_schema_migrator "archivo.twb" --from reds_base --to ciclo_ingresos_prd
"""

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from core.db_connector import (
    DatabaseConfig,
    DatabaseConnector,
)


class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class MigrationAlert:
    """Alerta generada durante validación."""

    level: AlertLevel
    table: str
    message: str
    details: str | None = None

    @property
    def emoji(self) -> str:
        return {
            AlertLevel.INFO: "ℹ️",
            AlertLevel.WARNING: "⚠️",
            AlertLevel.ERROR: "❌",
            AlertLevel.CRITICAL: "🚫",
        }.get(self.level, "❓")


@dataclass
class TableMigrationStatus:
    """Estado de migración para una tabla específica."""

    source_schema: str
    target_schema: str
    table: str

    # Validaciones
    exists_in_target: bool = False
    has_data_in_target: bool = False
    structure_matches: bool = False

    # Comparación de estructura
    source_columns: list[str] = field(default_factory=list)
    target_columns: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    extra_columns: list[str] = field(default_factory=list)

    # Comparación de datos
    source_row_count: int | None = None
    target_row_count: int | None = None
    row_count_diff_percent: float | None = None

    # Alertas
    alerts: list[MigrationAlert] = field(default_factory=list)

    @property
    def can_migrate(self) -> bool:
        """¿Es seguro migrar esta tabla?"""
        # Solo bloqueamos si no existe o hay error crítico
        has_critical = any(a.level == AlertLevel.CRITICAL for a in self.alerts)
        return self.exists_in_target and not has_critical

    @property
    def status_emoji(self) -> str:
        if not self.exists_in_target:
            return "❌"
        elif not self.has_data_in_target:
            return "⚠️"
        elif not self.structure_matches:
            return "🟡"
        elif self.row_count_diff_percent and abs(self.row_count_diff_percent) > 50:
            return "🟡"
        else:
            return "✅"


@dataclass
class MigrationReport:
    """Reporte completo de validación de migración."""

    source_schema: str
    target_schema: str
    twb_path: str
    timestamp: str

    # Tablas encontradas
    tables: list[TableMigrationStatus] = field(default_factory=list)

    # Resumen
    total_tables: int = 0
    tables_ready: int = 0
    tables_with_warnings: int = 0
    tables_blocked: int = 0

    # Alertas globales
    alerts: list[MigrationAlert] = field(default_factory=list)

    # Estado de migración
    migration_possible: bool = False
    migration_file: str | None = None

    def add_alert(self, level: AlertLevel, table: str, message: str, details: str | None = None):
        self.alerts.append(MigrationAlert(level, table, message, details))

    def calculate_summary(self):
        """Calcula resumen después de validar todas las tablas."""
        self.total_tables = len(self.tables)
        self.tables_ready = sum(1 for t in self.tables if t.can_migrate and not t.alerts)
        self.tables_with_warnings = sum(1 for t in self.tables if t.can_migrate and t.alerts)
        self.tables_blocked = sum(1 for t in self.tables if not t.can_migrate)
        self.migration_possible = self.tables_blocked == 0


def extract_tables_from_twb(twb_path: str) -> list[dict]:
    """
    Extrae información de todas las tablas usadas en un TWB.

    Returns:
        Lista de dicts con: datasource, schema, table, connection_name
    """
    tree = ET.parse(twb_path)
    root = tree.getroot()

    tables = []

    for ds in root.findall(".//datasources/datasource"):
        ds_name = ds.get("name", "")
        ds_caption = ds.get("caption", ds_name)

        if "Parameters" in ds_name:
            continue

        # Buscar conexión
        schema = ""
        server = ""
        dbname = ""
        for named_conn in ds.findall(".//named-connection"):
            # named_conn.get("name") ignored
            conn = named_conn.find("connection")

            if conn is not None:
                schema = conn.get("schema", "")
                server = conn.get("server", "")
                dbname = conn.get("dbname", "")

        # Buscar tablas/relaciones
        for relation in ds.findall(".//relation[@type='table']"):
            table_full = relation.get("table", "")
            table_name = relation.get("name", "")

            # Limpiar nombre de tabla
            if table_full:
                # Formato: [schema].[tabla]
                parts = table_full.strip("[]").split("].[")
                if len(parts) == 2:
                    schema = parts[0]
                    table = parts[1]
                else:
                    table = parts[0]
            else:
                table = table_name

            tables.append(
                {
                    "datasource": ds_name,
                    "datasource_caption": ds_caption,
                    "schema": schema,
                    "table": table,
                    "server": server,
                    "database": dbname,
                }
            )

    return tables


def validate_migration(
    twb_path: str, source_schema: str, target_schema: str, db_config: dict
) -> MigrationReport:
    """
    Valida todas las tablas antes de migrar.

    Checks:
    1. Tabla existe en target
    2. Tabla tiene datos en target
    3. Estructura coincide (columnas)
    4. Cantidad de filas similar (+/- 50%)
    """
    report = MigrationReport(
        source_schema=source_schema,
        target_schema=target_schema,
        twb_path=twb_path,
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Extraer tablas del TWB
    tables_info = extract_tables_from_twb(twb_path)

    # Filtrar solo las del esquema origen
    source_tables = [t for t in tables_info if t["schema"] == source_schema]

    if not source_tables:
        report.add_alert(
            AlertLevel.WARNING,
            "*",
            f"No se encontraron tablas con esquema '{source_schema}'",
        )
        return report

    # Conectar a BD y validar
    try:
        config = DatabaseConfig.from_dict(db_config)
        with DatabaseConnector(config) as conn:
            # Obtener tablas disponibles en target
            target_tables_list = conn.get_schema_tables(target_schema)
            target_tables_set = set(t.lower() for t in target_tables_list)

            for table_info in source_tables:
                table = table_info["table"]
                status = TableMigrationStatus(
                    source_schema=source_schema,
                    target_schema=target_schema,
                    table=table,
                )

                # 1. Verificar existencia
                status.exists_in_target = table.lower() in target_tables_set

                if not status.exists_in_target:
                    status.alerts.append(
                        MigrationAlert(
                            AlertLevel.ERROR,
                            table,
                            f"Tabla NO existe en esquema '{target_schema}'",
                        )
                    )
                    report.tables.append(status)
                    continue

                # 2. Verificar datos
                status.has_data_in_target = conn.table_has_data(target_schema, table)

                if not status.has_data_in_target:
                    status.alerts.append(
                        MigrationAlert(
                            AlertLevel.WARNING,
                            table,
                            "Tabla existe pero está VACÍA en destino",
                        )
                    )

                # 3. Comparar estructura
                try:
                    comparison = conn.compare_table_structure(source_schema, target_schema, table)

                    status.source_columns = comparison.source_columns
                    status.target_columns = comparison.target_columns
                    status.missing_columns = comparison.missing_in_target
                    status.extra_columns = comparison.extra_in_target
                    status.structure_matches = comparison.columns_match
                    status.source_row_count = comparison.source_row_count
                    status.target_row_count = comparison.target_row_count
                    status.row_count_diff_percent = comparison.row_count_diff_percent

                    if status.missing_columns:
                        status.alerts.append(
                            MigrationAlert(
                                AlertLevel.WARNING,
                                table,
                                f"Faltan {len(status.missing_columns)} columnas en destino",
                                f"Columnas faltantes: {', '.join(status.missing_columns[:5])}",
                            )
                        )

                    if status.extra_columns:
                        status.alerts.append(
                            MigrationAlert(
                                AlertLevel.INFO,
                                table,
                                f"Destino tiene {len(status.extra_columns)} columnas extra",
                                f"Columnas extra: {', '.join(status.extra_columns[:5])}",
                            )
                        )

                    # 4. Comparar cantidad de datos
                    if status.row_count_diff_percent:
                        diff = status.row_count_diff_percent
                        if abs(diff) > 80:
                            status.alerts.append(
                                MigrationAlert(
                                    AlertLevel.WARNING,
                                    table,
                                    f"Gran diferencia en filas: {diff:+.1f}%",
                                    f"Origen: {status.source_row_count:,} | Destino: {status.target_row_count:,}",
                                )
                            )
                        elif abs(diff) > 50:
                            status.alerts.append(
                                MigrationAlert(
                                    AlertLevel.INFO,
                                    table,
                                    f"Diferencia moderada en filas: {diff:+.1f}%",
                                )
                            )

                except Exception as e:
                    status.alerts.append(
                        MigrationAlert(
                            AlertLevel.ERROR,
                            table,
                            f"Error al comparar estructura: {str(e)}",
                        )
                    )

                report.tables.append(status)

    except Exception as e:
        report.add_alert(AlertLevel.CRITICAL, "*", f"Error de conexión: {str(e)}")

    report.calculate_summary()
    return report


def migrate_twb_schema(
    twb_path: str,
    source_schema: str,
    target_schema: str,
    output_path: str | None = None,
) -> str:
    """
    Modifica el esquema en un archivo TWB.
    Crea un archivo NUEVO, nunca modifica el original.

    Args:
        twb_path: Ruta al archivo .twb original
        source_schema: Esquema a reemplazar
        target_schema: Nuevo esquema
        output_path: Ruta de salida (opcional, se genera automáticamente)

    Returns:
        Ruta al archivo migrado
    """
    # Leer contenido
    with open(twb_path, encoding="utf-8") as f:
        content = f.read()

    # Patrones a reemplazar
    patterns = [
        # schema='esquema'
        (f"schema='{source_schema}'", f"schema='{target_schema}'"),
        (f'schema="{source_schema}"', f'schema="{target_schema}"'),
        # [esquema].[tabla]
        (f"[{source_schema}].", f"[{target_schema}]."),
        # table='[esquema].[tabla]'
        (f"'{source_schema}.", f"'{target_schema}."),
        # En captions y nombres
        (f"({source_schema})", f"({target_schema})"),
    ]

    new_content = content
    for old, new in patterns:
        new_content = new_content.replace(old, new)

    # Generar ruta de salida
    if not output_path:
        base = os.path.splitext(twb_path)[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"{base}_migrated_{timestamp}.twb"

    # Guardar archivo nuevo
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return output_path


def generate_migration_report_text(report: MigrationReport) -> str:
    """Genera reporte de texto de la validación."""
    lines = []
    lines.append("=" * 70)
    lines.append("REPORTE DE VALIDACIÓN DE MIGRACIÓN")
    lines.append("=" * 70)
    lines.append(f"Archivo: {report.twb_path}")
    lines.append(f"Fecha: {report.timestamp}")
    lines.append(f"Migración: {report.source_schema} → {report.target_schema}")
    lines.append("")
    lines.append("-" * 50)
    lines.append("RESUMEN")
    lines.append("-" * 50)
    lines.append(f"Total tablas: {report.total_tables}")
    lines.append(f"✅ Listas para migrar: {report.tables_ready}")
    lines.append(f"⚠️ Con advertencias: {report.tables_with_warnings}")
    lines.append(f"❌ Bloqueadas: {report.tables_blocked}")
    lines.append("")

    if report.migration_possible:
        lines.append("✅ MIGRACIÓN POSIBLE")
    else:
        lines.append("❌ MIGRACIÓN BLOQUEADA - Resolver errores primero")

    lines.append("")
    lines.append("-" * 50)
    lines.append("DETALLE POR TABLA")
    lines.append("-" * 50)

    for table_status in report.tables:
        status = table_status.status_emoji
        lines.append(f"\n{status} {table_status.table}")

        if table_status.exists_in_target:
            lines.append(
                f"   Existe: ✅ | Datos: {'✅' if table_status.has_data_in_target else '⚠️ vacía'}"
            )
            lines.append(f"   Columnas origen: {len(table_status.source_columns)}")
            lines.append(f"   Columnas destino: {len(table_status.target_columns)}")

            if table_status.source_row_count is not None:
                lines.append(f"   Filas origen: {table_status.source_row_count:,}")
                lines.append(f"   Filas destino: {table_status.target_row_count:,}")
                if table_status.row_count_diff_percent is not None:
                    lines.append(f"   Diferencia: {table_status.row_count_diff_percent:+.1f}%")

        for alert in table_status.alerts:
            lines.append(f"   {alert.emoji} {alert.message}")
            if alert.details:
                lines.append(f"      {alert.details}")

    # Alertas globales
    if report.alerts:
        lines.append("")
        lines.append("-" * 50)
        lines.append("ALERTAS GLOBALES")
        lines.append("-" * 50)
        for alert in report.alerts:
            lines.append(f"{alert.emoji} [{alert.level.value}] {alert.message}")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ============ CLI ============
if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Migrador de esquemas TWB")
    parser.add_argument("twb_file", help="Archivo .twb a migrar")
    parser.add_argument("--from", dest="source_schema", required=True, help="Esquema origen")
    parser.add_argument("--to", dest="target_schema", required=True, help="Esquema destino")
    parser.add_argument("--validate-only", action="store_true", help="Solo validar, no migrar")
    parser.add_argument("--force", action="store_true", help="Migrar aunque haya advertencias")

    # Conexión BD
    parser.add_argument("--server", default="10.190.65.25")
    parser.add_argument("--port", type=int, default=5439)
    parser.add_argument("--database", default="rds_dwh_qa")
    parser.add_argument("--user", default="mchicao")
    parser.add_argument("--password", default="bW9e2XW83x")

    args = parser.parse_args()

    if not os.path.exists(args.twb_file):
        print(f"Error: No se encuentra el archivo {args.twb_file}")
        sys.exit(1)

    db_config = {
        "server": args.server,
        "port": args.port,
        "database": args.database,
        "username": args.user,
        "password": args.password,
        "driver": "redshift",
    }

    print(f"[INFO] Validando migración: {args.source_schema} → {args.target_schema}")
    print(f"[INFO] Archivo: {args.twb_file}")
    print("")

    # Validar
    report = validate_migration(args.twb_file, args.source_schema, args.target_schema, db_config)

    # Mostrar reporte
    print(generate_migration_report_text(report))

    # Guardar reporte
    report_path = args.twb_file.replace(
        ".twb", f"_migration_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(generate_migration_report_text(report))
    print(f"\n[INFO] Reporte guardado en: {report_path}")

    # ¿Migrar?
    if args.validate_only:
        print("\n[INFO] Modo validación - no se realizó migración")
    elif report.migration_possible or args.force:
        if not report.migration_possible:
            print("\n[WARN] Forzando migración con errores...")

        output = migrate_twb_schema(args.twb_file, args.source_schema, args.target_schema)
        print(f"\n✅ Archivo migrado creado: {output}")
    else:
        print("\n❌ Migración cancelada - resolver errores primero")
        print("   Use --force para migrar de todas formas")
