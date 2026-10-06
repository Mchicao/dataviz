"""Power BI PBIP / PBIT / PbixProj importer for the canonical Source AST contract.

Parses Power BI PBIP (folder-based: PBIR report + TMDL semantic model) and PBIT
(ZIP template package) artifacts into a neutral, origin-aware
:class:`core.contracts.source_ast.SourceAST` tree without loss.

Preserves:

* Semantic model tables, columns, measures, hierarchies (:mod:`.tmdl_reader`)
* Relationships, expressions (parameters) and annotations (TMDL)
* PBIR report pages, visuals and filters (JSON)
* PBIT ``DataModelSchema`` (TOM schema) and ``Report/Layout`` (JSON)
* pbi-tools ``PbixProj`` model TMDL and report ``sections/visualContainers``
* Unknown TMDL/JSON elements (:attr:`NodeKind.UNKNOWN` + ``raw_payload``)
* Sanitized provenance (relative path + SHA-256 + ``power_bi`` origin)

Native PBIX extraction is intentionally kept outside this parser. The optional
``core.importers.powerbi.pbi_tools`` bridge invokes the user-installed
``pbi-tools`` process and feeds its ``PbixProj`` output back here.

Design (Ponytail): stdlib + :mod:`core.contracts` + the sibling TMDL reader.
Does NOT import destination compiler modules (``core.compilers.*``,
``core.pbir_*``, ``core.pbip_manager``, ``core.tom_engine``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from core.contracts.provenance import (
    OriginKind,
    ProvenanceRecord,
    sanitize_relative_path,
)
from core.contracts.source_ast import (
    SCHEMA_VERSION,
    NodeKind,
    SourceAST,
    SourceNode,
    stable_node_id,
)
from core.importers.powerbi.source_diagnosis import classify_partition, diagnose_sources
from core.importers.powerbi.tmdl_reader import TmdlBlock, parse_tmdl
from core.security.credential_safety import (
    CredentialScanReport,
    sanitize_json_value,
    scan_artifact,
)

_PBI_ORIGIN = OriginKind.POWER_BI


def _sanitize_tree(
    node: SourceNode,
    *,
    findings: list,
    source_prefix: str = "",
) -> SourceNode:
    """Recursively sanitize raw_payload/attributes of a SourceNode tree.

    Accumulates safe finding metadata into ``findings`` without storing
    secret values. Returns a new immutable SourceNode with redacted payloads.
    """
    safe_attrs, attr_report = sanitize_json_value(
        dict(node.attributes), source=f"{source_prefix}.attributes"
    )
    findings.extend(attr_report.findings)
    safe_payload, payload_report = (
        sanitize_json_value(dict(node.raw_payload), source=f"{source_prefix}.raw_payload")
        if node.raw_payload is not None
        else (None, CredentialScanReport())
    )
    findings.extend(payload_report.findings)
    safe_children = tuple(
        _sanitize_tree(child, findings=findings, source_prefix=f"{source_prefix}.{child.name}")
        for child in node.children
    )
    return SourceNode(
        node_id=node.node_id,
        kind=node.kind,
        origin_kind=node.origin_kind,
        origin_tag=node.origin_tag,
        name=node.name,
        attributes=safe_attrs,
        raw_payload=safe_payload,
        children=safe_children,
    )


_PARAMETER_ANNOTATION = "BI_Bridge_NeutralParameter"
_PARAMETER_MEASURE_ANNOTATION = "BI_Bridge_ParameterMeasure"

#: TMDL dataType -> neutral hint stored on FIELD nodes (display only; the IR
#: compiler maps these to :class:`DataType`). Kept here so the importer never
#: depends on the destination type map.
_TMDL_TYPE_HINTS: dict[str, str] = {
    "string": "string",
    "int64": "integer",
    "int32": "integer",
    "decimal": "decimal",
    "double": "decimal",
    "boolean": "boolean",
    "dateTime": "datetime",
    "date": "date",
    "time": "time",
    "binary": "binary",
    "variant": "variant",
}

#: TMDL cardinality tokens normalized for preservation.
_CARDINALITY_TOKENS: frozenset[str] = frozenset(
    {"oneToOne", "oneToMany", "manyToOne", "manyToMany"}
)

#: TMDL keywords that declare a (provider/structured) data source connection.
_DATA_SOURCE_KEYWORDS = ("structuredDataSource", "providerDataSource", "dataSource")


def _decode_parameter_annotation(raw: str) -> dict[str, Any]:
    """Decodifica metadatos neutrales del adaptador PBIP, fallando cerrado."""
    token = raw.strip().strip('"')
    try:
        payload = json.loads(base64.b64decode(token, validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid BI_Bridge_NeutralParameter annotation") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        raise ValueError("invalid BI_Bridge_NeutralParameter payload")
    return payload


#: File extensions this importer accepts.
_PBIP_EXT = ".pbip"
_PBIT_EXT = ".pbit"
_PBIX_EXT = ".pbix"

#: Files ignored when hashing/walking a PBIP/PbixProj folder.
_IGNORED_NAMES = {".platform"}


def _looks_like_pbixproj(path: Path) -> bool:
    """Return whether ``path`` has the source-control layout from pbi-tools."""
    return path.is_dir() and (
        (path / ".pbixproj.json").is_file()
        or ((path / "Model").is_dir() and (path / "Report").is_dir())
    )


def _compute_folder_hash(root: Path) -> str:
    """Deterministic SHA-256 over all (sorted) files under ``root``.

    Excludes ``.platform`` manifests (machine-generated, not source intent).
    Sorting makes the hash reproducible across platforms.
    """
    digest = hashlib.sha256()
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name not in _IGNORED_NAMES)
    for path in files:
        rel = path.relative_to(root).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _sanitize_path(source_path: Path, workspace_root: Path | None) -> str:
    """Sanitized relative provenance path for a PBIP folder or PBIT file."""
    root = (workspace_root or Path.cwd()).resolve()
    try:
        relative = source_path.resolve().relative_to(root)
        candidate = relative.as_posix()
    except ValueError:
        candidate = source_path.name
    # When the artifact sits exactly at the workspace root the relative path is
    # empty; fall back to the folder/file name so provenance stays non-empty.
    if not candidate or candidate == ".":
        candidate = source_path.name
    return sanitize_relative_path(candidate)


def _json_loads(text: str, source_label: str) -> Any:
    """Parse JSON tolerantly; return ``{}``/``[]`` for empty bodies."""
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {source_label}: {exc.msg}") from exc


def _read_json_file(path: Path) -> Any:
    return _json_loads(path.read_text(encoding="utf-8-sig"), str(path.name))


def _data_type_hint(tmdl_type: str) -> str:
    """Neutral type hint from a TMDL ``dataType`` token (lower-cased)."""
    return _TMDL_TYPE_HINTS.get(tmdl_type.strip().lower(), "unknown")


def _as_jsonable(obj: Any) -> Any:
    """Best-effort conversion for ``raw_payload`` (must be JSON-safe)."""
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)


def _pbir_filter_items(data: Any) -> list[Any]:
    """Return PBIR filter containers from current or legacy JSON shapes.

    Current PBIR stores filters under ``filterConfig.filters``. Older fixtures
    and pbi-tools projections may expose a top-level ``filters`` list instead.
    The importer only normalizes location; payload semantics stay untouched in
    ``SourceNode.raw_payload`` for the neutral compiler to lower.
    """
    if not isinstance(data, dict):
        return []
    config = data.get("filterConfig")
    if isinstance(config, dict):
        items = config.get("filters")
        if isinstance(items, list):
            return list(items)
    legacy = data.get("filters")
    return list(legacy) if isinstance(legacy, list) else []


def _pbir_filter_node(
    *,
    report_name: str,
    scope_parts: tuple[str, ...],
    index: int,
    payload: Any,
) -> SourceNode:
    """Build one lossless FILTER node with a deterministic origin id."""
    if isinstance(payload, dict):
        display = payload.get("displayName") or payload.get("name")
        name = str(display) if display else f"filter_{index}"
    else:
        name = f"filter_{index}"
    return SourceNode(
        node_id=stable_node_id(
            _PBI_ORIGIN, "report", report_name, *scope_parts, "filter", str(index)
        ),
        kind=NodeKind.FILTER,
        origin_kind=_PBI_ORIGIN,
        origin_tag="filter",
        name=name,
        raw_payload=_as_jsonable(payload),
    )


class PowerBIImporter:
    """Importer for Power BI PBIP / PBIT / PbixProj artifacts.

    Args:
        source_path: Path to a ``.pbip`` entry file, PBIP root folder, ``.pbit``
            ZIP package, or a ``PbixProj`` extraction folder.
        workspace_root: Optional anchor for the relative provenance path.

    Raises:
        FileNotFoundError: If ``source_path`` does not exist.
        ValueError: If the artifact is a native ``.pbix`` or has an unsupported layout.
    """

    def __init__(self, source_path: str | Path, workspace_root: str | Path | None = None) -> None:
        path = Path(source_path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"Source artifact not found: {source_path}")
        if path.suffix.lower() == _PBIX_EXT:
            raise ValueError(
                "Native PBIX is not auto-imported here. Use "
                "import_powerbi_pbix_partial for the layout-only partial mode, or "
                "import_powerbi_pbix with the optional pbi-tools executable."
            )
        self.source_path = path
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    def import_ast(self) -> SourceAST:
        """Parse the Power BI artifact into a validated :class:`SourceAST`."""
        if self.source_path.is_file() and self.source_path.suffix.lower() == _PBIT_EXT:
            return self._import_pbit()
        if _looks_like_pbixproj(self.source_path):
            return self._import_pbixproj()
        return self._import_pbip()

    # ------------------------------------------------------------------ PBIP

    def _import_pbip(self) -> SourceAST:
        """Import a PBIP folder (PBIR report + TMDL semantic model)."""
        root = self._resolve_pbip_root()
        artifact_path = self._relative_artifact_path(root)
        file_hash = _compute_folder_hash(root)
        provenance = ProvenanceRecord(
            artifact_path=artifact_path,
            artifact_hash=file_hash,
            origin=_PBI_ORIGIN.value,
        )

        semantic_dir, report_dir = self._locate_pbip_parts(root)
        children: list[SourceNode] = []

        if semantic_dir is not None:
            children.append(self._parse_semantic_model(semantic_dir))
        if report_dir is not None:
            children.append(self._parse_report(report_dir))

        # Capture leftover sibling artifacts at the root (config files, etc.).
        counter = 0
        for entry in sorted(root.iterdir(), key=lambda p: p.name) if root.is_dir() else []:
            if (
                entry.is_file()
                and entry.name not in _IGNORED_NAMES
                and entry.suffix.lower() != _PBIP_EXT
            ):
                counter += 1
                children.append(
                    self._unknown_node(("pbip", "root", entry.name, str(counter)), entry)
                )

        name = root.name if root.is_dir() else root.stem
        doc_node = SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbip", name),
            kind=NodeKind.DOCUMENT,
            origin_kind=_PBI_ORIGIN,
            origin_tag="pbip",
            name=name,
            attributes={"format": "pbip"},
            children=tuple(children),
        )
        doc = SourceAST(
            schema_version=SCHEMA_VERSION,
            origin_kind=_PBI_ORIGIN,
            root=doc_node,
            provenance=provenance,
            metadata={"source_name": name},
        )
        return self._finalize_doc(doc)

    def _finalize_doc(self, doc: SourceAST) -> SourceAST:
        """Sanitiza el AST y adjunta diagnÃ³stico/procedencia de orÃ­genes."""
        findings: list = []
        safe_root = _sanitize_tree(doc.root, findings=findings, source_prefix=doc.root.name)
        safe_metadata, _metadata_report = sanitize_json_value(
            dict(doc.metadata), source="metadata"
        )
        sanitized = SourceAST(
            schema_version=doc.schema_version,
            origin_kind=doc.origin_kind,
            root=safe_root,
            provenance=doc.provenance,
            metadata=safe_metadata,
        )
        provenance = doc.provenance.model_dump() if doc.provenance is not None else None
        entries = diagnose_sources(sanitized.root)
        for entry in entries:
            entry["provenance"] = provenance
        safe_entries, _source_report = sanitize_json_value(
            entries, source="metadata.data_sources"
        )
        sanitized.metadata["data_sources"] = safe_entries
        sanitized.validate()
        return sanitized

    def _import_pbixproj(self) -> SourceAST:
        """Import the source-control folder emitted by ``pbi-tools extract``."""
        root = self.source_path
        provenance = ProvenanceRecord(
            artifact_path=self._relative_artifact_path(root),
            artifact_hash=_compute_folder_hash(root),
            origin=_PBI_ORIGIN.value,
        )
        children: list[SourceNode] = []
        model_dir = root / "Model"
        report_dir = root / "Report"
        if model_dir.is_dir():
            children.append(self._parse_semantic_model(model_dir))
        if report_dir.is_dir():
            children.append(self._parse_report(report_dir))

        counter = 0
        root_files: list[str] = []
        root_directories: list[str] = []
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if entry.name in {"Model", "Report"} or entry.name in _IGNORED_NAMES:
                continue
            counter += 1
            if entry.is_file():
                root_files.append(entry.name)
                children.append(self._unknown_node(("pbixproj", entry.name, str(counter)), entry))
            elif entry.is_dir():
                root_directories.append(entry.name)
                children.append(
                    self._unknown_directory_node(("pbixproj", entry.name, str(counter)), entry)
                )

        name = root.name
        doc = SourceAST(
            schema_version=SCHEMA_VERSION,
            origin_kind=_PBI_ORIGIN,
            root=SourceNode(
                node_id=stable_node_id(_PBI_ORIGIN, "pbixproj", name),
                kind=NodeKind.DOCUMENT,
                origin_kind=_PBI_ORIGIN,
                origin_tag="pbixproj",
                name=name,
                attributes={"format": "pbixproj", "extractor": "pbi-tools"},
                children=tuple(children),
            ),
            provenance=provenance,
            metadata={
                "source_name": name,
                "format": "pbixproj",
                "extractor": "pbi-tools",
                "root_files": root_files,
                "root_directories": root_directories,
            },
        )
        return self._finalize_doc(doc)

    def _resolve_pbip_root(self) -> Path:
        """Resolve the PBIP project root folder from a file or directory input."""
        path = self.source_path
        if path.is_file() and path.suffix.lower() == _PBIP_EXT:
            return path.parent
        if path.is_dir():
            # Require a .pbip entry to confirm it is a PBIP project.
            pbip_entries = list(path.glob(f"*{_PBIP_EXT}"))
            if not pbip_entries:
                raise ValueError(
                    f"Directory {path} is not a PBIP project: no .pbip entry file found"
                )
            return path
        raise ValueError(
            f"Unsupported PBIP source: {path}. Expected a .pbip file or project folder."
        )

    def _locate_pbip_parts(self, root: Path) -> tuple[Path | None, Path | None]:
        """Locate the ``.SemanticModel`` and ``.Report`` folders of a PBIP root."""
        semantic_dir: Path | None = None
        report_dir: Path | None = None
        for entry in root.iterdir():
            if not entry.is_dir():
                continue
            low = entry.name.lower()
            if low.endswith(".semanticmodel"):
                semantic_dir = entry
            elif low.endswith(".report"):
                report_dir = entry
        return semantic_dir, report_dir

    def _relative_artifact_path(self, root: Path) -> str:
        return _sanitize_path(root, self.workspace_root)

    def _parse_semantic_model(self, semantic_dir: Path) -> SourceNode:
        """Parse a ``.SemanticModel`` folder into a DATASOURCE-rooted subtree."""
        name = semantic_dir.name
        node_id = stable_node_id(_PBI_ORIGIN, "semantic_model", name)
        children: list[SourceNode] = []
        counter = 0
        model_format = "tmdl"

        definition = semantic_dir / "definition"
        if not definition.is_dir():
            definition = semantic_dir
        model_tmdl = definition / "model.tmdl"
        relationships_tmdl = definition / "relationships.tmdl"
        if model_tmdl.is_file():
            blocks = parse_tmdl(model_tmdl.read_text(encoding="utf-8-sig"))
            model_block = next((b for b in blocks if b.keyword == "model"), None)
            children.extend(
                self._parse_model_tmdl(
                    model_tmdl,
                    model_block,
                    include_relationships=not relationships_tmdl.is_file(),
                )
            )
        if relationships_tmdl.is_file():
            for block in parse_tmdl(relationships_tmdl.read_text(encoding="utf-8-sig")):
                if block.keyword == "relationship":
                    children.append(self._relationship_node(block))

        tom_model = next(
            (
                path
                for path in (definition / "database.json", definition / "model.bim")
                if path.is_file()
            ),
            None,
        )
        if tom_model is not None:
            children.extend(self._parse_tom_model_file(tom_model))
            model_format = "tom"

        tables_dir = definition / "tables"
        if tables_dir.is_dir():
            for tmdl_file in sorted(tables_dir.glob("*.tmdl")):
                children.append(self._parse_table_tmdl(tmdl_file))

        roles_dir = definition / "roles"
        if roles_dir.is_dir():
            for tmdl_file in sorted(roles_dir.glob("*.tmdl")):
                for block in parse_tmdl(tmdl_file.read_text(encoding="utf-8-sig")):
                    if block.keyword == "role":
                        children.append(self._role_node(block))

        # Preserve any definition files we did not structurally map.
        if definition.is_dir():
            mapped = {"model.tmdl", "relationships.tmdl", "database.json", "model.bim"}
            mapped |= (
                {f"tables/{p.name}" for p in tables_dir.glob("*.tmdl")}
                if tables_dir.is_dir()
                else set()
            )
            mapped |= (
                {f"roles/{p.name}" for p in roles_dir.glob("*.tmdl")}
                if roles_dir.is_dir()
                else set()
            )
            for f in sorted(definition.rglob("*")):
                if f.is_file() and f.relative_to(definition).as_posix() not in mapped:
                    counter += 1
                    children.append(
                        self._unknown_node(("semantic_model", name, f.name, str(counter)), f)
                    )

        return SourceNode(
            node_id=node_id,
            kind=NodeKind.DATASOURCE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="semantic_model",
            name=name,
            attributes={"format": model_format},
            children=tuple(children),
        )

    def _parse_tom_model_file(self, path: Path) -> list[SourceNode]:
        """Map a pbi-tools Raw TOM/BIM model using the existing PBIT readers."""
        data = _read_json_file(path)
        model = data.get("model", data) if isinstance(data, dict) else {}
        if not isinstance(model, dict):
            return []
        nodes: list[SourceNode] = []
        nodes.extend(
            self._pbit_table_node(table, idx) for idx, table in enumerate(model.get("tables", []))
        )
        nodes.extend(
            self._pbit_relationship_node(relationship, idx)
            for idx, relationship in enumerate(model.get("relationships", []))
        )
        nodes.extend(
            self._pbit_role_node(role, idx)
            for idx, role in enumerate(model.get("roles", []))
            if isinstance(role, dict)
        )
        nodes.extend(
            self._pbit_expression_node(expression, idx)
            for idx, expression in enumerate(model.get("expressions", []))
        )
        return nodes

    def _parse_model_tmdl(
        self,
        path: Path,
        model_block: TmdlBlock | None,
        *,
        include_relationships: bool = True,
    ) -> list[SourceNode]:
        """Extract relationships + model-level expressions from ``model.tmdl``."""
        nodes: list[SourceNode] = []
        if model_block is None:
            return nodes

        for child in model_block.children:
            if child.keyword == "relationship" and include_relationships:
                nodes.append(self._relationship_node(child))
            elif child.keyword == "expression":
                nodes.append(self._expression_node(child, ("model", model_block.name)))
            elif child.keyword == "annotation":
                nodes.append(self._annotation_node(child, ("model", model_block.name)))
            elif child.keyword in _DATA_SOURCE_KEYWORDS:
                nodes.append(self._data_source_block_node(child, ("model", model_block.name)))

        # Preserve the raw model.tmdl text for full fidelity.
        return nodes

    def _parse_table_tmdl(self, path: Path) -> SourceNode:
        """Parse a single table TMDL file into a DATASOURCE node (the table)."""
        blocks = parse_tmdl(path.read_text(encoding="utf-8-sig"))
        table_block = next((b for b in blocks if b.keyword == "table"), None)
        table_name = table_block.name if table_block else path.stem
        node_id = stable_node_id(_PBI_ORIGIN, "table", table_name)

        children: list[SourceNode] = []
        if table_block is not None:
            idx = 0
            for child in table_block.children:
                if child.keyword in ("column", "calculatedColumn", "calculatedTableColumn"):
                    idx += 1
                    children.append(self._column_node(table_name, child, idx))
                elif child.keyword == "measure":
                    idx += 1
                    children.append(self._measure_node(table_name, child, idx))
                elif child.keyword == "partition":
                    idx += 1
                    children.append(self._partition_node(table_name, child, idx))
                elif child.keyword == "hierarchy":
                    idx += 1
                    children.append(self._hierarchy_node(table_name, child, idx))
                elif child.keyword == "annotation" and child.name == _PARAMETER_ANNOTATION:
                    idx += 1
                    metadata = _decode_parameter_annotation(child.inline_expr)
                    children.append(
                        SourceNode(
                            node_id=stable_node_id(
                                _PBI_ORIGIN, "table", table_name, "parameter", str(idx)
                            ),
                            kind=NodeKind.PARAMETER,
                            origin_kind=_PBI_ORIGIN,
                            origin_tag="neutral_parameter",
                            name=str(metadata["name"]),
                            attributes=metadata,
                            raw_payload=_block_payload(child),
                        )
                    )
                elif child.keyword == "annotation":
                    idx += 1
                    children.append(
                        self._annotation_node(child, ("table", table_name), suffix=str(idx))
                    )
                else:
                    idx += 1
                    children.append(
                        self._raw_block_node(("table", table_name, child.keyword, str(idx)), child)
                    )

        attributes: dict[str, Any] = {"source_file": path.name}
        if any(child.origin_tag == "neutral_parameter" for child in children):
            attributes["neutral_parameter_table"] = True
        if table_block is not None and table_block.properties:
            attributes["table_properties"] = dict(table_block.properties)

        return SourceNode(
            node_id=node_id,
            kind=NodeKind.DATASOURCE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="table",
            name=table_name,
            attributes=attributes,
            children=tuple(children),
        )

    # -- TMDL element -> SourceNode helpers -------------------------------

    def _column_node(self, table: str, block: TmdlBlock, idx: int) -> SourceNode:
        props = block.properties
        tmdl_type = props.get("dataType", "")
        attributes: dict[str, Any] = {
            "data_type_hint": _data_type_hint(tmdl_type),
            "tmdl_data_type": tmdl_type,
            "summarize_by": props.get("summarizeBy", ""),
            "source_column": props.get("sourceColumn", block.name),
        }
        if props.get("isHidden", "").lower() == "true":
            attributes["is_hidden"] = True
        if props.get("isKey", "").lower() == "true":
            attributes["is_key"] = True
        is_calc = block.keyword in ("calculatedColumn", "calculatedTableColumn") or bool(
            block.expression
        )
        kind = NodeKind.CALCULATION if is_calc else NodeKind.FIELD
        origin_tag = "calculated_column" if is_calc else "column"
        if is_calc and block.expression:
            attributes["expression"] = block.expression
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "table", table, origin_tag, block.name, str(idx)),
            kind=kind,
            origin_kind=_PBI_ORIGIN,
            origin_tag=origin_tag,
            name=block.name,
            attributes=attributes,
            raw_payload=_block_payload(block),
        )

    def _measure_node(self, table: str, block: TmdlBlock, idx: int) -> SourceNode:
        attributes: dict[str, Any] = {
            "expression": block.expression,
            "format_string": block.properties.get("formatString", ""),
            "display_folder": block.properties.get("displayFolder", ""),
        }
        if block.properties.get("isHidden", "").lower() == "true":
            attributes["is_hidden"] = True
        if "description" in block.properties:
            attributes["description"] = block.properties["description"]
        if any(
            child.keyword == "annotation"
            and child.name == _PARAMETER_MEASURE_ANNOTATION
            and child.inline_expr.strip().lower() == "true"
            for child in block.children
        ):
            attributes["neutral_parameter_measure"] = True
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "table", table, "measure", block.name, str(idx)),
            kind=NodeKind.CALCULATION,
            origin_kind=_PBI_ORIGIN,
            origin_tag="measure",
            name=block.name,
            attributes=attributes,
            raw_payload=_block_payload(block),
        )

    def _partition_node(self, table: str, block: TmdlBlock, idx: int) -> SourceNode:
        # El cuerpo M vive bajo el bloque hijo ``source``; el lector TMDL lo
        # captura como expresiÃ³n multilÃ­nea (cuerpo indentado tras ``=``).
        source_block = block.child("source")
        m_text = source_block.expression if source_block is not None else ""
        kind_token = block.inline_expr or ""
        diagnosis = classify_partition(
            name=block.name, table=table, source_kind=kind_token, m_text=m_text
        )
        attributes: dict[str, Any] = {
            "mode": block.properties.get("mode", ""),
            "source_kind": kind_token,
            "table": table,
            "source_expression": m_text,
            "source_connection_status": diagnosis["status"],
        }
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "table", table, "partition", block.name, str(idx)),
            kind=NodeKind.RELATIONSHIP,
            origin_kind=_PBI_ORIGIN,
            origin_tag="partition",
            name=block.name,
            attributes=attributes,
            raw_payload=_block_payload(block),
        )

    def _hierarchy_node(self, table: str, block: TmdlBlock, idx: int) -> SourceNode:
        levels = [c.name for c in block.children if c.keyword == "level"]
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "table", table, "hierarchy", block.name, str(idx)),
            kind=NodeKind.LAYOUT_ZONE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="hierarchy",
            name=block.name,
            attributes={"levels": levels},
            raw_payload=_block_payload(block),
        )

    def _relationship_node(self, block: TmdlBlock) -> SourceNode:
        """Map legacy inline or modern ``relationships.tmdl`` relationship blocks."""
        from_ref = block.properties.get("fromColumn", "")
        to_ref = block.properties.get("toColumn", "")
        if from_ref and to_ref:
            from_table, from_col = _split_property_ref(from_ref)
            to_table, to_col = _split_property_ref(to_ref)
            from_cols = [from_col] if from_col else []
            to_cols = [to_col] if to_col else []
            rel_name = block.properties.get("name") or block.name or f"{from_table}_{to_table}"
        else:
            from_cols, to_cols, from_table, to_table = _parse_relationship_signature(block.signature)
            rel_name = block.properties.get("name") or f"{from_table}_{to_table}"
        cardinality = block.properties.get("cardinality", "")
        attributes: dict[str, Any] = {
            "from_table": from_table,
            "from_columns": from_cols,
            "to_table": to_table,
            "to_columns": to_cols,
            "cardinality": cardinality,
            "cross_filter": block.properties.get("crossFilteringBehavior", "single"),
            "is_active": block.properties.get("isActive", "true").lower() != "false",
        }
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "relationship", rel_name),
            kind=NodeKind.RELATIONSHIP,
            origin_kind=_PBI_ORIGIN,
            origin_tag="relationship",
            name=rel_name,
            attributes=attributes,
            raw_payload=_block_payload(block),
        )

    def _role_node(self, block: TmdlBlock) -> SourceNode:
        """Preserve a TMDL security role and its table predicates without lowering it."""
        permissions = [
            {
                "table": child.name,
                "filter_expression": child.expression,
            }
            for child in block.children
            if child.keyword == "tablePermission"
        ]
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "security_role", block.name),
            kind=NodeKind.FILTER,
            origin_kind=_PBI_ORIGIN,
            origin_tag="security_role",
            name=block.name,
            attributes={
                "model_permission": block.properties.get("modelPermission", ""),
                "table_permissions": permissions,
                "requires_manual_review": True,
            },
            raw_payload=_block_payload(block),
        )

    def _expression_node(self, block: TmdlBlock, owner: tuple[str, ...]) -> SourceNode:
        """Model-level expression (often a parameter)."""
        kind_token = block.properties.get("kind", "")
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, *owner, "expression", block.name),
            kind=NodeKind.PARAMETER,
            origin_kind=_PBI_ORIGIN,
            origin_tag="expression",
            name=block.name,
            attributes={
                "expression": block.expression,
                "kind": kind_token,
            },
            raw_payload=_block_payload(block),
        )

    def _annotation_node(
        self, block: TmdlBlock, owner: tuple[str, ...], suffix: str = "1"
    ) -> SourceNode:
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, *owner, "annotation", block.name, suffix),
            kind=NodeKind.UNKNOWN,
            origin_kind=_PBI_ORIGIN,
            origin_tag="annotation",
            name=block.name,
            attributes={"value": block.inline_expr},
            raw_payload=_block_payload(block),
        )

    def _data_source_block_node(self, block: TmdlBlock, owner: tuple[str, ...]) -> SourceNode:
        """Preserve a declared (provider/structured) data source connection block."""
        tag = {
            "structuredDataSource": "structured_data_source",
            "providerDataSource": "provider_data_source",
            "dataSource": "data_source",
        }.get(block.keyword, "data_source")
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, *owner, block.keyword, block.name or "1"),
            kind=NodeKind.UNKNOWN,
            origin_kind=_PBI_ORIGIN,
            origin_tag=tag,
            name=block.name or block.keyword,
            attributes={"kind": block.keyword},
            raw_payload=_block_payload(block),
        )

    def _raw_block_node(self, path: tuple[str, ...], block: TmdlBlock) -> SourceNode:
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, *path),
            kind=NodeKind.UNKNOWN,
            origin_kind=_PBI_ORIGIN,
            origin_tag=block.keyword or "block",
            name=block.name or block.keyword,
            attributes=dict(block.properties) if block.properties else {},
            raw_payload=_block_payload(block),
        )

    def _unknown_node(self, path: tuple[str, ...], file_path: Path) -> SourceNode:
        """Preserve an unmapped file as an UNKNOWN node with its raw bytes text."""
        try:
            payload: Any = _read_json_file(file_path)
        except (ValueError, OSError):
            payload = {"text": file_path.read_text(encoding="utf-8-sig", errors="replace")}
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, *path),
            kind=NodeKind.UNKNOWN,
            origin_kind=_PBI_ORIGIN,
            origin_tag="file",
            name=file_path.name,
            attributes={"source_file": file_path.name},
            raw_payload=(payload if isinstance(payload, dict) else {"value": _as_jsonable(payload)}),
        )

    def _unknown_directory_node(self, path: tuple[str, ...], directory: Path) -> SourceNode:
        """Preserve an auxiliary PbixProj directory as a bounded file inventory."""
        files = []
        for file_path in sorted(p for p in directory.rglob("*") if p.is_file()):
            digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
            files.append(
                {
                    "path": file_path.relative_to(directory).as_posix(),
                    "size": file_path.stat().st_size,
                    "sha256": digest,
                }
            )
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, *path),
            kind=NodeKind.UNKNOWN,
            origin_kind=_PBI_ORIGIN,
            origin_tag="directory",
            name=directory.name,
            attributes={"source_directory": directory.name, "file_count": len(files)},
            raw_payload={"directory": directory.name, "files": files},
        )

    # ------------------------------------------------------------------ Report

    def _parse_report(self, report_dir: Path) -> SourceNode:
        """Parse a ``.Report`` folder (PBIR JSON) into a PAGE-rooted subtree."""
        name = report_dir.name
        node_id = stable_node_id(_PBI_ORIGIN, "report", name)
        children: list[SourceNode] = []

        definition = report_dir / "definition"
        if not definition.is_dir():
            return self._parse_pbixproj_report(report_dir, name, node_id)
        report_json = definition / "report.json"
        report_data: dict[str, Any] = _read_json_file(report_json) if report_json.is_file() else {}
        if report_json.is_file():
            children.append(
                SourceNode(
                    node_id=stable_node_id(_PBI_ORIGIN, "report", name, "config"),
                    kind=NodeKind.UNKNOWN,
                    origin_kind=_PBI_ORIGIN,
                    origin_tag="report_config",
                    name="report.json",
                    raw_payload=_as_jsonable(report_data),
                )
            )
            children.extend(
                _pbir_filter_node(
                    report_name=name, scope_parts=("report",), index=idx, payload=flt
                )
                for idx, flt in enumerate(_pbir_filter_items(report_data))
            )

        pages_dir = definition / "pages"
        if pages_dir.is_dir():
            children.extend(self._parse_pages(pages_dir, name))

        return SourceNode(
            node_id=node_id,
            kind=NodeKind.DATASOURCE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="report",
            name=name,
            attributes={"format": "pbir"},
            children=tuple(children),
        )

    def _parse_pbixproj_report(self, report_dir: Path, name: str, node_id: str) -> SourceNode:
        """Parse pbi-tools ``Report/sections`` into the PBIR-like tree."""
        children: list[SourceNode] = []
        report_json = report_dir / "report.json"
        if report_json.is_file():
            children.append(
                SourceNode(
                    node_id=stable_node_id(_PBI_ORIGIN, "report", name, "config"),
                    kind=NodeKind.UNKNOWN,
                    origin_kind=_PBI_ORIGIN,
                    origin_tag="report_config",
                    name="report.json",
                    raw_payload=_as_jsonable(_read_json_file(report_json)),
                )
            )

        sections_dir = report_dir / "sections"
        for section_dir in (
            sorted(p for p in sections_dir.iterdir() if p.is_dir()) if sections_dir.is_dir() else []
        ):
            children.append(self._parse_pbixproj_section(section_dir, name))
        return SourceNode(
            node_id=node_id,
            kind=NodeKind.DATASOURCE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="report",
            name=name,
            attributes={"format": "pbixproj_report"},
            children=tuple(children),
        )

    def _parse_pbixproj_section(self, section_dir: Path, report_name: str) -> SourceNode:
        """Parse one pbi-tools report section and all native visual files."""
        section_file = section_dir / "section.json"
        data = _read_json_file(section_file) if section_file.is_file() else {}
        page_name = data.get("displayName") or section_dir.name
        children: list[SourceNode] = []
        visuals_dir = section_dir / "visualContainers"
        for idx, visual_dir in (
            enumerate(sorted(p for p in visuals_dir.iterdir() if p.is_dir()))
            if visuals_dir.is_dir()
            else []
        ):
            children.append(
                self._parse_pbixproj_visual(visual_dir, report_name, section_dir.name, idx)
            )

        filters_path = section_dir / "filters.json"
        if filters_path.is_file():
            filters = _read_json_file(filters_path)
            if filters not in (None, [], {}):
                children.append(
                    SourceNode(
                        node_id=stable_node_id(
                            _PBI_ORIGIN, "report", report_name, "page", section_dir.name, "filters"
                        ),
                        kind=NodeKind.FILTER,
                        origin_kind=_PBI_ORIGIN,
                        origin_tag="filter",
                        name="filters.json",
                        raw_payload=_as_jsonable(filters),
                    )
                )
        raw_payload = dict(data) if isinstance(data, dict) else {"section": data}
        if filters_path.is_file():
            raw_payload["filters"] = _read_json_file(filters_path)
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "report", report_name, "page", section_dir.name),
            kind=NodeKind.PAGE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="page",
            name=page_name,
            attributes={
                "page_id": section_dir.name,
                "width": data.get("width") if isinstance(data, dict) else None,
                "height": data.get("height") if isinstance(data, dict) else None,
            },
            children=tuple(children),
            raw_payload=_as_jsonable(raw_payload),
        )

    def _parse_pbixproj_visual(
        self, visual_dir: Path, report_name: str, page_id: str, idx: int
    ) -> SourceNode:
        """Parse pbi-tools visual sidecars while preserving every JSON payload."""
        config_path = visual_dir / "config.json"
        config = _read_json_file(config_path) if config_path.is_file() else {}
        visual_type = ""
        if isinstance(config, dict):
            visual_type = str(config.get("singleVisual", {}).get("visualType", ""))
        payload: dict[str, Any] = {"config": config}
        for key, filename in (
            ("query", "query.json"),
            ("data_transforms", "dataTransforms.json"),
            ("filters", "filters.json"),
            ("visual_container", "visualContainer.json"),
        ):
            path = visual_dir / filename
            if path.is_file():
                payload[key] = _read_json_file(path)
        return SourceNode(
            node_id=stable_node_id(
                _PBI_ORIGIN, "report", report_name, "page", page_id, "visual", visual_dir.name
            ),
            kind=NodeKind.VISUAL,
            origin_kind=_PBI_ORIGIN,
            origin_tag="visual",
            name=visual_type or visual_dir.name,
            attributes={"visual_id": visual_dir.name, "visual_type": visual_type, "index": idx},
            raw_payload=_as_jsonable(payload),
        )

    def _parse_pages(self, pages_dir: Path, report_name: str) -> list[SourceNode]:
        """Parse PBIR pages: ``pages.json`` + each ``<pageId>/page.json``."""
        nodes: list[SourceNode] = []
        pages_json = pages_dir / "pages.json"
        page_order: list[str] = []
        if pages_json.is_file():
            data = _read_json_file(pages_json)
            page_order = list(data.get("pageOrder", [])) if isinstance(data, dict) else []
            nodes.append(
                SourceNode(
                    node_id=stable_node_id(_PBI_ORIGIN, "report", report_name, "pages"),
                    kind=NodeKind.UNKNOWN,
                    origin_kind=_PBI_ORIGIN,
                    origin_tag="pages_metadata",
                    name="pages.json",
                    attributes={"page_order": page_order},
                    raw_payload=_as_jsonable(data),
                )
            )

        for page_dir in sorted(p for p in pages_dir.iterdir() if p.is_dir()):
            nodes.append(self._parse_page(page_dir, report_name))
        return nodes

    def _parse_page(self, page_dir: Path, report_name: str) -> SourceNode:
        page_json = page_dir / "page.json"
        data: dict[str, Any] = _read_json_file(page_json) if page_json.is_file() else {}
        page_name = data.get("displayName") or page_dir.name
        node_id = stable_node_id(_PBI_ORIGIN, "report", report_name, "page", page_dir.name)
        children: list[SourceNode] = []

        visuals_dir = page_dir / "visuals"
        if visuals_dir.is_dir():
            for idx, vdir in enumerate(sorted(visuals_dir.iterdir())):
                if vdir.is_dir():
                    children.append(self._parse_visual(vdir, report_name, page_dir.name, idx))

        for idx, flt in enumerate(_pbir_filter_items(data)):
            children.append(
                _pbir_filter_node(
                    report_name=report_name,
                    scope_parts=("page", page_dir.name),
                    index=idx,
                    payload=flt,
                )
            )

        return SourceNode(
            node_id=node_id,
            kind=NodeKind.PAGE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="page",
            name=page_name,
            attributes={
                "page_id": page_dir.name,
                "width": data.get("width"),
                "height": data.get("height"),
            },
            children=tuple(children),
            raw_payload=_as_jsonable(data) if data else None,
        )

    def _parse_visual(self, vdir: Path, report_name: str, page_id: str, idx: int) -> SourceNode:
        visual_json = vdir / "visual.json"
        data: dict[str, Any] = _read_json_file(visual_json) if visual_json.is_file() else {}
        visual_type = ""
        if isinstance(data.get("visual", {}), dict):
            visual_type = (
                data["visual"].get("visualType", "") if isinstance(data.get("visual"), dict) else ""
            )
        filter_children = tuple(
            _pbir_filter_node(
                report_name=report_name,
                scope_parts=("page", page_id, "visual", vdir.name),
                index=idx,
                payload=flt,
            )
            for idx, flt in enumerate(_pbir_filter_items(data))
        )
        return SourceNode(
            node_id=stable_node_id(
                _PBI_ORIGIN, "report", report_name, "page", page_id, "visual", vdir.name
            ),
            kind=NodeKind.VISUAL,
            origin_kind=_PBI_ORIGIN,
            origin_tag="visual",
            name=visual_type or vdir.name,
            attributes={"visual_id": vdir.name, "visual_type": visual_type},
            raw_payload=_as_jsonable(data) if data else None,
            children=filter_children,
        )

    # ------------------------------------------------------------------- PBIT

    def _import_pbit(self) -> SourceAST:
        """Import a ``.pbit`` ZIP template package."""
        file_bytes = self.source_path.read_bytes()
        provenance = ProvenanceRecord(
            artifact_path=_sanitize_path(self.source_path, self.workspace_root),
            artifact_hash=hashlib.sha256(file_bytes).hexdigest(),
            origin=_PBI_ORIGIN.value,
        )
        children: list[SourceNode] = []
        with zipfile.ZipFile(self.source_path) as zf:
            names = {n.lower(): n for n in zf.namelist()}
            schema_name = _first_key(names, "datamodelschema")
            layout_name = _first_key(names, "report/layout")
            if schema_name is not None:
                children.append(self._parse_pbit_schema(zf, schema_name))
            if layout_name is not None:
                children.append(self._parse_pbit_layout(zf, layout_name))

        name = self.source_path.stem
        doc_node = SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit", name),
            kind=NodeKind.DOCUMENT,
            origin_kind=_PBI_ORIGIN,
            origin_tag="pbit",
            name=name,
            attributes={"format": "pbit"},
            children=tuple(children),
        )
        doc = SourceAST(
            schema_version=SCHEMA_VERSION,
            origin_kind=_PBI_ORIGIN,
            root=doc_node,
            provenance=provenance,
            metadata={"source_name": name},
        )
        return self._finalize_doc(doc)

    def _parse_pbit_schema(self, zf: zipfile.ZipFile, member: str) -> SourceNode:
        """Parse ``DataModelSchema`` (TOM/BIM JSON) into a model subtree."""
        data = _json_loads(zf.read(member).decode("utf-8-sig"), member)
        model = data.get("model", data) if isinstance(data, dict) else {}
        children: list[SourceNode] = []

        for idx, tbl in enumerate(model.get("tables", []) if isinstance(model, dict) else []):
            children.append(self._pbit_table_node(tbl, idx))

        for idx, rel in enumerate(
            model.get("relationships", []) if isinstance(model, dict) else []
        ):
            children.append(self._pbit_relationship_node(rel, idx))

        for idx, role in enumerate(model.get("roles", []) if isinstance(model, dict) else []):
            if isinstance(role, dict):
                children.append(self._pbit_role_node(role, idx))

        for idx, expr in enumerate(model.get("expressions", []) if isinstance(model, dict) else []):
            children.append(self._pbit_expression_node(expr, idx))

        name = self.source_path.stem
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit", name, "data_model"),
            kind=NodeKind.DATASOURCE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="data_model",
            name="DataModelSchema",
            attributes={"format": "tom"},
            children=tuple(children),
            raw_payload=_as_jsonable(model) if model else None,
        )

    def _parse_pbit_layout(self, zf: zipfile.ZipFile, member: str) -> SourceNode:
        """Parse ``Report/Layout`` JSON into pages/visuals."""
        # Real PBIX/PBIT archives serialize the layout as UTF-16 LE; the shared
        # decoder detects BOM and no-BOM variants (importer-decoder contract).
        text, _encoding = _decode_layout_bytes(zf.read(member))
        data = _json_loads(text, member)
        children = _layout_pages(data, "pbit_section")
        name = self.source_path.stem
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit", name, "report_layout"),
            kind=NodeKind.DATASOURCE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="report_layout",
            name="ReportLayout",
            attributes={"format": "layout"},
            children=tuple(children),
            raw_payload=_as_jsonable(data) if data else None,
        )

    def _pbit_table_node(self, tbl: dict[str, Any], idx: int) -> SourceNode:
        tname = tbl.get("name", f"table_{idx}")
        children: list[SourceNode] = []
        for cidx, col in enumerate(tbl.get("columns", [])):
            children.append(self._pbit_column_node(tname, col, cidx))
        for midx, meas in enumerate(tbl.get("measures", [])):
            children.append(self._pbit_measure_node(tname, meas, midx))
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit_table", tname),
            kind=NodeKind.DATASOURCE,
            origin_kind=_PBI_ORIGIN,
            origin_tag="table",
            name=tname,
            attributes={"source": "pbit"},
            children=tuple(children),
            raw_payload=_as_jsonable(tbl),
        )

    def _pbit_column_node(self, table: str, col: dict[str, Any], idx: int) -> SourceNode:
        cname = col.get("name", f"col_{idx}")
        tmdl_type = col.get("dataType", "")
        is_calc = bool(col.get("expression") or col.get("type") == "calculated")
        attributes: dict[str, Any] = {
            "data_type_hint": _data_type_hint(tmdl_type),
            "tmdl_data_type": tmdl_type,
            "summarize_by": col.get("summarizeBy", ""),
            "source_column": col.get("sourceColumn", cname),
        }
        if col.get("isKey"):
            attributes["is_key"] = True
        if col.get("isHidden"):
            attributes["is_hidden"] = True
        kind = NodeKind.CALCULATION if is_calc else NodeKind.FIELD
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit_table", table, "column", cname),
            kind=kind,
            origin_kind=_PBI_ORIGIN,
            origin_tag="calculated_column" if is_calc else "column",
            name=cname,
            attributes=attributes,
            raw_payload=_as_jsonable(col),
        )

    def _pbit_measure_node(self, table: str, meas: dict[str, Any], idx: int) -> SourceNode:
        mname = meas.get("name", f"measure_{idx}")
        attributes: dict[str, Any] = {
            "expression": meas.get("expression", ""),
            "format_string": meas.get("formatString", ""),
        }
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit_table", table, "measure", mname),
            kind=NodeKind.CALCULATION,
            origin_kind=_PBI_ORIGIN,
            origin_tag="measure",
            name=mname,
            attributes=attributes,
            raw_payload=_as_jsonable(meas),
        )

    def _pbit_relationship_node(self, rel: dict[str, Any], idx: int) -> SourceNode:
        from_table = rel.get("fromTable", "")
        to_table = rel.get("toTable", "")
        rel_name = rel.get("name", f"{from_table}_{to_table}_{idx}")
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit_relationship", rel_name),
            kind=NodeKind.RELATIONSHIP,
            origin_kind=_PBI_ORIGIN,
            origin_tag="relationship",
            name=rel_name,
            attributes={
                "from_table": from_table,
                "from_columns": [rel.get("fromColumn", "")],
                "to_table": to_table,
                "to_columns": [rel.get("toColumn", "")],
                "cardinality": _normalize_cardinality(rel.get("cardinality", "")),
                "cross_filter": rel.get("crossFilteringBehavior", "single").lower()
                if isinstance(rel.get("crossFilteringBehavior"), str)
                else "single",
                "is_active": rel.get("isActive", True),
            },
            raw_payload=_as_jsonable(rel),
        )

    def _pbit_role_node(self, role: dict[str, Any], idx: int) -> SourceNode:
        """Preserve TOM/TMSL model roles as source security metadata."""
        name = str(role.get("name") or f"role_{idx}")
        raw_permissions = role.get("tablePermissions", [])
        permissions = [
            {
                "table": str(permission.get("name") or ""),
                "filter_expression": str(permission.get("filterExpression") or ""),
            }
            for permission in raw_permissions
            if isinstance(permission, dict)
        ]
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "security_role", name),
            kind=NodeKind.FILTER,
            origin_kind=_PBI_ORIGIN,
            origin_tag="security_role",
            name=name,
            attributes={
                "model_permission": str(role.get("modelPermission") or ""),
                "table_permissions": permissions,
                "requires_manual_review": True,
            },
            raw_payload=_as_jsonable(role),
        )

    def _pbit_expression_node(self, expr: dict[str, Any], idx: int) -> SourceNode:
        ename = expr.get("name", f"expr_{idx}")
        return SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbit_expression", ename),
            kind=NodeKind.PARAMETER,
            origin_kind=_PBI_ORIGIN,
            origin_tag="expression",
            name=ename,
            attributes={
                "expression": expr.get("expression", ""),
                "kind": expr.get("kind", ""),
            },
            raw_payload=_as_jsonable(expr),
        )


# --- module-level helpers ----------------------------------------------------


def _decode_layout_bytes(data: bytes) -> tuple[str, str]:
    """Decode ``Report/Layout`` bytes, detecting UTF-16 LE/BE and UTF-8.

    Real PBIX/PBIT archives serialize the layout JSON as UTF-16 LE (with BOM,
    sometimes without). Returns ``(text, encoding_label)``. Raises
    ``UnicodeDecodeError`` when no known encoding produces valid text.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16-bom"
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig"), "utf-8-sig"
    # HeurÃ­stica sin BOM: JSON en UTF-16 muestra bytes NUL alternados (~50%).
    head = data[:2048]
    nulls = head.count(0)
    if nulls * 8 > len(head) and head:
        even = sum(1 for i in range(0, len(head), 2) if head[i] == 0)
        if nulls - even >= even:
            return data.decode("utf-16-le"), "utf-16-le"
        return data.decode("utf-16-be"), "utf-16-be"
    return data.decode("utf-8"), "utf-8"


