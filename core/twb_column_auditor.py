"""
Auditoría de columnas en archivos Tableau TWB/TWBX.
Detecta columnas definidas que no se usan en ninguna visualización.

Uso:
    python -m core.twb_column_auditor "archivo.twb"
    python -m core.twb_column_auditor "archivo.twbx"
"""

import os
import re

# Importar extractor existente
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.twbx_extractor import cleanup_temp_dir, extract_twb_from_twbx

# Tamaño estimado por tipo de dato (bytes típicos en Redshift/PostgreSQL)
DATA_TYPE_SIZES = {
    "string": 50,  # varchar promedio
    "integer": 4,
    "real": 8,  # double precision
    "date": 4,
    "datetime": 8,
    "boolean": 1,
    # Tipos internos de Tableau
    "i4": 4,
    "i2": 2,
    "r8": 8,
    "bool": 1,
    "tuple": 0,  # Spatial objects etc
    "spatial": 0,
}


@dataclass
class ColumnInfo:
    """Información de una columna en el datasource."""

    name: str
    local_name: str
    data_type: str
    width: int | None = None
    aggregation: str | None = None
    is_used: bool = False
    is_hidden: bool = False  # Ya oculta en Tableau
    is_calculated: bool = False  # Es campo calculado (no existe en BD)
    used_in_worksheets: list[str] = field(default_factory=list)

    @property
    def estimated_bytes(self) -> int:
        """Tamaño estimado en bytes por fila basado en tipo de dato."""
        if self.is_calculated:
            return 0  # Los campos calculados no ocupan espacio descargar en BD

        if self.width:
            return self.width
        return DATA_TYPE_SIZES.get(self.data_type, 8)

    @property
    def optimization_priority(self) -> int:
        """
        Prioridad de optimización (mayor = más impacto al ocultar).
        Strings largos y columnas no usadas tienen mayor prioridad.
        """
        if self.is_calculated:
            return 0

        priority = 0
        if not self.is_used:
            priority += 100
        if self.data_type == "string":
            priority += 50
            if self.width and self.width > 50:
                priority += self.width  # Strings muy largos pesan más
        elif self.data_type == "date" or self.data_type == "datetime":
            priority += 20
        return priority


@dataclass
class DatasourceAudit:
    """Resultado de auditoría de un datasource."""

    name: str
    caption: str
    table: str | None
    schema: str | None
    server: str | None
    database: str | None
    total_columns: int
    used_columns: int
    unused_columns: int
    hidden_columns: int = 0  # Ya ocultas en Tableau
    connection_class: str | None = None  # Tipo de BD: 'redshift', 'sqlserver', etc.
    custom_sql: str | None = None  # SQL personalizado si usa Custom SQL
    columns: list[ColumnInfo] = field(default_factory=list)

    @property
    def usage_percent(self) -> float:
        if self.total_columns == 0:
            return 100.0
        return (self.used_columns / self.total_columns) * 100

    @property
    def estimated_savings_bytes_per_row(self) -> int:
        """Bytes estimados que se ahorrarían por fila si se ocultan columnas no usadas."""
        return sum(c.estimated_bytes for c in self.columns if not c.is_used and not c.is_hidden)

    @property
    def optimization_suggestions(self) -> list[str]:
        """Lista de sugerencias de optimización."""
        suggestions = []
        # Solo considerar columnas no usadas Y no ya ocultas
        unused = [c for c in self.columns if not c.is_used and not c.is_hidden]

        if self.hidden_columns > 0:
            suggestions.append(f"✅ Ya hay {self.hidden_columns} columnas ocultas en Tableau")

        if unused:
            suggestions.append(f"Ocultar {len(unused)} columnas adicionales no usadas")

            # Detectar columnas de alta cardinalidad potencial
            high_cardinality = [
                c for c in unused if c.data_type == "string" and c.width and c.width > 30
            ]
            if high_cardinality:
                names = [c.name for c in high_cardinality[:5]]
                suggestions.append(f"Priorizar strings largos: {', '.join(names)}")

            # Detectar IDs potenciales
            id_columns = [
                c
                for c in unused
                if any(x in c.name.lower() for x in ["_id", "id_", "guid", "uuid", "key"])
            ]
            if id_columns:
                suggestions.append(f"Columnas tipo ID no usadas: {len(id_columns)} (alto impacto)")
        elif self.hidden_columns > 0:
            suggestions.append("✅ Workbook ya está completamente optimizado")

        # Check: Star Schema Advisor (Tablas Anchas)
        if self.total_columns > 50:
            suggestions.append(
                f"⚠️ Tabla Ancha ({self.total_columns} cols): Considera normalizar a Star Schema (Fact/Dim) en Power BI."
            )

        return suggestions


