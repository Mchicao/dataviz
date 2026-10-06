"""Power BI PBIP / PBIT / PbixProj / PBIX-partial importer and neutral IR compiler.

Public API:

* :func:`import_powerbi` -- PBIP folder, PBIT package or pbi-tools PbixProj
  folder -> neutral :class:`core.contracts.source_ast.SourceAST` with the
  D012/D017 origin diagnosis in ``metadata["data_sources"]``.
* :func:`import_powerbi_pbix` -- explicit PBIX -> pbi-tools extraction -> AST.
* :func:`import_powerbi_pbix_partial` -- PBIX partial mode: ``Report/Layout``
  only, with ``unsupported_data_model`` declared (no Vertipaq parsing).
* :func:`compile_powerbi_ast` -- Power BI :class:`SourceAST` -> accepted
  neutral :class:`core.contracts.semantic_ir.SemanticModel`.

The PBIX bridge is opt-in and extracts offline by default. A Desktop PID can be
provided explicitly for the compatibility path; no process discovery or GUI
automation happens implicitly.
"""

from pathlib import Path

from core.importers.powerbi.importer import (
    PowerBIImporter,
    import_powerbi,
    import_powerbi_with_security,
)
from core.importers.powerbi.ir_compiler import PowerBIAstCompiler, compile_powerbi_ast
from core.importers.powerbi.pbi_tools import PbiToolsError, extract_pbix
from core.importers.powerbi.pbix_partial import import_powerbi_pbix_partial


def import_powerbi_pbix(
    source_path: str | Path,
    *,
    pbi_tools_path: str | Path,
    desktop_pid: int | None = None,
    extraction_dir: str | Path | None = None,
    workspace_root: str | Path | None = None,
) -> object:
    """Extract a native PBIX with pbi-tools and import its PbixProj output."""
    extracted = extract_pbix(
        source_path,
        executable=pbi_tools_path,
        desktop_pid=desktop_pid,
        extraction_dir=extraction_dir,
    )
    return import_powerbi(extracted, workspace_root=workspace_root)


__all__ = [
    "PbiToolsError",
    "PowerBIAstCompiler",
    "PowerBIImporter",
    "compile_powerbi_ast",
    "extract_pbix",
    "import_powerbi",
    "import_powerbi_pbix",
    "import_powerbi_pbix_partial",
    "import_powerbi_with_security",
]
