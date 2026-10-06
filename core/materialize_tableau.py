"""Materialización neutral y segura de extractos Tableau empaquetados."""

from __future__ import annotations

import csv
import re
from collections.abc import Collection, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

import pandas as pd
from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry, escape_name

from core.contracts.dataset import Dataset
from core.contracts.source_ast import NodeKind, SourceAST, SourceNode
from core.importers.tableau import TableauPackage


class PackagedDataError(ValueError):
    """El paquete no contiene una fuente local materializable de forma segura."""


def materialize_tableau_datasets(
    source_ast: SourceAST,
    package: TableauPackage,
    *,
    max_rows: int = 1_000_000,
) -> dict[str, Dataset]:
    """Carga cada datasource local con límites explícitos y sin conexiones vivas."""
    if max_rows <= 0:
        raise ValueError("max_rows must be positive")
    datasets: dict[str, Dataset] = {}
    for datasource in source_ast.root.children:
        if datasource.kind is not NodeKind.DATASOURCE or datasource.name == "Parameters":
            continue
        member = _select_member(package.data_root, datasource)
        if member is None:
            continue
        federation = datasource.attributes.get("federation")
        if member.suffix.lower() == ".hyper":
            dataset = _load_hyper(datasource.name, member, federation, max_rows)
        elif member.suffix.lower() in {".xls", ".xlsx"}:
            dataset = _load_excel(datasource.name, member, federation, max_rows)
        elif member.suffix.lower() == ".csv":
            dataset = _load_csv(datasource.name, member, max_rows)
        else:  # pragma: no cover - _select_member has a closed suffix allowlist
            continue
        datasets[datasource.name] = _add_logical_aliases(dataset, datasource)
    return datasets


def tableau_serving_mode(
    source_ast: SourceAST,
    materialized_datasources: Collection[str] | None = None,
) -> str:
    """Classify serving from packaged sources and successful materialization."""
    datasources = {
        datasource.name: bool(datasource.attributes.get("packaged_files"))
        for datasource in source_ast.root.children
        if datasource.kind is NodeKind.DATASOURCE and datasource.name != "Parameters"
    }
    packaged = {name for name, is_packaged in datasources.items() if is_packaged}
    live = set(datasources) - packaged
    if materialized_datasources is None:
        available = packaged
    else:
        available = packaged & set(materialized_datasources)
    unavailable = packaged - available
    if unavailable:
        return "partial" if available else "unavailable"
    if packaged and live:
        return "mixed"
    return "extract" if packaged else "live"


def _add_logical_aliases(dataset: Dataset, datasource: SourceNode) -> Dataset:
    aliases: dict[str, str] = {}
    physical = set(dataset.columns)
    for child in datasource.children:
        if child.kind is not NodeKind.FIELD:
            continue
        desired = child.name.strip("[]")
        if desired in physical:
            continue
        match = re.fullmatch(r"(.+) \([^)]*?(\d+)\)", desired)
        candidate = f"{match.group(1)}{match.group(2)}" if match else ""
        if candidate in physical:
            aliases[desired] = candidate
    if not aliases:
        return dataset
    columns = (*dataset.columns, *aliases)
    rows = tuple(
        {**row, **{logical: row.get(source) for logical, source in aliases.items()}}
        for row in dataset.rows
    )
    return Dataset(name=dataset.name, columns=columns, rows=rows)


def _select_member(root: Path, datasource: SourceNode) -> Path | None:
    candidates = datasource.attributes.get("packaged_files", ())
    if not isinstance(candidates, list):
        return None
    allowed = {".csv", ".hyper", ".xls", ".xlsx"}
    existing: list[Path] = []
    for value in candidates:
        path = PurePosixPath(str(value))
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in allowed:
            raise PackagedDataError("Unsafe packaged datasource path")
        resolved = root.joinpath(*path.parts).resolve()
        if root.resolve() not in resolved.parents:
            raise PackagedDataError("Packaged datasource escapes the extraction root")
        if resolved.is_file():
            existing.append(resolved)
    hyper = next((path for path in existing if path.suffix.lower() == ".hyper"), None)
    return hyper or (existing[0] if existing else None)


def _load_csv(name: str, path: Path, max_rows: int) -> Dataset:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        records = []
        for index, row in enumerate(reader):
            if index >= max_rows:
                raise PackagedDataError(f"Datasource {name!r} exceeds the row limit")
            records.append(dict(row))
    return Dataset.from_records(name, records, columns=reader.fieldnames or ())


