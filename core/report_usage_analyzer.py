"""
Report Usage Analyzer (Measure Killer Logic).
Scans Power BI Report (.pbir/.json) definitions to identify used columns vs model columns.
"""

import glob
import json
import os
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

ANALYZER_VERSION = "1.1.0"
_REDACTED_PATH = "[REDACTED_PATH]"


def _safe_locator(file_path: str, *, local_only: bool) -> str:
    path = Path(file_path)
    return str(path) if local_only else path.name


def _evidence_reference(file_path: str, diagnostic: str) -> dict[str, object] | None:
    try:
        artifact_hash = sha256(Path(file_path).read_bytes()).hexdigest()
    except OSError:
        return None
    return {
        "artifact_hash": artifact_hash,
        "locator": Path(file_path).name,
        "diagnostic": diagnostic,
        "analyzer_version": ANALYZER_VERSION,
    }


@dataclass
class ReportUsage:
    report_name: str
    used_columns: set[str]  # Format: 'Table[Column]'
    used_measures: set[str]  # Format: 'Table[Measure]'
    scanned_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    evidence: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self, *, local_only: bool = False) -> dict[str, object]:
        """Serialize observed usage without exposing absolute paths by default."""
        return {
            "report_name": self.report_name,
            "used_columns": sorted(self.used_columns),
            "used_measures": sorted(self.used_measures),
            "scanned_files": sorted(
                _safe_locator(file_path, local_only=local_only) for file_path in self.scanned_files
            ),
            "errors": [
                error if local_only else error.replace(os.getcwd(), _REDACTED_PATH)
                for error in self.errors
            ],
            "coverage": {
                "status": "incomplete",
                "complete": False,
                "scanned_files": len(self.scanned_files),
                "reason": "Only discoverable PBIR JSON definitions were scanned.",
            },
            "evidence": [dict(item) for item in self.evidence],
        }