def _layout_pages(data: Any, prefix: str) -> list[SourceNode]:
    """Map a parsed ``Report/Layout`` document into PAGE nodes (with visuals)."""
    sections = data.get("sections", []) if isinstance(data, dict) else []
    return [_layout_section_node(prefix, sec, idx) for idx, sec in enumerate(sections)]


def _layout_section_node(prefix: str, sec: dict[str, Any], idx: int) -> SourceNode:
    sname = sec.get("displayName") or sec.get("name", f"section_{idx}")
    containers = sec.get("visualContainers", [])
    children = [
        _layout_visual_node(prefix, vc, idx, vidx)
        for vidx, vc in enumerate(containers if isinstance(containers, list) else [])
    ]
    return SourceNode(
        node_id=stable_node_id(_PBI_ORIGIN, prefix, str(idx), sname),
        kind=NodeKind.PAGE,
        origin_kind=_PBI_ORIGIN,
        origin_tag="page",
        name=sname,
        children=tuple(children),
        raw_payload=_as_jsonable(sec),
    )


def _visual_type_of(vc: dict[str, Any]) -> str:
    """Extract ``singleVisual.visualType`` from a dict or JSON-string config."""
    config = vc.get("config")
    if isinstance(config, str):
        try:
            config = json.loads(config)
        except json.JSONDecodeError:
            return ""
    single = config.get("singleVisual", {}) if isinstance(config, dict) else {}
    return str(single.get("visualType", "")) if isinstance(single, dict) else ""