def _load_excel(
    name: str,
    path: Path,
    federation: object,
    max_rows: int,
) -> Dataset:
    sheets = pd.read_excel(path, sheet_name=None)
    frame = _federate_frames(sheets, federation)
    if len(frame.index) > max_rows:
        raise PackagedDataError(f"Datasource {name!r} exceeds the row limit")
    frame = frame.where(pd.notna(frame), None)
    return Dataset.from_records(name, frame.to_dict(orient="records"), columns=list(frame.columns))


def _normalized_relation_name(value: object) -> str:
    return str(value or "").rstrip("$").casefold()


def _resolve_federation_column(
    references: Mapping[tuple[str, str], str],
    table: object,
    field: object,
) -> str | None:
    wanted_table = _normalized_relation_name(table)
    wanted_field = str(field or "").casefold()
    matches = [
        column
        for (source_table, source_field), column in references.items()
        if _normalized_relation_name(source_table) == wanted_table
        and source_field.casefold() == wanted_field
    ]
    if len(matches) == 1:
        return matches[0]
    return None


def _column_origin_label(
    references: Mapping[tuple[str, str], str], column: str
) -> str:
    labels = [
        str(table).rstrip("$")
        for (table, _field), mapped in references.items()
        if mapped == column
    ]
    return labels[0] if len(set(labels)) == 1 and labels else "right"


def _unique_join_column_name(base: str, label: str, occupied: set[str]) -> str:
    candidate = f"{base} ({label})"
    suffix = 2
    while candidate in occupied:
        candidate = f"{base} ({label}) {suffix}"
        suffix += 1
    return candidate


def _federate_frame_state(
    frames: Mapping[str, pd.DataFrame], federation: object
) -> tuple[pd.DataFrame, dict[tuple[str, str], str]]:
    if not frames:
        raise PackagedDataError("The workbook contains no data tables")
    if not isinstance(federation, Mapping):
        name, frame = next(iter(frames.items()))
        copied = frame.copy()
        refs = {(name, str(column)): str(column) for column in copied.columns}
        return copied, refs

    kind = federation.get("kind")
    if kind == "table":
        requested = str(federation.get("name", "")).rstrip("$")
        for name, frame in frames.items():
            if name.rstrip("$").casefold() == requested.casefold():
                copied = frame.copy()
                refs = {(requested, str(column)): str(column) for column in copied.columns}
                return copied, refs
        if len(frames) != 1:
            raise PackagedDataError("Federation table alias is absent from a multi-sheet workbook")
        # Tableau may serialize a self-join alias (for example ``Sheet11``)
        # while packaging only one physical worksheet. Reuse that sole sheet
        # but keep the requested logical relation name in provenance so parent
        # joins can resolve the alias explicitly.
        frame = next(iter(frames.values())).copy()
        refs = {(requested, str(column)): str(column) for column in frame.columns}
        return frame, refs
    if kind != "join":
        raise PackagedDataError("Unsupported federation node")

    left, left_refs = _federate_frame_state(frames, federation["left"])
    right, right_refs = _federate_frame_state(frames, federation["right"])
    left_key = _resolve_federation_column(
        left_refs, federation.get("left_table"), federation.get("left_field")
    )
    right_key = _resolve_federation_column(
        right_refs, federation.get("right_table"), federation.get("right_field")
    )
    if left_key is None or right_key is None:
        raise PackagedDataError("Federation join key is absent or ambiguous in packaged data")

    occupied = {str(column) for column in left.columns}
    rename_right: dict[str, str] = {}
    for column in (str(value) for value in right.columns):
        target = column
        if target in occupied:
            target = _unique_join_column_name(
                column, _column_origin_label(right_refs, column), occupied
            )
        rename_right[column] = target
        occupied.add(target)
    right = right.rename(columns=rename_right)
    right_refs = {key: rename_right.get(column, column) for key, column in right_refs.items()}
    right_key = rename_right.get(right_key, right_key)

    join = str(federation.get("join", "inner"))
    how = {"inner": "inner", "left": "left", "right": "right", "full": "outer"}.get(join)
    if how is None:
        raise PackagedDataError(f"Unsupported federation join: {join!r}")
    overlap = set(map(str, left.columns)) & set(map(str, right.columns))
    if overlap:
        raise PackagedDataError("Federation join produced ambiguous output columns")
    merged = left.merge(
        right,
        how=cast(Literal["inner", "left", "right", "outer"], how),
        left_on=left_key,
        right_on=right_key,
    )
    references = dict(left_refs)
    for key, column in right_refs.items():
        if key in references and references[key] != column:
            raise PackagedDataError("Federation relation reference is ambiguous")
        references[key] = column
    return merged, references