class ReportUsageAnalyzer:
    def __init__(self, definition_folder: str):
        """
        Args:
           definition_folder: Path to the 'definition' folder in a PBIP structure.
                              Normally contains 'pages' subfolder with report JSONs.
        """
        self.definition_folder = definition_folder

    def analyze_usage(self) -> ReportUsage:
        used_cols = set()
        used_measures = set()
        scanned_files = []
        errors = []

        # Find all page.json files (PBIR format)
        # Structure: definition/pages/PageName/page.json
        search_pattern = os.path.join(self.definition_folder, "pages", "**", "*.json")
        page_files = sorted(glob.glob(search_pattern, recursive=True))

        # Also check for report.json (Legacy/PBIRS format)
        report_json = os.path.join(self.definition_folder, "report.json")
        if os.path.exists(report_json):
            page_files.append(report_json)

        for page_file in page_files:
            try:
                with open(page_file, encoding="utf-8") as f:
                    data = json.load(f)
                    c, m = self._scan_visuals(data)
                    used_cols.update(c)
                    used_measures.update(m)
                    scanned_files.append(page_file)
            except Exception as e:
                errors.append(f"{page_file}: {e}")

        evidence = [
            reference
            for page_file in scanned_files
            if (reference := _evidence_reference(page_file, "PBIR definition scanned"))
        ]
        return ReportUsage(
            report_name="Analyzed Report",
            used_columns=used_cols,
            used_measures=used_measures,
            scanned_files=scanned_files,
            errors=errors,
            evidence=evidence,
        )

    def _scan_visuals(
        self, json_node: Any, source_entities: dict[str, str] | None = None
    ) -> tuple[set[str], set[str]]:
        """
        Recursively scans JSON for 'Column' and 'Measure' references in projections.
        Handles both PBIR (Entity/Property) and Legacy Layout (config string) formats.
        """
        cols = set()
        measures = set()

        source_entities = dict(source_entities or {})
        if isinstance(json_node, dict):
            for item in json_node.get("From", []):
                if isinstance(item, dict):
                    alias = item.get("Name")
                    entity = item.get("Entity") or item.get("Table")
                    if isinstance(alias, str) and isinstance(entity, str):
                        source_entities[alias] = entity

            # 1. PBIR measure patterns keep measures separate from columns.
            table = json_node.get("Entity") or json_node.get("Table")
            measure = json_node.get("Measure") or json_node.get("MeasureName")
            if isinstance(measure, str) and table:
                measures.add(f"{table}[{measure}]")

            # 2. PBIR column pattern: Entity + Property/Column.
            if table and ("Property" in json_node or "Column" in json_node) and not measure:
                col = json_node.get("Property") or json_node.get("Column")
                if isinstance(col, str) and col:
                    cols.add(f"{table}[{col}]")

            # 3. Generated PBIR visual shapes nest field.Column, field.Measure,
            # and field.Aggregation under the visual projection.
            field_node = json_node.get("field")
            if isinstance(field_node, dict):
                c, m = self._scan_field(field_node, source_entities)
                cols.update(c)
                measures.update(m)

            # 4. Legacy/Layout Pattern: Embedded "config" JSON string
            if "config" in json_node and isinstance(json_node["config"], str):
                try:
                    # Recursive parse of the embedded config JSON
                    config_data = json.loads(json_node["config"])
                    c, m = self._scan_visuals(config_data)
                    cols.update(c)
                    measures.update(m)
                except json.JSONDecodeError:
                    pass

            # 5. Legacy "selects" / "projections" inside config or layouts
            # Often found as: { "query": { "Select": [ ... ] } } or similar structures
            # We look for specific keys like "Property" and "Entity" even if nested differently

            # Recursive traversal
            for value in json_node.values():
                c, m = self._scan_visuals(value, source_entities)
                cols.update(c)
                measures.update(m)

        elif isinstance(json_node, list):
            for item in json_node:
                c, m = self._scan_visuals(item, source_entities)
                cols.update(c)
                measures.update(m)

        return cols, measures

    def _scan_field(
        self, field_node: dict[str, Any], source_entities: dict[str, str]
    ) -> tuple[set[str], set[str]]:
        columns: set[str] = set()
        measures: set[str] = set()
        for kind, target in (("Column", columns), ("Measure", measures)):
            descriptor = field_node.get(kind)
            if not isinstance(descriptor, dict):
                continue
            name = descriptor.get("Property") or descriptor.get("Name")
            table = self._field_table(descriptor, source_entities)
            if isinstance(name, str) and name and table:
                target.add(f"{table}[{name}]")

        aggregation = field_node.get("Aggregation")
        if isinstance(aggregation, dict):
            expression = aggregation.get("Expression")
            if isinstance(expression, dict):
                c, m = self._scan_field(expression, source_entities)
                columns.update(c)
                measures.update(m)
            else:
                name = aggregation.get("Property") or aggregation.get("Name")
                table = self._field_table(aggregation, source_entities)
                if isinstance(name, str) and name and table:
                    columns.add(f"{table}[{name}]")
        return columns, measures

    @staticmethod
    def _field_table(descriptor: dict[str, Any], source_entities: dict[str, str]) -> str | None:
        direct_table = descriptor.get("Entity") or descriptor.get("Table")
        if isinstance(direct_table, str) and direct_table:
            return direct_table
        expression = descriptor.get("Expression")
        if not isinstance(expression, dict):
            return None
        source_ref = expression.get("SourceRef")
        if not isinstance(source_ref, dict):
            return None
        source = source_ref.get("Source") or source_ref.get("Entity")
        if not isinstance(source, str):
            return None
        return source_entities.get(source, source)

    def cross_check_model(self, model_bim_path: str, usage: ReportUsage) -> dict[str, list[str]]:
        """
        Compares Model vs Usage to find unused artifacts.
        """
        unused_columns = []
        unused_measures = []

        if not os.path.exists(model_bim_path):
            return {"unused_columns": [], "unused_measures": []}

        try:
            with open(model_bim_path, encoding="utf-8") as f:
                model = json.load(f).get("model", {})

            for table in model.get("tables", []):
                table_name = table["name"]

                # Check Columns
                for col in table.get("columns", []):
                    ref = f"{table_name}[{col['name']}]"
                    # Ignore hidden or calculated columns depending on strictness
                    if ref not in usage.used_columns and col.get("type") != "calculated":
                        unused_columns.append(ref)

                # Check Measures
                for meas in table.get("measures", []):
                    ref = f"{table_name}[{meas['name']}]"
                    if ref not in usage.used_measures:
                        unused_measures.append(ref)

        except Exception as e:
            print(f"Error cross-checking model: {e}")

        return {"unused_columns": unused_columns, "unused_measures": unused_measures}
