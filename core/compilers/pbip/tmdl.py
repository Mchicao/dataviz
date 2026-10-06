"""Neutral :class:`SemanticModel` -> TMDL (Tabular Model Definition Language).

Emite texto TMDL para el modelo semántico de Power BI a partir del IR neutral,
sin reparséar Tableau y sin importar motores destino (TOM / PBIR / DAX runtime).
El TMDL conserva: columnas con tipo de dato, ``summarizeBy``, ``formatString``
de medidas, relaciones (cardinalidad + filtro cruzado) y particiones.

La partición física (origen de datos) no vive en el IR neutral, que es
agnóstico del destino: se emite una partición marcador explícita y se registra
la aproximación para que el operador configure el origen real.

Diseño (Ponytail): stdlib + ``core.contracts`` + el traductor DAX del propio
paquete. Determinista: mismos datos -> mismo TMDL.

Boundary: stdlib + ``core.contracts`` + ``core.compilers.pbip`` only.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import math
import re
import uuid
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from core.compilers.pbip.dax import expression_to_dax, quote_table
from core.contracts.semantic_ir import Expression

if TYPE_CHECKING:
    from core.contracts.dataset import Dataset
    from core.contracts.semantic_ir import (
        Entity,
        Field,
        Metric,
        Parameter,
        SemanticModel,
    )

#: Tipo de dato neutral -> ``dataType`` TMDL.
_TYPE_TO_TMDL: dict[str, str] = {
    "string": "string",
    "integer": "int64",
    "decimal": "double",
    "boolean": "boolean",
    "date": "dateTime",
    "datetime": "dateTime",
    "time": "dateTime",
    "binary": "binary",
    "variant": "variant",
    "unknown": "variant",
}


def _tmdl_type(neutral_type: str) -> tuple[str, str | None]:
    """Devuelve ``(tmdl_data_type, nota_aproximacion | None)`` para un tipo."""
    mapped = _TYPE_TO_TMDL.get(neutral_type)
    if mapped is None:
        return "variant", f"tipo neutral sin mapeo TMDL directo: {neutral_type!r}"
    if neutral_type in {"time", "date", "datetime"}:
        note = (
            "tipo fecha/hora neutral mapeado a dateTime de Power BI"
            if neutral_type == "time"
            else None
        )
        return mapped, note
    return mapped, None


def _summarize_by(neutral_type: str) -> str:
    """Evita inventar métricas implícitas al reimportar el modelo neutral."""
    return "none"


def _stable_uuid(*parts: str) -> str:
    """Deriva un ``lineageTag`` UUID determinista desde ``parts``.

    El legacy usaba ``uuid4()`` aleatorio, lo que rompía la reproducibilidad.
    Aquí derivamos un UUID v5-like desde SHA-256 para que la recompilación del
    mismo IR produzca TMDL idéntico (diffs estables).
    """
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return str(uuid.UUID(digest[:32]))


def _table_header(entity: Entity) -> list[str]:
    """Encabezado ``table <name>`` + ``lineageTag`` de la tabla."""
    name = quote_table(entity.name)
    return [
        f"table {name}",
        f"\tlineageTag: {_stable_uuid('table', entity.name)}",
        "",
    ]


def _expression_is_string(expr: Expression) -> bool:
    data_type = getattr(expr.data_type, "value", expr.data_type)
    if str(data_type) == "string":
        return True
    if expr.kind == "literal" and isinstance(expr.value, str):
        return True
    if expr.kind == "binary" and expr.op == "concat":
        return True
    if expr.kind == "binary" and expr.op == "add":
        return any(_expression_is_string(child) for child in expr.children)
    return False


def _coerce_string_add_to_concat(expr: Expression, *, force_root: bool = False) -> Expression:
    children = tuple(_coerce_string_add_to_concat(child) for child in expr.children)
    resolved = replace(expr, children=children) if children != expr.children else expr
    if resolved.kind == "binary" and resolved.op == "add" and (force_root or _expression_is_string(resolved)):
        return replace(resolved, op="concat")
    return resolved


def _column_block(field: Field, entity_name: str) -> tuple[list[str], list[str]]:
    """Bloque TMDL de una columna; devuelve ``(líneas, notas)``.

    Recibe el campo neutral y conserva su expresión calculada cuando existe.
    """
    notes: list[str] = []
    neutral_type = str(field.data_type.value)
    if neutral_type in {"unknown", "variant"}:
        raise ValueError(
            f"Power BI columns do not support {neutral_type} data type: "
            f"{entity_name}.{field.name}"
        )
    tmdl_type, note = _tmdl_type(neutral_type)
    if note:
        notes.append(note)
    summarize = _summarize_by(str(field.data_type.value))
    name = quote_table(field.name)
    if field.expression is not None:
        expression = (
            _coerce_string_add_to_concat(field.expression, force_root=True)
            if str(field.data_type.value) == "string"
            else field.expression
        )
        dax, dax_notes = expression_to_dax(expression, default_entity=entity_name)
        notes.extend(dax_notes)
        declaration = f"\tcolumn {name} = {dax}"
    else:
        declaration = f"\tcolumn {name}"
    lines = [
        declaration,
        f"\t\tdataType: {tmdl_type}",
        f"\t\tlineageTag: {_stable_uuid('column', field.name)}",
        f"\t\tsummarizeBy: {summarize}",
    ]
    if field.expression is None:
        lines.append(f"\t\tsourceColumn: {field.name}")
    if field.hidden:
        lines.append("\t\tisHidden: true")
    case_prefix = "case_sensitive_surrogate_for:"
    if field.description.startswith(case_prefix):
        original = field.description[len(case_prefix) :]
        lines.append(
            f"\t\tannotation PBI_Bridge_CaseOriginal = {json.dumps(original, ensure_ascii=False)}"
        )
    lines.append("")
    if field.is_key:
        # Marca la columna como clave para que PBI la trate como identificador.
        lines.append("\t\tisKey: true")
        lines.append("")
    if note:
        lines.append(f"\t\tannotation PBI_Bridge_Note = {note}")
        lines.append("")
    return lines, notes


def _json_value(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return str(isoformat())
    return str(value)


def _partition_block(
    entity: Entity,
    dataset: Dataset,
    *,
    parameter_definition: Parameter | None = None,
) -> list[str]:
    """Partición M autocontenida desde el dataset neutral materializado."""
    name = quote_table(entity.name)
    records = [
        {column: _json_value(row.get(column)) for column in dataset.columns} for row in dataset.rows
    ]
    raw = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = base64.b64encode(gzip.compress(raw, mtime=0)).decode("ascii")
    columns = ", ".join(f'"{column.replace(chr(34), chr(34) * 2)}"' for column in dataset.columns)
    physical_types = {
        field.name: {
            "string": "type text",
            "integer": "Int64.Type",
            "decimal": "type number",
            "boolean": "type logical",
            "date": "type datetime",
            "datetime": "type datetime",
            "time": "type datetime",
            "binary": "type binary",
        }.get(str(field.data_type.value), "type any")
        for field in entity.fields
        if field.expression is None
    }
    transformations = ", ".join(
        '{{"{}", {}}}'.format(column.replace('"', '""'), physical_types.get(column, "type any"))
        for column in dataset.columns
    )
    direct_empty_parameter = (
        parameter_definition is not None
        and dataset.columns == ("Value",)
        and len(dataset.rows) == 1
        and dataset.rows[0].get("Value") == ""
    )
    source_lines = (
        [
            "\t\t\tlet",
            '\t\t\t\tSource = #table({"Value"}, {{""}}),',
            f'\t\t\t\tTyped = Table.TransformColumnTypes(Source, {{{transformations}}}, "en-US")',
            "\t\t\tin",
            "\t\t\t\tTyped",
            "",
        ]
        if direct_empty_parameter
        else [
            "\t\t\tlet",
            f'\t\t\t\tPayload = Binary.FromText("{payload}", BinaryEncoding.Base64),',
            "\t\t\t\tRows = Json.Document(Binary.Decompress(Payload, Compression.GZip)),",
            f"\t\t\t\tSource = Table.FromRecords(Rows, {{{columns}}}, MissingField.UseNull),",
            f'\t\t\t\tTyped = Table.TransformColumnTypes(Source, {{{transformations}}}, "en-US")',
            "\t\t\tin",
            "\t\t\t\tTyped",
            "",
        ]
    )
    return [
        f"\tpartition {name} = m",
        "\t\tmode: import",
        "\t\tsource =",
        *source_lines,
    ]


def _measure_block(measure: Metric) -> tuple[list[str], list[str]]:
    """Bloque TMDL de una medida con su DAX y ``formatString``."""
    dax_body, dax_notes = expression_to_dax(measure.expression, default_entity=measure.entity)
    notes = list(dax_notes)

    # Multilínea o larga -> bloque con triple backtick (estilo legacy).
    use_block = "\n" in dax_body or len(dax_body) > 80
    lines: list[str] = []
    if use_block:
        lines.append(f"\tmeasure '{measure.name}' = ```")
        for line in dax_body.splitlines():
            lines.append(f"\t\t{line}")
        lines.append("\t\t```")
    else:
        lines.append(f"\tmeasure '{measure.name}' = {dax_body}")

    lines.append(f"\t\tlineageTag: {_stable_uuid('measure', measure.name)}")
    if measure.format_string:
        lines.append(f"\t\tformatString: {measure.format_string}")
    if measure.hidden:
        lines.append("\t\tisHidden: true")
    if notes:
        joined = "; ".join(notes).replace('"', "'")
        lines.append(f'\t\tannotation PBI_Bridge_DaxNote = "{joined}"')
    lines.append("")
    return lines, notes


def _parameter_measure_block(parameter: Parameter) -> list[str]:
    table = quote_table(f"_Parameter_{parameter.name}")
    default = parameter.default_value
    if isinstance(default, list):
        default = None
    default_dax, _ = expression_to_dax(Expression(kind="literal", value=default))
    return [
        f"\tmeasure '{parameter.name}' = SELECTEDVALUE({table}[Value], {default_dax})",
        f"\t\tlineageTag: {_stable_uuid('parameter-measure', parameter.name)}",
        "\t\tannotation BI_Bridge_ParameterMeasure = true",
        "",
    ]


def _parameter_metadata_block(parameter: Parameter) -> list[str]:
    """Codifica el parámetro neutral en una anotación TMDL válida y portable."""
    payload = {
        "name": parameter.name,
        "data_type": parameter.data_type.value,
        "is_set": parameter.is_set,
        "default_value": _json_value(parameter.default_value),
        "allowed_values": [_json_value(value) for value in parameter.allowed_values],
        "description": parameter.description,
    }
    encoded = base64.b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return [f'\tannotation BI_Bridge_NeutralParameter = "{encoded}"', ""]


def render_table_tmdl(
    entity: Entity,
    dataset: Dataset,
    measures: list[Metric] | None = None,
    parameters: list[Parameter] | None = None,
    parameter_definition: Parameter | None = None,
) -> tuple[str, list[str]]:
    """Genera el contenido ``.tmdl`` de una tabla con sus medidas asociadas.

    Devuelve ``(tmdl, notas)`` donde ``notas`` agrega las aproximaciones de
    tipos, DAX y partición para que el adaptador las conserve globalmente.
    """
    measures = measures or []
    parameters = parameters or []
    notes: list[str] = []
    lines = _table_header(entity)

    if parameter_definition is not None:
        lines.extend(_parameter_metadata_block(parameter_definition))

    if not entity.fields and not measures:
        notes.append(f"tabla {entity.name!r} sin columnas ni medidas")

    materialized_columns = set(dataset.columns)
    for field in entity.fields:
        if field.expression is None and field.name not in materialized_columns:
            notes.append(
                f"physical field {entity.name}.{field.name} omitted: not present in materialized dataset"
            )
            continue
        block, block_notes = _column_block(field, entity.name)
        lines.extend(block)
        notes.extend(block_notes)

    lines.extend(
        _partition_block(entity, dataset, parameter_definition=parameter_definition)
    )

    for measure in measures:
        block, block_notes = _measure_block(measure)
        lines.extend(block)
        notes.extend(block_notes)

    for parameter in parameters:
        lines.extend(_parameter_measure_block(parameter))

    return "\n".join(lines).rstrip() + "\n", notes


def _cardinality_to_tmdl(neutral: str) -> str:
    """one_to_many -> oneToMany, many_to_one -> manyToOne (camelCase TMDL)."""
    parts = [p for p in neutral.split("_") if p]
    if not parts:
        return neutral
    head = parts[0].lower()
    tail = "".join(p.capitalize() for p in parts[1:])
    return head + tail


def _cross_filter_to_tmdl(neutral: str) -> str:
    """single/both -> ``single``/``both`` (TMDL usa los mismos tokens)."""
    return neutral


def _tmdl_reference_part(name: str) -> str:
    """Quote one TMDL object-reference segment only when required."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _relationship_reference(entity: str, field: str) -> str:
    return f"{_tmdl_reference_part(entity)}.{_tmdl_reference_part(field)}"