def _federate_frames(frames: Mapping[str, pd.DataFrame], federation: object) -> pd.DataFrame:
    frame, _references = _federate_frame_state(frames, federation)
    return frame


def _load_hyper(
    name: str,
    path: Path,
    federation: object,
    max_rows: int,
) -> Dataset:
    with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU) as process:
        with Connection(process.endpoint, str(path), CreateMode.NONE) as connection:
            tables = [
                table
                for schema in connection.catalog.get_schema_names()
                for table in connection.catalog.get_table_names(schema)
            ]
            if not tables:
                raise PackagedDataError("The Hyper extract contains no tables")
            physical_tables = {table.name.unescaped.casefold(): table for table in tables}
            leaves = _relation_leaves(federation)
            table_by_name: dict[str, Any] = {}
            for leaf in leaves:
                key = leaf.casefold()
                matches = [
                    table
                    for physical_name, table in physical_tables.items()
                    if physical_name == key or physical_name.startswith(f"{key}_")
                ]
                if len(matches) == 1:
                    table_by_name[key] = matches[0]
            if not leaves or any(leaf.casefold() not in table_by_name for leaf in leaves):
                if len(tables) != 1:
                    raise PackagedDataError("Federation tables do not match the Hyper extract")
                leaves = [tables[0].name.unescaped]
                federation = {"kind": "table", "name": leaves[0]}
                table_by_name = {leaves[0].casefold(): tables[0]}
            aliases = {leaf: f"t{index}" for index, leaf in enumerate(leaves)}
            columns: list[str] = []
            projections: list[str] = []
            seen: set[str] = set()
            for leaf in leaves:
                table = table_by_name[leaf.casefold()]
                definition = connection.catalog.get_table_definition(table)
                for column in definition.columns:
                    source_name = column.name.unescaped
                    output_name = (
                        source_name if source_name not in seen else f"{source_name} ({leaf})"
                    )
                    seen.add(output_name)
                    columns.append(output_name)
                    projections.append(
                        f"{escape_name(aliases[leaf])}.{escape_name(source_name)} AS "
                        f"{escape_name(output_name)}"
                    )
            relation = _hyper_relation_sql(federation, table_by_name, aliases)
            sql = f"SELECT {', '.join(projections)} FROM {relation} LIMIT {max_rows + 1}"
            records = [dict(zip(columns, row)) for row in connection.execute_query(sql)]
            if len(records) > max_rows:
                raise PackagedDataError(f"Datasource {name!r} exceeds the row limit")
            return Dataset.from_records(name, records, columns=columns)


def _relation_leaves(spec: object) -> list[str]:
    if not isinstance(spec, Mapping):
        return []
    if spec.get("kind") == "table":
        return [str(spec.get("name", ""))]
    if spec.get("kind") == "join":
        return _relation_leaves(spec.get("left")) + _relation_leaves(spec.get("right"))
    return []


def _hyper_relation_sql(
    spec: object,
    tables: Mapping[str, Any],
    aliases: Mapping[str, str],
) -> str:
    if not isinstance(spec, Mapping):
        raise PackagedDataError("Missing federation")
    if spec.get("kind") == "table":
        name = str(spec["name"])
        return f"{tables[name.casefold()]} AS {escape_name(aliases[name])}"
    if spec.get("kind") != "join":
        raise PackagedDataError("Unsupported federation node")
    join = str(spec.get("join", "inner"))
    keyword = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full": "FULL"}.get(join)
    if keyword is None:
        raise PackagedDataError(f"Unsupported federation join: {join!r}")
    left = _hyper_relation_sql(spec["left"], tables, aliases)
    right = _hyper_relation_sql(spec["right"], tables, aliases)
    left_alias = aliases[str(spec["left_table"])]
    right_alias = aliases[str(spec["right_table"])]
    condition = (
        f"{escape_name(left_alias)}.{escape_name(str(spec['left_field']))} = "
        f"{escape_name(right_alias)}.{escape_name(str(spec['right_field']))}"
    )
    return f"({left} {keyword} JOIN {right} ON {condition})"


__all__ = ["PackagedDataError", "materialize_tableau_datasets", "tableau_serving_mode"]
