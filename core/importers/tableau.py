"""Tableau TWB/TWBX Importer for the canonical Source AST contract.

Parses Tableau TWB and TWBX workbook files into a neutral, origin-aware
:class:`core.contracts.source_ast.SourceAST` tree without loss.

Preserves:
* Datasources (:attr:`NodeKind.DATASOURCE`)
* Relations / Joins / Custom SQL (:attr:`NodeKind.RELATIONSHIP`)
* Fields / Columns (:attr:`NodeKind.FIELD`)
* Calculations (:attr:`NodeKind.CALCULATION`)
* Parameters (:attr:`NodeKind.PARAMETER`)
* Pages / Worksheets / Dashboards (:attr:`NodeKind.PAGE`)
* Zones / Containers (:attr:`NodeKind.LAYOUT_ZONE`)
* Marks / Encodings (:attr:`NodeKind.VISUAL`)
* Shelves (:attr:`NodeKind.LAYOUT_ZONE` / origin_tag="shelf")
* Filters (:attr:`NodeKind.FILTER`)
* Actions (origin_tag="action")
* Formatting (origin_tag="format" / "style")
* Unknown / Unmapped tags (:attr:`NodeKind.UNKNOWN` + raw_payload)

Design (Ponytail): Uses Python standard library (ET, hashlib, path) and
neutral core extractors/contracts. Does NOT import PBIP compiler modules.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from core.contracts.provenance import OriginKind, ProvenanceRecord, sanitize_relative_path
from core.contracts.source_ast import (
    SCHEMA_VERSION,
    NodeKind,
    SourceAST,
    SourceNode,
    stable_node_id,
)
from core.twbx_extractor import cleanup_temp_dir, extract_twb_from_twbx

_TWBX_SUFFIX = ".twbx"
_QUALIFIED_FIELD_RE = re.compile(r"\[[^\]]+\]\.\[([^\]]+)\]")
_QUALIFIED_FIELD_OWNER_RE = re.compile(r"\[([^\]]+)\]\.\[([^\]]+)\]")
_BRACKETED_FIELD_RE = re.compile(r"\[([^\]]+)\]")
_RELATION_FIELD_RE = re.compile(r"\[([^\]]+)\]\.\[([^\]]+)\]")
_WILDCARD_RE = re.compile(
    r"^\(?\s*(CONTAINS|STARTSWITH|ENDSWITH)\s*\(\s*(?:STR\s*\(\s*)?"
    r"\[[^\]]+\]\s*\)?\s*,\s*([\'\"])(.*)\2\s*\)\s*\)?$",
    re.IGNORECASE | re.DOTALL,
)
_WILDCARD_OPS = {
    "CONTAINS": "contains",
    "STARTSWITH": "starts_with",
    "ENDSWITH": "ends_with",
}


def _strip_ns(tag: str) -> str:
    """Return local tag name without XML namespace prefix."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _element_to_dict(elem: ET.Element) -> dict[str, Any]:
    """Convert an XML element and its children losslessly to a JSON-safe dict."""
    res: dict[str, Any] = {"tag": _strip_ns(elem.tag)}
    if elem.attrib:
        res["attrib"] = dict(elem.attrib)
    if elem.text and elem.text.strip():
        res["text"] = elem.text.strip()
    children = [_element_to_dict(child) for child in elem]
    if children:
        res["children"] = children
    return res


def _parse_wildcard_expression(expression: str) -> tuple[str, str] | None:
    """Parsea el subconjunto Tableau de CONTAINS/STARTSWITH/ENDSWITH."""

    value = expression.replace("&quot;", '"').strip()
    negated = bool(re.match(r"^\s*NOT\s+", value, re.IGNORECASE))
    if negated:
        value = re.sub(r"^\s*NOT\s+", "", value, count=1, flags=re.IGNORECASE)
    match = _WILDCARD_RE.match(value)
    if not match:
        return None
    operator = _WILDCARD_OPS[match.group(1).upper()]
    return (f"not_{operator}" if negated else operator, match.group(3))


def _local_attribute(element: ET.Element, name: str) -> str:
    """Obtiene un atributo aunque Tableau lo serialice con namespace XML."""

    return next(
        (str(value) for key, value in element.attrib.items() if key.rsplit("}", 1)[-1] == name),
        "",
    )


def _compute_sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of file at ``path``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _field_references(text: str) -> list[str]:
    """Extract stable field names from a Tableau shelf/encoding expression."""
    qualified = _QUALIFIED_FIELD_RE.findall(text)
    if qualified:
        return list(dict.fromkeys(qualified))
    return list(dict.fromkeys(_BRACKETED_FIELD_RE.findall(text)))


def _last_nonempty_shelf(parent: ET.Element, tag: str) -> ET.Element | None:
    """Return the last descendant shelf element (rows/cols) with non-empty text.

    Real Tableau workbooks often emit empty auxiliary ``<rows>``/``<cols>``
    placeholders (typically inside ``<view>``) before the real shelf, whose
    field expression lives in a sibling element (often a direct child of
    ``<table>``). ``Element.find`` returns the first direct child, which would
    resolve to the empty placeholder and drop every worksheet binding.

    Mirrors the proven legacy rule (``worksheet.findall('.//rows')``, last
    non-empty) so raw importer output never classifies a worksheet as unbound
    because of an empty placeholder shelf.
    """
    candidates = [
        element
        for element in parent.iter()
        if _strip_ns(element.tag).lower() == tag and (element.text or "").strip()
    ]
    return candidates[-1] if candidates else None


