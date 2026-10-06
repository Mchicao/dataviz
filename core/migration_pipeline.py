"""Single maintained Tableau/Power BI migration pipeline through neutral IR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.compilers.ast_render_plan import SourceASTCompilation, compile_source_ast
from core.compilers.pbip import PBIPAdapter, PBIPProject
from core.contracts.source_ast import SourceAST
from core.importers.tableau import TableauImporter, open_tableau_package
from core.materialize_tableau import materialize_tableau_datasets


@dataclass(frozen=True)
class MigrationResult:
    """Artifacts produced by the neutral Tableau-to-PBIP pipeline."""

    source_ast: SourceAST
    compilation: SourceASTCompilation
    pbip_project: PBIPProject
    output_dir: Path


def write_pbip_project(project: PBIPProject, output_dir: str | Path) -> Path:
    """Persist a PBIPProject without overwriting an existing destination."""
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for artifact in project.files:
        target = (destination / Path(artifact.relative_path)).resolve()
        if destination not in target.parents:
            raise ValueError(f"PBIP artifact escapes destination: {artifact.relative_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(artifact.content, encoding="utf-8")
    return destination


def migrate_tableau_to_pbip(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    display_name: str | None = None,
    workspace_root: str | Path | None = None,
) -> MigrationResult:
    """Import Tableau once, compile neutral IR once, then emit PBIP."""
    with open_tableau_package(source_path) as package:
        source_ast = TableauImporter(source_path, workspace_root=workspace_root).import_package(
            package
        )
        compilation = compile_source_ast(source_ast)
        datasets = materialize_tableau_datasets(source_ast, package)
        adapter = PBIPAdapter(
            datasets=datasets,
            origin="tableau",
            presentation=compilation.presentation_ir,
            interaction=compilation.interaction_ir,
            display_name=display_name or Path(source_path).stem,
        )
        project = adapter.build_project(compilation.semantic_model)
    destination = write_pbip_project(project, output_dir)
    return MigrationResult(source_ast, compilation, project, destination)


__all__ = ["MigrationResult", "migrate_tableau_to_pbip", "write_pbip_project"]
