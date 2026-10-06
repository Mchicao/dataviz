"""Comparación fila por fila entre resultados DAX agrupados y el payload validado."""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

_FIELD_SUFFIX = re.compile(r"\[([^]]+)\]$")
_CANONICAL_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat() if value.time().isoformat() == "00:00:00" else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        for suffix in ("T00:00:00", " 00:00:00"):
            if value.endswith(suffix):
                return value[: -len(suffix)]
        for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(value, fmt)
            except ValueError:
                continue
            if parsed.time().isoformat() == "00:00:00":
                return parsed.date().isoformat()
    return value


def _numeric_group_value(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value)).normalize()
        except InvalidOperation:
            return None
    if isinstance(value, str) and _CANONICAL_NUMBER.fullmatch(value):
        try:
            return Decimal(value).normalize()
        except InvalidOperation:
            return None
    return None


def _group_equivalent(
    expected: Any,
    actual: Any,
    *,
    allow_blank_false: bool = False,
    allow_blank_empty_string: bool = False,
) -> bool:
    expected = _normalize(expected)
    actual = _normalize(actual)
    if expected == actual:
        return True
    if allow_blank_false and expected is None and actual is False:
        return True
    if allow_blank_empty_string and expected == "" and actual is None:
        return True
    expected_number = _numeric_group_value(expected)
    actual_number = _numeric_group_value(actual)
    return expected_number is not None and expected_number == actual_number


def _field_value(
    row: dict[str, Any],
    name: str,
    *,
    surrogate: str | None = None,
) -> tuple[Any, bool]:
    candidates = (surrogate, name) if surrogate else (name,)
    for candidate in candidates:
        for key, value in row.items():
            match = _FIELD_SUFFIX.search(str(key))
            if not match or match.group(1) != candidate:
                continue
            normalized = _normalize(value)
            if surrogate and candidate == surrogate and isinstance(normalized, str):
                stripped = normalized.replace("\u200b", "")
                return stripped, stripped != normalized
            return normalized, False
    raise KeyError(surrogate or name)


def _value(row: dict[str, Any]) -> Any:
    if "[value]" in row:
        return row["[value]"]
    if "value" in row:
        return row["value"]
    raise KeyError("value")


def _same(expected: Any, actual: Any, relative: float, absolute: float) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=relative, abs_tol=absolute)
    return _normalize(expected) == _normalize(actual)


def _matching_expected_key(
    actual_key: tuple[Any, ...],
    expected_keys: list[tuple[Any, ...]],
    group_names: list[str],
    blank_false_group_fields: set[str],
    blank_empty_string_group_fields: set[str],
) -> tuple[Any, ...] | None:
    matches = [
        key
        for key in expected_keys
        if len(key) == len(actual_key)
        and all(
            _group_equivalent(
                expected,
                actual,
                allow_blank_false=field in blank_false_group_fields,
                allow_blank_empty_string=field in blank_empty_string_group_fields,
            )
            for field, expected, actual in zip(group_names, key, actual_key)
        )
    ]
    return matches[0] if len(matches) == 1 else None


