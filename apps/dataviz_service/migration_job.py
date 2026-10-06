"""Durable migration handlers that converge supported BI sources into canonical IRs."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from apps.dataviz_service.contracts import Job, SanitizedServiceError
from apps.dataviz_service.job_worker import CheckpointWriter, JobHandlerResult, VersionJobCompletion
from apps.dataviz_service.runtime.materializer import materialize_runtime_results
from apps.dataviz_service.storage.backend import ObjectBackend
from core.compilers.ast_render_plan import compile_source_ast
from core.compilers.pbip import PBIPAdapter
from core.compilers.powerbi import compile_powerbi_render_plan
from core.compilers.render_plan import RenderPlan
from core.contracts.dataset import Dataset
from core.contracts.semantic_ir import SemanticModel, walk_expression
from core.contracts.source_ast import NodeKind, SourceAST, SourceNode
from core.importers.powerbi import PbiToolsError, extract_pbix, import_powerbi_with_security
from core.importers.tableau import TableauImporter, open_tableau_package
from core.materialize_tableau import materialize_tableau_datasets, tableau_serving_mode
from core.security.credential_safety import scan_artifact


class InvalidMigrationInput(SanitizedServiceError):
    def __init__(self, message: str = "The migration input is invalid.") -> None:
        super().__init__(message, status_code=422, error_code="INVALID_MIGRATION_INPUT")


class SourceSecurityPolicyUnsupported(SanitizedServiceError):
    """Source contains row/object security that has no verified target translation."""

    def __init__(self) -> None:
        super().__init__(
            "The source contains a security policy that requires manual review before migration.",
            status_code=422,
            error_code="SOURCE_SECURITY_POLICY_UNSUPPORTED",
        )


class PBIXExtractionUnavailable(SanitizedServiceError):
    """Native PBIX import is disabled because no managed extractor is configured."""

    def __init__(self) -> None:
        super().__init__(
            "Native PBIX import is not enabled on this worker.",
            status_code=422,
            error_code="PBIX_EXTRACTION_UNAVAILABLE",
        )


class PBIXExtractionFailed(SanitizedServiceError):
    """Managed PBIX extraction failed without exposing tool output or local paths."""

    def __init__(self) -> None:
        super().__init__(
            "The native PBIX source could not be extracted by the configured bridge.",
            status_code=422,
            error_code="PBIX_EXTRACTION_FAILED",
        )


def _walk_source_nodes(node: SourceNode):
    yield node
    for child in node.children:
        yield from _walk_source_nodes(child)


def _source_security_nodes(source_ast: SourceAST) -> tuple[SourceNode, ...]:
    """Return explicit source security constructs; never infer ordinary filters as RLS."""
    return tuple(
        node
        for node in _walk_source_nodes(source_ast.root)
        if node.origin_tag == "security_role" or bool(node.attributes.get("security_policy"))
    )


def _reject_untranslated_source_security(source_ast: SourceAST) -> None:
    if _source_security_nodes(source_ast):
        raise SourceSecurityPolicyUnsupported()


class TableauMigrationHandler:
    """Descarga una fuente inmutable, compila una vez y publica artefactos inmutables."""

    def __init__(
        self,
        object_store: ObjectBackend,
        *,
        max_source_bytes: int = 512 * 1024 * 1024,
        max_runtime_rows: int = 1_000_000,
    ) -> None:
        if max_source_bytes <= 0:
            raise ValueError("max_source_bytes must be positive")
        if max_runtime_rows <= 0:
            raise ValueError("max_runtime_rows must be positive")
        self.object_store = object_store
        self.max_source_bytes = max_source_bytes
        self.max_runtime_rows = max_runtime_rows

    def __call__(
        self,
        job: Job,
        input_payload: Mapping[str, Any],
        checkpoint: CheckpointWriter,
        resume: Mapping[str, Any] | None = None,
    ) -> JobHandlerResult:
        source_key = _source_key(job.tenant_id, input_payload.get("source_key"))
        source_name = str(input_payload.get("source_name", ""))
        suffix = Path(source_name).suffix.lower()
        if suffix not in {".twb", ".twbx"}:
            raise InvalidMigrationInput("The source must be a Tableau TWB or TWBX file.")
        source = self.object_store.get(source_key)
        if len(source) > self.max_source_bytes:
            raise InvalidMigrationInput("The Tableau source exceeds the configured size limit.")

        with tempfile.TemporaryDirectory(prefix="dataviz-job-") as temporary:
            source_path = Path(temporary) / f"source{suffix}"
            source_path.write_bytes(source)
            credential_report = scan_artifact(source_path)
            with open_tableau_package(source_path) as package:
                source_ast = TableauImporter(source_path, workspace_root=temporary).import_package(
                    package
                )
                _reject_untranslated_source_security(source_ast)
                checkpoint({"step": "source_ast"})
                compilation = compile_source_ast(source_ast)
                checkpoint({"step": "neutral_ir"})
                datasets = materialize_tableau_datasets(
                    source_ast, package, max_rows=self.max_runtime_rows
                )
                runtime_results = materialize_runtime_results(
                    RenderPlan.from_dict(compilation.render_plan.body),
                    compilation.semantic_model,
                    datasets,
                )
                serving = _serving_metadata(source_ast, datasets)
                runtime_results["serving"] = serving
                checkpoint({"step": "runtime_results"})
                display_name = str(input_payload.get("display_name") or Path(source_name).stem)
                project = PBIPAdapter(
                    datasets=datasets,
                    origin="tableau",
                    presentation=compilation.presentation_ir,
                    interaction=compilation.interaction_ir,
                    display_name=display_name,
                    relationship_strategy=_tableau_relationship_strategy(source_ast, datasets),
                ).build_project(compilation.semantic_model)

        prefix = (
            f"{job.tenant_id}/projects/{job.project_id}/versions/{job.version_id}"
            f"/staging/{job.id}"
        )
        neutral = {
            "source_ast.json": source_ast.to_json(indent=None),
            "semantic_model.json": compilation.semantic_model.to_json(indent=None),
            "capability_report.json": compilation.capability_report.to_json(indent=None),
            "presentation_ir.json": compilation.presentation_ir.to_json(indent=None),
            "interaction_ir.json": compilation.interaction_ir.to_json(indent=None),
            "render_plan.json": json.dumps(compilation.render_plan.body, sort_keys=True),
            "runtime_results.json": json.dumps(runtime_results, sort_keys=True),
            "security_scan.json": json.dumps(credential_report.to_dict(), sort_keys=True),
        }
        keys: list[str] = []
        for name, content in neutral.items():
            key = f"{prefix}/neutral/{name}"
            _put_immutable(self.object_store, key, content.encode("utf-8"))
            keys.append(key)
        for artifact in project.files:
            key = f"{prefix}/pbip/{artifact.relative_path}"
            _put_immutable(self.object_store, key, artifact.content.encode("utf-8"))
            keys.append(key)
        checkpoint({"step": "staged", "artifact_count": len(keys)})
        return VersionJobCompletion(
            result={
                "artifact_keys": keys,
                "runtime_materialized": True,
                "serving": serving,
                "cache": {"kind": "idempotency_result", "resumed": resume is not None},
                "credential_scan": credential_report.to_dict(),
            },
            materialization={
                "render_plan": compilation.render_plan.body,
                "runtime_results": runtime_results,
                "datasets": datasets,
                "semantic_model": compilation.semantic_model,
                "presentation_ir": compilation.presentation_ir,
                "interaction_ir": compilation.interaction_ir,
                "credential_scan": credential_report.to_dict(),
            },
        )


class PowerBIMigrationHandler:
    """Import Power BI sources through one durable SourceAST -> canonical-IR path."""

    def __init__(
        self,
        object_store: ObjectBackend,
        *,
        pbi_tools_path: str | Path | None = None,
        max_source_bytes: int = 512 * 1024 * 1024,
        max_archive_files: int = 20_000,
        max_expanded_bytes: int = 2 * 1024 * 1024 * 1024,
        pbix_extract_timeout_seconds: int = 900,
    ) -> None:
        if (
            max_source_bytes <= 0
            or max_archive_files <= 0
            or max_expanded_bytes <= 0
            or pbix_extract_timeout_seconds <= 0
        ):
            raise ValueError("Power BI migration limits must be positive")
        self.object_store = object_store
        self.pbi_tools_path = Path(pbi_tools_path) if pbi_tools_path is not None else None
        self.max_source_bytes = max_source_bytes
        self.max_archive_files = max_archive_files
        self.max_expanded_bytes = max_expanded_bytes
        self.pbix_extract_timeout_seconds = pbix_extract_timeout_seconds

    def __call__(
        self,
        job: Job,
        input_payload: Mapping[str, Any],
        checkpoint: CheckpointWriter,
        resume: Mapping[str, Any] | None = None,
    ) -> JobHandlerResult:
        source_key = _source_key(job.tenant_id, input_payload.get("source_key"))
        source_name = str(input_payload.get("source_name", ""))
        suffix = Path(source_name).suffix.lower()
        if suffix not in {".pbit", ".pbix", ".zip"}:
            raise InvalidMigrationInput(
                "Power BI imports require PBIX, PBIT, or a ZIP containing one PBIP/PbixProj project."
            )
        if suffix == ".pbix" and self.pbi_tools_path is None:
            raise PBIXExtractionUnavailable()
        source = self.object_store.get(source_key)
        if len(source) > self.max_source_bytes:
            raise InvalidMigrationInput("The Power BI source exceeds the configured size limit.")

        with tempfile.TemporaryDirectory(prefix="dataviz-job-") as temporary:
            temporary_path = Path(temporary)
            source_path = temporary_path / f"source{suffix}"
            source_path.write_bytes(source)
            if suffix == ".pbit":
                import_path = source_path
            elif suffix == ".pbix":
                expanded = temporary_path / "powerbi-project"
                try:
                    import_path = extract_pbix(
                        source_path,
                        executable=self.pbi_tools_path,
                        extraction_dir=expanded,
                        timeout_seconds=self.pbix_extract_timeout_seconds,
                    )
                except (OSError, PbiToolsError) as exc:
                    raise PBIXExtractionFailed() from exc
                _validate_extracted_powerbi_project(
                    import_path,
                    max_files=self.max_archive_files,
                    max_expanded_bytes=self.max_expanded_bytes,
                )
                import_path = _locate_powerbi_project(import_path)
            else:
                expanded = temporary_path / "powerbi-project"
                _extract_powerbi_archive(
                    source_path,
                    expanded,
                    max_files=self.max_archive_files,
                    max_expanded_bytes=self.max_expanded_bytes,
                )
                import_path = _locate_powerbi_project(expanded)

            source_ast, credential_report = import_powerbi_with_security(
                import_path, workspace_root=temporary_path
            )
            _reject_untranslated_source_security(source_ast)
            checkpoint({"step": "source_ast"})
            compilation = compile_powerbi_render_plan(source_ast)
            checkpoint({"step": "neutral_ir"})

            # PBIP/PBIT carries report/model metadata but normally not queryable
            # row data. Preserve a useful derived runtime envelope without
            # fabricating datasets or pretending an external connector is live.
            render_plan = RenderPlan.from_dict(compilation.render_plan.body)
            runtime_results = _unmaterialized_powerbi_runtime(render_plan, compilation.semantic_model)
            checkpoint({"step": "runtime_results"})

        prefix = (
            f"{job.tenant_id}/projects/{job.project_id}/versions/{job.version_id}"
            f"/staging/{job.id}"
        )
        diagnostics = [
            {"code": "POWER_BI_IMPORT_NOTE", "severity": "warning", "message": note}
            for note in compilation.notes
        ]
        neutral = {
            "source_ast.json": source_ast.to_json(indent=None),
            "semantic_model.json": compilation.semantic_model.to_json(indent=None),
            "presentation_ir.json": compilation.presentation_ir.to_json(indent=None),
            "interaction_ir.json": compilation.interaction_ir.to_json(indent=None),
            "render_plan.json": json.dumps(compilation.render_plan.body, sort_keys=True),
            "runtime_results.json": json.dumps(runtime_results, sort_keys=True),
            "migration_diagnostics.json": json.dumps(diagnostics, sort_keys=True),
            "security_scan.json": json.dumps(credential_report.to_dict(), sort_keys=True),
        }
        keys: list[str] = []
        for name, content in neutral.items():
            key = f"{prefix}/neutral/{name}"
            _put_immutable(self.object_store, key, content.encode("utf-8"))
            keys.append(key)
        checkpoint({"step": "staged", "artifact_count": len(keys)})
        return VersionJobCompletion(
            result={
                "artifact_keys": keys,
                "runtime_materialized": False,
                "serving": {
                    "mode": "external_data_required",
                    "materialized_datasources": [],
                    "cache": "idempotency_result",
                },
                "diagnostic_count": len(diagnostics),
                "export_status": "not_generated_external_data",
                "cache": {"kind": "idempotency_result", "resumed": resume is not None},
                "credential_scan": credential_report.to_dict(),
            },
            materialization={
                "render_plan": compilation.render_plan.body,
                "runtime_results": runtime_results,
                "datasets": {},
                "semantic_model": compilation.semantic_model,
                "presentation_ir": compilation.presentation_ir,
                "interaction_ir": compilation.interaction_ir,
                "credential_scan": credential_report.to_dict(),
            },
        )

def _put_immutable(store: ObjectBackend, key: str, payload: bytes) -> None:
    """Accept an identical retry, but reject an attempt to overwrite an artifact."""
    if store.put_if_absent(key, payload):
        return
    try:
        existing = store.get(key)
    except Exception as exc:
        raise SanitizedServiceError(
            "An immutable migration artifact already exists.",
            status_code=409,
            error_code="ARTIFACT_IMMUTABLE",
        ) from exc
    if existing != payload:
        raise SanitizedServiceError(
            "An immutable migration artifact already exists.",
            status_code=409,
            error_code="ARTIFACT_IMMUTABLE",
        )


def _unmaterialized_powerbi_runtime(plan: RenderPlan, model: SemanticModel) -> dict[str, Any]:
    """Describe missing Power BI runtime capabilities without fabricating results."""
    opaque_metrics = {metric.name for metric in model.metrics if metric.expression.kind == "opaque"}
    visuals: dict[str, dict[str, object]] = {}
    requires_dax = False
    for visual in plan.visuals:
        measure_refs = {
            reference.partition(":")[2]
            for reference in visual.data_roles.values()
            if reference.startswith("measure:")
        }
        if visual.query is not None:
            measure_refs.update(
                node.name
                for item in visual.query.select
                for node in walk_expression(item.expression)
                if node.kind == "measure_ref"
            )
        state: dict[str, object] = {
            "status": "data_unavailable",
            "reason": "external_datasource_requires_connector",
        }
        if measure_refs & opaque_metrics:
            state["required_capabilities"] = ["dax_execution"]
            requires_dax = True
        visuals[visual.name] = state

    diagnostics: list[dict[str, str]] = [
        {
            "code": "EXTERNAL_DATA_REQUIRED",
            "severity": "warning",
            "message": "Power BI model metadata imported; runtime data requires a configured connector.",
        }
    ]
    if requires_dax:
        diagnostics.append(
            {
                "code": "OPAQUE_DAX_EXECUTION_UNAVAILABLE",
                "severity": "warning",
                "message": (
                    "One or more visuals require opaque DAX execution; the neutral runtime "
                    "will not evaluate it without an authorized DAX execution capability."
                ),
            }
        )
    return {
        "schema_version": "2.0.0",
        "visuals": visuals,
        "forecast_models": {},
        "diagnostics": diagnostics,
    }


def _validate_extracted_powerbi_project(
    root: Path,
    *,
    max_files: int,
    max_expanded_bytes: int,
) -> None:
    """Bound a managed PBIX extraction before any parsed content is trusted."""
    if not root.is_dir():
        raise InvalidMigrationInput("The extracted Power BI project is unavailable.")
    root_resolved = root.resolve()
    file_count = 0
    expanded_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise InvalidMigrationInput("The extracted Power BI project contains an unsafe path.")
        if not path.is_file():
            continue
        resolved = path.resolve()
        if root_resolved not in resolved.parents:
            raise InvalidMigrationInput("The extracted Power BI project contains an unsafe path.")
        file_count += 1
        if file_count > max_files:
            raise InvalidMigrationInput("The extracted Power BI project contains too many files.")
        expanded_bytes += path.stat().st_size
        if expanded_bytes > max_expanded_bytes:
            raise InvalidMigrationInput(
                "The extracted Power BI project exceeds the configured size limit."
            )


def _extract_powerbi_archive(
    archive_path: Path,
    destination: Path,
    *,
    max_files: int,
    max_expanded_bytes: int,
) -> None:
    """Extract a project archive with traversal and zip-bomb limits."""
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with ZipFile(archive_path) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > max_files:
                raise InvalidMigrationInput("The Power BI archive contains too many files.")
            expanded_bytes = sum(item.file_size for item in members)
            if expanded_bytes > max_expanded_bytes:
                raise InvalidMigrationInput("The Power BI archive expands beyond the configured limit.")
            destination_resolved = destination.resolve()
            for info in archive.infolist():
                relative = PurePosixPath(info.filename.replace("\\", "/"))
                if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                    raise InvalidMigrationInput("The Power BI archive contains an unsafe path.")
                target = destination.joinpath(*relative.parts).resolve()
                if target != destination_resolved and destination_resolved not in target.parents:
                    raise InvalidMigrationInput("The Power BI archive contains an unsafe path.")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    remaining = info.file_size
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        output.write(chunk)
                        remaining -= len(chunk)
    except BadZipFile as exc:
        raise InvalidMigrationInput("The Power BI project archive is not a valid ZIP file.") from exc


def _locate_powerbi_project(root: Path) -> Path:
    pbip_entries = sorted(root.rglob("*.pbip"))
    if len(pbip_entries) == 1:
        # Import the project directory, not only the entry file, so the raw
        # credential scan covers sibling PBIR/TMDL/config artifacts as well.
        return pbip_entries[0].parent
    if len(pbip_entries) > 1:
        raise InvalidMigrationInput("The Power BI archive must contain exactly one PBIP project.")

    candidates = sorted(
        path
        for path in [root, *[item for item in root.iterdir() if item.is_dir()]]
        if (path / "Model").is_dir() and (path / "Report").is_dir()
    )
    if len(candidates) == 1:
        return candidates[0]
    raise InvalidMigrationInput("The ZIP does not contain a recognizable PBIP/PbixProj project.")


def _tableau_relationship_strategy(
    source_ast: SourceAST, datasets: Mapping[str, Dataset]
) -> str:
    """Describe source joins only when their joined datasource was actually materialized."""
    joined = [
        datasource
        for datasource in source_ast.root.children
        if datasource.kind is NodeKind.DATASOURCE
        and isinstance(datasource.attributes.get("federation"), Mapping)
        and datasource.attributes["federation"].get("kind") == "join"
    ]
    if joined and all(datasource.name in datasets for datasource in joined):
        return "materialized_federation"
    return ""


def _serving_metadata(source_ast: Any, datasets: Mapping[str, Dataset]) -> dict[str, Any]:
    packaged = {
        datasource.name
        for datasource in source_ast.root.children
        if datasource.name != "Parameters" and datasource.attributes.get("packaged_files")
    }
    materialized = set(datasets)
    return {
        "mode": tableau_serving_mode(source_ast, materialized),
        "materialized_datasources": sorted(materialized),
        "unavailable_datasources": sorted(packaged - materialized),
        "cache": "idempotency_result",
    }


def _source_key(tenant_id: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidMigrationInput()
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidMigrationInput()
    if not path.parts or path.parts[0] != tenant_id:
        raise InvalidMigrationInput()
    return value
