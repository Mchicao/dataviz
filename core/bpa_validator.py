"""
Best Practice Analyzer (BPA) for Power BI Models ('model.bim').
Compatible with Tabular Editor logic styles.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BPAResult:
    rule_id: str
    rule_name: str
    severity: int
    object_name: str
    object_type: str
    message: str


class BPAValidator:
    def __init__(self, rules_path: str | None = None):
        if rules_path is None:
            rules_path = os.path.join(os.path.dirname(__file__), "best_practices.json")

        self.rules = []
        if os.path.exists(rules_path):
            with open(rules_path, encoding="utf-8") as f:
                self.rules = json.load(f)
        else:
            logger.warning(f"BPA Rules file not found at {rules_path}")

    def validate_model(self, model_bim_path: str) -> list[BPAResult]:
        """Validates a model (model.bim) against loaded rules."""
        results = []

        if not os.path.exists(model_bim_path):
            logger.error(f"Model file not found: {model_bim_path}")
            return results

        try:
            with open(model_bim_path, encoding="utf-8") as f:
                data = json.load(f)

            model = data.get("model", {})
            tables = model.get("tables", [])

            for rule in self.rules:
                scope = rule.get("scope", "model")
                expression = rule.get("expression", "False")

                if scope == "table":
                    for table in tables:
                        if self._evaluate_rule(table, expression):
                            results.append(self._create_violation(rule, table["name"], "Table"))

                elif scope == "column":
                    for table in tables:
                        for col in table.get("columns", []):
                            if self._evaluate_rule(col, expression):
                                obj_name = f"{table['name']}[{col['name']}]"
                                results.append(self._create_violation(rule, obj_name, "Column"))

                elif scope == "measure":
                    for table in tables:
                        for measure in table.get("measures", []):
                            if self._evaluate_rule(measure, expression):
                                obj_name = f"{table['name']}[{measure['name']}]"
                                results.append(self._create_violation(rule, obj_name, "Measure"))

        except Exception as e:
            logger.error(f"Error executing BPA: {e}")

        return results

    def _evaluate_rule(self, obj: dict[str, Any], expression: str) -> bool:
        """Safely evaluates a python expression against an object."""
        try:
            # Dangerous but effective for dynamic rules.
            # In a production env, use a safer parser or specific DSL.
            # Local namespace restricts access only to 'object'.
            return eval(expression, {"__builtins__": {}}, {"object": obj})
        except Exception:
            return False

    def _create_violation(self, rule, obj_name, obj_type):
        return BPAResult(
            rule_id=rule["id"],
            rule_name=rule["name"],
            severity=rule["severity"],
            object_name=obj_name,
            object_type=obj_type,
            message=rule["description"],
        )


# Example Usage
if __name__ == "__main__":
    # Create a dummy model.bim for testing if none exists
    dummy_model = {
        "model": {
            "tables": [
                {
                    "name": "Sales",
                    "columns": [
                        {"name": "Amount", "dataType": "double", "isHidden": False},
                        {"name": "Date", "dataType": "dateTime", "isHidden": False},
                        {"name": "ID", "dataType": "int64", "isHidden": True},
                    ],
                    "measures": [
                        {"name": "Total Sales", "expression": "SUM(Sales[Amount])"}
                        # No description -> Violation
                    ],
                }
            ]
        }
    }

    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", suffix=".bim", delete=False) as tmp:
        json.dump(dummy_model, tmp)
        tmp_path = tmp.name

    validator = BPAValidator()  # Will attempt to load best_practices.json from same dir
    # Ensure json exists for this test context or mock it

    # Run
    violations = validator.validate_model(tmp_path)
    for v in violations:
        print(f"[{v.rule_id}] {v.object_name}: {v.message}")

    os.remove(tmp_path)
