"""Exportación de tablas Hyper a fuentes locales consumibles por Power BI."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HyperExport:
    """Resultado de exportar una tabla con su esquema equivalente en M."""

    path: Path
    column_types: dict[str, str]


def export_excel_table(
    excel_path: str | Path,
    output_csv: str | Path,
    preferred_sheet: str | None = None,
) -> HyperExport:
    """Exporta una hoja Excel empaquetada a CSV UTF-8 con tipos M estables."""
    import pandas as pd
    from pandas.api import types as pd_types

    excel_path = Path(excel_path).resolve()
    output_csv = Path(output_csv).resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)

    workbook = pd.ExcelFile(excel_path)
    preferred = _normalized_table_name(preferred_sheet or "")
    sheet_lookup = {
        _normalized_table_name(sheet): sheet for sheet in workbook.sheet_names
    }
    if preferred and preferred in sheet_lookup:
        selected_sheet = sheet_lookup[preferred]
    elif len(workbook.sheet_names) == 1:
        selected_sheet = workbook.sheet_names[0]
    else:
        available = ", ".join(workbook.sheet_names)
        raise ValueError(
            f"No se pudo asociar la hoja '{preferred_sheet}' con Excel. "
            f"Disponibles: {available}"
        )

    frame = pd.read_excel(workbook, sheet_name=selected_sheet)
    frame.to_csv(output_csv, index=False, encoding="utf-8", lineterminator="\n")

    column_types: dict[str, str] = {}
    for name, dtype in frame.dtypes.items():
        if pd_types.is_datetime64_any_dtype(dtype):
            column_types[str(name)] = "type datetime"
        elif pd_types.is_bool_dtype(dtype):
            column_types[str(name)] = "type logical"
        elif pd_types.is_integer_dtype(dtype):
            column_types[str(name)] = "Int64.Type"
        elif pd_types.is_numeric_dtype(dtype):
            column_types[str(name)] = "type number"
        else:
            column_types[str(name)] = "type text"

    return HyperExport(path=output_csv, column_types=column_types)


def _normalized_table_name(value: str) -> str:
    """Normaliza nombres físicos para comparar tablas Tableau/Hyper."""
    return value.strip("[]$\"'").casefold()


def export_hyper_table(
    hyper_path: str | Path,
    output_csv: str | Path,
    preferred_table: str | None = None,
) -> HyperExport:
    """Exporta Hyper aislando extractos grandes del runtime TOM/pythonnet."""
    resolved_hyper = Path(hyper_path).resolve()
    resolved_output = Path(output_csv).resolve()
    if resolved_hyper.stat().st_size < 128 * 1024 * 1024:
        return _export_hyper_table_in_process(
            resolved_hyper,
            resolved_output,
            preferred_table,
        )

    # HYPER-ISOLATE-01: Hyper y Analysis Services cargan runtimes nativos en el
    # mismo proceso. En extractos grandes esa combinación termina el proceso;
    # un worker limpio mantiene la migración y el streaming independientes.
    manifest_path = resolved_output.with_suffix(".hyper-export.json")
    project_root = Path(__file__).resolve().parents[1]
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    generation_root = resolved_output.parent / ".hyper-export-generations"
    generation_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="generation-", dir=generation_root))
    generation = generation_root / uuid.uuid4().hex
    pointer_tmp: Path | None = None
    public_tmp: Path | None = None
    try:
        staged_output = staging / resolved_output.name
        staged_manifest = staging / manifest_path.name
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "core.hyper_extract_worker",
                str(resolved_hyper),
                str(staged_output),
                preferred_table or "",
                str(staged_manifest),
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"Hyper export worker failed with exit code {completed.returncode}."
            )
        if not staged_output.is_file() or not staged_manifest.is_file():
            raise RuntimeError("Hyper export worker produced no CSV or manifest.")
        manifest = json.loads(staged_manifest.read_text(encoding="utf-8"))
        manifest["generation_id"] = generation.name
        manifest["csv_sha256"] = hashlib.sha256(staged_output.read_bytes()).hexdigest()
        staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(staging, generation)
        public_tmp = resolved_output.with_name(f".{resolved_output.name}.{uuid.uuid4().hex}.tmp")
        shutil.copyfile(generation / resolved_output.name, public_tmp)
        os.replace(public_tmp, resolved_output)
        pointer = {
            "generation_id": generation.name,
            "csv_path": str((generation / resolved_output.name).relative_to(resolved_output.parent)),
            "csv_sha256": manifest["csv_sha256"],
        }
        pointer_tmp = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
        pointer_tmp.write_text(json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(pointer_tmp, manifest_path)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if pointer_tmp is not None:
            pointer_tmp.unlink(missing_ok=True)
        if public_tmp is not None:
            public_tmp.unlink(missing_ok=True)
        raise
    return HyperExport(
        path=resolved_output,
        column_types={str(key): str(value) for key, value in manifest["column_types"].items()},
    )


def _export_hyper_table_in_process(
    hyper_path: str | Path,
    output_csv: str | Path,
    preferred_table: str | None = None,
) -> HyperExport:
    """Exporta en streaming la tabla Hyper más adecuada a CSV UTF-8.

    Tableau suele añadir un sufijo hexadecimal a las tablas del extracto. Se
    prioriza la coincidencia exacta o por prefijo con la relación lógica del
    TWB; si el extracto sólo contiene una tabla, se usa esa tabla.
    """
    try:
        from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry
    except ImportError as exc:  # pragma: no cover - depende del entorno instalado
        raise RuntimeError(
            "Se requiere 'tableauhyperapi' para migrar extractos .hyper"
        ) from exc

    hyper_path = Path(hyper_path).resolve()
    output_csv = Path(output_csv).resolve()
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    preferred = _normalized_table_name(preferred_table or "")

    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as process:
        with Connection(
            process.endpoint, str(hyper_path), CreateMode.NONE
        ) as connection:
            tables = [
                table
                for schema in connection.catalog.get_schema_names()
                for table in connection.catalog.get_table_names(schema)
            ]
            if not tables:
                raise ValueError(f"El extracto Hyper no contiene tablas: {hyper_path}")

            def rank(table) -> tuple[int, str]:
                physical = _normalized_table_name(table.name.unescaped)
                if preferred and physical == preferred:
                    return (0, physical)
                if preferred and physical.startswith(f"{preferred}_"):
                    return (1, physical)
                return (2, physical)

            selected = min(tables, key=rank)
            if len(tables) > 1 and rank(selected)[0] == 2:
                available = ", ".join(table.name.unescaped for table in tables)
                raise ValueError(
                    f"No se pudo asociar la tabla '{preferred_table}' con Hyper. "
                    f"Disponibles: {available}"
                )

            definition = connection.catalog.get_table_definition(selected)
            headers = [column.name.unescaped for column in definition.columns]
            column_types = {
                column.name.unescaped: _hyper_type_to_m(str(column.type))
                for column in definition.columns
            }
            with output_csv.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(headers)
                for row in connection.execute_query(f"SELECT * FROM {selected}"):
                    writer.writerow("" if value is None else value for value in row)

    return HyperExport(path=output_csv, column_types=column_types)


def _hyper_type_to_m(hyper_type: str) -> str:
    """Traduce tipos Hyper a tipos escalares estables de Power Query."""
    normalized = hyper_type.casefold()
    if "timestamp" in normalized:
        return "type datetime"
    if normalized.startswith("date"):
        return "type date"
    if "bool" in normalized:
        return "type logical"
    if any(token in normalized for token in ("small_int", "big_int", "integer")):
        return "Int64.Type"
    if any(token in normalized for token in ("double", "numeric", "decimal")):
        return "type number"
    return "type text"


def generate_csv_m_expression(
    csv_path: str | Path,
    column_types: dict[str, str] | None = None,
    derived_year_columns: dict[str, str] | None = None,
    derived_month_columns: dict[str, str] | None = None,
) -> str:
    """Genera una partición M para un CSV local exportado del TWBX."""
    escaped = str(Path(csv_path).resolve()).replace('"', '""')
    type_pairs = ", ".join(
        f'{{"{name.replace(chr(34), chr(34) * 2)}", {m_type}}}'
        for name, m_type in (column_types or {}).items()
    )
    typed_step = (
        f',\n    TypedColumns = Table.TransformColumnTypes(PromotedHeaders, {{{type_pairs}}}, "en-US")'
        if type_pairs
        else ""
    )
    result_step = "TypedColumns" if type_pairs else "PromotedHeaders"
    derived_steps = ""
    for index, (new_column, source_column) in enumerate(
        (derived_year_columns or {}).items(), start=1
    ):
        step_name = f"DerivedYear{index}"
        escaped_new = new_column.replace('"', '""')
        escaped_source = source_column.replace('"', '""')
        derived_steps += (
            f',\n    {step_name} = Table.AddColumn({result_step}, "{escaped_new}", '
            f'each Date.Year([#"{escaped_source}"]), Int64.Type)'
        )
        result_step = step_name
    for index, (new_column, source_column) in enumerate(
        (derived_month_columns or {}).items(), start=1
    ):
        step_name = f"DerivedMonth{index}"
        escaped_new = new_column.replace('"', '""')
        escaped_source = source_column.replace('"', '""')
        derived_steps += (
            f',\n    {step_name} = Table.AddColumn({result_step}, "{escaped_new}", '
            f'each Date.StartOfMonth(Date.From([#"{escaped_source}"])), type date)'
        )
        result_step = step_name
    return f'''let
    Source = Csv.Document(File.Contents("{escaped}"), [Delimiter=",", Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
    PromotedHeaders = Table.PromoteHeaders(Source, [PromoteAllScalars=true]){typed_step}{derived_steps}
in
    {result_step}'''