def _layout_visual_node(prefix: str, vc: dict[str, Any], section_idx: int, vidx: int) -> SourceNode:
    config = vc.get("config") if isinstance(vc.get("config"), str) else ""
    vtype = _visual_type_of(vc)
    return SourceNode(
        node_id=stable_node_id(_PBI_ORIGIN, prefix, str(section_idx), "visual", str(vidx)),
        kind=NodeKind.VISUAL,
        origin_kind=_PBI_ORIGIN,
        origin_tag="visual",
        name=vtype or f"visual_{vidx}",
        attributes={"visual_type": vtype, "config": config} if config else {"visual_type": vtype},
        raw_payload=_as_jsonable(vc),
    )


def _block_payload(block: TmdlBlock) -> dict[str, Any]:
    """Build a lossless JSON payload for a TMDL block."""
    return {
        "keyword": block.keyword,
        "name": block.name,
        "expression": block.expression,
        "properties": dict(block.properties),
        "children": [
            {
                "keyword": c.keyword,
                "name": c.name,
                "properties": dict(c.properties),
                "expression": c.expression,
            }
            for c in block.children
        ],
        "raw_text": block.raw_text,
    }


def _split_property_ref(ref: str) -> tuple[str, str]:
    """Split modern TMDL ``Table.Column`` references, preserving quoted names."""
    text = ref.strip()
    if "." not in text:
        return "", ""
    table, column = text.rsplit(".", 1)
    table = table.strip()
    column = column.strip()
    if len(table) >= 2 and table[0] == table[-1] == "'":
        table = table[1:-1].replace("''", "'")
    if len(column) >= 2 and column[0] == column[-1] == "'":
        column = column[1:-1].replace("''", "'")
    return table, column