@dataclass
class WorkbookAudit:
    """Resultado completo de auditoría de un workbook."""

    file_path: str
    workbook_name: str
    datasources: list[DatasourceAudit] = field(default_factory=list)
    worksheets_count: int = 0
    dashboards_count: int = 0
    total_columns: int = 0
    total_used: int = 0
    total_unused: int = 0

    @property
    def optimization_potential(self) -> str:
        """Evaluación del potencial de optimización."""
        if self.total_columns == 0:
            return "N/A"
        unused_pct = (self.total_unused / self.total_columns) * 100
        if unused_pct > 50:
            return "🔴 ALTO - Más del 50% de columnas no usadas"
        elif unused_pct > 25:
            return "🟡 MEDIO - 25-50% de columnas no usadas"
        elif unused_pct > 10:
            return "🟢 BAJO - 10-25% de columnas no usadas"
        else:
            return "✅ ÓPTIMO - Menos del 10% no usadas"


def extract_defined_columns(datasource_elem: ET.Element) -> dict[str, ColumnInfo]:
    """
    Extrae todas las columnas definidas en un datasource.
    Busca en metadata-records dentro de connection.
    Detecta columnas ya ocultas (hidden='true').
    """
    columns = {}
    hidden_columns = set()

    # Primero, identificar columnas ya ocultas en Tableau
    for col in datasource_elem.findall(".//column[@hidden='true']"):
        col_name = col.get("name", "").strip("[]")
        if col_name:
            hidden_columns.add(col_name)

    # Buscar en metadata-records (columnas físicas)
    for metadata in datasource_elem.findall(".//metadata-record[@class='column']"):
        remote_name = metadata.findtext("remote-name", "")
        local_name = metadata.findtext("local-name", "").strip("[]")
        local_type = metadata.findtext("local-type", "string")
        width_text = metadata.findtext("width")
        aggregation = metadata.findtext("aggregation")

        if remote_name:
            columns[local_name] = ColumnInfo(
                name=remote_name,
                local_name=local_name,
                data_type=local_type,
                width=int(width_text) if width_text else None,
                aggregation=aggregation,
                is_hidden=local_name in hidden_columns,
            )

    # También buscar en column (campos calculados y aliases)
    for col in datasource_elem.findall(".//column"):
        col_name = col.get("name", "").strip("[]")

        # Detectar si es calculada
        calc = col.find("calculation")
        is_calc = calc is not None

        if col_name:
            if col_name in columns:
                # Si ya existe y es calculada (ej. override), marcar
                if is_calc:
                    columns[col_name].is_calculated = True
            else:
                datatype = col.get("datatype", "string")
                is_hidden = col.get("hidden", "false") == "true"
                columns[col_name] = ColumnInfo(
                    name=col_name,
                    local_name=col_name,
                    data_type=datatype,
                    is_hidden=is_hidden,
                    is_calculated=is_calc,
                )

    return columns