def render_relationships_tmdl(model: SemanticModel) -> tuple[str, list[str]]:
    """Emit current Desktop relationship objects in ``relationships.tmdl``.

    Power BI's folder TMDL stores relationships as top-level named objects,
    not as nested ``model`` children. Cardinality defaults are many-to-one,
    so only non-default ends are written. Composite keys are not representable
    by a SingleColumnRelationship and remain an explicit approximation.
    """
    notes: list[str] = []
    lines: list[str] = []
    for rel in model.relationships:
        if not rel.from_fields or not rel.to_fields:
            notes.append(f"relación {rel.name!r} sin columnas: no emitida")
            continue
        if len(rel.from_fields) > 1 or len(rel.to_fields) > 1:
            notes.append(
                f"relación {rel.name!r} con clave compuesta: "
                "Power BI TMDL SingleColumnRelationship sólo admite un par; "
                "se emitió el primer par"
            )
        lines.append(f"relationship {quote_table(rel.name)}")
        cardinality = str(rel.cardinality)
        if cardinality == "one_to_many":
            lines.append("	fromCardinality: one")
            lines.append("	toCardinality: many")
        elif cardinality == "one_to_one":
            lines.append("	fromCardinality: one")
        elif cardinality == "many_to_many":
            lines.append("	toCardinality: many")
        elif cardinality != "many_to_one":
            notes.append(
                f"relación {rel.name!r} cardinalidad {cardinality!r} no reconocida; "
                "se usó el default many-to-one de TMDL"
            )
        cross_filter = str(rel.cross_filter)
        if cross_filter == "both":
            lines.append("	crossFilteringBehavior: bothDirections")
        elif cross_filter not in {"single", "oneDirection"}:
            notes.append(
                f"relación {rel.name!r} cross_filter {cross_filter!r} no reconocido; "
                "se usó oneDirection por default"
            )
        if not rel.active:
            lines.append("	isActive: false")
        lines.append(
            "	fromColumn: "
            + _relationship_reference(rel.from_entity, rel.from_fields[0])
        )
        lines.append(
            "	toColumn: " + _relationship_reference(rel.to_entity, rel.to_fields[0])
        )
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else ""), notes