def _parse_relationship_signature(signature: str) -> tuple[list[str], list[str], str, str]:
    """Parse ``'Table'[col] to 'Table2'[col2]`` into endpoints.

    Returns ``(from_cols, to_cols, from_table, to_table)``. Falls back to empty
    strings when the signature does not match the expected shape; the raw text
    is always preserved in the node payload.
    """
    if " to " not in signature:
        return [], [], "", ""
    left, _, right = signature.partition(" to ")
    ft, fc = _split_ref(left)
    tt, tc = _split_ref(right)
    return [fc], [tc], ft, tt


def _split_ref(ref: str) -> tuple[str, str]:
    """Split ``'Table'[Col]`` into ``("Table", "Col")``."""
    text = ref.strip()
    table = ""
    col = ""
    if "[" in text and text.endswith("]"):
        col = text[text.index("[") + 1 : -1]
        table = text[: text.index("[")].strip()
        table = table[1:-1] if table.startswith("'") and table.endswith("'") else table
    return table, col


def _normalize_cardinality(token: str) -> str:
    """Normalize a TOM cardinality token to the neutral form if known."""
    low = token.lower() if isinstance(token, str) else ""
    mapping = {
        "onetoone": "one_to_one",
        "onetomany": "one_to_many",
        "manytoone": "many_to_one",
        "manytomany": "many_to_many",
    }
    return mapping.get(low, token if isinstance(token, str) else "")


