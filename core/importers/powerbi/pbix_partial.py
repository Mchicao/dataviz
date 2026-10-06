"""Importación PARCIAL de un ``.pbix`` nativo (sin pbi-tools ni credenciales).

Un ``.pbix`` es un contenedor ZIP cuyo ``Report/Layout`` es JSON (UTF-16 LE en
artefactos reales) y cuyo modelo de datos es un binario Vertipaq que este
importador NO lee (requeriría TOM/AMO o la extracción opcional con pbi-tools).
El resultado declara explícitamente ``unsupported_data_model`` con causa y
nunca promete el modelo de datos (D019; riesgo documentado de G0-PBI-IMPORT).

Diseño (Ponytail): stdlib + :mod:`core.contracts` + los helpers compartidos del
importador PBIP. No importa runtimes de destino.
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any

from core.contracts.provenance import OriginKind, ProvenanceRecord
from core.contracts.source_ast import (
    SCHEMA_VERSION,
    NodeKind,
    SourceAST,
    SourceNode,
    stable_node_id,
)
from core.importers.powerbi.importer import (
    _as_jsonable,
    _decode_layout_bytes,
    _json_loads,
    _layout_pages,
    _sanitize_path,
)

_PBI_ORIGIN = OriginKind.POWER_BI

#: Estado declarado para el modelo de datos de un PBIX (Vertipaq binario).
UNSUPPORTED_DATA_MODEL_STATUS = "unsupported_data_model"
_DATA_MODEL_CAUSE = "vertipaq_binary_model"

_PBIX_EXT = ".pbix"


def import_powerbi_pbix_partial(
    source_path: str | Path,
    *,
    workspace_root: str | Path | None = None,
) -> SourceAST:
    """Import a native ``.pbix`` in PARTIAL mode: report layout only.

    Extracts ``Report/Layout`` (detecting UTF-16 LE), maps it to PAGE/VISUAL
    nodes and declares the data model as ``unsupported_data_model`` with an
    explicit cause. No data model content is promised or imported.

    Args:
        source_path: Path to the ``.pbix`` file.
        workspace_root: Optional anchor for the relative provenance path.

    Returns:
        Validated :class:`SourceAST` with ``metadata["mode"] = "partial"``,
        ``metadata["layout"]`` (member presence, encoding, page/visual counts)
        and ``metadata["unsupported_elements"]`` (data model, and layout when
        unreadable/missing).

    Raises:
        FileNotFoundError: If ``source_path`` does not exist.
        ValueError: If the suffix is not ``.pbix``.
    """
    path = Path(source_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Source artifact not found: {source_path}")
    if path.suffix.lower() != _PBIX_EXT:
        raise ValueError(f"import_powerbi_pbix_partial expects a .pbix file, got: {path.name}")

    raw = path.read_bytes()
    provenance = ProvenanceRecord(
        artifact_path=_sanitize_path(path, workspace_root),
        artifact_hash=hashlib.sha256(raw).hexdigest(),
        origin=_PBI_ORIGIN.value,
    )

    children: list[SourceNode] = []
    unsupported: list[dict[str, Any]] = []
    data_model_member_present = False
    layout_meta: dict[str, Any] = {
        "member_present": False,
        "encoding": None,
        "pages": 0,
        "visuals": 0,
    }

    try:
        with zipfile.ZipFile(path) as zf:
            names = {n.lower(): n for n in zf.namelist()}
            data_model_member_present = "datamodel" in names
            layout_member = names.get("report/layout")
            if layout_member is not None:
                layout_meta["member_present"] = True
                try:
                    text, encoding = _decode_layout_bytes(zf.read(layout_member))
                    layout_meta["encoding"] = encoding
                    data = _json_loads(text, "Report/Layout")
                    pages = _layout_pages(data, "pbix_section")
                    children.append(
                        SourceNode(
                            node_id=stable_node_id(_PBI_ORIGIN, "pbix", path.stem, "report_layout"),
                            kind=NodeKind.DATASOURCE,
                            origin_kind=_PBI_ORIGIN,
                            origin_tag="report_layout",
                            name="ReportLayout",
                            attributes={"format": "layout"},
                            children=tuple(pages),
                            raw_payload=_as_jsonable(data) if data else None,
                        )
                    )
                    layout_meta["pages"] = len(pages)
                    layout_meta["visuals"] = sum(len(page.children) for page in pages)
                except ValueError as exc:
                    # UnicodeDecodeError y JSONDecodeError son ValueError: se
                    # degrada honesto sin abortar la importación parcial.
                    unsupported.append(
                        {
                            "element": "report_layout",
                            "status": "unsupported",
                            "cause": f"layout_unreadable: {type(exc).__name__}",
                        }
                    )
    except zipfile.BadZipFile as exc:
        unsupported.append(
            {
                "element": "report_layout",
                "status": "unsupported",
                "cause": f"bad_zip_file: {type(exc).__name__}",
            }
        )

    if not layout_meta["member_present"] and not any(
        u["element"] == "report_layout" for u in unsupported
    ):
        # Sin layout no se promete reporte importado: se registra la ausencia.
        unsupported.append(
            {
                "element": "report_layout",
                "status": "missing",
                "cause": "report_layout_member_absent",
            }
        )

    unsupported.append(
        {
            "element": "data_model",
            "status": UNSUPPORTED_DATA_MODEL_STATUS,
            "cause": _DATA_MODEL_CAUSE,
            "detail": (
                "El modelo de datos de un PBIX es un binario Vertipaq: este "
                "importador no lo lee ni promete su contenido. Requiere TOM/AMO "
                "o la extracción opcional con pbi-tools."
            ),
            "member_present": data_model_member_present,
        }
    )
    children.append(
        SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbix", path.stem, "data_model"),
            kind=NodeKind.UNKNOWN,
            origin_kind=_PBI_ORIGIN,
            origin_tag="data_model",
            name="DataModel",
            attributes={
                "status": UNSUPPORTED_DATA_MODEL_STATUS,
                "cause": _DATA_MODEL_CAUSE,
            },
        )
    )

    name = path.stem
    doc = SourceAST(
        schema_version=SCHEMA_VERSION,
        origin_kind=_PBI_ORIGIN,
        root=SourceNode(
            node_id=stable_node_id(_PBI_ORIGIN, "pbix", name),
            kind=NodeKind.DOCUMENT,
            origin_kind=_PBI_ORIGIN,
            origin_tag="pbix",
            name=name,
            attributes={"format": "pbix", "mode": "partial"},
            children=tuple(children),
        ),
        provenance=provenance,
        metadata={
            "source_name": name,
            "format": "pbix",
            "mode": "partial",
            "layout": layout_meta,
            "unsupported_elements": unsupported,
        },
    )
    doc.validate()
    return doc


__all__ = ["UNSUPPORTED_DATA_MODEL_STATUS", "import_powerbi_pbix_partial"]