def render_model_tmdl(
    model: SemanticModel,
    extra_table_names: tuple[str, ...] = (),
    *,
    relationship_strategy: str = "",
) -> tuple[str, list[str]]:
    """Generate ``model.tmdl`` with model properties and table references.

    Relationships are emitted separately by :func:`render_relationships_tmdl`
    because current Power BI Desktop folder TMDL treats them as top-level
    objects in ``relationships.tmdl``.
    """
    if relationship_strategy not in {"", "materialized_federation"}:
        raise ValueError(f"unsupported relationship strategy: {relationship_strategy!r}")
    if relationship_strategy and model.relationships:
        raise ValueError("relationship strategy cannot coexist with canonical model relationships")

    notes: list[str] = []
    lines: list[str] = [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "",
    ]

    for entity in model.entities:
        lines.append(f"ref table {quote_table(entity.name)}")
    for table_name in extra_table_names:
        lines.append(f"ref table {quote_table(table_name)}")
    if model.entities or extra_table_names:
        lines.append("")

    if not model.relationships:
        lines.append("\tannotation PBI_Bridge_NoRelationships = true")
        if relationship_strategy:
            lines.append(
                f'\tannotation PBI_Bridge_RelationshipStrategy = "{relationship_strategy}"'
            )
            notes.append(
                "source joins were materialized into destination datasets; no model relationship emitted"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n", notes


__all__ = [
    "render_model_tmdl",
    "render_relationships_tmdl",
    "render_table_tmdl",
]