def extract_used_columns_from_worksheet(worksheet_elem: ET.Element) -> set[str]:
    """
    Extrae columnas referenciadas en un worksheet.
    Busca en datasource-dependencies y otros lugares.
    """
    used = set()

    # Buscar en datasource-dependencies > column
    for dep in worksheet_elem.findall(".//datasource-dependencies"):
        for col in dep.findall(".//column"):
            col_name = col.get("name", "").strip("[]")
            if col_name:
                used.add(col_name)

    # Buscar en encodings (rows, columns, color, etc)
    for encoding in worksheet_elem.findall(".//encoding"):
        col_ref = encoding.get("column", "").strip("[]")
        if col_ref:
            used.add(col_ref)

    # Buscar en filter/filter-ops
    for filter_elem in worksheet_elem.findall(".//filter"):
        col_ref = filter_elem.get("column", "").strip("[]")
        if col_ref:
            used.add(col_ref)

    # Buscar campos en formato [campo]
    xml_str = ET.tostring(worksheet_elem, encoding="unicode")
    pattern = r"\[([^\[\]]+)\]"
    matches = re.findall(pattern, xml_str)
    for match in matches:
        # Ignorar referencias a datasource (contienen punto)
        if "." not in match and not match.startswith("Parameters"):
            used.add(match)

    return used


def audit_twb(twb_path: str) -> WorkbookAudit:
    """
    Audita un archivo TWB y detecta columnas no usadas.

    Args:
        twb_path: Ruta al archivo .twb

    Returns:
        WorkbookAudit con resultados completos
    """
    tree = ET.parse(twb_path)
    root = tree.getroot()

    workbook_name = os.path.basename(twb_path).replace(".twb", "")
    audit = WorkbookAudit(file_path=twb_path, workbook_name=workbook_name)

    # Contar worksheets y dashboards
    audit.worksheets_count = len(root.findall(".//worksheet"))
    audit.dashboards_count = len(root.findall(".//dashboard"))

    # Recopilar todas las columnas usadas en worksheets
    all_used_columns: dict[str, set[str]] = {}  # ds_name -> set of columns
    worksheet_usage: dict[str, list[str]] = {}  # column -> list of worksheets

    for ws in root.findall(".//worksheet"):
        ws_name = ws.get("name", "Unknown")

        # Obtener datasource del worksheet
        for dep in ws.findall(".//datasource-dependencies"):
            ds_name = dep.get("datasource", "")
            if ds_name not in all_used_columns:
                all_used_columns[ds_name] = set()

            used_in_ws = extract_used_columns_from_worksheet(ws)
            all_used_columns[ds_name].update(used_in_ws)

            for col in used_in_ws:
                if col not in worksheet_usage:
                    worksheet_usage[col] = []
                worksheet_usage[col].append(ws_name)

    # Auditar cada datasource
    for ds in root.findall(".//datasources/datasource"):
        ds_name = ds.get("name", "")
        ds_caption = ds.get("caption", ds_name)

        # Ignorar datasource de parámetros
        if "Parameters" in ds_name:
            continue

        # Extraer info de conexión
        conn = ds.find(".//connection")
        relation = ds.find(".//connection/relation")

        schema = conn.get("schema") if conn is not None else None
        server = conn.get("server") if conn is not None else None
        database = conn.get("dbname") if conn is not None else None
        table = relation.get("table") if relation is not None else None
        if table:
            table = table.strip("[]").split(".")[-1]  # Limpiar [schema].[table]

        # Detectar tipo de BD desde connection class
        # Buscar primero en named-connections (estructura más común)
        connection_class = None
        named_conn = ds.find(".//named-connections/named-connection/connection")
        if named_conn is not None:
            connection_class = named_conn.get("class")
        # Fallback: buscar connection directa con class != 'federated'
        if not connection_class and conn is not None:
            direct_class = conn.get("class")
            if direct_class and direct_class != "federated":
                connection_class = direct_class

        # Extraer columnas definidas
        defined_columns = extract_defined_columns(ds)

        # Marcar columnas usadas
        used_in_ds = all_used_columns.get(ds_name, set())
        for col_name, col_info in defined_columns.items():
            if col_name in used_in_ds or col_info.name in used_in_ds:
                col_info.is_used = True
                col_info.used_in_worksheets = worksheet_usage.get(col_name, [])

        # Crear auditoría del datasource
        columns_list = list(defined_columns.values())
        used_count = sum(1 for c in columns_list if c.is_used)
        hidden_count = sum(1 for c in columns_list if c.is_hidden)

        ds_audit = DatasourceAudit(
            name=ds_name,
            caption=ds_caption,
            table=table,
            schema=schema,
            server=server,
            database=database,
            total_columns=len(columns_list),
            used_columns=used_count,
            unused_columns=len(columns_list) - used_count,
            hidden_columns=hidden_count,
            connection_class=connection_class,
            columns=sorted(columns_list, key=lambda c: -c.optimization_priority),
        )

        audit.datasources.append(ds_audit)
        audit.total_columns += ds_audit.total_columns
        audit.total_used += ds_audit.used_columns
        audit.total_unused += ds_audit.unused_columns

    return audit


