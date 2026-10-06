"""PBIP adapter: compiles accepted neutral IR into a concrete PBIP project.

The :class:`PBIPAdapter` consumes the accepted neutral IR
(:class:`SemanticModel`, optional :class:`PresentationIR`, optional
:class:`InteractionIR`) and emits a :class:`PBIPProject`: an in-memory,
deterministic set of PBIP files (TMDL for the semantic model, PBIR JSON for
the report, plus the ``.pbip`` entry and ``.platform`` manifests).

Key properties:

* **No Tableau reparsing**: reads exclusively the neutral IR. Contrast with
  the legacy ``core.pbir_logic`` / ``core.tmdl_generator`` which reopen the
  TWB and re-derive everything from XML.
* **Preserves generic bindings**: visual :class:`DataBinding` are resolved
  against the model and emitted as native ``query.queryState`` projections.
* **Preserves formats**: measure ``format_string`` -> TMDL ``formatString``.
* **Preserves filters**: verified neutral operators are emitted through native
  PBIR ``filterConfig`` at report/page/visual scope; unsupported operators are
  retained only in canonical IR and recorded as explicit approximations.
* **Preserves layout**: ``VisualGeometry`` -> PBIR ``position``.
* **Explicit approximations**: every non-native mapping (visual intent, DAX
  node, partition source, measure home table) is recorded in
  :attr:`PBIPProject.approximations` -- nothing is silently dropped.

Boundary (one-way): imports stdlib, ``core.contracts``, ``core.compilers`` and
the sibling ``core.compilers.pbip`` emitters only. Never imports destination
runtimes (``core.pbir_*`` / ``core.pbip_*`` / ``core.visual_*`` / DAX engines).
The artifact is pure data; persistence is the caller's responsibility.

Schema Versioning Policy (SemVer): MAJOR breaking, MINOR additive, PATCH fix.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from core.compilers.base import (
    SCHEMA_VERSION as ENVELOPE_SCHEMA_VERSION,
)
from core.compilers.base import DestinationCompiler, DestinationPlan
from core.compilers.pbip import pbir, tmdl
from core.contracts.dataset import Dataset
from core.contracts.interaction_ir import InteractionIR
from core.contracts.presentation_ir import (
    SCHEMA_VERSION as PRES_SCHEMA_VERSION,
)
from core.contracts.presentation_ir import (
    DataBinding,
    FieldRole,
    PagePresentation,
    PresentationIR,
    VisualGeometry,
    VisualIntentKind,
    VisualPresentation,
)
from core.contracts.semantic_ir import (
    DataType,
    Entity,
    Expression,
    Field,
    Parameter,
    SemanticModel,
    walk_expression,
)

SCHEMA_VERSION: str = "1.0.0"
"""Current schema version of the :class:`PBIPProject` artifact (SemVer)."""

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_]+")


_CASE_SURROGATE_MARKER = "\u200b"
_CASE_UNSUPPORTED = object()


def _case_expression_value(
    expression: Expression,
    row: Mapping[str, Any],
    parameters: Mapping[str, Any],
) -> Any:
    """Evaluate the small row-level string subset used for case-collision detection."""
    kind = expression.kind
    if kind == "literal":
        return expression.value
    if kind == "field_ref":
        return row.get(expression.name, _CASE_UNSUPPORTED)
    if kind == "parameter_ref":
        return parameters.get(expression.name, _CASE_UNSUPPORTED)
    if kind == "func" and expression.name == "split" and len(expression.children) >= 3:
        source = _case_expression_value(expression.children[0], row, parameters)
        delimiter = _case_expression_value(expression.children[1], row, parameters)
        index = _case_expression_value(expression.children[2], row, parameters)
        if not isinstance(source, str) or not isinstance(delimiter, str) or not isinstance(index, int):
            return _CASE_UNSUPPORTED
        if not delimiter or index == 0:
            return _CASE_UNSUPPORTED
        parts = source.split(delimiter)
        position = index - 1 if index > 0 else len(parts) + index
        return parts[position] if 0 <= position < len(parts) else None
    if kind == "binary" and len(expression.children) == 2:
        left = _case_expression_value(expression.children[0], row, parameters)
        right = _case_expression_value(expression.children[1], row, parameters)
        if left is _CASE_UNSUPPORTED or right is _CASE_UNSUPPORTED:
            return _CASE_UNSUPPORTED
        if expression.op in {"concat", "add"} and isinstance(left, str) and isinstance(right, str):
            return left + right
        if expression.op == "eq":
            return left == right
        if expression.op == "ne":
            return left != right
        return _CASE_UNSUPPORTED
    if kind == "conditional" and len(expression.children) == 3:
        condition = _case_expression_value(expression.children[0], row, parameters)
        if not isinstance(condition, bool):
            return _CASE_UNSUPPORTED
        branch = expression.children[1] if condition else expression.children[2]
        return _case_expression_value(branch, row, parameters)
    return _CASE_UNSUPPORTED


def _case_surrogate_values(values: list[Any]) -> list[Any] | None:
    """Return visually identical case-preserving keys, or ``None`` without collisions."""
    variants: dict[str, list[str]] = {}
    for value in values:
        if not isinstance(value, str):
            continue
        folded = value.casefold()
        bucket = variants.setdefault(folded, [])
        if value not in bucket:
            bucket.append(value)
    collisions = {folded: items for folded, items in variants.items() if len(items) > 1}
    if not collisions:
        return None

    occupied = {value.casefold() for value in values if isinstance(value, str)}
    replacements: dict[str, str] = {}
    for items in collisions.values():
        replacements[items[0]] = items[0]
        reserved = {items[0].casefold()}
        for item in items[1:]:
            repeat = 1
            while True:
                candidate = item + (_CASE_SURROGATE_MARKER * repeat)
                folded_candidate = candidate.casefold()
                if folded_candidate not in occupied and folded_candidate not in reserved:
                    break
                repeat += 1
            replacements[item] = candidate
            reserved.add(folded_candidate)
    return [replacements.get(value, value) if isinstance(value, str) else value for value in values]


def _case_surrogate_name(entity: str, field_name: str) -> str:
    digest = hashlib.sha256(f"{entity}\0{field_name}".encode()).hexdigest()[:12]
    return f"__bridge_case_{digest}"


def _safe_stem(name: str) -> str:
    """Convierte ``name`` en un identificador de carpeta seguro para PBIP."""
    stem = _SAFE_NAME_RE.sub("_", name).strip("_")
    return stem or "bi_bridge_project"


@dataclass(frozen=True)
class PBIPFile:
    """Un archivo del proyecto PBIP, identificado por ruta relativa POSIX.

    Attributes:
        relative_path: ruta POSIX relativa dentro del proyecto (sin ``..``).
        content: contenido de texto (TMDL o JSON serializado).
        kind: categoría cerrada (``pbip_entry`` / ``platform`` / ``pbir_json``
            / ``tmdl``).
    """

    relative_path: str
    content: str
    kind: str

    _ALLOWED_KINDS: frozenset[str] = field(
        default=frozenset({"pbip_entry", "platform", "pbir_json", "tmdl"}),
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.relative_path or not isinstance(self.relative_path, str):
            raise ValueError("PBIPFile.relative_path is required")
        if self.kind not in self._ALLOWED_KINDS:
            raise ValueError(
                f"PBIPFile.kind no permitido: {self.kind!r}; "
                f"permitidos={sorted(self._ALLOWED_KINDS)}"
            )
        if "\\" in self.relative_path or self.relative_path.startswith("/"):
            raise ValueError(
                f"PBIPFile.relative_path debe ser POSIX relativa: {self.relative_path!r}"
            )
        if ".." in self.relative_path.split("/"):
            raise ValueError(f"PBIPFile.relative_path con '..': {self.relative_path!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serializa a un dict JSON-compatible."""
        return {
            "relative_path": self.relative_path,
            "content": self.content,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PBIPFile:
        """Reconstruye desde un dict."""
        return cls(
            relative_path=str(data["relative_path"]),
            content=str(data["content"]),
            kind=str(data["kind"]),
        )


@dataclass(frozen=True)
class PBIPProject:
    """Artefacto PBIP completo: archivos + aproximaciones explícitas.

    Attributes:
        schema_version: SemVer; debe igualar :data:`SCHEMA_VERSION`.
        display_name: nombre legible del proyecto (carpeta + ``.pbip``).
        origin: origen del IR compilado (un :class:`OriginKind` value).
        source_model: nombre del :class:`SemanticModel` compilado.
        files: tupla ordenada de :class:`PBIPFile` (rutas únicas).
        approximations: tupla de notas ``{category, source, detail}``.
    """

    schema_version: str
    display_name: str
    origin: str
    source_model: str
    files: tuple[PBIPFile, ...] = ()
    approximations: tuple[dict[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: esperada {SCHEMA_VERSION!r}, "
                f"recibida {self.schema_version!r}"
            )
        if not self.display_name or not isinstance(self.display_name, str):
            raise ValueError("PBIPProject.display_name is required")
        if not self.source_model or not isinstance(self.source_model, str):
            raise ValueError("PBIPProject.source_model is required")
        paths = [f.relative_path for f in self.files]
        if len(paths) != len(set(paths)):
            dupes = {p for p in paths if paths.count(p) > 1}
            raise ValueError(f"rutas PBIPFile duplicadas: {sorted(dupes)}")

    def to_dict(self) -> dict[str, Any]:
        """Serializa el proyecto a un dict JSON-compatible."""
        return {
            "schema_version": self.schema_version,
            "display_name": self.display_name,
            "origin": self.origin,
            "source_model": self.source_model,
            "files": [f.to_dict() for f in self.files],
            "approximations": [dict(a) for a in self.approximations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PBIPProject:
        """Reconstruye desde un dict, validando ``schema_version``."""
        received = data.get("schema_version")
        if received != SCHEMA_VERSION:
            raise ValueError(
                f"schema_version incompatible: esperada {SCHEMA_VERSION!r}, recibida {received!r}"
            )
        files = tuple(PBIPFile.from_dict(f) for f in data.get("files", []))
        approx = tuple(dict(a) for a in data.get("approximations", []))
        return cls(
            schema_version=SCHEMA_VERSION,
            display_name=str(data["display_name"]),
            origin=str(data["origin"]),
            source_model=str(data["source_model"]),
            files=files,
            approximations=approx,
        )

    @property
    def file_paths(self) -> list[str]:
        """Rutas relativas ordenadas (utilidad para verificación)."""
        return [f.relative_path for f in self.files]


def _pbir_filter_name(*parts: str) -> str:
    """Deterministic PBIR filter id (``Filter`` + 24 lowercase hex chars)."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"Filter{digest}"


def _pbir_literal(value: object) -> dict[str, dict[str, str]]:
    """Encode one neutral scalar using the SemanticQuery literal grammar."""
    if value is None:
        token = "null"
    elif isinstance(value, bool):
        token = "true" if value else "false"
    elif isinstance(value, int):
        token = f"{value}L"
    elif isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("PBIR filter literals must be finite")
        token = f"{value!r}D"
    elif isinstance(value, str):
        token = "'" + value.replace("'", "''") + "'"
    else:
        raise TypeError(f"unsupported PBIR filter literal type: {type(value).__name__}")
    return {"Literal": {"Value": token}}


def _pbir_target_expression(
    *, kind: str, entity: str, property_name: str, source_alias: str | None = None
) -> dict[str, Any]:
    """Build a Column/Measure QueryExpressionContainer for PBIR filters."""
    ref = {"Source": source_alias} if source_alias else {"Entity": entity}
    key = "Measure" if kind == "measure" else "Column"
    return {key: {"Expression": {"SourceRef": ref}, "Property": property_name}}


def _pbir_filter_container(
    *,
    stable_key: str,
    display_name: str,
    kind: str,
    entity: str,
    property_name: str,
    operator: str,
    values: tuple[object, ...] | list[object],
) -> tuple[dict[str, Any] | None, str | None]:
    """Translate a closed neutral filter subset into a native PBIR filter.

    Returns ``(container, reason)``. Unsupported or under-specified operators are
    omitted rather than encoded with proprietary properties.
    """
    aliases = {
        "equals": "eq",
        "not_equals": "ne",
        "greater_than": "gt",
        "less_than": "lt",
        "not_starts_with": "not_starts_with",
        "top_n": "top_n",
    }
    op = aliases.get(operator, operator)
    vals = tuple(values)
    source_alias = "f"
    top_field = _pbir_target_expression(kind=kind, entity=entity, property_name=property_name)
    where_field = _pbir_target_expression(
        kind=kind, entity=entity, property_name=property_name, source_alias=source_alias
    )

    condition: dict[str, Any]
    filter_type: str
    if op in {"eq", "in", "ne", "not_in"}:
        if not vals:
            return None, "filter has no selected values"
        if op in {"eq", "ne"} and len(vals) != 1:
            return None, f"operator {op!r} requires exactly one value"
        in_expr = {
            "In": {
                "Expressions": [where_field],
                "Values": [[_pbir_literal(value)] for value in vals],
            }
        }
        condition = {"Not": {"Expression": in_expr}} if op in {"ne", "not_in"} else in_expr
        filter_type = "Categorical"
    elif op == "between":
        if len(vals) != 2:
            return None, "operator 'between' requires exactly two values"
        condition = {
            "Between": {
                "Expression": where_field,
                "LowerBound": _pbir_literal(vals[0]),
                "UpperBound": _pbir_literal(vals[1]),
            }
        }
        filter_type = "Advanced"
    elif op in {"gt", "gte", "lt", "lte"}:
        if len(vals) != 1:
            return None, f"operator {op!r} requires exactly one value"
        comparison_kind = {"gt": 1, "gte": 2, "lt": 3, "lte": 4}[op]
        condition = {
            "Comparison": {
                "ComparisonKind": comparison_kind,
                "Left": where_field,
                "Right": _pbir_literal(vals[0]),
            }
        }
        filter_type = "Advanced"
    elif op in {"is_null", "is_not_null"}:
        comparison = {
            "Comparison": {
                "ComparisonKind": 0,
                "Left": where_field,
                "Right": _pbir_literal(None),
            }
        }
        condition = {"Not": {"Expression": comparison}} if op == "is_not_null" else comparison
        filter_type = "Advanced"
    elif op in {"contains", "not_contains", "starts_with", "not_starts_with"}:
        if len(vals) != 1:
            return None, f"operator {op!r} requires exactly one value"
        expression_key = "StartsWith" if "starts_with" in op else "Contains"
        predicate = {
            expression_key: {
                "Left": where_field,
                "Right": _pbir_literal(vals[0]),
            }
        }
        condition = (
            {"Not": {"Expression": predicate}}
            if op in {"not_contains", "not_starts_with"}
            else predicate
        )
        filter_type = "Advanced"
    elif op in {"ends_with", "not_ends_with"}:
        return None, "PBIR SemanticQuery 1.3.0 has no EndsWith expression"
    elif op == "top_n":
        return None, "TopN requires an order-by expression not present in the neutral FilterCondition"
    else:
        return None, f"operator {op!r} has no verified PBIR mapping"

    return (
        {
            "name": _pbir_filter_name(stable_key, entity, property_name, op),
            "displayName": display_name or property_name,
            "field": top_field,
            "type": filter_type,
            "filter": {
                "Version": 2,
                "From": [{"Name": source_alias, "Entity": entity, "Type": 0}],
                "Where": [{"Condition": condition}],
            },
            "howCreated": "User",
        },
        None,
    )


class PBIPAdapter(DestinationCompiler):
    """Compila IR neutral aceptado en un proyecto PBIP (TMDL + PBIR + envoltura).

    Subclase de :class:`DestinationCompiler` con destino ``"power_bi_pbip"``.
    A diferencia de :class:`PBIPPlanCompiler` (que sólo emite la *forma* del
    plan), este adaptador genera los artefactos concretos: texto TMDL, JSON
    PBIR y manifiestos ``.pbip``/``.platform``, sin reparséar Tableau.

    Args:
        origin: origen del IR (un :class:`OriginKind` value).
        presentation: :class:`PresentationIR` opcional (layout + bindings). Si
            se omite, se deriva uno por defecto (anotado como aproximación).
        interaction: :class:`InteractionIR` opcional (filtros por página).
        display_name: nombre del proyecto PBIP; por defecto el del modelo.
    """

    def __init__(
        self,
        *,
        datasets: Mapping[str, Dataset],
        origin: str = "unknown",
        presentation: PresentationIR | None = None,
        interaction: InteractionIR | None = None,
        display_name: str | None = None,
        relationship_strategy: str = "",
    ) -> None:
        super().__init__(origin=origin)
        self._datasets = dict(datasets)
        self._presentation = presentation
        if relationship_strategy not in {"", "materialized_federation"}:
            raise ValueError(f"unsupported relationship strategy: {relationship_strategy!r}")
        self._interaction = interaction
        self._display_name = display_name
        self._relationship_strategy = relationship_strategy
        self._case_surrogates: dict[tuple[str, str], str] = {}

    @property
    def destination(self) -> str:
        return "power_bi_pbip"

    def compile(self, model: SemanticModel) -> DestinationPlan:
        """Compila ``model`` en un :class:`PBIPProject` envuelto en DestinationPlan."""
        project = self.build_project(model)
        return self._envelope(model, project.to_dict())

    def _prepare_case_sensitive_surrogates(
        self, model: SemanticModel
    ) -> tuple[
        SemanticModel,
        dict[str, Dataset],
        dict[tuple[str, str], str],
        list[dict[str, str]],
    ]:
        """Materialize invisible grouping keys only for proven case collisions."""
        if self._presentation is None:
            return model, dict(self._datasets), {}, []
        bound_fields = {
            binding.field_name
            for page in self._presentation.pages
            for visual in page.visuals
            for binding in visual.bindings
        }
        parameter_defaults = {parameter.name: parameter.default_value for parameter in model.parameters}
        datasets = dict(self._datasets)
        surrogate_map: dict[tuple[str, str], str] = {}
        notes: list[dict[str, str]] = []
        entities: list[Entity] = []

        for entity in model.entities:
            dataset = datasets.get(entity.name)
            if dataset is None:
                entities.append(entity)
                continue
            rows = [dict(row) for row in dataset.rows]
            columns = list(dataset.columns)
            fields = list(entity.fields)
            changed = False
            for model_field in entity.fields:
                if model_field.name not in bound_fields or model_field.data_type is not DataType.STRING:
                    continue
                if model_field.expression is None:
                    if model_field.name not in dataset.columns:
                        continue
                    values = [row.get(model_field.name) for row in rows]
                else:
                    values = [
                        _case_expression_value(model_field.expression, row, parameter_defaults)
                        for row in rows
                    ]
                    if any(value is _CASE_UNSUPPORTED for value in values):
                        continue
                surrogate_values = _case_surrogate_values(values)
                if surrogate_values is None:
                    continue
                helper = _case_surrogate_name(entity.name, model_field.name)
                if helper in columns:
                    continue
                for row, value in zip(rows, surrogate_values):
                    row[helper] = value
                columns.append(helper)
                fields.append(
                    Field(
                        name=helper,
                        data_type=DataType.STRING,
                        hidden=True,
                        description=f"case_sensitive_surrogate_for:{model_field.name}",
                        source_column=helper,
                    )
                )
                surrogate_map[(entity.name, model_field.name)] = helper
                notes.append(
                    {
                        "category": "case_sensitive_surrogate",
                        "source": f"{entity.name}.{model_field.name}",
                        "detail": (
                            f"case-insensitive Power BI grouping for {model_field.name!r} uses "
                            f"hidden {helper!r}; original field remains unchanged"
                        ),
                    }
                )
                changed = True
            if changed:
                datasets[entity.name] = Dataset.from_records(
                    entity.name,
                    rows,
                    columns=columns,
                )
                entities.append(replace(entity, fields=fields))
            else:
                entities.append(entity)

        if not surrogate_map:
            return model, datasets, {}, notes
        return replace(model, entities=entities), datasets, surrogate_map, notes

    def build_project(self, model: SemanticModel) -> PBIPProject:
        """Construye el :class:`PBIPProject` completo desde el IR neutral."""
        destination_model, destination_datasets, case_surrogates, case_notes = (
            self._prepare_case_sensitive_surrogates(model)
        )
        previous_datasets = self._datasets
        previous_surrogates = self._case_surrogates
        self._datasets = destination_datasets
        self._case_surrogates = case_surrogates
        try:
            approximations: list[dict[str, str]] = list(case_notes)
            display_name = self._display_name or destination_model.name
            stem = _safe_stem(display_name)
            report_folder = f"{stem}.Report"
            model_folder = f"{stem}.SemanticModel"

            files: list[PBIPFile] = []
            files.extend(
                self._build_semantic_model(model_folder, destination_model, approximations)
            )
            files.extend(
                self._build_report(report_folder, destination_model, display_name, approximations)
            )
            files.append(self._pbip_entry(stem, report_folder, model_folder, approximations))
            project = PBIPProject(
                schema_version=SCHEMA_VERSION,
                display_name=display_name,
                origin=self.origin,
                source_model=destination_model.name,
                files=tuple(files),
                approximations=tuple(approximations),
            )
        finally:
            self._datasets = previous_datasets
            self._case_surrogates = previous_surrogates
        return project

    # --- Semantic model (TMDL) ---------------------------------------------

    def _build_semantic_model(
        self, model_folder: str, model: SemanticModel, approximations: list[dict[str, str]]
    ) -> list[PBIPFile]:
        """Genera ``model.tmdl`` + un ``.tmdl`` por tabla + ``.platform``."""
        files: list[PBIPFile] = []

        # Medidas: el IR neutral no fija tabla anfitriona. Se asignan a la
        # entidad indicada salvo que su nombre choque con una columna de esa
        # tabla. Power BI rechaza ese caso; se usa `_measures` sólo entonces.
        measure_home, synthesized = self._measure_home(model)
        if synthesized:
            approximations.append(
                {
                    "category": "measure_home",
                    "source": model.name,
                    "detail": (
                        "medidas con conflicto columna/medida (o sin entidad) "
                        f"alojadas en tabla sintética {measure_home!r}"
                    ),
                }
            )
        else:
            approximations.append(
                {
                    "category": "measure_home",
                    "source": model.name,
                    "detail": f"medidas sin tabla anfitriona neutrales; asignadas a {measure_home!r}",
                }
            )

        extra_table_names = (
            ((measure_home,) if synthesized else ())
            + tuple(f"_Parameter_{parameter.name}" for parameter in model.parameters)
        )
        model_tmdl, model_notes = tmdl.render_model_tmdl(
            model, extra_table_names, relationship_strategy=self._relationship_strategy
        )
        relationships_tmdl, relationship_notes = tmdl.render_relationships_tmdl(model)
        for note in (*model_notes, *relationship_notes):
            approximations.append(
                {"category": "tmdl_relationship", "source": model.name, "detail": note}
            )
        files.append(
            PBIPFile(
                relative_path=f"{model_folder}/definition/model.tmdl",
                content=model_tmdl,
                kind="tmdl",
            )
        )
        if relationships_tmdl:
            files.append(
                PBIPFile(
                    relative_path=f"{model_folder}/definition/relationships.tmdl",
                    content=relationships_tmdl,
                    kind="tmdl",
                )
            )
        parameter_tables: dict[str, tuple[Entity, Dataset, Parameter]] = {}
        for parameter in model.parameters:
            table_name = f"_Parameter_{parameter.name}"
            values = list(parameter.allowed_values)
            if not values:
                values = (
                    list(parameter.default_value)
                    if isinstance(parameter.default_value, (list, tuple))
                    else [parameter.default_value]
                )
            values = [value for value in values if value is not None]
            data_type = (
                DataType.STRING
                if parameter.data_type in {DataType.UNKNOWN, DataType.VARIANT}
                else parameter.data_type
            )
            parameter_tables[table_name] = (
                Entity(name=table_name, fields=[Field(name="Value", data_type=data_type)]),
                Dataset.from_records(
                    table_name,
                    [{"Value": value} for value in values],
                    columns=("Value",),
                ),
                parameter,
            )

        tables = list(model.entities)
        if synthesized:
            tables.append(measure_home_placeholder(measure_home))
        tables.extend(entity for entity, _, _ in parameter_tables.values())

        for entity in tables:
            owns = [
                metric
                for metric in model.metrics
                if self._metric_home(metric, model, measure_home) == entity.name
            ]
            parameter_entry = parameter_tables.get(entity.name)
            if parameter_entry is not None:
                dataset = parameter_entry[1]
            elif synthesized and entity.name == measure_home:
                dataset = Dataset.from_records(
                    entity.name,
                    [{"__bridge_anchor": 1}],
                    columns=("__bridge_anchor",),
                )
            else:
                dataset = self._datasets.get(entity.name)
                if dataset is None:
                    raise ValueError(f"dataset materializado ausente para {entity.name!r}")
            owns_parameters = (
                [parameter for parameter in model.parameters if not parameter.is_set]
                if entity.name == measure_home
                else []
            )
            table_tmdl, table_notes = tmdl.render_table_tmdl(
                entity,
                dataset,
                owns,
                owns_parameters,
                parameter_definition=parameter_entry[2] if parameter_entry is not None else None,
            )
            for note in table_notes:
                approximations.append(
                    {"category": "tmdl_table", "source": entity.name, "detail": note}
                )
            safe = _safe_stem(entity.name) or entity.name
            files.append(
                PBIPFile(
                    relative_path=f"{model_folder}/definition/tables/{safe}.tmdl",
                    content=table_tmdl,
                    kind="tmdl",
                )
            )

        files.append(
            self._platform(model_folder, "SemanticModel", self._display_name or model.name)
        )
        files.append(
            _json_file(
                f"{model_folder}/definition.pbism",
                {
                    "$schema": (
                        "https://developer.microsoft.com/json-schemas/fabric/item/"
                        "semanticModel/definitionProperties/1.0.0/schema.json"
                    ),
                    "version": "4.2",
                    "settings": {},
                },
            )
        )
        return files

    def _measure_home(self, model: SemanticModel) -> tuple[str, bool]:
        """Devuelve la tabla por defecto y si hace falta una anfitriona segura."""
        if model.entities:
            default = model.entities[0].name
            collision = any(
                metric.name
                in {
                    field.name
                    for entity in model.entities
                    if entity.name == (metric.entity or default)
                    for field in entity.fields
                }
                for metric in model.metrics
            )
            if not collision:
                return default, False
        occupied = {entity.name for entity in model.entities}
        candidate = "_measures"
        suffix = 2
        while candidate in occupied:
            candidate = f"_measures_{suffix}"
            suffix += 1
        return candidate, True

    @staticmethod
    def _metric_home(metric: Any, model: SemanticModel, safe_home: str) -> str:
        """Aloja sólo las medidas que chocan con columnas en la tabla segura."""
        default = model.entities[0].name if model.entities else safe_home
        requested = metric.entity or default
        fields = {
            field.name
            for entity in model.entities
            if entity.name == requested
            for field in entity.fields
        }
        return safe_home if metric.name in fields else requested

    # --- Report (PBIR) ------------------------------------------------------

    def _build_report(
        self,
        report_folder: str,
        model: SemanticModel,
        display_name: str,
        approximations: list[dict[str, str]],
    ) -> list[PBIPFile]:
        """Genera los artefactos PBIR (version/report/pages/page/visual)."""
        files: list[PBIPFile] = []
        definition = f"{report_folder}/definition"

        presentation = self._presentation
        if presentation is None:
            presentation = _derive_default_presentation(model)
            approximations.append(
                {
                    "category": "presentation",
                    "source": model.name,
                    "detail": "sin PresentationIR: layout por defecto derivado del modelo",
                }
            )

        report_filters = self._report_filters(model, presentation, approximations)

        files.append(_json_file(f"{definition}/version.json", pbir.render_version_json()))
        files.append(
            _json_file(
                f"{definition}/report.json",
                pbir.render_report_json(
                    display_name, presentation, filters_annotation=report_filters
                ),
            )
        )

        page_ids: list[str] = []
        for page_index, page in enumerate(presentation.pages, start=1):
            pid = pbir.page_id(page.name)
            page_ids.append(pid)
            page_dir = f"{definition}/pages/{pid}"
            page_inter = self._page_interaction(page.page_id)
            page_filters = self._page_filters(
                model, page, page_index, page_inter, approximations
            )
            visual_interactions = self._visual_interactions(page, page_inter, approximations)
            self._record_unemitted_interactions(page, page_inter, approximations)
            files.append(
                _json_file(
                    f"{page_dir}/page.json",
                    pbir.render_page_json(
                        page,
                        pid,
                        bridge_filters=page_filters,
                        visual_interactions=visual_interactions,
                    ),
                )
            )

            for visual in page.visuals:
                resolved, bind_notes = self._resolve_bindings(visual.bindings, model)
                for note in bind_notes:
                    approximations.append(
                        {"category": "binding", "source": visual.visual_id, "detail": note}
                    )
                visual_filters = self._visual_filters(
                    model, page, page_index, visual, page_inter, approximations
                )
                visual_doc, vis_notes = pbir.render_visual_json(
                    page, visual, resolved, filters=visual_filters
                )
                for note in vis_notes:
                    approximations.append(
                        {"category": "visual", "source": visual.visual_id, "detail": note}
                    )
                vid = pbir.visual_id(page.name, visual.visual_id)
                files.append(_json_file(f"{page_dir}/visuals/{vid}/visual.json", visual_doc))

        files.append(_json_file(f"{definition}/pages/pages.json", pbir.render_pages_json(page_ids)))
        files.append(
            _json_file(
                f"{report_folder}/definition.pbir",
                {
                    "$schema": (
                        "https://developer.microsoft.com/json-schemas/fabric/item/"
                        "report/definitionProperties/2.0.0/schema.json"
                    ),
                    "version": "4.0",
                    "datasetReference": {
                        "byPath": {"path": f"../{_safe_stem(display_name)}.SemanticModel"}
                    },
                },
            )
        )
        files.append(self._platform(report_folder, "Report", display_name))
        return files

    def _page_interaction(self, page_id: str) -> Any:
        """Localiza la interacción de página por ``page_id`` en InteractionIR."""
        if self._interaction is None:
            return None
        for page in self._interaction.pages:
            if page.page_id == page_id:
                return page
        return None

    def _resolve_bindings(
        self, bindings: tuple[DataBinding, ...], model: SemanticModel
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Resuelve cada :class:`DataBinding` contra el modelo (medida/columna)."""
        metrics = {metric.name: metric for metric in model.metrics}
        parameters = {parameter.name: parameter for parameter in model.parameters}
        resolved: list[dict[str, Any]] = []
        notes: list[str] = []
        for binding in bindings:
            field_name = binding.field_name
            role = binding.role.value if hasattr(binding.role, "value") else str(binding.role)
            if field_name in metrics:
                metric = metrics[field_name]
                measure_home, _ = self._measure_home(model)
                resolved.append(
                    {
                        "role": role,
                        "kind": "measure",
                        "table": self._metric_home(metric, model, measure_home),
                        "name": field_name,
                        "display_name": binding.display_name or field_name,
                        "aggregation": binding.aggregation,
                    }
                )
                continue
            if field_name in parameters:
                if role == "color":
                    notes.append(
                        f"parámetro {field_name!r} omitido como categoría de color PBIR"
                    )
                    continue
                resolved.append(
                    {
                        "role": role,
                        "kind": "column",
                        "table": f"_Parameter_{field_name}",
                        "name": "Value",
                        "display_name": binding.display_name or field_name,
                        "aggregation": None,
                    }
                )
                continue
            owners = [e.name for e in model.entities if any(f.name == field_name for f in e.fields)]
            if owners:
                table = owners[0]
                source_field = next(
                    field
                    for entity in model.entities
                    if entity.name == table
                    for field in entity.fields
                    if field.name == field_name
                )
                if role == "color" and source_field.data_type == DataType.BOOLEAN:
                    notes.append(
                        f"campo booleano {field_name!r} omitido como categoría de color PBIR"
                    )
                    continue
                surrogate = self._case_surrogates.get((table, field_name))
                if surrogate:
                    notes.append(
                        f"campo {field_name!r} usa surrogate case-sensitive oculto {surrogate!r} en PBIR"
                    )
                    field_name = surrogate
                elif source_field.expression is not None:
                    physical_names = {
                        field.name
                        for entity in model.entities
                        if entity.name == table
                        for field in entity.fields
                        if field.expression is None
                    }
                    fallback = next(
                        (
                            node.name
                            for node in walk_expression(source_field.expression)
                            if node.kind == "field_ref" and node.name in physical_names
                        ),
                        "",
                    )
                    if fallback:
                        notes.append(
                            f"campo calculado {field_name!r} aproximado con {fallback!r} en PBIR"
                        )
                        field_name = fallback
                if len(owners) > 1:
                    notes.append(
                        f"campo {field_name!r} presente en {len(owners)} tablas; "
                        f"resuelto a {table!r}"
                    )
                resolved.append(
                    {
                        "role": role,
                        "kind": "column",
                        "table": table,
                        "name": field_name,
                        "display_name": binding.display_name or field_name,
                        "aggregation": binding.aggregation,
                    }
                )
            else:
                notes.append(f"binding {field_name!r} no resuelto contra el modelo")
                resolved.append(
                    {
                        "role": role,
                        "kind": "unresolved",
                        "table": "",
                        "name": field_name,
                        "display_name": binding.display_name or field_name,
                        "aggregation": binding.aggregation,
                    }
                )
        return resolved, notes

    @staticmethod
    def _filter_approximation(
        approximations: list[dict[str, str]], source: str, detail: str
    ) -> None:
        approximations.append(
            {"category": "filter", "source": source or "unnamed_filter", "detail": detail}
        )

    def _semantic_filter_target(
        self, model: SemanticModel, target: Expression
    ) -> tuple[str, str, str] | None:
        """Resolve a SemanticModel filter target to PBIR kind/entity/property."""
        if target.kind == "measure_ref":
            metric = next((item for item in model.metrics if item.name == target.name), None)
            if metric is None:
                return None
            measure_home, _ = self._measure_home(model)
            return (
                "measure",
                self._metric_home(metric, model, measure_home),
                metric.name,
            )
        if target.kind != "field_ref":
            return None
        if target.entity:
            entity = next((item for item in model.entities if item.name == target.entity), None)
            if entity is not None and any(field.name == target.name for field in entity.fields):
                return "column", entity.name, target.name
        owners = [
            entity.name
            for entity in model.entities
            if any(field.name == target.name for field in entity.fields)
        ]
        if len(owners) != 1:
            return None
        return "column", owners[0], target.name

    def _interaction_filter_target(
        self, model: SemanticModel, target_field: str
    ) -> tuple[str, str, str] | None:
        """Resolve InteractionIR target_field without guessing ambiguous ownership."""
        if "." in target_field:
            prefix, suffix = target_field.split(".", 1)
            entity = next((item for item in model.entities if item.name == prefix), None)
            if entity is not None and any(field.name == suffix for field in entity.fields):
                return "column", entity.name, suffix
        owners = [
            entity.name
            for entity in model.entities
            if any(field.name == target_field for field in entity.fields)
        ]
        metrics = [metric for metric in model.metrics if metric.name == target_field]
        parameters = [parameter for parameter in model.parameters if parameter.name == target_field]
        candidates = len(owners) + len(metrics) + len(parameters)
        if candidates != 1:
            return None
        if owners:
            return "column", owners[0], target_field
        if metrics:
            measure_home, _ = self._measure_home(model)
            metric = metrics[0]
            return "measure", self._metric_home(metric, model, measure_home), metric.name
        return "column", f"_Parameter_{parameters[0].name}", "Value"

    def _native_filter(
        self,
        *,
        stable_key: str,
        display_name: str,
        target: tuple[str, str, str] | None,
        operator: object,
        values: tuple[object, ...] | list[object],
        approximations: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        source = display_name or stable_key
        if target is None:
            self._filter_approximation(
                approximations,
                source,
                "target field/measure is missing or ambiguous; filter not emitted to PBIR",
            )
            return None
        op = operator.value if hasattr(operator, "value") else str(operator)
        kind, entity, property_name = target
        try:
            container, reason = _pbir_filter_container(
                stable_key=stable_key,
                display_name=display_name,
                kind=kind,
                entity=entity,
                property_name=property_name,
                operator=str(op),
                values=values,
            )
        except (TypeError, ValueError) as exc:
            self._filter_approximation(
                approximations, source, f"invalid PBIR filter literal: {exc}"
            )
            return None
        if container is None:
            self._filter_approximation(
                approximations,
                source,
                f"{reason or 'no verified PBIR mapping'}; retained only in canonical IR",
            )
        return container

    @staticmethod
    def _semantic_scope_tokens(applies_to: list[str]) -> tuple[str, ...]:
        return tuple(token.strip() for token in applies_to if token.strip()) or ("report",)

    @staticmethod
    def _page_scope_matches(token: str, page: PagePresentation, page_index: int) -> bool:
        lowered = token.casefold()
        page_values = {
            page.page_id.casefold(),
            page.name.casefold(),
            (page.display_name or "").casefold(),
            str(page_index),
        }
        if lowered.startswith("page:"):
            return lowered[5:].strip() in page_values
        return lowered in page_values

    @staticmethod
    def _visual_scope_matches(token: str, visual_id_value: str) -> bool:
        lowered = token.casefold()
        if lowered.startswith("visual:"):
            return lowered[7:].strip() == visual_id_value.casefold()
        return False

    def _report_filters(
        self,
        model: SemanticModel,
        presentation: PresentationIR,
        approximations: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Compile report/workbook-scoped filters into report.filterConfig."""
        items: list[dict[str, Any]] = []
        known_scopes = {"report", "workbook"}
        for flt in model.filters:
            scopes = self._semantic_scope_tokens(flt.applies_to)
            if any(scope.casefold() in known_scopes for scope in scopes):
                native = self._native_filter(
                    stable_key=f"semantic:{flt.name}:report",
                    display_name=flt.name,
                    target=self._semantic_filter_target(model, flt.target),
                    operator=flt.operator,
                    values=flt.values,
                    approximations=approximations,
                )
                if native is not None:
                    items.append(native)
            for scope in scopes:
                lowered = scope.casefold()
                if lowered in known_scopes or lowered.startswith(("page:", "visual:")):
                    continue
                if any(
                    self._page_scope_matches(scope, page, index)
                    for index, page in enumerate(presentation.pages, start=1)
                ):
                    continue
                self._filter_approximation(
                    approximations,
                    flt.name,
                    f"unknown applies_to scope {scope!r}; filter not widened to report scope",
                )

        if self._interaction is not None:
            report_conditions = list(self._interaction.global_filters)
            report_conditions.extend(
                condition
                for page in self._interaction.pages
                for condition in page.filters
                if (condition.scope.value if hasattr(condition.scope, "value") else str(condition.scope))
                == "workbook"
            )
            for condition in report_conditions:
                native = self._native_filter(
                    stable_key=f"interaction:{condition.filter_id}:report",
                    display_name=condition.name or condition.filter_id,
                    target=self._interaction_filter_target(model, condition.target_field),
                    operator=condition.operator,
                    values=condition.values,
                    approximations=approximations,
                )
                if native is not None:
                    items.append(native)
        return items

    def _page_filters(
        self,
        model: SemanticModel,
        page: PagePresentation,
        page_index: int,
        page_inter: Any,
        approximations: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Compile filters whose effective scope is the whole PBIR page."""
        items: list[dict[str, Any]] = []
        for flt in model.filters:
            scopes = self._semantic_scope_tokens(flt.applies_to)
            if not any(self._page_scope_matches(scope, page, page_index) for scope in scopes):
                continue
            native = self._native_filter(
                stable_key=f"semantic:{flt.name}:page:{page.page_id}",
                display_name=flt.name,
                target=self._semantic_filter_target(model, flt.target),
                operator=flt.operator,
                values=flt.values,
                approximations=approximations,
            )
            if native is not None:
                items.append(native)
        if page_inter is None:
            return items
        for condition in page_inter.filters:
            scope = condition.scope.value if hasattr(condition.scope, "value") else str(condition.scope)
            if scope != "page" or condition.target_visual_ids:
                continue
            native = self._native_filter(
                stable_key=f"interaction:{condition.filter_id}:page:{page.page_id}",
                display_name=condition.name or condition.filter_id,
                target=self._interaction_filter_target(model, condition.target_field),
                operator=condition.operator,
                values=condition.values,
                approximations=approximations,
            )
            if native is not None:
                items.append(native)
        return items

    def _visual_filters(
        self,
        model: SemanticModel,
        page: PagePresentation,
        page_index: int,
        visual: VisualPresentation,
        page_inter: Any,
        approximations: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        """Compile target-specific filters into visual.filterConfig."""
        items: list[dict[str, Any]] = []
        for flt in model.filters:
            scopes = self._semantic_scope_tokens(flt.applies_to)
            if not any(self._visual_scope_matches(scope, visual.visual_id) for scope in scopes):
                continue
            native = self._native_filter(
                stable_key=f"semantic:{flt.name}:visual:{visual.visual_id}",
                display_name=flt.name,
                target=self._semantic_filter_target(model, flt.target),
                operator=flt.operator,
                values=flt.values,
                approximations=approximations,
            )
            if native is not None:
                items.append(native)
        if page_inter is None:
            return items
        for condition in page_inter.filters:
            scope = condition.scope.value if hasattr(condition.scope, "value") else str(condition.scope)
            target_ids = set(condition.target_visual_ids)
            if condition.is_interactive_slicer:
                # The initial selection is state of the slicer itself. Applying
                # it as a static filter to every target would make the targets
                # independent of subsequent slicer changes. Empty values mean
                # "All"/cleared selection and require no filterConfig.
                if visual.visual_id != condition.filter_id or not condition.values:
                    continue
            else:
                applies = visual.visual_id in target_ids or (scope == "visual" and not target_ids)
                if not applies:
                    continue
                if scope == "visual" and not target_ids:
                    self._filter_approximation(
                        approximations,
                        condition.name or condition.filter_id,
                        "visual-scoped filter has no target_visual_ids; filter not widened to every visual",
                    )
                    continue
            native = self._native_filter(
                stable_key=f"interaction:{condition.filter_id}:visual:{visual.visual_id}",
                display_name=condition.name or condition.filter_id,
                target=self._interaction_filter_target(model, condition.target_field),
                operator=condition.operator,
                values=condition.values,
                approximations=approximations,
            )
            if native is not None:
                items.append(native)
        return items

    def _visual_interactions(
        self,
        page: PagePresentation,
        page_inter: Any,
        approximations: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Emit CrossFilterRule as native page.visualInteractions entries."""
        if page_inter is None:
            return []
        known = {visual.visual_id for visual in page.visuals}
        mapping = {"filter": "DataFilter", "highlight": "HighlightFilter", "none": "NoFilter"}
        entries: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for rule in page_inter.cross_filters:
            behavior = rule.behavior.value if hasattr(rule.behavior, "value") else str(rule.behavior)
            pbir_type = mapping.get(behavior)
            if rule.source_visual_id not in known or pbir_type is None:
                approximations.append(
                    {
                        "category": "interaction_cross_filter",
                        "source": rule.rule_id,
                        "detail": "source visual missing or interaction behavior unsupported; not emitted",
                    }
                )
                continue
            if not rule.target_visual_ids:
                approximations.append(
                    {
                        "category": "interaction_cross_filter",
                        "source": rule.rule_id,
                        "detail": "no target visuals declared; default PBIR interaction remains in effect",
                    }
                )
                continue
            source = pbir.visual_id(page.name, rule.source_visual_id)
            for target_visual_id in rule.target_visual_ids:
                if target_visual_id not in known:
                    approximations.append(
                        {
                            "category": "interaction_cross_filter",
                            "source": rule.rule_id,
                            "detail": f"target visual {target_visual_id!r} missing; edge not emitted",
                        }
                    )
                    continue
                target = pbir.visual_id(page.name, target_visual_id)
                edges = [(source, target, pbir_type)]
                if rule.bidirectional:
                    edges.append((target, source, pbir_type))
                for edge in edges:
                    if edge in seen:
                        continue
                    seen.add(edge)
                    entries.append({"source": edge[0], "target": edge[1], "type": edge[2]})

        # Interactive slicers are visual interaction sources too. When the
        # canonical rule names explicit targets, pin those targets to DataFilter
        # and explicitly disable non-target visuals. Existing CrossFilterRule
        # edges take precedence for the same source/target pair.
        occupied_pairs = {(entry["source"], entry["target"]) for entry in entries}
        for condition in page_inter.filters:
            if not condition.is_interactive_slicer or not condition.target_visual_ids:
                continue
            if condition.filter_id not in known:
                approximations.append(
                    {
                        "category": "interaction_slicer",
                        "source": condition.filter_id,
                        "detail": "interactive slicer source visual missing; target edges not emitted",
                    }
                )
                continue
            requested = set(condition.target_visual_ids)
            missing = sorted(requested - known)
            if missing:
                approximations.append(
                    {
                        "category": "interaction_slicer",
                        "source": condition.filter_id,
                        "detail": f"target visual(s) missing: {missing!r}",
                    }
                )
            source = pbir.visual_id(page.name, condition.filter_id)
            for target_visual_id in sorted(known - {condition.filter_id}):
                target = pbir.visual_id(page.name, target_visual_id)
                pair = (source, target)
                if pair in occupied_pairs:
                    continue
                interaction_type = "DataFilter" if target_visual_id in requested else "NoFilter"
                occupied_pairs.add(pair)
                entries.append(
                    {"source": source, "target": target, "type": interaction_type}
                )
        return entries

    @staticmethod
    def _record_unemitted_interactions(
        page: PagePresentation,
        page_inter: Any,
        approximations: list[dict[str, str]],
    ) -> None:
        """Make unsupported InteractionIR features explicit instead of dropping silently."""
        if page_inter is None:
            return
        categories = (
            ("selection", page_inter.selections),
            ("drill", page_inter.drills),
            ("tooltip", page_inter.tooltips),
            ("navigation", page_inter.navigations),
        )
        for kind, values in categories:
            if not values:
                continue
            approximations.append(
                {
                    "category": f"interaction_{kind}",
                    "source": page.page_id,
                    "detail": (
                        f"{len(values)} {kind} interaction(s) retained in canonical InteractionIR; "
                        "no verified PBIR emission in this slice"
                    ),
                }
            )

    # --- Envelope files -----------------------------------------------------

    def _pbip_entry(
        self, stem: str, report_folder: str, model_folder: str, approximations: list[dict[str, str]]
    ) -> PBIPFile:
        """Genera el archivo ``.pbip`` de entrada del proyecto."""
        approximations.append(
            {
                "category": "pbip_entry",
                "source": stem,
                "detail": "manifiesto PBIP Desktop 1.0 con artefacto Report",
            }
        )
        entry = {
            "$schema": (
                "https://developer.microsoft.com/json-schemas/fabric/pbip/"
                "pbipProperties/1.0.0/schema.json"
            ),
            "version": "1.0",
            "artifacts": [{"report": {"path": report_folder}}],
            "settings": {"enableAutoRecovery": True},
        }
        return PBIPFile(
            relative_path=f"{stem}.pbip",
            content=_canonical_json(entry),
            kind="pbip_entry",
        )

    def _platform(self, folder: str, item_type: str, display_name: str) -> PBIPFile:
        """Genera el ``.platform`` de un ítem (Report / SemanticModel)."""
        import uuid

        manifest = {
            "$schema": (
                "https://developer.microsoft.com/json-schemas/fabric/"
                "gitIntegration/platformProperties/2.0.0/schema.json"
            ),
            "metadata": {"type": item_type, "displayName": display_name},
            "config": {
                "version": "2.0",
                "logicalId": str(uuid.uuid5(uuid.NAMESPACE_URL, f"bi-bridge:{folder}")),
            },
        }
        return PBIPFile(
            relative_path=f"{folder}/.platform",
            content=_canonical_json(manifest),
            kind="platform",
        )


# --- Helpers -----------------------------------------------------------------


def _canonical_json(data: Mapping[str, Any]) -> str:
    """JSON canónico (claves ordenadas, UTF-8 puro, 2 espacios)."""
    import json

    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)


def _json_file(relative_path: str, data: Mapping[str, Any]) -> PBIPFile:
    """Envuelve un dict JSON en un :class:`PBIPFile` ``pbir_json``."""
    return PBIPFile(relative_path=relative_path, content=_canonical_json(data), kind="pbir_json")


def _expr_target_name(expr: Any) -> str:
    """Nombre legible del ``target`` de un :class:`Filter` neutral."""
    return getattr(expr, "name", "") or getattr(expr, "entity", "") or str(expr.kind)


def measure_home_placeholder(name: str):
    """Entidad sintética mínima para alojar medidas cuando el modelo no tiene tablas.

    Devuelve un :class:`Entity` con una columna marcador; el IR neutral puede
    no declarar entidades (sólo medidas), pero TMDL requiere una tabla anfitriona.
    """
    from core.contracts.semantic_ir import DataType, Entity, Field

    return Entity(
        name=name,
        fields=[Field("__bridge_anchor", data_type=DataType.INTEGER, is_key=True)],
        hidden=True,
    )


def _derive_default_presentation(model: SemanticModel) -> PresentationIR:
    """Deriva un :class:`PresentationIR` por defecto: una tarjeta por medida y
    una tabla por entidad visible, dispuestas en una cuadrícula simple.

    Se usa cuando el llamador no aporta presentación; queda registrado como
    aproximación para que el operador sepa que el layout no es autoral.
    """
    visuals: list[VisualPresentation] = []
    x = y = 0.0
    col = 0
    step_x, step_y, max_cols = 420.0, 320.0, 3

    for metric in model.metrics:
        if metric.hidden:
            continue
        visuals.append(_make_card(metric.name, x, y))
        col += 1
        x += step_x
        if col >= max_cols:
            col = 0
            x = 0.0
            y += step_y

    for entity in model.entities:
        if entity.hidden:
            continue
        shown = [f for f in entity.fields if not f.hidden]
        if not shown:
            continue
        bindings = tuple(
            DataBinding(
                binding_id=f"{entity.name}_{f.name}",
                field_name=f.name,
                role=FieldRole.VALUE,
            )
            for f in shown
        )
        visuals.append(_make_table(entity.name, bindings, x, y))
        col += 1
        x += step_x
        if col >= max_cols:
            col = 0
            x = 0.0
            y += step_y

    page = PagePresentation(
        page_id="overview",
        name="Overview",
        display_name="Overview",
        visuals=tuple(visuals),
    )
    return PresentationIR(
        schema_version=PRES_SCHEMA_VERSION,
        doc_id=f"bridge_default_{model.name}",
        title=model.name,
        pages=(page,),
    )


def _make_card(measure_name: str, x: float, y: float) -> VisualPresentation:
    """Construye un visual card neutrall enlazado a una medida."""
    return VisualPresentation(
        visual_id=f"card_{measure_name}",
        intent=VisualIntentKind.KPI_CARD,
        title=measure_name,
        geometry=VisualGeometry(x=x, y=y, width=400.0, height=300.0),
        bindings=(
            DataBinding(
                binding_id=f"card_{measure_name}_value",
                field_name=measure_name,
                role=FieldRole.VALUE,
            ),
        ),
    )


def _make_table(
    entity_name: str, bindings: tuple[DataBinding, ...], x: float, y: float
) -> VisualPresentation:
    """Construye un visual table neutrall enlazado a las columnas de una entidad."""
    return VisualPresentation(
        visual_id=f"table_{entity_name}",
        intent=VisualIntentKind.TABLE,
        title=entity_name,
        geometry=VisualGeometry(x=x, y=y, width=400.0, height=300.0),
        bindings=bindings,
    )


__all__ = [
    "ENVELOPE_SCHEMA_VERSION",
    "PBIPAdapter",
    "PBIPFile",
    "PBIPProject",
    "SCHEMA_VERSION",
]