def compare_visual_rows(
    visual_id: str,
    measure: str,
    visual: dict[str, Any],
    expected_result: dict[str, list[Any]],
    actual_rows: list[dict[str, Any]],
    *,
    relative_tolerance: float = 1e-9,
    absolute_tolerance: float = 1e-8,
    blank_false_group_fields: set[str] | None = None,
    blank_empty_string_group_fields: set[str] | None = None,
    case_surrogate_group_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    blank_false_group_fields = blank_false_group_fields or set()
    blank_empty_string_group_fields = blank_empty_string_group_fields or set()
    case_surrogate_group_fields = case_surrogate_group_fields or {}
    group_names = [
        str(expr.get("name"))
        for expr in (visual.get("query") or {}).get("group_by", [])
        if expr.get("kind") == "field_ref" and expr.get("name")
    ]
    field_columns = [f"field:{name}" for name in group_names]
    measure_column = f"measure:{measure}"
    required = field_columns + [measure_column]
    missing_columns = [column for column in required if column not in expected_result]
    if missing_columns:
        return {
            "visual_id": visual_id,
            "measure": measure,
            "status": "not_evaluable",
            "cause": f"expected payload missing columns: {missing_columns}",
            "expected_rows": 0,
            "actual_rows": len(actual_rows),
            "checked_values": 0,
            "missing_groups": [],
            "extra_groups": [],
            "ignored_blank_extra_groups": [],
            "key_type_coercions": [],
            "case_surrogate_coercions": [],
            "value_mismatches": [],
        }

    expected_columns: dict[str, list[Any]] = {
        column: value if isinstance((value := expected_result[column]), list) else [value]
        for column in required
    }
    lengths = {len(expected_columns[column]) for column in required}
    if len(lengths) != 1:
        return {
            "visual_id": visual_id,
            "measure": measure,
            "status": "not_evaluable",
            "cause": "expected payload columns have inconsistent lengths",
            "expected_rows": 0,
            "actual_rows": len(actual_rows),
            "checked_values": 0,
            "missing_groups": [],
            "extra_groups": [],
            "ignored_blank_extra_groups": [],
            "key_type_coercions": [],
            "case_surrogate_coercions": [],
            "value_mismatches": [],
        }

    source_expected_rows = next(iter(lengths), 0)
    expected_indices = list(range(source_expected_rows))
    ignored_forecast_rows = 0
    if isinstance(visual.get("forecast"), dict):
        indicators = expected_result.get("field:Forecast Indicator")
        if isinstance(indicators, list) and len(indicators) == source_expected_rows:
            expected_indices = [
                index for index, indicator in enumerate(indicators) if str(indicator) == "Actual"
            ]
            ignored_forecast_rows = source_expected_rows - len(expected_indices)
    expected_rows = len(expected_indices)
    expected: dict[tuple[Any, ...], Any] = {}
    duplicate_expected: list[list[Any]] = []
    for index in expected_indices:
        key = tuple(_normalize(expected_columns[column][index]) for column in field_columns)
        if key in expected:
            duplicate_expected.append(list(key))
        expected[key] = expected_columns[measure_column][index]

    expected_keys = list(expected)
    actual: dict[tuple[Any, ...], Any] = {}
    duplicate_actual: list[list[Any]] = []
    malformed_rows: list[str] = []
    key_type_coercions: list[dict[str, Any]] = []
    coercion_seen: set[tuple[str, type, type]] = set()
    case_surrogate_coercions: list[dict[str, Any]] = []
    surrogate_seen: set[str] = set()
    for index, row in enumerate(actual_rows):
        try:
            raw_values: list[Any] = []
            for name in group_names:
                surrogate = case_surrogate_group_fields.get(name)
                field_value, marker_stripped = _field_value(row, name, surrogate=surrogate)
                raw_values.append(field_value)
                if surrogate and marker_stripped and name not in surrogate_seen:
                    surrogate_seen.add(name)
                    case_surrogate_coercions.append(
                        {"field": name, "surrogate": surrogate, "marker_stripped": True}
                    )
            raw_key = tuple(raw_values)
            value = _value(row)
        except KeyError as exc:
            malformed_rows.append(f"row {index}: missing {exc.args[0]}")
            continue
        key = raw_key if raw_key in expected else (
            _matching_expected_key(
                raw_key,
                expected_keys,
                group_names,
                blank_false_group_fields,
                blank_empty_string_group_fields,
            )
            or raw_key
        )
        if key != raw_key and key in expected:
            for field, expected_value, actual_value in zip(group_names, key, raw_key):
                if expected_value == actual_value or not _group_equivalent(
                    expected_value,
                    actual_value,
                    allow_blank_false=field in blank_false_group_fields,
                    allow_blank_empty_string=field in blank_empty_string_group_fields,
                ):
                    continue
                signature = (field, type(expected_value), type(actual_value))
                if signature not in coercion_seen:
                    coercion_seen.add(signature)
                    key_type_coercions.append(
                        {"field": field, "expected": expected_value, "actual": actual_value}
                    )
        if key in actual:
            duplicate_actual.append(list(key))
        actual[key] = value

    missing = sorted((list(key) for key in expected.keys() - actual.keys()), key=repr)
    raw_extra_keys = actual.keys() - expected.keys()
    ignored_blank: list[list[Any]] = []
    extra = sorted((list(key) for key in raw_extra_keys), key=repr)
    mismatches = []
    checked = 0
    for key in expected.keys() & actual.keys():
        checked += 1
        if not _same(expected[key], actual[key], relative_tolerance, absolute_tolerance):
            mismatches.append(
                {"group": list(key), "expected": expected[key], "actual": actual[key]}
            )
    mismatches.sort(key=lambda item: repr(item["group"]))
    status = (
        "match"
        if not (missing or extra or mismatches or duplicate_expected or duplicate_actual or malformed_rows)
        else "mismatch"
    )
    return {
        "visual_id": visual_id,
        "measure": measure,
        "status": status,
        "cause": None,
        "expected_rows": expected_rows,
        "actual_rows": len(actual_rows),
        "checked_values": checked,
        "missing_groups": missing,
        "extra_groups": extra,
        "ignored_blank_extra_groups": ignored_blank,
        "key_type_coercions": key_type_coercions,
        "case_surrogate_coercions": case_surrogate_coercions,
        "value_mismatches": mismatches,
        "duplicate_expected_groups": duplicate_expected,
        "duplicate_actual_groups": duplicate_actual,
        "malformed_rows": malformed_rows,
        "ignored_forecast_rows": ignored_forecast_rows,
    }


__all__ = ["compare_visual_rows"]