def audit_twbx(twbx_path: str) -> WorkbookAudit:
    """
    Audita un archivo TWBX extrayendo primero el TWB interno.
    """
    with open(twbx_path, "rb") as f:
        twb_path, temp_dir = extract_twb_from_twbx(f)
        try:
            return audit_twb(twb_path)
        finally:
            cleanup_temp_dir(temp_dir)


def audit_tableau_file(file_path: str) -> WorkbookAudit:
    """
    Audita un archivo Tableau (.twb o .twbx).
    Detecta automáticamente el tipo.
    """
    if file_path.lower().endswith(".twbx"):
        return audit_twbx(file_path)
    elif file_path.lower().endswith(".twb"):
        return audit_twb(file_path)
    else:
        raise ValueError(f"Tipo de archivo no soportado: {file_path}")


def generate_audit_report(audit: WorkbookAudit) -> str:
    """Genera reporte de texto de la auditoría."""
    lines = []
    lines.append("=" * 70)
    lines.append(f"AUDITORÍA DE COLUMNAS: {audit.workbook_name}")
    lines.append("=" * 70)
    lines.append(f"Archivo: {audit.file_path}")
    lines.append(f"Worksheets: {audit.worksheets_count} | Dashboards: {audit.dashboards_count}")
    lines.append(f"Columnas totales: {audit.total_columns}")
    lines.append(f"Columnas usadas: {audit.total_used}")
    lines.append(f"Columnas NO usadas: {audit.total_unused}")
    lines.append(f"Potencial de optimización: {audit.optimization_potential}")
    lines.append("")

    for ds in audit.datasources:
        lines.append("-" * 50)
        lines.append(f"📊 DATASOURCE: {ds.caption}")
        lines.append(f"   Tabla: {ds.schema}.{ds.table}" if ds.schema else f"   Tabla: {ds.table}")
        lines.append(f"   Servidor: {ds.server}")
        lines.append(
            f"   Columnas: {ds.used_columns}/{ds.total_columns} usadas ({ds.usage_percent:.1f}%)"
        )

        if ds.optimization_suggestions:
            lines.append("   💡 Sugerencias:")
            for sug in ds.optimization_suggestions:
                lines.append(f"      - {sug}")

        # Mostrar columnas no usadas (top 10)
        unused = [c for c in ds.columns if not c.is_used]
        if unused:
            lines.append("   ⚠️ Top columnas a ocultar:")
            for col in unused[:10]:
                type_info = f"({col.data_type}"
                if col.width:
                    type_info += f", width={col.width}"
                type_info += ")"
                lines.append(f"      - {col.name} {type_info}")
            if len(unused) > 10:
                lines.append(f"      ... y {len(unused) - 10} más")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ============ CLI ============
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python -m core.twb_column_auditor <archivo.twb|archivo.twbx>")
        sys.exit(1)

    file_path = sys.argv[1]

    if not os.path.exists(file_path):
        print(f"Error: No se encuentra el archivo {file_path}")
        sys.exit(1)

    print(f"[INFO] Auditando: {file_path}")
    audit = audit_tableau_file(file_path)

    report = generate_audit_report(audit)
    print(report)

    # Guardar reporte
    report_path = file_path.replace(".twbx", "").replace(".twb", "") + "_audit.txt"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n[INFO] Reporte guardado en: {report_path}")
