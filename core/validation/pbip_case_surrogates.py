"""Discover case-sensitive grouping surrogates emitted in PBIP TMDL."""

from __future__ import annotations

import json
import re
from pathlib import Path

_TABLE = re.compile(r"^table\s+(.+?)\s*$")
_COLUMN = re.compile(r"^\s*column\s+(.+?)(?:\s*=.*)?$")
_ANNOTATION = re.compile(r"^\s*annotation\s+PBI_Bridge_CaseOriginal\s*=\s*(.+?)\s*$")


def _identifier(token: str) -> str:
    value = token.strip()
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def discover_case_surrogates(pbip_root: Path) -> dict[tuple[str, str], str]:
    """Return ``(table, original field) -> hidden surrogate field`` from TMDL annotations."""
    root = Path(pbip_root)
    mappings: dict[tuple[str, str], str] = {}
    for path in sorted(root.rglob("*.tmdl")):
        table = ""
        current_column = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            table_match = _TABLE.match(line)
            if table_match:
                table = _identifier(table_match.group(1))
                current_column = ""
                continue
            column_match = _COLUMN.match(line)
            if column_match:
                current_column = _identifier(column_match.group(1))
                continue
            annotation = _ANNOTATION.match(line)
            if not annotation or not table or not current_column:
                continue
            original = json.loads(annotation.group(1))
            if not isinstance(original, str) or not original:
                raise ValueError(f"invalid PBI_Bridge_CaseOriginal in {path}")
            key = (table, original)
            previous = mappings.get(key)
            if previous is not None and previous != current_column:
                raise ValueError(f"ambiguous case surrogate for {table}.{original}")
            mappings[key] = current_column
    return mappings


__all__ = ["discover_case_surrogates"]
