"""
VPAX Reader - Lee archivos .vpax exportados de DAX Studio.

El formato VPAX es un archivo ZIP que contiene:
- DaxModel.json: Metadata del modelo (tablas, columnas, cardinalidad)
- DaxVpaView.json: Vista para VertiPaq Analyzer
- Model.bim (opcional): Modelo en formato TOM
"""

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class VpaxColumn:
    """Columna extraída de un archivo VPAX."""

    name: str
    data_type: str
    cardinality: int
    total_size: int
    data_size: int
    dictionary_size: int
    is_hidden: bool = False


@dataclass
class VpaxTable:
    """Tabla extraída de un archivo VPAX."""

    name: str
    row_count: int
    columns: list[VpaxColumn]

    @property
    def total_size(self) -> int:
        return sum(c.total_size for c in self.columns)

    @property
    def total_mb(self) -> float:
        return self.total_size / (1024 * 1024)


@dataclass
class VpaxModel:
    """Modelo completo extraído de un archivo VPAX."""

    model_name: str
    tables: list[VpaxTable]
    compatibility_level: int = 0
    culture: str = ""

    @property
    def total_size(self) -> int:
        return sum(t.total_size for t in self.tables)

    @property
    def total_mb(self) -> float:
        return self.total_size / (1024 * 1024)


def read_vpax(vpax_path: str) -> VpaxModel:
    """
    Lee un archivo VPAX y extrae la información del modelo.

    Args:
        vpax_path: Ruta al archivo .vpax

    Returns:
        VpaxModel con toda la información extraída

    Raises:
        FileNotFoundError: Si el archivo no existe
        ValueError: Si el archivo no es un VPAX válido
    """
    path = Path(vpax_path)
    if not path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {vpax_path}")

    if not path.suffix.lower() == ".vpax":
        raise ValueError(f"El archivo debe tener extensión .vpax: {vpax_path}")

    try:
        with zipfile.ZipFile(vpax_path, "r") as zf:
            # Leer DaxModel.json
            if "DaxModel.json" not in zf.namelist():
                raise ValueError("El archivo VPAX no contiene DaxModel.json")

            with zf.open("DaxModel.json") as f:
                dax_model = json.load(f)

            return _parse_dax_model(dax_model)

    except zipfile.BadZipFile:
        raise ValueError(f"El archivo no es un ZIP válido: {vpax_path}")


def _parse_dax_model(data: dict[str, Any]) -> VpaxModel:
    """Parsea el JSON de DaxModel a objetos Python."""

    def get_name(obj):
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return obj.get("Name", "Unknown")
        return "Unknown"

    model_name = get_name(data.get("ModelName", {}))
    compatibility = data.get("CompatibilityLevel", 0)
    culture = data.get("Culture", "")

    tables = []
    for table_data in data.get("Tables", []):
        columns = []

        for col_data in table_data.get("Columns", []):
            column = VpaxColumn(
                name=get_name(col_data.get("ColumnName", {})),
                data_type=col_data.get("DataType", "String"),
                cardinality=col_data.get("ColumnCardinality", 0),
                total_size=col_data.get("TotalSize", 0),
                data_size=col_data.get("DataSize", 0),
                dictionary_size=col_data.get("DictionarySize", 0),
                is_hidden=col_data.get("IsHidden", False),
            )
            columns.append(column)

        table = VpaxTable(
            name=get_name(table_data.get("TableName", {})),
            row_count=table_data.get("RowsCount", 0),
            columns=columns,
        )
        tables.append(table)

    return VpaxModel(
        model_name=model_name,
        tables=tables,
        compatibility_level=compatibility,
        culture=culture,
    )


def format_vpax_report(model: VpaxModel) -> str:
    """Genera un reporte Markdown del modelo VPAX."""
    lines = [
        f"# Análisis VPAX: {model.model_name}",
        "",
        f"**Memoria total:** {model.total_mb:.1f} MB",
        f"**Tablas:** {len(model.tables)}",
        "",
        "## Tablas (ordenadas por tamaño)",
        "",
    ]

    for table in sorted(model.tables, key=lambda t: t.total_size, reverse=True):
        lines.append(f"### {table.name}")
        lines.append(f"- Filas: {table.row_count:,}")
        lines.append(f"- Tamaño: {table.total_mb:.2f} MB")
        lines.append(f"- Columnas: {len(table.columns)}")
        lines.append("")

        # Top columnas más pesadas
        top_cols = sorted(table.columns, key=lambda c: c.total_size, reverse=True)[:5]
        if top_cols:
            lines.append("**Columnas más pesadas:**")
            for col in top_cols:
                size_mb = col.total_size / (1024 * 1024)
                lines.append(
                    f"- `{col.name}`: {size_mb:.2f} MB (cardinalidad: {col.cardinality:,})"
                )
            lines.append("")

    return "\n".join(lines)


def get_high_cardinality_columns(model: VpaxModel, threshold: float = 0.5) -> list[tuple]:
    """
    Encuentra columnas con alta cardinalidad relativa.

    Args:
        model: Modelo VPAX
        threshold: Ratio cardinalidad/filas (0.5 = 50% únicos)

    Returns:
        Lista de (tabla, columna, ratio)
    """
    results = []

    for table in model.tables:
        if table.row_count == 0:
            continue

        for col in table.columns:
            ratio = col.cardinality / table.row_count
            if ratio >= threshold:
                results.append((table.name, col.name, ratio, col.total_size))

    return sorted(results, key=lambda x: x[3], reverse=True)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Uso: python vpax_reader.py <archivo.vpax>")
        sys.exit(1)

    try:
        model = read_vpax(sys.argv[1])
        print(format_vpax_report(model))

        print("\n## Columnas con Alta Cardinalidad (>50%)")
        for table, col, ratio, size in get_high_cardinality_columns(model):
            print(f"- {table}.{col}: {ratio * 100:.1f}% únicos")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
