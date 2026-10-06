"""Validate the Sprint 02 DataVIZ fidelity corpus without overstating coverage."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

CORPUS_SCHEMA_VERSION = "2.0.0"
VISUAL_STATUSES = frozenset({"observed", "missing", "blocked", "not_evaluated"})
REQUIRED_COMPLEXITIES = frozenset({"simple", "medium", "complex"})
REQUIRED_DIMENSIONS = ("numeric", "semantic", "visual", "interactive", "operational")
RUNTIME_DIMENSIONS = frozenset({"visual", "interactive", "operational"})
REQUIRED_FIDELITY_FIELDS = (
    "axes",
    "colors",
    "layout",
    "filters",
    "actions",
    "timings",
    "dom",
    "cpu",
    "heap",
)


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _status(value: object, label: str) -> str:
    result = _required_text(value, label)
    if result not in VISUAL_STATUSES:
        raise ValueError(f"{label} must be one of {sorted(VISUAL_STATUSES)}")
    return result


def _relative_path(value: object, label: str) -> Path:
    path = Path(_required_text(value, label))
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{label} must be relative to the corpus root")
    return path


def _digest(value: object, label: str) -> str:
    result = _required_text(value, label).lower()
    if len(result) != 64 or any(char not in "0123456789abcdef" for char in result):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _read_hashed_json(
    relative: object, digest: object, *, root: Path, label: str
) -> Mapping[str, Any]:
    path = _relative_path(relative, f"{label}.path")
    expected_digest = _digest(digest, f"{label}.sha256")
    resolved = root / path
    if not resolved.is_file():
        raise ValueError(f"{label}.path does not exist: {path.as_posix()}")
    if hashlib.sha256(resolved.read_bytes()).hexdigest() != expected_digest:
        raise ValueError(f"{label}.sha256 mismatch for {path.as_posix()}")
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label}.path must contain JSON") from exc
    return _mapping(payload, f"{label}.payload")


def _validate_source(
    source: Mapping[str, Any], *, root: Path | None, demo_id: str
) -> tuple[str, list[Mapping[str, Any]] | None]:
    kind = _required_text(source.get("kind"), "source.kind")
    if kind not in {"public", "synthetic"}:
        raise ValueError("source.kind must be 'public' or 'synthetic'")
    source_path = _relative_path(source.get("path"), "source.path")
    source_digest = _digest(source.get("sha256"), "source.sha256")
    if root is None:
        return source_digest, None
    resolved = root / source_path
    if not resolved.is_file():
        raise ValueError(f"source.path does not exist: {source_path.as_posix()}")
    if hashlib.sha256(resolved.read_bytes()).hexdigest() != source_digest:
        raise ValueError(f"source.sha256 mismatch for {source_path.as_posix()}")
    if kind != "synthetic":
        return source_digest, None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"synthetic source must contain JSON: {source_path}") from exc
    payload = _mapping(payload, f"{demo_id}.source.payload")
    if payload.get("demo_id") != demo_id:
        raise ValueError(f"{demo_id}.source.payload.demo_id must match the demo")
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError(f"{demo_id}.source.payload.rows must contain row objects")
    if payload.get("row_count") != len(rows):
        raise ValueError(f"{demo_id}.source.payload.row_count does not match rows")
    return source_digest, rows


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _matches(expected: object, observed: object, tolerance: Mapping[str, Any], label: str) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        actual = _number(observed, f"{label}.observed")
        absolute = _number(tolerance.get("absolute", 0), f"{label}.tolerance.absolute")
        relative = _number(tolerance.get("relative", 0), f"{label}.tolerance.relative")
        if absolute < 0 or relative < 0:
            raise ValueError(f"{label}.tolerance values must be non-negative")
        return math.isclose(float(expected), actual, abs_tol=absolute, rel_tol=relative)
    if isinstance(expected, Mapping):
        return (
            isinstance(observed, Mapping)
            and set(expected) == set(observed)
            and all(
                _matches(expected[key], observed[key], tolerance, f"{label}.{key}")
                for key in expected
            )
        )
    if isinstance(expected, list):
        return (
            isinstance(observed, list)
            and len(expected) == len(observed)
            and all(
                _matches(left, right, tolerance, f"{label}[{index}]")
                for index, (left, right) in enumerate(zip(expected, observed))
            )
        )
    return expected == observed


def _recompute(check: Mapping[str, Any], rows: list[Mapping[str, Any]], label: str) -> object:
    recipe = _mapping(check.get("recompute"), f"{label}.recompute")
    operation = _required_text(recipe.get("operation"), f"{label}.recompute.operation")
    if operation == "sum":
        field = _required_text(recipe.get("field"), f"{label}.recompute.field")
        try:
            return sum(_number(row[field], f"{label}.source.{field}") for row in rows)
        except KeyError as exc:
            raise ValueError(
                f"{label}.recompute.field is absent from synthetic source rows"
            ) from exc
    if operation == "fields":
        fields = recipe.get("fields")
        if (
            not isinstance(fields, list)
            or not fields
            or any(not isinstance(field, str) for field in fields)
        ):
            raise ValueError(f"{label}.recompute.fields must be a non-empty string list")
        if any(field not in row for row in rows for field in fields):
            raise ValueError(f"{label}.recompute.fields are absent from synthetic source rows")
        return fields
    raise ValueError(f"{label}.recompute.operation is unsupported: {operation}")


def _validate_observation(
    check: Mapping[str, Any],
    *,
    label: str,
    dimension: str,
    demo_id: str,
    visual_id: str,
    source_digest: str,
    rows: list[Mapping[str, Any]] | None,
    root: Path | None,
) -> dict[str, Any] | None:
    if check["status"] != "observed":
        return None
    tolerance = _mapping(check["tolerance"], f"{label}.tolerance")
    if dimension in RUNTIME_DIMENSIONS:
        if root is None:
            raise ValueError(f"{label} observed runtime checks require rooted evidence")
        evidence_map = _mapping(check.get("evidence"), f"{label}.evidence")
        artifact = _read_hashed_json(
            evidence_map.get("path"),
            evidence_map.get("sha256"),
            root=root,
            label=f"{label}.evidence",
        )
        if artifact.get("demo_id") != demo_id or artifact.get("source_sha256") != source_digest:
            raise ValueError(f"{label}.evidence is not bound to the demo source")
        observations = _mapping(artifact.get("observations"), f"{label}.evidence.observations")
        visual_observations = _mapping(observations.get(visual_id), f"{label}.evidence.visual")
        artifact_check = _mapping(visual_observations.get(dimension), f"{label}.evidence.check")
        if "observed" not in artifact_check:
            raise ValueError(f"{label}.evidence.check must contain observed")
        if not _matches(check["observed"], artifact_check["observed"], tolerance, label):
            raise ValueError(f"{label}.observed differs from the hashed observation artifact")
        return {
            "demo_id": demo_id,
            "visual_id": visual_id,
            "dimension": dimension,
            "source_sha256": source_digest,
            "observation_path": evidence_map["path"],
            "observation_sha256": evidence_map["sha256"],
            "recomputed": None,
            "expected": check["expected"],
            "observed": check["observed"],
            "within_tolerance": True,
        }
    if dimension == "semantic":
        if not _matches(check["expected"], check["observed"], tolerance, label):
            raise ValueError(f"{label}.observed does not match expected semantic bindings")
        return None
    if rows is None or root is None:
        raise ValueError(f"{label} observed numeric checks require rooted synthetic evidence")
    recomputed = _recompute(check, rows, label)
    if not _matches(recomputed, check["expected"], tolerance, label):
        raise ValueError(f"{label}.expected does not match the recomputed synthetic source value")
    if not _matches(recomputed, check["observed"], tolerance, label):
        raise ValueError(f"{label}.observed does not match the recomputed synthetic source value")
    evidence = check.get("evidence")
    if evidence is None:
        return {
            "demo_id": demo_id,
            "visual_id": visual_id,
            "dimension": dimension,
            "source_sha256": source_digest,
            "observation_path": None,
            "observation_sha256": None,
            "recomputed": recomputed,
            "expected": check["expected"],
            "observed": check["observed"],
            "within_tolerance": True,
        }
    evidence_map = _mapping(evidence, f"{label}.evidence")
    artifact = _read_hashed_json(
        evidence_map.get("path"), evidence_map.get("sha256"), root=root, label=f"{label}.evidence"
    )
    if artifact.get("demo_id") != demo_id or artifact.get("source_sha256") != source_digest:
        raise ValueError(f"{label}.evidence is not bound to the demo source")
    observations = _mapping(artifact.get("observations"), f"{label}.evidence.observations")
    visual_observations = _mapping(observations.get(visual_id), f"{label}.evidence.visual")
    artifact_check = _mapping(visual_observations.get(dimension), f"{label}.evidence.check")
    if artifact_check.get("observed") != check.get("observed"):
        raise ValueError(f"{label}.observed differs from the hashed observation artifact")
    return {
        "demo_id": demo_id,
        "visual_id": visual_id,
        "dimension": dimension,
        "source_sha256": source_digest,
        "observation_path": evidence_map["path"],
        "observation_sha256": evidence_map["sha256"],
        "recomputed": recomputed,
        "expected": check["expected"],
        "observed": check["observed"],
        "within_tolerance": True,
    }


def _validate_check(
    check: Mapping[str, Any],
    label: str,
    *,
    dimension: str,
    demo_id: str,
    visual_id: str,
    source_digest: str,
    rows: list[Mapping[str, Any]] | None,
    root: Path | None,
) -> tuple[str, dict[str, Any] | None]:
    state = _status(check.get("status"), f"{label}.status")
    _required_text(check.get("cause"), f"{label}.cause")
    tolerance = _mapping(check.get("tolerance"), f"{label}.tolerance")
    if not tolerance:
        raise ValueError(f"{label}.tolerance must not be empty")
    if state == "observed" and ("expected" not in check or "observed" not in check):
        raise ValueError(f"{label} observed checks require expected and observed values")
    if state != "observed" and "observed" in check:
        raise ValueError(f"{label} non-observed checks must not contain observed values")
    if (
        state == "observed"
        and dimension not in {"numeric", "semantic", "operational"}
        and not _matches(check["expected"], check["observed"], tolerance, label)
    ):
        raise ValueError(f"{label}.observed does not match expected within tolerance")
    return state, _validate_observation(
        check,
        label=label,
        dimension=dimension,
        demo_id=demo_id,
        visual_id=visual_id,
        source_digest=source_digest,
        rows=rows,
        root=root,
    )


def _validate_fidelity_field(
    value: object, label: str
) -> tuple[str, dict[str, Any] | None]:
    """Validate one explicit visual-fidelity field and return its observation."""
    field = _mapping(value, label)
    state = _status(field.get("status"), f"{label}.status")
    _required_text(field.get("oracle"), f"{label}.oracle")
    _required_text(field.get("cause"), f"{label}.cause")
    tolerance = _mapping(field.get("tolerance"), f"{label}.tolerance")
    if not tolerance:
        raise ValueError(f"{label}.tolerance must not be empty")
    if state == "observed":
        if "expected" not in field or "observed" not in field:
            raise ValueError(f"{label} observed fields require expected and observed values")
        if not _matches(field["expected"], field["observed"], tolerance, label):
            raise ValueError(f"{label}.observed does not match expected within tolerance")
        return state, {
            "field": label.rsplit(".", 1)[-1],
            "expected": field["expected"],
            "observed": field["observed"],
            "within_tolerance": True,
        }
    if "observed" in field:
        raise ValueError(f"{label} non-observed fields must not contain observed values")
    return state, None


def validate_fidelity_corpus(
    manifest: Mapping[str, Any], *, root: Path | None = None
) -> dict[str, Any]:
    """Valida evidencia, cobertura y estados sin convertirlos en aceptación."""
    if manifest.get("schema_version") != CORPUS_SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {CORPUS_SCHEMA_VERSION!r}")
    corpus_id = _required_text(manifest.get("corpus_id"), "corpus_id")
    scope_complete = manifest.get("scope_complete")
    if not isinstance(scope_complete, bool):
        raise ValueError("scope_complete must be a boolean")
    acceptance = _required_text(manifest.get("acceptance"), "acceptance")
    if acceptance not in {"blocked", "not_evaluated", "accepted"}:
        raise ValueError("acceptance must be blocked, not_evaluated or accepted")
    acceptance_reason = _required_text(manifest.get("acceptance_reason"), "acceptance_reason")
    demos = manifest.get("demos")
    if not isinstance(demos, list) or len(demos) != 3:
        raise ValueError("demos must contain exactly three entries")
    seen_demo_ids: set[str] = set()
    seen_sources: set[str] = set()
    visual_states: list[str] = []
    observations: list[dict[str, Any]] = []
    visual_reports: list[dict[str, Any]] = []
    fidelity_counts = {
        field: {state: 0 for state in sorted(VISUAL_STATUSES)}
        for field in REQUIRED_FIDELITY_FIELDS
    }
    dimension_counts = {
        dimension: {state: 0 for state in sorted(VISUAL_STATUSES)}
        for dimension in REQUIRED_DIMENSIONS
    }
    for demo_index, raw_demo in enumerate(demos):
        demo = _mapping(raw_demo, f"demos[{demo_index}]")
        demo_id = _required_text(demo.get("id"), f"demos[{demo_index}].id")
        if demo_id in seen_demo_ids:
            raise ValueError(f"duplicate demo id: {demo_id}")
        seen_demo_ids.add(demo_id)
        complexity = _required_text(demo.get("complexity"), f"demos[{demo_index}].complexity")
        if complexity not in REQUIRED_COMPLEXITIES:
            raise ValueError("demo complexity must be simple, medium or complex")
        source = _mapping(demo.get("source"), f"demos[{demo_index}].source")
        source_digest, rows = _validate_source(source, root=root, demo_id=demo_id)
        source_path = str(source["path"])
        if source_path in seen_sources:
            raise ValueError(f"duplicate source path: {source_path}")
        seen_sources.add(source_path)
        visuals = demo.get("visuals")
        if not isinstance(visuals, list) or not visuals:
            raise ValueError(f"demos[{demo_index}].visuals must not be empty")
        seen_visual_ids: set[str] = set()
        for visual_index, raw_visual in enumerate(visuals):
            visual = _mapping(raw_visual, f"demos[{demo_index}].visuals[{visual_index}]")
            visual_id = _required_text(
                visual.get("id"), f"demos[{demo_index}].visuals[{visual_index}].id"
            )
            if visual_id in seen_visual_ids:
                raise ValueError(f"duplicate visual id in {demo_id}: {visual_id}")
            seen_visual_ids.add(visual_id)
            visual_state = _status(visual.get("status"), f"{demo_id}.{visual_id}.status")
            _required_text(visual.get("cause"), f"{demo_id}.{visual_id}.cause")
            _required_text(visual.get("oracle"), f"{demo_id}.{visual_id}.oracle")
            if not _mapping(visual.get("tolerance"), f"{demo_id}.{visual_id}.tolerance"):
                raise ValueError(f"{demo_id}.{visual_id}.tolerance must not be empty")
            fidelity = _mapping(visual.get("fidelity"), f"{demo_id}.{visual_id}.fidelity")
            missing_fields = [
                field for field in REQUIRED_FIDELITY_FIELDS if field not in fidelity
            ]
            if missing_fields:
                raise ValueError(
                    f"{demo_id}.{visual_id}.fidelity missing fields: {missing_fields}"
                )
            fidelity_results = {
                field: _validate_fidelity_field(
                    fidelity[field], f"{demo_id}.{visual_id}.fidelity.{field}"
                )
                for field in REQUIRED_FIDELITY_FIELDS
            }
            fidelity_states = {field: result[0] for field, result in fidelity_results.items()}
            if visual_state == "observed" and any(
                state != "observed" for state in fidelity_states.values()
            ):
                raise ValueError(
                    f"{demo_id}.{visual_id}.observed requires every fidelity field to be observed"
                )
            for field, (state, observation) in fidelity_results.items():
                fidelity_counts[field][state] += 1
                if observation is not None:
                    observations.append(
                        {
                            "demo_id": demo_id,
                            "visual_id": visual_id,
                            "source_sha256": source_digest,
                            **observation,
                        }
                    )
            checks = _mapping(visual.get("checks"), f"{demo_id}.{visual_id}.checks")
            missing_dimensions = [name for name in REQUIRED_DIMENSIONS if name not in checks]
            if missing_dimensions:
                raise ValueError(
                    f"{demo_id}.{visual_id}.checks missing dimensions: {missing_dimensions}"
                )
            results = {
                dimension: _validate_check(
                    _mapping(checks[dimension], f"{demo_id}.{visual_id}.{dimension}"),
                    f"{demo_id}.{visual_id}.{dimension}",
                    dimension=dimension,
                    demo_id=demo_id,
                    visual_id=visual_id,
                    source_digest=source_digest,
                    rows=rows,
                    root=root,
                )
                for dimension in REQUIRED_DIMENSIONS
            }
            states = {dimension: result[0] for dimension, result in results.items()}
            if visual_state == "observed" and any(state != "observed" for state in states.values()):
                raise ValueError(
                    f"{demo_id}.{visual_id}.observed requires every check to be observed"
                )
            observations.extend(result[1] for result in results.values() if result[1] is not None)
            visual_states.append(visual_state)
            visual_reports.append(
                {
                    "demo_id": demo_id,
                    "visual_id": visual_id,
                    "status": visual_state,
                    "oracle": visual["oracle"],
                    "tolerance": visual["tolerance"],
                    "cause": visual["cause"],
                    "fidelity_status": {
                        field: result[0] for field, result in fidelity_results.items()
                    },
                }
            )
            for dimension, state in states.items():
                dimension_counts[dimension][state] += 1
    complexities = {
        _required_text(_mapping(demo, "demo").get("complexity"), "demo.complexity")
        for demo in demos
    }
    missing_complexities = REQUIRED_COMPLEXITIES - complexities
    if missing_complexities:
        raise ValueError(f"missing complexity tiers: {sorted(missing_complexities)}")
    counts = {state: visual_states.count(state) for state in sorted(VISUAL_STATUSES)}
    derived_status = next(
        (state for state in ("blocked", "missing", "not_evaluated") if counts[state]), "observed"
    )
    fully_observed = counts["observed"] == len(visual_states)
    if acceptance == "accepted" and (not scope_complete or not fully_observed):
        raise ValueError("accepted corpus requires complete scope and all visuals observed")
    blockers = [
        acceptance_reason,
        *(
            f"{state}_visuals={counts[state]}"
            for state in ("missing", "blocked", "not_evaluated")
            if counts[state]
        ),
    ]
    return {
        "schema_version": CORPUS_SCHEMA_VERSION,
        "corpus_id": corpus_id,
        "scope_complete": scope_complete,
        "acceptance": acceptance,
        "accepted": acceptance == "accepted",
        "corpus_status": derived_status,
        "demo_count": len(demos),
        "visual_count": len(visual_states),
        "counts": counts,
        "fidelity_counts": fidelity_counts,
        "visuals": visual_reports,
        "dimension_counts": dimension_counts,
        "observations": observations,
        "blockers": blockers if acceptance != "accepted" else [],
    }


__all__ = [
    "CORPUS_SCHEMA_VERSION",
    "REQUIRED_DIMENSIONS",
    "REQUIRED_FIDELITY_FIELDS",
    "REQUIRED_COMPLEXITIES",
    "VISUAL_STATUSES",
    "validate_fidelity_corpus",
]