def _worksheet_title(element: ET.Element, fallback: str) -> str:
    """Recupera el tÃƒÂ­tulo formateado, sustituyendo el placeholder de Tableau."""

    for title in element.iter():
        if _strip_ns(title.tag) != "title":
            continue
        text = "".join(title.itertext()).strip()
        if text:
            return text.replace("<Sheet Name>", fallback)
    return fallback


def _tableau_literal(text: str) -> object:
    value = text.strip()
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value[1:-1].replace('\\"', '"').replace("\\'", "'")
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("\\'", "'").replace('\\"', '"')
    if value.casefold() in {"true", "false"}:
        return value.casefold() == "true"
    try:
        return float(value) if any(marker in value for marker in ".eE") else int(value)
    except ValueError:
        return value


def _pane_encodings(pane: ET.Element) -> dict[str, list[str]]:
    """Project pane encoding nodes to neutral channel -> field lists."""
    encodings: dict[str, list[str]] = {}
    for element in pane.iter():
        channel = _strip_ns(element.tag).lower()
        if channel not in {"color", "detail", "geometry", "label", "lod", "path", "size", "text"}:
            continue
        raw = element.attrib.get("column") or element.attrib.get("field") or ""
        fields = _field_references(raw)
        if fields:
            encodings.setdefault(channel, []).extend(fields)
    return {key: list(dict.fromkeys(values)) for key, values in encodings.items()}


def _forecast_node(
    element: ET.Element,
    worksheet_name: str,
    table: ET.Element,
) -> SourceNode:
    """Normaliza una especificaciÃƒÂ³n predictiva sin acoplarla al runtime destino."""
    attributes = element.attrib
    fill_type = str(attributes.get("fill-type", ""))
    columns = table.findtext("cols") or table.findtext("{*}cols") or ""
    period = next(
        (
            value
            for token, value in (
                ("mn:", "month"),
                ("qr:", "quarter"),
                ("wk:", "week"),
                ("dy:", "day"),
                ("yr:", "year"),
            )
            if token in columns.lower()
        ),
        "month",
    )
    return SourceNode(
        node_id=stable_node_id(OriginKind.TABLEAU, "worksheet", worksheet_name, "forecast"),
        kind=NodeKind.FORECAST,
        origin_kind=OriginKind.TABLEAU,
        origin_tag="forecast-specification",
        name="forecast",
        attributes={
            "enabled": str(attributes.get("enabled", "false")).lower() == "true",
            "confidence_level": float(attributes.get("band-confidence-level", 95)),
            "fill_missing": fill_type == "fill-missing",
            "ignore_last": int(attributes.get("ignore-last", 0)),
            "model_family": str(attributes.get("model-type", "auto")),
            "range": str(attributes.get("range-type", "auto")),
            "prediction_intervals": (
                str(attributes.get("show-prediction-bands", "false")).lower() == "true"
            ),
            "aggregate_before_fit": (
                str(attributes.get("auto-forecast-agg", "false")).lower() == "true"
            ),
            "period": period,
        },
        raw_payload=_element_to_dict(element),
    )


def _packaged_files(datasource: ET.Element) -> list[str]:
    """Return safe relative data members referenced by a datasource."""
    members: list[str] = []
    for element in datasource.iter():
        if _strip_ns(element.tag) != "connection":
            continue
        for key in ("dbname", "filename"):
            value = str(element.attrib.get(key, "")).replace("\\", "/").strip()
            path = PurePosixPath(value)
            if (
                value
                and not path.is_absolute()
                and not re.match(r"^[A-Za-z]:/", value)
                and ".." not in path.parts
                and path.suffix.lower() in {".csv", ".hyper", ".xls", ".xlsx"}
            ):
                members.append(value)
    return list(dict.fromkeys(members))


def _relation_spec(element: ET.Element) -> dict[str, Any] | None:
    if _strip_ns(element.tag) != "relation":
        return None
    relations = [child for child in element if _strip_ns(child.tag) == "relation"]
    if element.attrib.get("type") == "table":
        name = str(element.attrib.get("name") or element.attrib.get("table") or "").strip("[]")
        return {"kind": "table", "name": name} if name else None
    if len(relations) != 2:
        return None
    clause = next((child for child in element if _strip_ns(child.tag) == "clause"), None)
    root_expression = next(iter(clause), None) if clause is not None else None
    operands = list(root_expression) if root_expression is not None else []
    if len(operands) != 2:
        return None
    references = [
        _RELATION_FIELD_RE.fullmatch(str(operand.attrib.get("op", ""))) for operand in operands
    ]
    if any(reference is None for reference in references):
        return None
    left_reference, right_reference = references
    assert left_reference is not None and right_reference is not None
    left = _relation_spec(relations[0])
    right = _relation_spec(relations[1])
    if left is None or right is None:
        return None
    return {
        "kind": "join",
        "join": str(element.attrib.get("join", "inner")).lower(),
        "left": left,
        "right": right,
        "left_table": left_reference.group(1),
        "left_field": left_reference.group(2),
        "right_table": right_reference.group(1),
        "right_field": right_reference.group(2),
    }


def _relation_leaf_count(spec: dict[str, Any]) -> int:
    if spec.get("kind") == "table":
        return 1
    return _relation_leaf_count(spec["left"]) + _relation_leaf_count(spec["right"])


def _federation_spec(datasource: ET.Element) -> dict[str, Any] | None:
    candidates: dict[str, dict[str, Any]] = {}
    for element in datasource.iter():
        spec = _relation_spec(element)
        if spec is not None:
            candidates[json.dumps(spec, sort_keys=True)] = spec
    if not candidates:
        return None
    return max(candidates.values(), key=_relation_leaf_count)