def _first_key(names: dict[str, str], wanted: str) -> str | None:
    """Return the archive member matching ``wanted`` (case-insensitive)."""
    return names.get(wanted)


def import_powerbi(
    source_path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> SourceAST:
    """Import a Power BI PBIP folder or PBIT package into a neutral :class:`SourceAST`.

    Args:
        source_path: Path to a ``.pbip`` entry file, a PBIP project folder, or a
            ``.pbit`` ZIP package. For a native ``.pbix`` use the explicit
            :func:`.pbix_partial.import_powerbi_pbix_partial` partial mode or
            :func:`import_powerbi_pbix` with pbi-tools.
        workspace_root: Optional anchor for the relative provenance path.

    Returns:
        Validated :class:`SourceAST` tree with ``origin_kind = power_bi``.
        ``metadata["data_sources"]`` carries the D012/D017 origin diagnosis
        (``pending_credentials`` / ``declared``; never ``connected``).
        All ``raw_payload`` and ``attributes`` values are sanitized so that
        embedded credentials (if any) are replaced with ``[REDACTED]``.
    """
    return PowerBIImporter(source_path, workspace_root=workspace_root).import_ast()


def import_powerbi_with_security(
    source_path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> tuple[SourceAST, CredentialScanReport]:
    """Import and return both the sanitized AST and a credential scan report.

    The scan inspects the raw artifact bytes before parsing, then the
    importer sanitizes all node payloads/attributes. The returned report
    contains only safe metadata (kind, severity, source, line, message);
    no secret values are exposed.

    Args:
        source_path: Path to the artifact (PBIP folder, PBIT, or PbixProj).
        workspace_root: Optional anchor for the relative provenance path.

    Returns:
        A ``(SourceAST, CredentialScanReport)`` tuple.
    """
    raw_report = scan_artifact(source_path)
    doc = import_powerbi(source_path, workspace_root=workspace_root)

    tree_findings: list = []
    sanitized_root = _sanitize_tree(doc.root, findings=tree_findings, source_prefix=doc.root.name)
    tree_report = CredentialScanReport(tuple(tree_findings))
    merged = CredentialScanReport(
        tuple(dict.fromkeys(raw_report.findings + tree_report.findings)),
        scanned_files=raw_report.scanned_files,
        redactions=raw_report.redactions + tree_report.redactions,
    )
    sanitized_doc = SourceAST(
        schema_version=doc.schema_version,
        origin_kind=doc.origin_kind,
        root=sanitized_root,
        provenance=doc.provenance,
        metadata=dict(doc.metadata),
    )
    sanitized_doc.validate()
    return sanitized_doc, merged


__all__ = [
    "PowerBIImporter",
    "import_powerbi",
    "import_powerbi_with_security",
]