def _dashboard_control(zone: SourceNode) -> dict[str, Any] | None:
    """Return a neutral filter/parameter control for a Tableau dashboard zone."""
    zone_type = str(zone.attributes.get("type") or zone.attributes.get("type-v2") or "").lower()
    if zone_type not in {"filter", "paramctrl", "parameter", "quickfilter"}:
        return None
    raw_target = str(
        zone.attributes.get("param")
        or zone.attributes.get("field")
        or zone.attributes.get("name")
        or ""
    )
    fields = _field_references(raw_target)
    target = fields[-1] if fields else raw_target.strip()
    if not target:
        return None
    layout = {
        "x": float(zone.attributes.get("x", 0) or 0),
        "y": float(zone.attributes.get("y", 0) or 0),
        "width": float(zone.attributes.get("w", zone.attributes.get("width", 0)) or 0),
        "height": float(zone.attributes.get("h", zone.attributes.get("height", 0)) or 0),
        "z_index": int(zone.attributes.get("z_order", 0) or 0),
        "layout_mode": "floating" if zone.attributes.get("floating") else "tiled",
    }
    key = "parameter" if zone_type in {"paramctrl", "parameter"} else "field"
    control = {
        key: target,
        "title": str(zone.attributes.get("title") or target),
        "type": zone_type,
        "layout": layout,
    }
    if key == "field":
        qualified = _QUALIFIED_FIELD_OWNER_RE.search(raw_target)
        if qualified:
            control["datasource"] = qualified.group(1).strip()
    return control


def _sanitize_workbook_path(source_path: Path, workspace_root: Path | None) -> str:
    """Return sanitized relative path for provenance."""
    root = (workspace_root or Path.cwd()).resolve()
    try:
        relative = source_path.resolve().relative_to(root)
        candidate = relative.as_posix()
    except ValueError:
        candidate = source_path.name
    return sanitize_relative_path(candidate)


@dataclass(frozen=True)
class TableauPackage:
    """Una extracciÃƒÂ³n Tableau abierta y reutilizable durante toda la compilaciÃƒÂ³n."""

    source_path: Path
    workbook_path: Path
    data_root: Path
    artifact_hash: str


@contextmanager
def open_tableau_package(source_path: str | Path) -> Iterator[TableauPackage]:
    """Abre TWB/TWBX una sola vez y limpia sÃƒÂ³lo al abandonar el contexto."""
    source = Path(source_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")
    temporary: str | None = None
    workbook = source
    try:
        if source.suffix.lower() == _TWBX_SUFFIX:
            with source.open("rb") as handle:
                workbook_text, temporary = extract_twb_from_twbx(handle)
            workbook = Path(workbook_text).resolve()
        yield TableauPackage(
            source_path=source,
            workbook_path=workbook,
            data_root=Path(temporary).resolve() if temporary else source.parent,
            artifact_hash=_compute_sha256(source),
        )
    finally:
        if temporary is not None:
            cleanup_temp_dir(temporary)



def _hierarchy_navigator_configs(dashboard: ET.Element) -> list[dict[str, Any]]:
    """Extract unique Hierarchy Navigator extension configs from a dashboard."""
    configs: list[dict[str, Any]] = []
    seen: set[str] = set()
    geometry_by_addin: dict[int, dict[str, str]] = {}
    for zone in dashboard.iter():
        if _strip_ns(zone.tag) != "zone":
            continue
        for child in zone:
            if _strip_ns(child.tag) == "add-in":
                geometry_by_addin[id(child)] = dict(zone.attrib)
    for addin in dashboard.iter():
        if _strip_ns(addin.tag) != "add-in":
            continue
        identity = " ".join(str(value) for value in addin.attrib.values()).casefold()
        if "hierarchynavigator" not in identity and "hierarchy-navigator" not in identity:
            continue
        instance_id = str(addin.attrib.get("instance-id") or "")
        if instance_id and instance_id in seen:
            continue
        raw_config = ""
        for setting in addin.iter():
            if _strip_ns(setting.tag) == "setting" and setting.attrib.get("key") == "data":
                raw_config = str(setting.attrib.get("value") or "")
                break
        if not raw_config:
            continue
        try:
            config = json.loads(raw_config)
        except json.JSONDecodeError:
            continue
        if not isinstance(config, dict):
            continue
        if instance_id:
            seen.add(instance_id)
        record: dict[str, Any] = {"instance_id": instance_id, "config": config}
        geometry = geometry_by_addin.get(id(addin))
        if geometry:
            record["geometry"] = geometry
        configs.append(record)
    return configs


class TableauImporter:
    """Importer for Tableau TWB/TWBX workbooks to neutral SourceAST."""

    def __init__(self, source_path: str | Path, workspace_root: str | Path | None = None) -> None:
        self.source_path = Path(source_path).resolve()
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        if not self.source_path.is_file():
            raise FileNotFoundError(f"Source file not found: {source_path}")

    def import_ast(self) -> SourceAST:
        """Parse workbook into a canonical SourceAST instance."""
        with open_tableau_package(self.source_path) as package:
            return self.import_package(package)

    def import_package(self, package: TableauPackage) -> SourceAST:
        """Parse an already-open package so data and metadata share one extraction."""
        tree = ET.parse(package.workbook_path)
        root_elem = tree.getroot()

        relative_path = _sanitize_workbook_path(self.source_path, self.workspace_root)
        provenance = ProvenanceRecord(
            artifact_path=relative_path,
            artifact_hash=package.artifact_hash,
            origin=OriginKind.TABLEAU.value,
        )

        ast_root = self._parse_workbook(root_elem)
        doc = SourceAST(
            schema_version=SCHEMA_VERSION,
            origin_kind=OriginKind.TABLEAU,
            root=ast_root,
            provenance=provenance,
            metadata={"source_name": self.source_path.name},
        )
        doc.validate()
        return doc

    def _parse_workbook(self, root_elem: ET.Element) -> SourceNode:
        wb_name = self.source_path.stem
        wb_node_id = stable_node_id(OriginKind.TABLEAU, "workbook", wb_name)

        children: list[SourceNode] = []
        counter: int = 0

        for child in root_elem:
            tag = _strip_ns(child.tag)
            if tag == "datasources":
                children.extend(self._parse_datasources(child, wb_name))
            elif tag == "worksheets":
                children.extend(self._parse_worksheets(child, wb_name))
            elif tag == "dashboards":
                children.extend(self._parse_dashboards(child, wb_name))
            elif tag == "actions":
                children.extend(self._parse_actions(child, wb_name, "workbook"))
            elif tag in ("style", "format"):
                counter += 1
                children.append(self._parse_format(child, wb_name, "workbook", counter))
            else:
                counter += 1
                node_id = stable_node_id(OriginKind.TABLEAU, "workbook", wb_name, tag, str(counter))
                children.append(
                    SourceNode(
                        node_id=node_id,
                        kind=NodeKind.UNKNOWN,
                        origin_kind=OriginKind.TABLEAU,
                        origin_tag=tag,
                        name=tag,
                        attributes=dict(child.attrib),
                        raw_payload=_element_to_dict(child),
                    )
                )

        dependencies: dict[str, list[ET.Element]] = {}
        for element in root_elem.iter():
            if _strip_ns(element.tag) != "datasource-dependencies":
                continue
            datasource = str(element.attrib.get("datasource", ""))
            if not datasource:
                continue
            dependencies.setdefault(datasource, []).extend(
                child for child in element if _strip_ns(child.tag) == "column"
            )
        merged: list[SourceNode] = []
        for node in children:
            if node.kind is not NodeKind.DATASOURCE or node.name not in dependencies:
                merged.append(node)
                continue
            existing = {child.name for child in node.children}
            additions: list[SourceNode] = []
            for index, element in enumerate(dependencies[node.name]):
                name = str(element.attrib.get("name") or element.attrib.get("caption") or "")
                if not name or name in existing:
                    continue
                additions.append(self._parse_column(element, node.name, 100_000 + index))
                existing.add(name)
            merged.append(replace(node, children=(*node.children, *additions)))
        children = merged

        return SourceNode(
            node_id=wb_node_id,
            kind=NodeKind.DOCUMENT,
            origin_kind=OriginKind.TABLEAU,
            origin_tag="workbook",
            name=wb_name,
            attributes={
                "version": root_elem.attrib.get("version", ""),
                "attrib": dict(root_elem.attrib),
            },
            children=tuple(children),
        )

    def _parse_datasources(self, elem: ET.Element, wb_name: str) -> list[SourceNode]:
        nodes: list[SourceNode] = []
        for idx, ds_elem in enumerate(elem):
            ds_name = ds_elem.attrib.get("name") or ds_elem.attrib.get("caption") or f"ds_{idx}"
            ds_node_id = stable_node_id(OriginKind.TABLEAU, "datasource", ds_name)

            children: list[SourceNode] = []
            drill_paths: list[dict[str, Any]] = []
            c_idx = 0

            for child in ds_elem:
                ctag = _strip_ns(child.tag)
                if ctag in ("relation", "connection"):
                    c_idx += 1
                    children.append(self._parse_relation(child, ds_name, c_idx))
                elif ctag == "column":
                    c_idx += 1
                    children.append(self._parse_column(child, ds_name, c_idx))
                elif ctag == "group":
                    c_idx += 1
                    children.append(self._parse_set(child, ds_name, c_idx))
                elif ctag == "filter":
                    c_idx += 1
                    children.append(self._parse_filter(child, ds_name, "datasource", c_idx))
                elif ctag == "drill-paths":
                    for drill_path in child:
                        if _strip_ns(drill_path.tag) != "drill-path":
                            continue
                        levels: list[str] = []
                        for field in drill_path:
                            if _strip_ns(field.tag) != "field" or not field.text:
                                continue
                            refs = _field_references(field.text.strip())
                            if refs:
                                levels.append(refs[-1])
                        if len(levels) >= 2:
                            drill_paths.append({
                                "name": str(drill_path.attrib.get("name") or ""),
                                "levels": levels,
                            })
                elif ctag in ("style", "format"):
                    c_idx += 1
                    children.append(self._parse_format(child, ds_name, "datasource", c_idx))
                else:
                    c_idx += 1
                    nid = stable_node_id(
                        OriginKind.TABLEAU, "datasource", ds_name, ctag, str(c_idx)
                    )
                    children.append(
                        SourceNode(
                            node_id=nid,
                            kind=NodeKind.UNKNOWN,
                            origin_kind=OriginKind.TABLEAU,
                            origin_tag=ctag,
                            name=ctag,
                            attributes=dict(child.attrib),
                            raw_payload=_element_to_dict(child),
                        )
                    )

            attributes: dict[str, Any] = dict(ds_elem.attrib)
            if drill_paths:
                attributes["drill_paths"] = drill_paths
            packaged_files = _packaged_files(ds_elem)
            if packaged_files:
                attributes["packaged_files"] = packaged_files
            federation = _federation_spec(ds_elem)
            if federation is not None:
                attributes["federation"] = federation
            nodes.append(
                SourceNode(
                    node_id=ds_node_id,
                    kind=NodeKind.DATASOURCE,
                    origin_kind=OriginKind.TABLEAU,
                    origin_tag="datasource",
                    name=ds_name,
                    attributes=attributes,
                    children=tuple(children),
                )
            )
        return nodes

    def _parse_set(self, elem: ET.Element, ds_name: str, idx: int) -> SourceNode:
        name = str(elem.attrib.get("name") or elem.attrib.get("caption") or f"set_{idx}")
        target = ""
        members: list[object] = []
        for group_filter in elem.iter():
            if _strip_ns(group_filter.tag) != "groupfilter":
                continue
            level = str(group_filter.attrib.get("level", ""))
            if level and not target:
                fields = _field_references(level)
                target = fields[-1] if fields else ""
            member = str(group_filter.attrib.get("member", ""))
            if member:
                fields = _field_references(member)
                if fields and not target:
                    target = fields[-1]
                if str(group_filter.attrib.get("function", "")) != "empty-level":
                    members.append(_tableau_literal(member))
        attributes: dict[str, Any] = dict(elem.attrib)
        security_expressions = [
            str(group_filter.attrib.get("expression") or "")
            for group_filter in elem.iter()
            if _strip_ns(group_filter.tag) == "groupfilter"
            and str(group_filter.attrib.get("expression") or "")
            and re.search(
                r"\b(?:ISCURRENTUSER|ISMEMBEROF|USERNAME|FULLNAME|USERDOMAIN|USERATTRIBUTE)\s*\(",
                str(group_filter.attrib.get("expression") or ""),
                re.IGNORECASE,
            )
        ]
        is_identity_set = _local_attribute(elem, "ui-builder").casefold() == "identity-set"
        attributes.update({"set_target": target, "default_members": members})
        if is_identity_set or security_expressions:
            attributes["security_policy"] = True
            attributes["security_kind"] = "tableau_identity_set"
            attributes["security_expressions"] = security_expressions
            attributes["requires_manual_review"] = True
        return SourceNode(
            node_id=stable_node_id(OriginKind.TABLEAU, "datasource", ds_name, "set", name),
            kind=NodeKind.PARAMETER,
            origin_kind=OriginKind.TABLEAU,
            origin_tag="set",
            name=name,
            attributes=attributes,
            raw_payload=_element_to_dict(elem),
        )

    def _parse_relation(self, elem: ET.Element, ds_name: str, idx: int) -> SourceNode:
        rel_name = elem.attrib.get("name") or elem.attrib.get("table") or f"rel_{idx}"
        nid = stable_node_id(
            OriginKind.TABLEAU, "datasource", ds_name, "relation", rel_name, str(idx)
        )

        children: list[SourceNode] = []
        c_idx = 0
        for child in elem:
            ctag = _strip_ns(child.tag)
            if ctag in ("relation", "connection"):
                c_idx += 1
                children.append(self._parse_relation(child, ds_name, idx * 100 + c_idx))

        attributes: dict[str, Any] = dict(elem.attrib)
        if elem.text and elem.text.strip():
            attributes["text"] = elem.text.strip()

        return SourceNode(
            node_id=nid,
            kind=NodeKind.RELATIONSHIP,
            origin_kind=OriginKind.TABLEAU,
            origin_tag="relation",
            name=rel_name,
            attributes=attributes,
            children=tuple(children),
        )

    def _parse_column(self, elem: ET.Element, ds_name: str, idx: int) -> SourceNode:
        col_name = elem.attrib.get("name") or elem.attrib.get("caption") or f"col_{idx}"
        calc_elem = elem.find("calculation") or elem.find("{*}calculation")

        is_param = bool(elem.attrib.get("param-domain-type")) or ds_name.lower() == "parameters"

        if is_param:
            kind = NodeKind.PARAMETER
            origin_tag = "parameter"
        elif calc_elem is not None or "formula" in elem.attrib:
            kind = NodeKind.CALCULATION
            origin_tag = "calculation"
        else:
            kind = NodeKind.FIELD
            origin_tag = "column"

        nid = stable_node_id(
            OriginKind.TABLEAU, "datasource", ds_name, origin_tag, col_name, str(idx)
        )
        attributes: dict[str, Any] = dict(elem.attrib)

        if calc_elem is not None:
            attributes["calculation"] = dict(calc_elem.attrib)
            if calc_elem.attrib.get("formula"):
                attributes["formula"] = calc_elem.attrib["formula"]
            table_calc = next(
                (child for child in calc_elem if _strip_ns(child.tag) == "table-calc"),
                None,
            )
            if table_calc is not None:
                order_fields: list[str] = []
                for order in table_calc.iter():
                    if _strip_ns(order.tag) != "order":
                        continue
                    refs = _field_references(str(order.attrib.get("field") or ""))
                    if refs:
                        order_fields.append(refs[-1])
                attributes["table_calc"] = {
                    "ordering_type": str(table_calc.attrib.get("ordering-type") or ""),
                    "order_fields": order_fields,
                }
            if calc_elem.attrib.get("class") == "categorical-bin":
                mapping: dict[str, object] = {}
                for bin_element in calc_elem:
                    if _strip_ns(bin_element.tag) != "bin":
                        continue
                    target = _tableau_literal(str(bin_element.attrib.get("value", "")))
                    for value_element in bin_element:
                        if _strip_ns(value_element.tag) == "value" and value_element.text:
                            mapping[str(_tableau_literal(value_element.text))] = target
                source = _field_references(str(calc_elem.attrib.get("column", "")))
                if source:
                    attributes["group_source"] = source[-1]
                    attributes["group_mapping"] = mapping

        return SourceNode(
            node_id=nid,
            kind=kind,
            origin_kind=OriginKind.TABLEAU,
            origin_tag=origin_tag,
            name=col_name,
            attributes=attributes,
        )

    def _parse_worksheets(self, elem: ET.Element, wb_name: str) -> list[SourceNode]:
        nodes: list[SourceNode] = []
        for idx, ws_elem in enumerate(elem):
            ws_name = ws_elem.attrib.get("name") or f"sheet_{idx}"
            ws_node_id = stable_node_id(OriginKind.TABLEAU, "worksheet", ws_name)

            children: list[SourceNode] = []
            c_idx = 0
            worksheet_attributes: dict[str, Any] = {
                **dict(ws_elem.attrib),
                "title": _worksheet_title(ws_elem, ws_name),
            }
            neutral_encodings: dict[str, list[str]] = {}

            table_elem = ws_elem.find("table") or ws_elem.find("{*}table")
            if table_elem is not None:
                view_elem = table_elem.find("view") or table_elem.find("{*}view")
                panes_elem = table_elem.find("panes") or table_elem.find("{*}panes")
                forecast_elem = table_elem.find("forecast-specification")
                if forecast_elem is None:
                    forecast_elem = table_elem.find("{*}forecast-specification")
                if forecast_elem is not None:
                    children.append(_forecast_node(forecast_elem, ws_name, table_elem))

                if panes_elem is not None:
                    for p_idx, pane in enumerate(panes_elem):
                        c_idx += 1
                        mark_elem = pane.find("mark") or pane.find("{*}mark")
                        mark_type = (
                            mark_elem.attrib.get("class")
                            if mark_elem is not None
                            else pane.attrib.get("mark-class", "automatic")
                        )
                        mnid = stable_node_id(
                            OriginKind.TABLEAU, "worksheet", ws_name, "mark", str(p_idx), str(c_idx)
                        )
                        children.append(
                            SourceNode(
                                node_id=mnid,
                                kind=NodeKind.VISUAL,
                                origin_kind=OriginKind.TABLEAU,
                                origin_tag="mark",
                                name=mark_type or "automatic",
                                attributes=dict(pane.attrib),
                                raw_payload=_element_to_dict(pane),
                            )
                        )
                        for channel, fields in _pane_encodings(pane).items():
                            neutral_encodings.setdefault(channel, []).extend(fields)

                if view_elem is not None:
                    dependencies = [
                        dep.attrib.get("datasource", "")
                        for dep in view_elem
                        if _strip_ns(dep.tag) == "datasource-dependencies"
                        and dep.attrib.get("datasource")
                    ]
                    if dependencies:
                        worksheet_attributes["datasources"] = list(dict.fromkeys(dependencies))

                # Resolve the real (non-empty) shelf among any empty placeholders
                # so worksheet bindings are never dropped (G1-AST-001 L-1).
                rows_elem = _last_nonempty_shelf(table_elem, "rows")
                cols_elem = _last_nonempty_shelf(table_elem, "cols")

                for shelf_tag, shelf_elem in (("rows", rows_elem), ("cols", cols_elem)):
                    if shelf_elem is not None:
                        c_idx += 1
                        snid = stable_node_id(
                            OriginKind.TABLEAU,
                            "worksheet",
                            ws_name,
                            "shelf",
                            shelf_tag,
                            str(c_idx),
                        )
                        text = shelf_elem.text.strip() if shelf_elem.text else ""
                        worksheet_attributes["columns" if shelf_tag == "cols" else "rows"] = (
                            _field_references(text)
                        )
                        children.append(
                            SourceNode(
                                node_id=snid,
                                kind=NodeKind.LAYOUT_ZONE,
                                origin_kind=OriginKind.TABLEAU,
                                origin_tag="shelf",
                                name=shelf_tag,
                                attributes={"text": text, **dict(shelf_elem.attrib)},
                            )
                        )

            if neutral_encodings:
                worksheet_attributes["encodings"] = {
                    channel: list(dict.fromkeys(fields))
                    for channel, fields in neutral_encodings.items()
                }

            # Tableau stores worksheet filters inside <table>/<view>, not only
            # as direct worksheet children. Preserve every nested declaration.
            if table_elem is not None:
                for filter_elem in table_elem.iter():
                    if _strip_ns(filter_elem.tag) != "filter":
                        continue
                    c_idx += 1
                    children.append(self._parse_filter(filter_elem, ws_name, "worksheet", c_idx))

            for child in ws_elem:
                ctag = _strip_ns(child.tag)
                if ctag == "table":
                    continue
                elif ctag == "filter":
                    c_idx += 1
                    children.append(self._parse_filter(child, ws_name, "worksheet", c_idx))
                elif ctag in ("style", "format"):
                    c_idx += 1
                    children.append(self._parse_format(child, ws_name, "worksheet", c_idx))
                else:
                    c_idx += 1
                    nid = stable_node_id(OriginKind.TABLEAU, "worksheet", ws_name, ctag, str(c_idx))
                    children.append(
                        SourceNode(
                            node_id=nid,
                            kind=NodeKind.UNKNOWN,
                            origin_kind=OriginKind.TABLEAU,
                            origin_tag=ctag,
                            name=ctag,
                            attributes=dict(child.attrib),
                            raw_payload=_element_to_dict(child),
                        )
                    )

            nodes.append(
                SourceNode(
                    node_id=ws_node_id,
                    kind=NodeKind.PAGE,
                    origin_kind=OriginKind.TABLEAU,
                    origin_tag="worksheet",
                    name=ws_name,
                    attributes=worksheet_attributes,
                    children=tuple(children),
                )
            )
        return nodes

    def _parse_dashboards(self, elem: ET.Element, wb_name: str) -> list[SourceNode]:
        nodes: list[SourceNode] = []
        for idx, db_elem in enumerate(elem):
            db_name = db_elem.attrib.get("name") or f"dash_{idx}"
            db_node_id = stable_node_id(OriginKind.TABLEAU, "dashboard", db_name)

            children: list[SourceNode] = []
            c_idx = 0

            for child in db_elem:
                ctag = _strip_ns(child.tag)
                if ctag == "zones":
                    for z_idx, z_elem in enumerate(child):
                        c_idx += 1
                        children.append(self._parse_zone(z_elem, db_name, f"z_{z_idx}_{c_idx}"))
                elif ctag == "actions":
                    children.extend(self._parse_actions(child, db_name, "dashboard"))
                elif ctag in ("style", "format"):
                    c_idx += 1
                    children.append(self._parse_format(child, db_name, "dashboard", c_idx))
                else:
                    c_idx += 1
                    nid = stable_node_id(OriginKind.TABLEAU, "dashboard", db_name, ctag, str(c_idx))
                    children.append(
                        SourceNode(
                            node_id=nid,
                            kind=NodeKind.UNKNOWN,
                            origin_kind=OriginKind.TABLEAU,
                            origin_tag=ctag,
                            name=ctag,
                            attributes=dict(child.attrib),
                            raw_payload=_element_to_dict(child),
                        )
                    )

            controls = [
                control
                for zone in children
                for nested in self._walk_zones(zone)
                if (control := _dashboard_control(nested)) is not None
            ]
            dashboard_attributes: dict[str, Any] = dict(db_elem.attrib)
            if controls:
                dashboard_attributes["controls"] = controls
            hierarchy_navigators = _hierarchy_navigator_configs(db_elem)
            if hierarchy_navigators:
                dashboard_attributes["hierarchy_navigators"] = hierarchy_navigators

            nodes.append(
                SourceNode(
                    node_id=db_node_id,
                    kind=NodeKind.PAGE,
                    origin_kind=OriginKind.TABLEAU,
                    origin_tag="dashboard",
                    name=db_name,
                    attributes=dashboard_attributes,
                    children=tuple(children),
                )
            )
        return nodes

    @staticmethod
    def _walk_zones(node: SourceNode):
        """Yield a layout zone and its nested zones."""
        if node.kind is NodeKind.LAYOUT_ZONE:
            yield node
        for child in node.children:
            yield from TableauImporter._walk_zones(child)

    def _parse_zone(self, elem: ET.Element, db_name: str, path_key: str) -> SourceNode:
        zone_id = elem.attrib.get("id") or path_key
        zone_name = elem.attrib.get("name") or elem.attrib.get("type") or zone_id
        nid = stable_node_id(OriginKind.TABLEAU, "dashboard", db_name, "zone", zone_id, path_key)

        children: list[SourceNode] = []
        for idx, child in enumerate(elem):
            ctag = _strip_ns(child.tag)
            if ctag == "zone":
                children.append(self._parse_zone(child, db_name, f"{path_key}_{idx}"))

        return SourceNode(
            node_id=nid,
            kind=NodeKind.LAYOUT_ZONE,
            origin_kind=OriginKind.TABLEAU,
            origin_tag="zone",
            name=zone_name,
            attributes=dict(elem.attrib),
            children=tuple(children),
        )

    def _parse_filter(
        self, elem: ET.Element, owner_name: str, owner_type: str, idx: int
    ) -> SourceNode:
        fname = elem.attrib.get("column") or elem.attrib.get("name") or f"filter_{idx}"
        nid = stable_node_id(OriginKind.TABLEAU, owner_type, owner_name, "filter", fname, str(idx))
        attributes: dict[str, Any] = dict(elem.attrib)
        group_filters = [
            group_filter
            for group_filter in elem.iter()
            if _strip_ns(group_filter.tag) == "groupfilter"
        ]
        member_filters = [
            group_filter
            for group_filter in group_filters
            if group_filter.attrib.get("function") == "member" and "member" in group_filter.attrib
        ]
        member_values = [
            _tableau_literal(str(group_filter.attrib["member"])) for group_filter in member_filters
        ]
        if member_values:
            exclusive = any(
                _local_attribute(group_filter, "ui-enumeration").casefold() == "exclusive"
                for group_filter in group_filters
            ) or any(
                group_filter.attrib.get("function") == "except" for group_filter in group_filters
            )
            attributes["operator"] = (
                "ne"
                if exclusive and len(member_values) == 1
                else "not_in"
                if exclusive
                else "eq"
                if len(member_values) == 1
                else "in"
            )
            attributes["values"] = member_values
        range_element = next(
            (child for child in elem.iter() if _strip_ns(child.tag) == "range"),
            None,
        )
        if range_element is not None:
            lower = range_element.attrib.get("from", range_element.attrib.get("min"))
            upper = range_element.attrib.get("to", range_element.attrib.get("max"))
            if lower not in {None, ""} and upper not in {None, ""}:
                attributes["operator"] = "between"
                attributes["values"] = [
                    _tableau_literal(str(lower)),
                    _tableau_literal(str(upper)),
                ]
                attributes["filter_kind"] = "range"
            elif lower not in {None, ""}:
                attributes["operator"] = "gte"
                attributes["values"] = [_tableau_literal(str(lower))]
                attributes["filter_kind"] = "range"
            elif upper not in {None, ""}:
                attributes["operator"] = "lte"
                attributes["values"] = [_tableau_literal(str(upper))]
                attributes["filter_kind"] = "range"

        top_filter = next(
            (
                group_filter
                for group_filter in group_filters
                if group_filter.attrib.get("function") == "end"
            ),
            None,
        )
        if top_filter is not None:
            order_filter = next(
                (
                    group_filter
                    for group_filter in top_filter.iter()
                    if _strip_ns(group_filter.tag) == "groupfilter"
                    and group_filter.attrib.get("function") == "order"
                ),
                None,
            )
            attributes["filter_kind"] = "top_n"
            attributes["top_n"] = {
                "end": str(top_filter.attrib.get("end") or "top").lower(),
                "count": _tableau_literal(str(top_filter.attrib.get("count") or "")),
                "units": str(top_filter.attrib.get("units") or "records").lower(),
                "direction": str(
                    order_filter.attrib.get("direction") if order_filter is not None else ""
                ).lower(),
                "expression": str(
                    order_filter.attrib.get("expression") if order_filter is not None else ""
                ),
            }
            attributes["manual_reason"] = "top_n execution pending"

        raw_class = str(attributes.get("class") or attributes.get("filter_type") or "")
        if raw_class.casefold() == "relative-date" or any(
            key in attributes
            for key in ("first-period", "last-period", "period-type", "period-type-v2")
        ):
            attributes["filter_kind"] = "relative_date"
            attributes["relative_date"] = {
                "first_period": attributes.get("first-period"),
                "last_period": attributes.get("last-period"),
                "period": attributes.get("period-type-v2", attributes.get("period-type")),
            }
            attributes["manual_reason"] = "relative-date execution pending"

        for group_filter in group_filters:
            expression = str(group_filter.attrib.get("expression") or "")
            if group_filter.attrib.get("function") != "filter" or not expression:
                continue
            wildcard = _parse_wildcard_expression(expression)
            if wildcard:
                attributes["operator"], pattern = wildcard
                attributes["values"] = [pattern]
                attributes["filter_kind"] = "wildcard"
            elif re.search(r"\b(CONTAINS|STARTSWITH|ENDSWITH)\s*\(", expression, re.I):
                attributes["filter_kind"] = "wildcard"
                attributes["manual_reason"] = "unparseable wildcard expression"
        return SourceNode(
            node_id=nid,
            kind=NodeKind.FILTER,
            origin_kind=OriginKind.TABLEAU,
            origin_tag="filter",
            name=fname,
            attributes=attributes,
            raw_payload=_element_to_dict(elem),
        )

    def _parse_actions(
        self, elem: ET.Element, owner_name: str, owner_type: str
    ) -> list[SourceNode]:
        nodes: list[SourceNode] = []
        action_elements = [child for child in elem if _strip_ns(child.tag) == "action"]
        for idx, act_elem in enumerate(action_elements):
            aname = act_elem.attrib.get("name") or f"action_{idx}"
            nid = stable_node_id(
                OriginKind.TABLEAU, owner_type, owner_name, "action", aname, str(idx)
            )
            attributes: dict[str, Any] = dict(act_elem.attrib)
            for child in act_elem:
                tag = _strip_ns(child.tag)
                if tag == "activation":
                    attributes["activation"] = child.attrib.get("type", "")
                    attributes["auto_clear"] = (
                        str(child.attrib.get("auto-clear") or "").lower() == "true"
                    )
                elif tag == "source":
                    attributes["source_dashboard"] = child.attrib.get("dashboard", "")
                    attributes["source_worksheet"] = child.attrib.get(
                        "worksheet", child.attrib.get("sheet", "")
                    )
                elif tag == "command":
                    attributes["command"] = child.attrib.get("command", "")
                    for param in child:
                        if _strip_ns(param.tag) != "param":
                            continue
                        param_name = str(param.attrib.get("name") or "")
                        if param_name:
                            attributes[f"command_{param_name.replace('-', '_')}"] = (
                                param.attrib.get("value", "")
                            )
                elif tag == "link":
                    attributes["target_url"] = child.attrib.get("expression", "")
            nodes.append(
                SourceNode(
                    node_id=nid,
                    kind=NodeKind.UNKNOWN,
                    origin_kind=OriginKind.TABLEAU,
                    origin_tag="action",
                    name=aname,
                    attributes=attributes,
                    raw_payload=_element_to_dict(act_elem),
                )
            )
        return nodes

    def _parse_format(
        self, elem: ET.Element, owner_name: str, owner_type: str, idx: int
    ) -> SourceNode:
        tag = _strip_ns(elem.tag)
        nid = stable_node_id(OriginKind.TABLEAU, owner_type, owner_name, tag, str(idx))
        return SourceNode(
            node_id=nid,
            kind=NodeKind.UNKNOWN,
            origin_kind=OriginKind.TABLEAU,
            origin_tag=tag,
            name=tag,
            attributes=dict(elem.attrib),
            raw_payload=_element_to_dict(elem),
        )


def import_tableau(
    source_path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> SourceAST:
    """Import a Tableau TWB or TWBX workbook into a neutral SourceAST.

    Args:
        source_path: Path to a .twb or .twbx file.
        workspace_root: Optional anchor for relative provenance path.

    Returns:
        Validated :class:`SourceAST` tree instance.
    """
    importer = TableauImporter(source_path, workspace_root=workspace_root)
    return importer.import_ast()


__all__ = ["TableauImporter", "TableauPackage", "import_tableau", "open_tableau_package"]
