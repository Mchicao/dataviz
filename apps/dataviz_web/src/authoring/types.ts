import type { InteractionIR } from '../interactions';
import { parseCustomVisualSpec } from '../visuals/custom/spec';
import type { CustomVisualSpec } from '../visuals/custom/spec';

/**
 * Typed DataVIZ Authoring contracts (TypeScript projection of the Python
 * canonical contracts).
 *
 * Python (`core/contracts/semantic_ir.py`) is the semantic authority. The
 * `CANONICAL_CONTRACT` object below is mechanically generated from
 * `build_cross_language_contract()` and embedded here like a lockfile: never
 * hand-edit its vocabularies. Every enum-like union in this file is derived
 * from that embedding, so TypeScript cannot drift into a second manually
 * maintained list of kinds/ops/discriminants.
 *
 * Regenerate (output must match this embedding verbatim):
 *   uv run python -c "from core.contracts.semantic_ir import \
 *       canonical_contract_json as j; print(j())"
 * Verify this embedding against the Python authority:
 *   node --input-type=module -e "const m = await \
 *       import('./apps/dataviz_web/src/authoring/types.ts'); \
 *       console.log(JSON.stringify(m.CANONICAL_CONTRACT))" \
 *     | uv run python -c "import json,sys; from \
 *       core.contracts.semantic_ir import verify_cross_language_contract as v; \
 *       v(json.load(sys.stdin))"
 *
 * Version policy (single explicit policy, `same-major`): boundary validators
 * accept well-formed SemVer schema versions with the same MAJOR as the
 * canonical contract (additive MINOR compatibility in both directions) and
 * reject unknown majors and malformed versions.
 *
 * Elements are addressed by stable IR id (per-section `name`).
 */

// ---------------------------------------------------------------------------
// Canonical cross-language contract (generated; do not hand-edit values)
// ---------------------------------------------------------------------------

export interface CanonicalVersionPolicy {
  rule: string;
  unknown_major: string;
  additive_minor: string;
}

export interface CanonicalExpressionConstraint {
  children: string;
  requires?: readonly string[];
  op_enum?: 'unary_ops' | 'binary_ops';
  name_enum?: 'scalar_funcs' | 'agg_funcs' | 'lod_names' | 'window_funcs' | 'opaque_languages';
}

export interface CanonicalSectionSchema {
  required: readonly string[];
  optional: readonly string[];
}

export interface CanonicalContract {
  contract_version: string;
  version_policy: CanonicalVersionPolicy;
  semantic: {
    schema_version: string;
    data_types: readonly string[];
    expression: {
      kinds: readonly string[];
      unary_ops: readonly string[];
      binary_ops: readonly string[];
      scalar_funcs: readonly string[];
      agg_funcs: readonly string[];
      lod_names: readonly string[];
      window_funcs: readonly string[];
      opaque_languages: readonly string[];
      constraints: Readonly<Record<string, CanonicalExpressionConstraint>>;
    };
    filter_operators: readonly string[];
    filter_value_rules: Readonly<Record<string, readonly string[]>>;
    cardinalities: readonly string[];
    cross_filter_directions: readonly string[];
    sections: Readonly<Record<string, CanonicalSectionSchema>>;
  };
  presentation: {
    schema_version: string;
    visual_intents: readonly string[];
    field_roles: readonly string[];
    layout_modes: readonly string[];
  };
  interaction: {
    schema_version: string;
    selection_modes: readonly string[];
    filter_operators: readonly string[];
    filter_scopes: readonly string[];
    cross_filter_behaviors: readonly string[];
    tooltip_kinds: readonly string[];
    navigation_kinds: readonly string[];
  };
  operations: {
    semantic: { kinds: readonly string[]; targets: readonly string[]; id_field: string };
    presentation: { kinds: readonly string[]; targets: readonly string[]; id_field: string };
    interaction: { kinds: readonly string[]; targets: readonly string[]; id_field: string };
  };
}

export const CANONICAL_CONTRACT = {
  "contract_version": "1.0.0",
  "interaction": {
    "cross_filter_behaviors": [
      "filter",
      "highlight",
      "none"
    ],
    "filter_operators": [
      "between",
      "contains",
      "ends_with",
      "equals",
      "greater_than",
      "in",
      "less_than",
      "not_contains",
      "not_ends_with",
      "not_equals",
      "not_in",
      "not_starts_with",
      "starts_with",
      "top_n"
    ],
    "filter_scopes": [
      "page",
      "visual",
      "workbook"
    ],
    "navigation_kinds": [
      "back",
      "bookmark",
      "clear_filters",
      "page_nav",
      "url_nav"
    ],
    "schema_version": "1.1.0",
    "selection_modes": [
      "literal_list",
      "multi",
      "range",
      "single"
    ],
    "tooltip_kinds": [
      "custom_fields",
      "default",
      "report_page"
    ]
  },
  "operations": {
    "interaction": {
      "id_field": "page_id|filter_id",
      "kinds": [
        "add",
        "remove",
        "update"
      ],
      "targets": [
        "global_filter",
        "page"
      ]
    },
    "presentation": {
      "id_field": "page_id|visual_id (+page_id for visual)",
      "kinds": [
        "add",
        "remove",
        "update"
      ],
      "targets": [
        "page",
        "visual"
      ]
    },
    "semantic": {
      "id_field": "name",
      "kinds": [
        "add",
        "remove",
        "update"
      ],
      "targets": [
        "entity",
        "filter",
        "metric",
        "parameter",
        "relationship",
        "rls_intent"
      ]
    }
  },
  "presentation": {
    "field_roles": [
      "color",
      "column",
      "comparison_metric",
      "filter_target",
      "label",
      "row",
      "series",
      "size",
      "target_metric",
      "tooltip",
      "value",
      "x_axis",
      "y_axis"
    ],
    "layout_modes": [
      "fixed",
      "floating",
      "responsive",
      "tiled"
    ],
    "schema_version": "1.0.0",
    "visual_intents": [
      "area",
      "bar",
      "column",
      "container",
      "custom_visual",
      "donut",
      "gauge",
      "heatmap",
      "image",
      "kpi_card",
      "line",
      "map",
      "pie",
      "pivot_matrix",
      "scatter",
      "slicer_filter",
      "table",
      "text_box",
      "treemap",
      "waterfall"
    ]
  },
  "semantic": {
    "cardinalities": [
      "many_to_many",
      "many_to_one",
      "one_to_many",
      "one_to_one"
    ],
    "cross_filter_directions": [
      "both",
      "single"
    ],
    "data_types": [
      "binary",
      "boolean",
      "date",
      "datetime",
      "decimal",
      "integer",
      "string",
      "time",
      "unknown",
      "variant"
    ],
    "expression": {
      "agg_funcs": [
        "avg",
        "count",
        "count_distinct",
        "max",
        "median",
        "min",
        "sum"
      ],
      "binary_ops": [
        "add",
        "and",
        "concat",
        "div",
        "eq",
        "ge",
        "gt",
        "le",
        "lt",
        "mul",
        "ne",
        "or",
        "sub"
      ],
      "constraints": {
        "agg": {
          "children": "one_or_two",
          "name_enum": "agg_funcs",
          "requires": [
            "name"
          ]
        },
        "binary": {
          "children": "two",
          "op_enum": "binary_ops",
          "requires": [
            "op"
          ]
        },
        "conditional": {
          "children": "three"
        },
        "field_ref": {
          "children": "none",
          "requires": [
            "name"
          ]
        },
        "func": {
          "children": "any",
          "name_enum": "scalar_funcs",
          "requires": [
            "name"
          ]
        },
        "literal": {
          "children": "none"
        },
        "lod": {
          "children": "one_or_more",
          "name_enum": "lod_names",
          "requires": [
            "name"
          ]
        },
        "lookup_map": {
          "children": "none",
          "requires": [
            "name",
            "value"
          ]
        },
        "measure_ref": {
          "children": "none",
          "requires": [
            "name"
          ]
        },
        "modify": {
          "children": "one_or_more"
        },
        "opaque": {
          "children": "none",
          "name_enum": "opaque_languages",
          "requires": [
            "name",
            "value"
          ]
        },
        "parameter_ref": {
          "children": "none",
          "requires": [
            "name"
          ]
        },
        "principal": {
          "children": "none"
        },
        "set_membership": {
          "children": "none",
          "requires": [
            "name",
            "entity"
          ]
        },
        "unary": {
          "children": "one",
          "op_enum": "unary_ops",
          "requires": [
            "op"
          ]
        },
        "window": {
          "children": "any",
          "name_enum": "window_funcs",
          "requires": [
            "name"
          ]
        }
      },
      "kinds": [
        "agg",
        "binary",
        "conditional",
        "field_ref",
        "func",
        "literal",
        "lod",
        "lookup_map",
        "measure_ref",
        "modify",
        "opaque",
        "parameter_ref",
        "principal",
        "set_membership",
        "unary",
        "window"
      ],
      "lod_names": [
        "exclude",
        "fixed",
        "include"
      ],
      "opaque_languages": [
        "dax"
      ],
      "scalar_funcs": [
        "abs",
        "cast",
        "ceil",
        "coalesce",
        "contains",
        "date_add",
        "date_diff",
        "day",
        "floor",
        "is_blank",
        "length",
        "lower",
        "month",
        "now",
        "quarter",
        "regexp_extract",
        "regexp_replace",
        "round",
        "split",
        "substring",
        "to_string",
        "today",
        "trim",
        "upper",
        "year"
      ],
      "unary_ops": [
        "neg",
        "not"
      ],
      "window_funcs": [
        "lookup",
        "max",
        "min",
        "size",
        "sum"
      ]
    },
    "filter_operators": [
      "between",
      "contains",
      "ends_with",
      "eq",
      "gt",
      "gte",
      "in",
      "is_not_null",
      "is_null",
      "lt",
      "lte",
      "ne",
      "not_contains",
      "not_in",
      "starts_with"
    ],
    "filter_value_rules": {
      "at_least_one": [
        "in",
        "not_in"
      ],
      "exactly_one": [
        "contains",
        "ends_with",
        "eq",
        "gt",
        "gte",
        "lt",
        "lte",
        "ne",
        "not_contains",
        "starts_with"
      ],
      "exactly_two": [
        "between"
      ],
      "none": [
        "is_not_null",
        "is_null"
      ]
    },
    "schema_version": "2.0.0",
    "sections": {
      "entity": {
        "optional": [
          "fields",
          "grain",
          "description",
          "hidden"
        ],
        "required": [
          "name"
        ]
      },
      "expression": {
        "optional": [
          "value",
          "data_type",
          "name",
          "entity",
          "op",
          "distinct",
          "children"
        ],
        "required": [
          "kind"
        ]
      },
      "field": {
        "optional": [
          "data_type",
          "is_key",
          "nullable",
          "hidden",
          "description",
          "source_column",
          "expression"
        ],
        "required": [
          "name"
        ]
      },
      "filter": {
        "optional": [
          "values",
          "applies_to",
          "description",
          "viewer_hidden",
          "viewer_locked"
        ],
        "required": [
          "name",
          "target",
          "operator"
        ]
      },
      "grain": {
        "optional": [
          "fields",
          "description"
        ],
        "required": []
      },
      "metric": {
        "optional": [
          "entity",
          "data_type",
          "format_string",
          "description",
          "hidden"
        ],
        "required": [
          "name",
          "expression"
        ]
      },
      "parameter": {
        "optional": [
          "is_set",
          "default_value",
          "allowed_values",
          "expression",
          "description"
        ],
        "required": [
          "name",
          "data_type"
        ]
      },
      "relationship": {
        "optional": [
          "cardinality",
          "cross_filter",
          "active",
          "description"
        ],
        "required": [
          "name",
          "from_entity",
          "from_fields",
          "to_entity",
          "to_fields"
        ]
      },
      "rls_intent": {
        "optional": [
          "roles",
          "description"
        ],
        "required": [
          "name",
          "entity",
          "expression"
        ]
      },
      "semantic_model": {
        "optional": [
          "entities",
          "relationships",
          "metrics",
          "parameters",
          "filters",
          "rls_intents",
          "description"
        ],
        "required": [
          "schema_version",
          "name"
        ]
      }
    }
  },
  "version_policy": {
    "additive_minor": "accept",
    "rule": "same-major",
    "unknown_major": "reject"
  }
} as const satisfies CanonicalContract;

/** Widened view of the embedding for runtime lookups by dynamic keys. */
const CONTRACT: CanonicalContract = CANONICAL_CONTRACT;

// ---------------------------------------------------------------------------
// Derived contract unions (never hand-enumerate these)
// ---------------------------------------------------------------------------

export type DataType = (typeof CANONICAL_CONTRACT.semantic.data_types)[number];
export type ExpressionKind = (typeof CANONICAL_CONTRACT.semantic.expression.kinds)[number];
export type SemanticFilterOperator = (typeof CANONICAL_CONTRACT.semantic.filter_operators)[number];
export type Cardinality = (typeof CANONICAL_CONTRACT.semantic.cardinalities)[number];
export type CrossFilterDirection = (typeof CANONICAL_CONTRACT.semantic.cross_filter_directions)[number];
export type PresentationVisualIntent = (typeof CANONICAL_CONTRACT.presentation.visual_intents)[number];
export type PresentationFieldRole = (typeof CANONICAL_CONTRACT.presentation.field_roles)[number];
export type PresentationLayoutMode = (typeof CANONICAL_CONTRACT.presentation.layout_modes)[number];
export type OpKind = (typeof CANONICAL_CONTRACT.operations.semantic.kinds)[number];
export type TargetKind = (typeof CANONICAL_CONTRACT.operations.semantic.targets)[number];
export type VisualOperationKind = (typeof CANONICAL_CONTRACT.operations.presentation.kinds)[number];

export const MODEL_SCHEMA_VERSION: string = CANONICAL_CONTRACT.semantic.schema_version;
export const CROSS_LANGUAGE_CONTRACT_VERSION: string = CANONICAL_CONTRACT.contract_version;

// ---------------------------------------------------------------------------
// Semantic model contracts (mirror of the Python authority)
// ---------------------------------------------------------------------------

export interface Expression {
  kind: ExpressionKind;
  value?: unknown;
  data_type?: DataType;
  name?: string;
  entity?: string;
  /**
   * Kept as `string` (not the canonical binary/unary unions) because an
   * in-repo producer currently emits the non-canonical token `/`; the
   * boundary validator `validateCanonicalExpression` still rejects any op
   * outside the canonical allowlist.
   */
  op?: string;
  distinct?: boolean;
  children?: Expression[];
}

export interface Field {
  name: string;
  data_type?: DataType;
  is_key?: boolean;
  nullable?: boolean;
  hidden?: boolean;
  description?: string;
  source_column?: string;
  /** Calculated column expression (closed neutral grammar). */
  expression?: Expression | null;
}

export interface Grain {
  fields: string[];
  description?: string;
}

export interface Entity {
  name: string;
  fields: Field[];
  grain?: Grain;
  description?: string;
  hidden?: boolean;
}

export interface Relationship {
  name: string;
  from_entity: string;
  from_fields: string[];
  to_entity: string;
  to_fields: string[];
  cardinality?: Cardinality;
  cross_filter?: CrossFilterDirection;
  active?: boolean;
  description?: string;
}

export interface Metric {
  name: string;
  expression: Expression;
  /** Optional entity qualifier the metric is attached to. */
  entity?: string;
  data_type?: DataType;
  format_string?: string;
  description?: string;
  hidden?: boolean;
}

export interface Parameter {
  name: string;
  data_type: DataType;
  default_value?: unknown;
  allowed_values?: unknown[];
  expression?: Expression;
  description?: string;
}

export interface Filter {
  name: string;
  target: Expression;
  operator: SemanticFilterOperator;
  values?: unknown[];
  applies_to?: string[];
  description?: string;
  /** Cuando es true, el filtro no se muestra a usuarios visualizadores. */
  viewer_hidden?: boolean;
  /** Cuando es true, los usuarios visualizadores no pueden modificar el filtro. */
  viewer_locked?: boolean;
}

export interface RLSIntent {
  name: string;
  entity: string;
  expression: Expression;
  roles?: string[];
  description?: string;
}

export interface SemanticModel {
  schema_version: string;
  name: string;
  description?: string;
  entities: Entity[];
  relationships: Relationship[];
  metrics: Metric[];
  parameters: Parameter[];
  filters: Filter[];
  rls_intents: RLSIntent[];
}

// ---------------------------------------------------------------------------
// Canonical authoring operations (semantic)
// ---------------------------------------------------------------------------

export interface Operation {
  kind: OpKind;
  target: TargetKind;
  name: string;
  payload?: unknown;
}

export type ChangeKind = 'added' | 'removed' | 'changed';

export interface Change {
  target: TargetKind;
  name: string;
  kind: ChangeKind;
  before?: unknown;
  after?: unknown;
}

export interface SemanticDiff {
  changes: Change[];
  is_empty: boolean;
  added: Change[];
  removed: Change[];
  changed: Change[];
}

export type VersionStatus = 'draft' | 'published' | 'superseded';

export interface Version {
  number: number;
  model: SemanticModel;
  /** Canonical portable presentation contract; `presentation` is a validated editor projection. */
  presentation_ir: PresentationIR;
  presentation: PresentationSnapshot;
  /** Canonical portable interaction contract. */
  interaction_ir: InteractionIR;
  status: VersionStatus;
  parent: number | null;
  message: string;
  created_at: string;
  /** Fingerprint of the imported dataset that originated this model; absent when synthetic. */
  dataset_fingerprint?: string;
  /** Active page projected in `presentation` for multi-page documents. */
  active_page_id?: string;
}

export interface Document {
  doc_id: string;
  versions: Version[];
}

// ---------------------------------------------------------------------------
// Editor projections (NON-AUTHORITATIVE adapters)
// ---------------------------------------------------------------------------
// Everything from here through `AgentProposal` is a web-editor convenience
// projection derived from the canonical Presentation IR by
// `presentationBridge.ts`. These shapes are never the persisted truth: the
// canonical PresentationIR / InteractionIR on each `Version` are.

export interface VisualLayoutSpec {
  id: string;
  name: string;
  kind: string;
  title: string;
  data_roles: Record<string, string>;
  geometry: VisualGeometry;
  format_settings: VisualFormatSettings;
  /**
   * Spec declarativa de capas para `custom_visual`. Viaja serializada en el IR
   * canónico como `properties.custom_visual_spec` y se valida con el parser
   * fail-closed compartido en cada frontera.
   */
  custom_spec?: CustomVisualSpec;
}

export interface VisualGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface VisualFormatSettings {
  accent_color: string;
  background_color: string;
  text_color: string;
  show_title: boolean;
}

export interface PresentationSnapshot {
  visuals: VisualLayoutSpec[];
  canvas: {
    width: number;
    height: number;
    background_color: string;
  };
}

export interface VisualOperation {
  kind: VisualOperationKind;
  visual_id: string;
  payload?: VisualLayoutSpec;
}

export interface VisualChange {
  kind: 'added' | 'changed' | 'removed';
  visual_id: string;
  before?: VisualLayoutSpec;
  after?: VisualLayoutSpec;
}

export interface AgentProposal {
  proposal_id: string;
  prompt: string;
  summary: string;
  rationale: string[];
  warnings: string[];
  base_version: number;
  semantic_ops: Operation[];
  visual_ops: VisualOperation[];
  visual_changes: VisualChange[];
}

// ---------------------------------------------------------------------------
// Canonical Presentation IR contracts (mirror of the Python authority)
// ---------------------------------------------------------------------------

export interface CanonicalThemeConfig {
  font_family: string;
  font_size_pt: number;
  primary_color: string;
  background_color: string;
  card_background_color: string;
  text_color: string;
  color_palette: string[];
  border_color: string;
  border_width_px: number;
  border_radius_px: number;
  shadow_enabled: boolean;
}

export interface CanonicalVisualGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
  z_index: number;
  layout_mode: PresentationLayoutMode;
  padding: [number, number, number, number];
}

export interface CanonicalDataBinding {
  binding_id: string;
  field_name: string;
  role: PresentationFieldRole;
  aggregation: string | null;
  format_string: string | null;
  display_name: string | null;
  sort_order: string | null;
}

export interface CanonicalAccessibilityConfig {
  alt_text: string;
  aria_label: string;
  tab_index: number;
  screen_reader_summary: string;
  high_contrast_aware: boolean;
}

export interface CanonicalVisualPresentation {
  visual_id: string;
  intent: PresentationVisualIntent;
  title: string;
  subtitle: string;
  geometry: CanonicalVisualGeometry;
  bindings: CanonicalDataBinding[];
  style: CanonicalThemeConfig;
  accessibility: CanonicalAccessibilityConfig;
  is_visible: boolean;
  properties: Record<string, unknown>;
}

export interface CanonicalPagePresentation {
  page_id: string;
  name: string;
  display_name: string;
  is_hidden: boolean;
  width: number;
  height: number;
  theme: CanonicalThemeConfig;
  accessibility: CanonicalVisualPresentation['accessibility'];
  visuals: CanonicalVisualPresentation[];
  properties: Record<string, unknown>;
}

export interface PresentationIR {
  schema_version: typeof CANONICAL_CONTRACT.presentation.schema_version;
  doc_id: string;
  title: string;
  theme: CanonicalThemeConfig;
  pages: CanonicalPagePresentation[];
  metadata: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Runtime boundary validation (canonical, descriptor-driven)
// ---------------------------------------------------------------------------
// These validators are the acceptance gate for payloads that cross the
// language boundary (Python-serialized JSON, agent proposals, imported
// documents). They enforce exactly what the Python constructors enforce —
// closed vocabularies, per-kind arity, filter value-count rules, required
// keys, the same-major version policy — and reject anything outside the
// canonical contract. Unknown extra keys are ignored (additive-compatible),
// mirroring the Python `from_dict` parsers. Referential integrity (unknown
// entities/measures/parameters inside expressions) remains the job of
// `materialize` in `operations.ts`, mirroring the Python side.

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== 'string' || value === '') {
    throw new Error(`${label} must be a non-empty string, got ${JSON.stringify(value)}`);
  }
  return value;
}

function optionalString(value: unknown, label: string): string {
  if (value !== undefined && typeof value !== 'string') {
    throw new Error(`${label} must be a string`);
  }
  return value ?? '';
}

function optionalBoolean(value: unknown, label: string): boolean {
  if (value !== undefined && typeof value !== 'boolean') {
    throw new Error(`${label} must be a boolean`);
  }
  return value ?? false;
}

function optionalStringArray(value: unknown, label: string): string[] {
  if (value === undefined) {return [];}
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error(`${label} must be an array of strings`);
  }
  return value as string[];
}

function requireEnum(value: unknown, allow: readonly string[], label: string): string {
  if (typeof value !== 'string' || !allow.includes(value)) {
    throw new Error(`${label} must be one of [${allow.join(', ')}], got ${JSON.stringify(value)}`);
  }
  return value;
}

const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;

/**
 * Enforce the explicit `same-major` policy: well-formed SemVer with the same
 * MAJOR as `known` is accepted (additive minor compatibility); anything else
 * is rejected. Mirrors `check_additive_schema_version` in Python.
 */
export function checkCanonicalSchemaVersion(
  received: unknown,
  known: string,
  label = 'schema_version',
): string {
  if (typeof received !== 'string') {
    throw new TypeError(`${label} must be a string, got ${typeof received}`);
  }
  const match = SEMVER.exec(received);
  const knownMatch = SEMVER.exec(known);
  if (!match || !knownMatch) {
    throw new Error(`${label} is not a valid semantic version: "${received}"`);
  }
  if (match[1] !== knownMatch[1]) {
    throw new Error(
      `${label} incompatible major: received "${received}", known "${known}" `
        + `(policy "${CONTRACT.version_policy.rule}" rejects unknown majors)`,
    );
  }
  return received;
}

const OPAQUE_DAX_FORBIDDEN_LEAD = /^\s*(?:select|insert|update|delete|create|alter|drop|truncate|merge|grant|revoke|execute|exec|pragma|attach|detach|vacuum|import|from|def|class|lambda|print|eval)\b/i;

const CHILD_COUNT_RULES: Readonly<Record<string, (count: number) => boolean>> = {
  any: () => true,
  none: (count) => count === 0,
  one: (count) => count === 1,
  one_or_more: (count) => count >= 1,
  one_or_two: (count) => count === 1 || count === 2,
  three: (count) => count === 3,
  two: (count) => count === 2,
};

const ALLOWED_EXPRESSION_KEYS = new Set([
  'kind',
  'value',
  'data_type',
  'name',
  'entity',
  'op',
  'distinct',
  'children',
]);

/** Validate an expression payload against the closed neutral grammar. */
export function validateCanonicalExpression(payload: unknown): Expression {
  if (!isRecord(payload)) {throw new Error('Expression payload must be an object');}
  for (const k of Object.keys(payload)) {
    if (!ALLOWED_EXPRESSION_KEYS.has(k)) {
      throw new Error(`unknown expression field(s): ${k}`);
    }
  }
  const kind = requireEnum(payload.kind, CONTRACT.semantic.expression.kinds, 'Expression.kind');
  const rule = CONTRACT.semantic.expression.constraints[kind];
  const children = payload.children ?? [];
  if (!Array.isArray(children)) {throw new Error('Expression.children must be an array');}
  const childrenOk = CHILD_COUNT_RULES[rule.children]?.(children.length) ?? false;
  if (!childrenOk) {
    throw new Error(
      `Expression kind "${kind}" violates children rule "${rule.children}" (got ${children.length})`,
    );
  }

  if (kind === 'literal') {
    if (children.length > 0) {
      throw new Error('literal must have no children');
    }
    if (
      payload.value !== null
      && typeof payload.value !== 'boolean'
      && typeof payload.value !== 'number'
      && typeof payload.value !== 'string'
    ) {
      throw new Error(`literal value must be a scalar (bool, int, float, str), got ${typeof payload.value}`);
    }
  } else if (kind === 'lookup_map') {
    if (!isRecord(payload.value)) {
      throw new Error('lookup_map requires a mapping object as "value"');
    }
    for (const [mk, mv] of Object.entries(payload.value)) {
      if (typeof mk !== 'string') {
        throw new TypeError('lookup_map keys must be strings');
      }
      if (
        mv !== null
        && typeof mv !== 'boolean'
        && typeof mv !== 'number'
        && typeof mv !== 'string'
      ) {
        throw new Error(`lookup_map values must be scalars, got ${typeof mv}`);
      }
    }
  } else if (kind === 'set_membership') {
    if (payload.value !== undefined && payload.value !== null) {
      if (!Array.isArray(payload.value)) {
        throw new TypeError('set_membership value must be an array of scalars');
      }
      for (const item of payload.value) {
        if (
          item !== null
          && typeof item !== 'boolean'
          && typeof item !== 'number'
          && typeof item !== 'string'
        ) {
          throw new Error(`set_membership value items must be scalars, got ${typeof item}`);
        }
      }
    }
  } else if (kind === 'opaque') {
    if (typeof payload.value !== 'string' || payload.value.trim().length === 0) {
      throw new Error('opaque dax expression requires a non-empty string value');
    }
    if (OPAQUE_DAX_FORBIDDEN_LEAD.test(payload.value)) {
      throw new Error('opaque dax expression must not carry SQL/Python source');
    }
  } else if (payload.value !== undefined && payload.value !== null) {
    throw new Error(`${kind} expression must not have a value`);
  }

  for (const attribute of rule.requires ?? []) {
    if (attribute === 'value') {
      continue;
    }
    requireString(payload[attribute], `Expression(${kind}).${attribute}`);
  }
  if (rule.op_enum) {
    requireEnum(
      payload.op,
      CONTRACT.semantic.expression[rule.op_enum],
      `Expression(${kind}).op`,
    );
  }
  if (rule.name_enum) {
    requireEnum(
      payload.name,
      CONTRACT.semantic.expression[rule.name_enum],
      `Expression(${kind}).name`,
    );
  }
  if (payload.data_type !== undefined) {
    requireEnum(payload.data_type, CONTRACT.semantic.data_types, 'Expression.data_type');
  }
  optionalString(payload.name, 'Expression.name');
  optionalString(payload.entity, 'Expression.entity');
  optionalString(payload.op, 'Expression.op');
  optionalBoolean(payload.distinct, 'Expression.distinct');
  children.forEach((child) => validateCanonicalExpression(child));
  return payload as unknown as Expression;
}

function requireSectionKeys(payload: Record<string, unknown>, section: string, label: string): void {
  const schema = CONTRACT.semantic.sections[section];
  for (const key of schema.required) {
    if (payload[key] === undefined) {
      throw new Error(`${label}: missing required key "${key}"`);
    }
  }
  const allowed = new Set([...schema.required, ...schema.optional]);
  for (const key of Object.keys(payload)) {
    if (!allowed.has(key)) {
      throw new Error(`unknown ${label} field(s): ${key}`);
    }
  }
}

function validateCanonicalField(payload: unknown): Field {
  if (!isRecord(payload)) {throw new Error('field payload must be an object');}
  requireSectionKeys(payload, 'field', 'field');
  requireString(payload.name, 'field.name');
  if (payload.data_type !== undefined) {
    requireEnum(payload.data_type, CONTRACT.semantic.data_types, 'field.data_type');
  }
  optionalBoolean(payload.is_key, 'field.is_key');
  optionalBoolean(payload.hidden, 'field.hidden');
  optionalBoolean(payload.nullable, 'field.nullable');
  optionalString(payload.description, 'field.description');
  optionalString(payload.source_column, 'field.source_column');
  if (payload.expression !== undefined && payload.expression !== null) {
    validateCanonicalExpression(payload.expression);
  }
  return payload as unknown as Field;
}

function validateCanonicalEntity(payload: unknown): Entity {
  if (!isRecord(payload)) {throw new Error('entity payload must be an object');}
  requireSectionKeys(payload, 'entity', 'entity');
  requireString(payload.name, 'entity.name');
  const fields = payload.fields === undefined ? [] : payload.fields;
  if (!Array.isArray(fields)) {throw new Error('entity.fields must be an array');}
  fields.forEach((item) => validateCanonicalField(item));
  const names = (fields as Record<string, unknown>[]).map((item) => item.name);
  if (new Set(names).size !== names.length) {
    throw new Error(`entity "${String(payload.name)}": duplicate field names`);
  }
  if (payload.grain !== undefined && payload.grain !== null) {
    if (!isRecord(payload.grain)) {throw new Error('entity.grain must be an object');}
    const grainFields = optionalStringArray(payload.grain.fields, 'entity.grain.fields');
    if (grainFields.length === 0) {throw new Error('entity.grain.fields must be non-empty');}
    const unknownFields = grainFields.filter((name) => !names.includes(name));
    if (unknownFields.length > 0) {
      throw new Error(
        `entity "${String(payload.name)}": grain references unknown fields ${JSON.stringify(unknownFields)}`,
      );
    }
    optionalString(payload.grain.description, 'entity.grain.description');
  }
  optionalString(payload.description, 'entity.description');
  optionalBoolean(payload.hidden, 'entity.hidden');
  return payload as unknown as Entity;
}

function validateCanonicalRelationship(payload: unknown): Relationship {
  if (!isRecord(payload)) {throw new Error('relationship payload must be an object');}
  requireSectionKeys(payload, 'relationship', 'relationship');
  requireString(payload.name, 'relationship.name');
  requireString(payload.from_entity, 'relationship.from_entity');
  requireString(payload.to_entity, 'relationship.to_entity');
  const fromFields = optionalStringArray(payload.from_fields, 'relationship.from_fields');
  const toFields = optionalStringArray(payload.to_fields, 'relationship.to_fields');
  if (fromFields.length === 0 || toFields.length === 0) {
    throw new Error('relationship from_fields/to_fields must be non-empty');
  }
  if (fromFields.length !== toFields.length) {
    throw new Error('relationship from_fields/to_fields length mismatch');
  }
  if (payload.cardinality !== undefined) {
    requireEnum(payload.cardinality, CONTRACT.semantic.cardinalities, 'relationship.cardinality');
  }
  if (payload.cross_filter !== undefined) {
    requireEnum(
      payload.cross_filter,
      CONTRACT.semantic.cross_filter_directions,
      'relationship.cross_filter',
    );
  }
  optionalBoolean(payload.active, 'relationship.active');
  optionalString(payload.description, 'relationship.description');
  return payload as unknown as Relationship;
}

function validateCanonicalMetric(payload: unknown): Metric {
  if (!isRecord(payload)) {throw new Error('metric payload must be an object');}
  requireSectionKeys(payload, 'metric', 'metric');
  requireString(payload.name, 'metric.name');
  validateCanonicalExpression(payload.expression);
  optionalString(payload.entity, 'metric.entity');
  if (payload.data_type !== undefined) {
    requireEnum(payload.data_type, CONTRACT.semantic.data_types, 'metric.data_type');
  }
  optionalString(payload.format_string, 'metric.format_string');
  optionalString(payload.description, 'metric.description');
  optionalBoolean(payload.hidden, 'metric.hidden');
  return payload as unknown as Metric;
}

function validateCanonicalParameter(payload: unknown): Parameter {
  if (!isRecord(payload)) {throw new Error('parameter payload must be an object');}
  requireSectionKeys(payload, 'parameter', 'parameter');
  requireString(payload.name, 'parameter.name');
  requireEnum(payload.data_type, CONTRACT.semantic.data_types, 'parameter.data_type');
  if (payload.allowed_values !== undefined && !Array.isArray(payload.allowed_values)) {
    throw new Error('parameter.allowed_values must be an array');
  }
  if (payload.expression !== undefined && payload.expression !== null) {
    validateCanonicalExpression(payload.expression);
  }
  optionalString(payload.description, 'parameter.description');
  return payload as unknown as Parameter;
}

function validateCanonicalFilter(payload: unknown): Filter {
  if (!isRecord(payload)) {throw new Error('filter payload must be an object');}
  requireSectionKeys(payload, 'filter', 'filter');
  requireString(payload.name, 'filter.name');
  const target = validateCanonicalExpression(payload.target);
  if (target.kind !== 'field_ref' && target.kind !== 'measure_ref') {
    throw new Error(`filter.target must be a field_ref or measure_ref, got "${target.kind}"`);
  }
  const operator = requireEnum(
    payload.operator,
    CONTRACT.semantic.filter_operators,
    'filter.operator',
  );
  const values = payload.values === undefined ? [] : payload.values;
  if (!Array.isArray(values)) {throw new Error('filter.values must be an array');}
  const rules = CONTRACT.semantic.filter_value_rules;
  if (rules.none.includes(operator) && values.length !== 0) {
    throw new Error(`filter operator "${operator}" takes no values`);
  }
  if (rules.exactly_two.includes(operator) && values.length !== 2) {
    throw new Error(`filter operator "${operator}" requires exactly two values`);
  }
  if (rules.at_least_one.includes(operator) && values.length < 1) {
    throw new Error(`filter operator "${operator}" requires at least one value`);
  }
  if (
    rules.exactly_one.includes(operator) && values.length !== 1
  ) {
    throw new Error(`filter operator "${operator}" requires exactly one value`);
  }
  optionalStringArray(payload.applies_to, 'filter.applies_to');
  optionalString(payload.description, 'filter.description');
  for (const flag of ['viewer_hidden', 'viewer_locked'] as const) {
    const value = payload[flag];
    if (value !== undefined && typeof value !== 'boolean') {
      throw new Error(`filter.${flag} must be a boolean`);
    }
  }
  return payload as unknown as Filter;
}

function validateCanonicalRLSIntent(payload: unknown): RLSIntent {
  if (!isRecord(payload)) {throw new Error('rls_intent payload must be an object');}
  requireSectionKeys(payload, 'rls_intent', 'rls_intent');
  requireString(payload.name, 'rls_intent.name');
  requireString(payload.entity, 'rls_intent.entity');
  validateCanonicalExpression(payload.expression);
  optionalStringArray(payload.roles, 'rls_intent.roles');
  optionalString(payload.description, 'rls_intent.description');
  return payload as unknown as RLSIntent;
}

const SECTION_VALIDATORS: Readonly<Record<TargetKind, (payload: unknown) => unknown>> = {
  entity: validateCanonicalEntity,
  filter: validateCanonicalFilter,
  metric: validateCanonicalMetric,
  parameter: validateCanonicalParameter,
  relationship: validateCanonicalRelationship,
  rls_intent: validateCanonicalRLSIntent,
};

function sectionArray(payload: Record<string, unknown>, key: string): unknown[] {
  const value = payload[key] === undefined ? [] : payload[key];
  if (!Array.isArray(value)) {throw new Error(`${key} must be an array`);}
  return value;
}

function assertUniqueNames(items: unknown[], validator: (payload: unknown) => { name: unknown }, label: string): void {
  const names = items.map((item) => requireString(validator(item).name, `${label}.name`));
  if (new Set(names).size !== names.length) {
    throw new Error(`duplicate ${label} names: ${JSON.stringify(names)}`);
  }
}

/**
 * Validate a SemanticModel boundary payload (e.g. Python-serialized canonical
 * JSON) and reject anything outside the canonical contract.
 */
export function validateCanonicalSemanticModel(payload: unknown): SemanticModel {
  if (!isRecord(payload)) {throw new Error('SemanticModel payload must be an object');}
  requireSectionKeys(payload, 'semantic_model', 'SemanticModel');
  checkCanonicalSchemaVersion(
    payload.schema_version,
    CONTRACT.semantic.schema_version,
    'SemanticModel.schema_version',
  );
  requireString(payload.name, 'SemanticModel.name');
  optionalString(payload.description, 'SemanticModel.description');

  const entities = sectionArray(payload, 'entities');
  entities.forEach((item) => validateCanonicalEntity(item));
  assertUniqueNames(entities, validateCanonicalEntity, 'entity');

  const relationships = sectionArray(payload, 'relationships');
  relationships.forEach((item) => validateCanonicalRelationship(item));
  assertUniqueNames(relationships, validateCanonicalRelationship, 'relationship');
  const entityNames = new Set(entities.map((item) => (item as Record<string, unknown>).name));
  for (const item of relationships as Record<string, unknown>[]) {
    for (const endpoint of ['from_entity', 'to_entity'] as const) {
      if (!entityNames.has(item[endpoint])) {
        throw new Error(`relationship "${String(item.name)}": unknown ${endpoint} "${String(item[endpoint])}"`);
      }
    }
  }

  const metrics = sectionArray(payload, 'metrics');
  metrics.forEach((item) => validateCanonicalMetric(item));
  assertUniqueNames(metrics, validateCanonicalMetric, 'metric');

  const parameters = sectionArray(payload, 'parameters');
  parameters.forEach((item) => validateCanonicalParameter(item));
  assertUniqueNames(parameters, validateCanonicalParameter, 'parameter');

  const filters = sectionArray(payload, 'filters');
  filters.forEach((item) => validateCanonicalFilter(item));
  assertUniqueNames(filters, validateCanonicalFilter, 'filter');

  const rlsIntents = sectionArray(payload, 'rls_intents');
  rlsIntents.forEach((item) => validateCanonicalRLSIntent(item));
  assertUniqueNames(rlsIntents, validateCanonicalRLSIntent, 'rls_intent');
  for (const item of rlsIntents as Record<string, unknown>[]) {
    if (!entityNames.has(item.entity)) {
      throw new Error(`rls_intent "${String(item.name)}": unknown entity "${String(item.entity)}"`);
    }
  }

  return payload as unknown as SemanticModel;
}

/**
 * Validate a canonical semantic authoring operation: closed kind/target
 * discriminants, payload presence rules, payload.name === op.name and a full
 * payload validation for add/update ops.
 */
export function validateCanonicalOperation(payload: unknown): Operation {
  if (!isRecord(payload)) {throw new Error('Operation payload must be an object');}
  const allowedKeys = new Set(['kind', 'target', 'name', 'payload']);
  for (const k of Object.keys(payload)) {
    if (!allowedKeys.has(k)) {
      throw new Error(`unknown field "${k}" in operation`);
    }
  }
  const kind = requireEnum(payload.kind, CONTRACT.operations.semantic.kinds, 'Operation.kind');
  const target = requireEnum(
    payload.target,
    CONTRACT.operations.semantic.targets,
    'Operation.target',
  );
  requireString(payload.name, 'Operation.name');
  if (kind === 'remove') {
    if (payload.payload !== undefined && payload.payload !== null) {
      throw new Error('remove ops must not carry a payload');
    }
    return { kind, name: payload.name as string, target } as unknown as Operation;
  }
  if (!isRecord(payload.payload)) {
    throw new Error(`${kind} ops require a payload object`);
  }
  SECTION_VALIDATORS[target as TargetKind](payload.payload);
  const payloadName = (payload.payload as Record<string, unknown>).name;
  if (payloadName !== payload.name) {
    throw new Error(
      `payload.name "${String(payloadName)}" must equal op.name "${String(payload.name)}"`,
    );
  }
  return {
    kind,
    name: payload.name as string,
    payload: payload.payload,
    target,
  } as unknown as Operation;
}

const FORBIDDEN_CARRIER_KEYS = new Set([
  'raw_sql',
  'query',
  'secret',
  'password',
  'token',
  'connection_string',
  'dsn',
  'sql',
]);

const ALLOWED_GEOMETRY_KEYS = new Set([
  'x', 'y', 'width', 'height', 'z_index', 'layout_mode', 'padding',
]);

const ALLOWED_ACCESSIBILITY_KEYS = new Set([
  'alt_text', 'aria_label', 'tab_index', 'screen_reader_summary', 'high_contrast_aware',
]);

const ALLOWED_THEME_KEYS = new Set([
  'font_family',
  'font_size_pt',
  'primary_color',
  'background_color',
  'card_background_color',
  'text_color',
  'color_palette',
  'border_color',
  'border_width_px',
  'border_radius_px',
  'shadow_enabled',
]);

const ALLOWED_DATA_BINDING_KEYS = new Set([
  'binding_id',
  'field_name',
  'role',
  'aggregation',
  'format_string',
  'display_name',
  'sort_order',
]);

const ALLOWED_VISUAL_KEYS = new Set([
  'visual_id',
  'intent',
  'title',
  'subtitle',
  'geometry',
  'bindings',
  'style',
  'accessibility',
  'is_visible',
  'properties',
]);

const ALLOWED_PAGE_PRESENTATION_KEYS = new Set([
  'page_id',
  'name',
  'display_name',
  'is_hidden',
  'width',
  'height',
  'theme',
  'accessibility',
  'visuals',
  'properties',
]);

const ALLOWED_PRESENTATION_IR_KEYS = new Set([
  'schema_version',
  'doc_id',
  'title',
  'theme',
  'pages',
  'metadata',
]);

const ALLOWED_SELECTION_KEYS = new Set([
  'selection_id',
  'source_visual_id',
  'target_field',
  'mode',
  'selected_values',
  'is_cleared_on_deselect',
]);

const ALLOWED_FILTER_CONDITION_KEYS = new Set([
  'filter_id',
  'name',
  'target_field',
  'operator',
  'values',
  'scope',
  'target_visual_ids',
  'is_interactive_slicer',
]);

const ALLOWED_CROSS_FILTER_KEYS = new Set([
  'rule_id',
  'source_visual_id',
  'target_visual_ids',
  'behavior',
  'bidirectional',
]);

const ALLOWED_DRILL_KEYS = new Set([
  'drill_id',
  'target_visual_id',
  'hierarchy_levels',
  'current_level_index',
  'allow_drill_down',
  'allow_drill_up',
  'drill_through_page_id',
]);

const ALLOWED_TOOLTIP_KEYS = new Set([
  'tooltip_id',
  'source_visual_id',
  'kind',
  'fields',
  'target_page_id',
  'trigger',
]);

const ALLOWED_NAVIGATION_KEYS = new Set([
  'action_id',
  'trigger_source_id',
  'kind',
  'target_page_id',
  'target_url',
  'parameters',
]);

const ALLOWED_PAGE_INTERACTION_KEYS = new Set([
  'page_id',
  'selections',
  'filters',
  'cross_filters',
  'drills',
  'tooltips',
  'navigations',
]);

const ALLOWED_INTERACTION_IR_KEYS = new Set([
  'schema_version',
  'doc_id',
  'global_filters',
  'pages',
  'metadata',
]);

function rejectUnknownKeys(record: Record<string, unknown>, allowed: Set<string>, context: string): void {
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) {
      throw new Error(`unknown field "${key}" in ${context}`);
    }
  }
}

function validateSafeProperties(props: unknown, context: string): Record<string, unknown> {
  if (!isRecord(props)) {throw new Error(`${context} properties must be an object`);}
  for (const k of Object.keys(props)) {
    if (FORBIDDEN_CARRIER_KEYS.has(k)) {
      throw new Error(`${context} properties contains forbidden carrier key(s): ${k}`);
    }
    const v = props[k];
    // Única estructura anidada admitida: la spec de custom visuals, validada
    // con el mismo parser fail-closed del contrato compartido.
    if (k === 'custom_visual_spec') {
      parseCustomVisualSpec(v);
      continue;
    }
    if (k === 'editor_data_roles' && isRecord(v) && Object.entries(v).every(([role, reference]) => (
      ['category', ...CONTRACT.presentation.field_roles].includes(role)
      && typeof reference === 'string' && /^(field|measure):[^\r\n]+$/.test(reference)
    ))) {continue;}
    if (v !== null && v !== undefined) {
      if (typeof v === 'boolean' || typeof v === 'number' || typeof v === 'string') {
        // scalar ok
      } else if (Array.isArray(v)) {
        for (const item of v) {
          if (item !== null && item !== undefined && typeof item !== 'boolean' && typeof item !== 'number' && typeof item !== 'string') {
            throw new Error(`${context} property "${k}" list items must be scalars`);
          }
        }
      } else {
        throw new TypeError(`${context} property "${k}" must be a scalar or list of scalars`);
      }
    }
  }
  return props;
}

function validateCanonicalTheme(payload: unknown, label: string): void {
  if (!isRecord(payload)) {throw new Error(`${label} must be an object`);}
  rejectUnknownKeys(payload, ALLOWED_THEME_KEYS, label);
  const stringKeys = [
    'font_family',
    'primary_color',
    'background_color',
    'card_background_color',
    'text_color',
    'border_color',
  ];
  for (const key of stringKeys) {
    optionalString(payload[key], `${label}.${key}`);
  }
  for (const key of ['font_size_pt', 'border_width_px', 'border_radius_px']) {
    if (payload[key] !== undefined && typeof payload[key] !== 'number') {
      throw new Error(`${label}.${key} must be a number`);
    }
  }
  if (payload.color_palette !== undefined && !Array.isArray(payload.color_palette)) {
    throw new Error(`${label}.color_palette must be an array`);
  }
  optionalBoolean(payload.shadow_enabled, `${label}.shadow_enabled`);
}

function validateCanonicalAccessibility(payload: unknown, label: string): void {
  if (!isRecord(payload)) {throw new Error(`${label} must be an object`);}
  rejectUnknownKeys(payload, ALLOWED_ACCESSIBILITY_KEYS, label);
  optionalString(payload.alt_text, `${label}.alt_text`);
  optionalString(payload.aria_label, `${label}.aria_label`);
  optionalString(payload.screen_reader_summary, `${label}.screen_reader_summary`);
  if (payload.tab_index !== undefined && typeof payload.tab_index !== 'number') {
    throw new Error(`${label}.tab_index must be a number`);
  }
  optionalBoolean(payload.high_contrast_aware, `${label}.high_contrast_aware`);
}

export function validateCanonicalVisual(payload: unknown): CanonicalVisualPresentation {
  if (!isRecord(payload)) {throw new Error('visual payload must be an object');}
  rejectUnknownKeys(payload, ALLOWED_VISUAL_KEYS, 'visual');
  requireString(payload.visual_id, 'visual.visual_id');
  requireEnum(payload.intent, CONTRACT.presentation.visual_intents, 'visual.intent');
  optionalString(payload.title, 'visual.title');
  optionalString(payload.subtitle, 'visual.subtitle');
  optionalBoolean(payload.is_visible, 'visual.is_visible');
  if (payload.geometry !== undefined) {
    const {geometry} = payload;
    if (!isRecord(geometry)) {throw new Error('visual.geometry must be an object');}
    rejectUnknownKeys(geometry, ALLOWED_GEOMETRY_KEYS, 'visual.geometry');
    for (const key of ['x', 'y', 'width', 'height', 'z_index']) {
      if (geometry[key] !== undefined && typeof geometry[key] !== 'number') {
        throw new Error(`visual.geometry.${key} must be a number`);
      }
    }
    if (geometry.layout_mode !== undefined) {
      requireEnum(
        geometry.layout_mode,
        CONTRACT.presentation.layout_modes,
        'visual.geometry.layout_mode',
      );
    }
    if (
      geometry.padding !== undefined
      && (!Array.isArray(geometry.padding) || geometry.padding.length !== 4
        || geometry.padding.some((item) => typeof item !== 'number'))
    ) {
      throw new Error('visual.geometry.padding must be an array of four numbers');
    }
  }
  const bindings = payload.bindings === undefined ? [] : payload.bindings;
  if (!Array.isArray(bindings)) {throw new Error('visual.bindings must be an array');}
  for (const binding of bindings as Record<string, unknown>[]) {
    if (!isRecord(binding)) {throw new Error('visual.bindings entries must be objects');}
    rejectUnknownKeys(binding, ALLOWED_DATA_BINDING_KEYS, 'binding');
    requireString(binding.binding_id, 'binding.binding_id');
    requireString(binding.field_name, 'binding.field_name');
    requireEnum(binding.role, CONTRACT.presentation.field_roles, 'binding.role');
    for (const key of ['aggregation', 'format_string', 'display_name', 'sort_order']) {
      if (binding[key] !== undefined && binding[key] !== null && typeof binding[key] !== 'string') {
        throw new Error(`binding.${key} must be a string or null`);
      }
    }
  }
  if (payload.style !== undefined) {validateCanonicalTheme(payload.style, 'visual.style');}
  if (payload.accessibility !== undefined) {
    validateCanonicalAccessibility(payload.accessibility, 'visual.accessibility');
  }
  if (payload.properties !== undefined) {
    const properties = validateSafeProperties(payload.properties, 'visual');
    // Coherencia del contrato custom (espejo de core/contracts/coherence.py):
    // la spec anidada sólo es válida en visuales custom_visual. Un custom sin
    // spec se conserva como fallback opaco; una spec presente falla cerrado.
    const rawCustomSpec = properties.custom_visual_spec;
    if (payload.intent === 'custom_visual') {
      if (rawCustomSpec !== undefined && rawCustomSpec !== null) {
        const spec = parseCustomVisualSpec(rawCustomSpec);
        const bindingRoles = new Set(
          (bindings as Record<string, unknown>[]).map((binding) => binding.role as string),
        );
        const missingRoles = new Set(
          spec.layers.flatMap((layer) => [layer.x_role, layer.y_role])
            .filter((role) => !bindingRoles.has(role)),
        );
        if (missingRoles.size > 0) {
          throw new Error(
            `visual.properties.custom_visual_spec references missing binding role(s): ${[...missingRoles].join(', ')}`,
          );
        }
      }
    } else if (rawCustomSpec !== undefined && rawCustomSpec !== null) {
      throw new Error('visual.properties.custom_visual_spec is only allowed on custom_visual intent');
    }
  }
  return payload as unknown as CanonicalVisualPresentation;
}

export function validateCanonicalPagePresentation(page: unknown): CanonicalPagePresentation {
  if (!isRecord(page)) {throw new Error('page payload must be an object');}
  rejectUnknownKeys(page, ALLOWED_PAGE_PRESENTATION_KEYS, 'page presentation');
  const pageId = requireString(page.page_id, 'page.page_id');
  requireString(page.name, 'page.name');
  optionalString(page.display_name, 'page.display_name');
  optionalBoolean(page.is_hidden, 'page.is_hidden');
  for (const key of ['width', 'height']) {
    if (page[key] !== undefined && (typeof page[key] !== 'number' || (page[key] as number) <= 0)) {
      throw new Error(`page.${key} must be a positive number`);
    }
  }
  if (page.theme !== undefined) {validateCanonicalTheme(page.theme, 'page.theme');}
  if (page.accessibility !== undefined) {
    validateCanonicalAccessibility(page.accessibility, 'page.accessibility');
  }
  if (page.properties !== undefined) {
    validateSafeProperties(page.properties, 'page');
  }
  const visuals = page.visuals === undefined ? [] : page.visuals;
  if (!Array.isArray(visuals)) {throw new Error('page.visuals must be an array');}
  visuals.forEach((visual) => validateCanonicalVisual(visual));
  const visualIds = new Set<string>();
  for (const visual of visuals as Record<string, unknown>[]) {
    const visualId = requireString(visual.visual_id, 'visual.visual_id');
    if (visualIds.has(visualId)) {
      throw new Error(`duplicate visual_id "${visualId}" in page "${pageId}"`);
    }
    visualIds.add(visualId);
  }
  return page as unknown as CanonicalPagePresentation;
}

/** Validate a canonical PresentationIR boundary payload. */
export function validateCanonicalPresentationIR(payload: unknown): PresentationIR {
  if (!isRecord(payload)) {throw new Error('PresentationIR payload must be an object');}
  rejectUnknownKeys(payload, ALLOWED_PRESENTATION_IR_KEYS, 'PresentationIR');
  checkCanonicalSchemaVersion(
    payload.schema_version,
    CONTRACT.presentation.schema_version,
    'PresentationIR.schema_version',
  );
  optionalString(payload.doc_id, 'PresentationIR.doc_id');
  optionalString(payload.title, 'PresentationIR.title');
  if (payload.theme !== undefined) {validateCanonicalTheme(payload.theme, 'PresentationIR.theme');}
  if (payload.metadata !== undefined) {
    validateSafeProperties(payload.metadata, 'presentation metadata');
  }
  const pages = payload.pages === undefined ? [] : payload.pages;
  if (!Array.isArray(pages)) {throw new Error('PresentationIR.pages must be an array');}
  const pageIds = new Set<string>();
  for (const page of pages as Record<string, unknown>[]) {
    validateCanonicalPagePresentation(page);
    const pageId = page.page_id as string;
    if (pageIds.has(pageId)) {throw new Error(`duplicate page_id "${pageId}"`);}
    pageIds.add(pageId);
  }
  return payload as unknown as PresentationIR;
}

export function validateCanonicalInteractionFilter(payload: unknown): void {
  if (!isRecord(payload)) {throw new Error('interaction filter payload must be an object');}
  rejectUnknownKeys(payload, ALLOWED_FILTER_CONDITION_KEYS, 'interaction filter');
  requireString(payload.filter_id, 'interaction filter.filter_id');
  requireString(payload.target_field, 'interaction filter.target_field');
  requireEnum(
    payload.operator,
    CONTRACT.interaction.filter_operators,
    'interaction filter.operator',
  );
  optionalString(payload.name, 'interaction filter.name');
  if (payload.scope !== undefined) {
    requireEnum(payload.scope, CONTRACT.interaction.filter_scopes, 'interaction filter.scope');
  }
  optionalStringArray(payload.target_visual_ids, 'interaction filter.target_visual_ids');
  optionalBoolean(payload.is_interactive_slicer, 'interaction filter.is_interactive_slicer');
  if (payload.values !== undefined) {
    if (!Array.isArray(payload.values)) {
      throw new TypeError('interaction filter.values must be an array');
    }
    for (const item of payload.values) {
      if (item !== null && typeof item !== 'boolean' && typeof item !== 'number' && typeof item !== 'string') {
        throw new Error('interaction filter values must be scalars');
      }
    }
  }
}

export function validateCanonicalPageInteraction(page: unknown): unknown {
  if (!isRecord(page)) {throw new Error('interaction page payload must be an object');}
  rejectUnknownKeys(page, ALLOWED_PAGE_INTERACTION_KEYS, 'interaction page');
  requireString(page.page_id, 'interaction page.page_id');
  const selections = page.selections === undefined ? [] : page.selections;
  if (!Array.isArray(selections)) {throw new Error('interaction page.selections must be an array');}
  for (const selection of selections as Record<string, unknown>[]) {
    if (!isRecord(selection)) {throw new Error('selection payload must be an object');}
    rejectUnknownKeys(selection, ALLOWED_SELECTION_KEYS, 'selection');
    requireString(selection.selection_id, 'selection.selection_id');
    requireString(selection.source_visual_id, 'selection.source_visual_id');
    requireString(selection.target_field, 'selection.target_field');
    if (selection.mode !== undefined) {
      requireEnum(selection.mode, CONTRACT.interaction.selection_modes, 'selection.mode');
    }
    if (selection.selected_values !== undefined) {
      if (!Array.isArray(selection.selected_values)) {
        throw new TypeError('selection.selected_values must be an array');
      }
      for (const val of selection.selected_values) {
        if (val !== null && typeof val !== 'boolean' && typeof val !== 'number' && typeof val !== 'string') {
          throw new Error('selection selected_values must contain scalars');
        }
      }
    }
    optionalBoolean(selection.is_cleared_on_deselect, 'selection.is_cleared_on_deselect');
  }
  const filters = page.filters === undefined ? [] : page.filters;
  if (!Array.isArray(filters)) {throw new Error('interaction page.filters must be an array');}
  filters.forEach((item) => validateCanonicalInteractionFilter(item));
  const crossFilters = page.cross_filters === undefined ? [] : page.cross_filters;
  if (!Array.isArray(crossFilters)) {
    throw new TypeError('interaction page.cross_filters must be an array');
  }
  for (const rule of crossFilters as Record<string, unknown>[]) {
    if (!isRecord(rule)) {throw new Error('cross filter payload must be an object');}
    rejectUnknownKeys(rule, ALLOWED_CROSS_FILTER_KEYS, 'cross filter');
    requireString(rule.rule_id, 'cross filter.rule_id');
    requireString(rule.source_visual_id, 'cross filter.source_visual_id');
    if (rule.behavior !== undefined) {
      requireEnum(rule.behavior, CONTRACT.interaction.cross_filter_behaviors, 'cross filter.behavior');
    }
    optionalStringArray(rule.target_visual_ids, 'cross filter.target_visual_ids');
    optionalBoolean(rule.bidirectional, 'cross filter.bidirectional');
  }
  const drills = page.drills === undefined ? [] : page.drills;
  if (!Array.isArray(drills)) {throw new Error('interaction page.drills must be an array');}
  for (const drill of drills as Record<string, unknown>[]) {
    if (!isRecord(drill)) {throw new Error('drill payload must be an object');}
    rejectUnknownKeys(drill, ALLOWED_DRILL_KEYS, 'drill');
    requireString(drill.drill_id, 'drill.drill_id');
    requireString(drill.target_visual_id, 'drill.target_visual_id');
    optionalStringArray(drill.hierarchy_levels, 'drill.hierarchy_levels');
    if (drill.current_level_index !== undefined && typeof drill.current_level_index !== 'number') {
      throw new Error('drill.current_level_index must be a number');
    }
    optionalBoolean(drill.allow_drill_down, 'drill.allow_drill_down');
    optionalBoolean(drill.allow_drill_up, 'drill.allow_drill_up');
    if (
      drill.drill_through_page_id !== undefined
      && drill.drill_through_page_id !== null
      && typeof drill.drill_through_page_id !== 'string'
    ) {
      throw new Error('drill.drill_through_page_id must be a string or null');
    }
  }
  const tooltips = page.tooltips === undefined ? [] : page.tooltips;
  if (!Array.isArray(tooltips)) {throw new Error('interaction page.tooltips must be an array');}
  for (const tooltip of tooltips as Record<string, unknown>[]) {
    if (!isRecord(tooltip)) {throw new Error('tooltip payload must be an object');}
    rejectUnknownKeys(tooltip, ALLOWED_TOOLTIP_KEYS, 'tooltip');
    requireString(tooltip.tooltip_id, 'tooltip.tooltip_id');
    requireString(tooltip.source_visual_id, 'tooltip.source_visual_id');
    if (tooltip.kind !== undefined) {
      requireEnum(tooltip.kind, CONTRACT.interaction.tooltip_kinds, 'tooltip.kind');
    }
    optionalStringArray(tooltip.fields, 'tooltip.fields');
    optionalString(tooltip.trigger, 'tooltip.trigger');
    optionalString(tooltip.target_page_id, 'tooltip.target_page_id');
  }
  const navigations = page.navigations === undefined ? [] : page.navigations;
  if (!Array.isArray(navigations)) {
    throw new TypeError('interaction page.navigations must be an array');
  }
  for (const navigation of navigations as Record<string, unknown>[]) {
    if (!isRecord(navigation)) {throw new Error('navigation payload must be an object');}
    rejectUnknownKeys(navigation, ALLOWED_NAVIGATION_KEYS, 'navigation');
    requireString(navigation.action_id, 'navigation.action_id');
    requireString(navigation.trigger_source_id, 'navigation.trigger_source_id');
    requireEnum(
      navigation.kind,
      CONTRACT.interaction.navigation_kinds,
      'navigation.kind',
    );
    optionalString(navigation.target_page_id, 'navigation.target_page_id');
    optionalString(navigation.target_url, 'navigation.target_url');
    if (navigation.parameters !== undefined) {
      validateSafeProperties(navigation.parameters, 'navigation parameters');
    }
  }
  return page;
}

/** Validate a canonical InteractionIR boundary payload. */
export function validateCanonicalInteractionIR(payload: unknown): InteractionIR {
  if (!isRecord(payload)) {throw new Error('InteractionIR payload must be an object');}
  rejectUnknownKeys(payload, ALLOWED_INTERACTION_IR_KEYS, 'InteractionIR');
  checkCanonicalSchemaVersion(
    payload.schema_version,
    CONTRACT.interaction.schema_version,
    'InteractionIR.schema_version',
  );
  optionalString(payload.doc_id, 'InteractionIR.doc_id');
  if (payload.metadata !== undefined) {
    validateSafeProperties(payload.metadata, 'interaction metadata');
  }
  const globalFilters = payload.global_filters === undefined ? [] : payload.global_filters;
  if (!Array.isArray(globalFilters)) {
    throw new TypeError('InteractionIR.global_filters must be an array');
  }
  globalFilters.forEach((item) => validateCanonicalInteractionFilter(item));
  const pages = payload.pages === undefined ? [] : payload.pages;
  if (!Array.isArray(pages)) {throw new Error('InteractionIR.pages must be an array');}
  const pageIds = new Set<string>();
  for (const page of pages as Record<string, unknown>[]) {
    validateCanonicalPageInteraction(page);
    const pageId = (page as Record<string, unknown>).page_id as string;
    if (pageIds.has(pageId)) {throw new Error(`duplicate interaction page_id "${pageId}"`);}
    pageIds.add(pageId);
  }
  return payload as unknown as InteractionIR;
}

// ---------------------------------------------------------------------------
// Canonical contract fixture (generated by Python; covers every kind)
// ---------------------------------------------------------------------------

/**
 * Canonical JSON produced by `build_contract_fixture()` in
 * `core/contracts/semantic_ir.py`. Covers all expression kinds (including
 * `conditional`, `lod`, `window`, `lookup_map`, `set_membership`),
 * `Field.expression` and `Metric.entity`. Parsed at module load and kept as
 * the shared fixture for boundary validation on both languages.
 */
const CANONICAL_CONTRACT_FIXTURE_JSON = "{\"description\":\"Mechanical coverage fixture for boundary validators\",\"entities\":[{\"description\":\"\",\"fields\":[{\"data_type\":\"integer\",\"description\":\"\",\"expression\":null,\"hidden\":false,\"is_key\":true,\"name\":\"OrderID\",\"nullable\":false,\"source_column\":\"\"},{\"data_type\":\"decimal\",\"description\":\"\",\"expression\":null,\"hidden\":false,\"is_key\":false,\"name\":\"SalesAmount\",\"nullable\":true,\"source_column\":\"\"},{\"data_type\":\"date\",\"description\":\"\",\"expression\":null,\"hidden\":false,\"is_key\":false,\"name\":\"OrderDate\",\"nullable\":true,\"source_column\":\"\"},{\"data_type\":\"string\",\"description\":\"\",\"expression\":null,\"hidden\":false,\"is_key\":false,\"name\":\"Region\",\"nullable\":true,\"source_column\":\"\"},{\"data_type\":\"string\",\"description\":\"\",\"expression\":null,\"hidden\":false,\"is_key\":false,\"name\":\"RegionCode\",\"nullable\":true,\"source_column\":\"\"},{\"data_type\":\"decimal\",\"description\":\"calculated column coverage\",\"expression\":{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"SalesAmount\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"decimal\",\"distinct\":false,\"entity\":\"\",\"kind\":\"literal\",\"name\":\"\",\"op\":\"\",\"value\":0.9}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"mul\",\"value\":null},\"hidden\":false,\"is_key\":false,\"name\":\"DiscountedAmount\",\"nullable\":true,\"source_column\":\"\"}],\"grain\":{\"description\":\"\",\"fields\":[\"OrderID\"]},\"hidden\":false,\"name\":\"Sales\"},{\"description\":\"\",\"fields\":[{\"data_type\":\"integer\",\"description\":\"\",\"expression\":null,\"hidden\":false,\"is_key\":true,\"name\":\"CustomerKey\",\"nullable\":false,\"source_column\":\"\"},{\"data_type\":\"string\",\"description\":\"\",\"expression\":null,\"hidden\":false,\"is_key\":false,\"name\":\"Region\",\"nullable\":true,\"source_column\":\"\"}],\"grain\":{\"description\":\"\",\"fields\":[\"CustomerKey\"]},\"hidden\":false,\"name\":\"Customer\"}],\"filters\":[{\"applies_to\":[],\"description\":\"\",\"name\":\"High Sales\",\"operator\":\"gt\",\"target\":{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"measure_ref\",\"name\":\"Total Sales\",\"op\":\"\",\"value\":null},\"values\":[1000]},{\"applies_to\":[\"page:1\"],\"description\":\"\",\"name\":\"Region Filter\",\"operator\":\"in\",\"target\":{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"Region\",\"op\":\"\",\"value\":null},\"values\":[\"West\",\"East\"]}],\"metrics\":[{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"SalesAmount\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"agg\",\"name\":\"sum\",\"op\":\"\",\"value\":null},\"format_string\":\"#,##0.00\",\"hidden\":false,\"name\":\"Total Sales\"},{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"SalesAmount\",\"op\":\"\",\"value\":null},{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"Region\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"string\",\"distinct\":false,\"entity\":\"\",\"kind\":\"literal\",\"name\":\"\",\"op\":\"\",\"value\":\"West\"}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"eq\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"agg\",\"name\":\"sum\",\"op\":\"\",\"value\":null},\"format_string\":\"\",\"hidden\":false,\"name\":\"West Sales\"},{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"measure_ref\",\"name\":\"Total Sales\",\"op\":\"\",\"value\":null},{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"OrderID\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"agg\",\"name\":\"count\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"div\",\"value\":null},\"format_string\":\"\",\"hidden\":false,\"name\":\"Avg Order Size\"},{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[{\"children\":[{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"SalesAmount\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"integer\",\"distinct\":false,\"entity\":\"\",\"kind\":\"literal\",\"name\":\"\",\"op\":\"\",\"value\":100}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"sub\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"func\",\"name\":\"abs\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"unary\",\"name\":\"\",\"op\":\"neg\",\"value\":null},\"format_string\":\"\",\"hidden\":true,\"name\":\"Negated Variance\"},{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"SalesAmount\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"parameter_ref\",\"name\":\"Sales Floor\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"lt\",\"value\":null},{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"measure_ref\",\"name\":\"Total Sales\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"integer\",\"distinct\":false,\"entity\":\"\",\"kind\":\"literal\",\"name\":\"\",\"op\":\"\",\"value\":0}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"conditional\",\"name\":\"\",\"op\":\"\",\"value\":null},\"format_string\":\"\",\"hidden\":false,\"name\":\"Priority Sales\"},{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"Sales\",\"expression\":{\"children\":[{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"SalesAmount\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"agg\",\"name\":\"sum\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"Region\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"lod\",\"name\":\"fixed\",\"op\":\"\",\"value\":null},\"format_string\":\"\",\"hidden\":false,\"name\":\"Region Fixed Sales\"},{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"measure_ref\",\"name\":\"Total Sales\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"integer\",\"distinct\":false,\"entity\":\"\",\"kind\":\"literal\",\"name\":\"\",\"op\":\"\",\"value\":1}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"window\",\"name\":\"lookup\",\"op\":\"\",\"value\":null},\"format_string\":\"\",\"hidden\":false,\"name\":\"Trend Lookup\"},{\"data_type\":\"decimal\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"measure_ref\",\"name\":\"Total Sales\",\"op\":\"\",\"value\":null},{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Sales\",\"kind\":\"field_ref\",\"name\":\"Region\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"string\",\"distinct\":false,\"entity\":\"\",\"kind\":\"literal\",\"name\":\"\",\"op\":\"\",\"value\":\"West\"}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"eq\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"modify\",\"name\":\"\",\"op\":\"\",\"value\":null},\"format_string\":\"\",\"hidden\":false,\"name\":\"Adjusted Total\"},{\"data_type\":\"string\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"lookup_map\",\"name\":\"RegionCode\",\"op\":\"\",\"value\":{\"N\":\"Norte\",\"S\":\"Sur\"}},\"format_string\":\"\",\"hidden\":false,\"name\":\"Region Label\"},{\"data_type\":\"boolean\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Region\",\"kind\":\"set_membership\",\"name\":\"RegionSet\",\"op\":\"\",\"value\":[]},\"format_string\":\"\",\"hidden\":false,\"name\":\"In Target Set\"},{\"data_type\":\"integer\",\"description\":\"\",\"entity\":\"\",\"expression\":{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"opaque\",\"name\":\"dax\",\"op\":\"\",\"value\":\"COUNTROWS('Sales')\"},\"format_string\":\"\",\"hidden\":false,\"name\":\"Opaque DAX Fixture\"}],\"name\":\"Cross Language Contract Fixture\",\"parameters\":[{\"allowed_values\":[],\"data_type\":\"decimal\",\"default_value\":0,\"description\":\"\",\"expression\":null,\"is_set\":false,\"name\":\"Sales Floor\"},{\"allowed_values\":[],\"data_type\":\"string\",\"default_value\":[],\"description\":\"\",\"expression\":null,\"is_set\":true,\"name\":\"RegionSet\"},{\"allowed_values\":[],\"data_type\":\"date\",\"default_value\":null,\"description\":\"\",\"expression\":{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"func\",\"name\":\"today\",\"op\":\"\",\"value\":null},\"is_set\":false,\"name\":\"Run Marker\"}],\"relationships\":[{\"active\":true,\"cardinality\":\"many_to_one\",\"cross_filter\":\"single\",\"description\":\"\",\"from_entity\":\"Sales\",\"from_fields\":[\"OrderID\"],\"name\":\"SalesToCustomer\",\"to_entity\":\"Customer\",\"to_fields\":[\"CustomerKey\"]}],\"rls_intents\":[{\"description\":\"\",\"entity\":\"Customer\",\"expression\":{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Region\",\"kind\":\"set_membership\",\"name\":\"RegionSet\",\"op\":\"\",\"value\":[]},{\"children\":[{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"Customer\",\"kind\":\"field_ref\",\"name\":\"Region\",\"op\":\"\",\"value\":null},{\"children\":[],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"principal\",\"name\":\"\",\"op\":\"\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"eq\",\"value\":null}],\"data_type\":\"unknown\",\"distinct\":false,\"entity\":\"\",\"kind\":\"binary\",\"name\":\"\",\"op\":\"or\",\"value\":null},\"name\":\"Customer Region Policy\",\"roles\":[\"Sales\"]}],\"schema_version\":\"2.0.0\"}";

export const CANONICAL_CONTRACT_FIXTURE: SemanticModel = JSON.parse(CANONICAL_CONTRACT_FIXTURE_JSON);

// ---------------------------------------------------------------------------
// Runnable self-check (in-file proof surface; wire into CI when allowed)
// ---------------------------------------------------------------------------

function expectRejection(callback: () => void, label: string): void {
  try {
    callback();
  } catch {
    return;
  }
  throw new Error(`canonical contract self-check: ${label} was not rejected`);
}

function assertSortedUniqueVocabulary(values: readonly string[], label: string): void {
  const sorted = [...values].sort();
  for (let index = 1; index < sorted.length; index += 1) {
    if (sorted[index] === sorted[index - 1]) {
      throw new Error(`canonical contract self-check: duplicate ${label} "${sorted[index]}"`);
    }
  }
  if (JSON.stringify(values) !== JSON.stringify(sorted)) {
    throw new Error(`canonical contract self-check: ${label} is not canonically sorted`);
  }
}

/**
 * Assertion-based proof that the embedded contract stays self-consistent:
 * canonical vocabularies, fixture acceptance (full kind coverage), rejection
 * of invalid payloads, and the explicit same-major version policy.
 */
export function runCanonicalContractSelfCheck(): void {
  const vocabularies: readonly (readonly string[])[] = [
    CONTRACT.semantic.data_types,
    CONTRACT.semantic.expression.kinds,
    CONTRACT.semantic.expression.unary_ops,
    CONTRACT.semantic.expression.binary_ops,
    CONTRACT.semantic.expression.scalar_funcs,
    CONTRACT.semantic.expression.agg_funcs,
    CONTRACT.semantic.expression.lod_names,
    CONTRACT.semantic.expression.window_funcs,
    CONTRACT.semantic.filter_operators,
    CONTRACT.semantic.cardinalities,
    CONTRACT.semantic.cross_filter_directions,
    CONTRACT.presentation.visual_intents,
    CONTRACT.presentation.field_roles,
    CONTRACT.presentation.layout_modes,
    CONTRACT.interaction.selection_modes,
    CONTRACT.interaction.filter_operators,
    CONTRACT.interaction.filter_scopes,
    CONTRACT.interaction.cross_filter_behaviors,
    CONTRACT.interaction.tooltip_kinds,
    CONTRACT.interaction.navigation_kinds,
    CONTRACT.operations.semantic.kinds,
    CONTRACT.operations.semantic.targets,
    CONTRACT.operations.presentation.kinds,
    CONTRACT.operations.presentation.targets,
    CONTRACT.operations.interaction.kinds,
    CONTRACT.operations.interaction.targets,
  ];
  vocabularies.forEach((values) => assertSortedUniqueVocabulary(values, 'vocabulary'));

  const fixture = validateCanonicalSemanticModel(CANONICAL_CONTRACT_FIXTURE);

  const coveredKinds = new Set<string>();
  const collect = (expression: Expression): void => {
    coveredKinds.add(expression.kind);
    expression.children?.forEach(collect);
  };
  fixture.metrics.forEach((metric) => collect(metric.expression));
  fixture.entities.forEach((entity) => entity.fields.forEach((field) => {
    if (field.expression) {collect(field.expression);}
  }));
  fixture.parameters.forEach((parameter) => {
    if (parameter.expression) {collect(parameter.expression);}
  });
  fixture.filters.forEach((filter) => collect(filter.target));
  fixture.rls_intents.forEach((rls) => collect(rls.expression));
  const missingKinds = CONTRACT.semantic.expression.kinds.filter((kind) => !coveredKinds.has(kind));
  if (missingKinds.length > 0) {
    throw new Error(`canonical contract self-check: fixture misses kinds ${missingKinds.join(', ')}`);
  }

  const tampered: SemanticModel = JSON.parse(JSON.stringify(CANONICAL_CONTRACT_FIXTURE));
  (tampered.metrics[0].expression as { kind: string }).kind = 'not_a_kind';
  expectRejection(
    () => validateCanonicalExpression(tampered.metrics[0].expression),
    'unknown expression kind',
  );
  expectRejection(
    () => validateCanonicalExpression({ children: [{ kind: 'literal', value: 2 }], kind: 'literal', value: 1 }),
    'literal with children',
  );
  expectRejection(
    () => validateCanonicalExpression({
      children: [
        { kind: 'literal', value: 1 },
        { kind: 'literal', value: 2 },
      ],
      kind: 'binary',
      op: 'xor',
    }),
    'binary op outside the canonical allowlist',
  );
  expectRejection(
    () => validateCanonicalExpression({ entity: 'Sales', kind: 'field_ref' }),
    'field_ref without name',
  );
  expectRejection(
    () => validateCanonicalFilter({ name: 'f', operator: 'eq', target: { kind: 'literal', value: 1 }, values: [1] }),
    'filter target outside field_ref/measure_ref',
  );
  expectRejection(
    () => validateCanonicalFilter({
      name: 'f',
      operator: 'between',
      target: { kind: 'field_ref', name: 'Region' },
      values: [1],
    }),
    'between with one value',
  );

  const [major, minor, patch] = CONTRACT.semantic.schema_version.split('.').map(Number);
  checkCanonicalSchemaVersion(CONTRACT.semantic.schema_version, CONTRACT.semantic.schema_version);
  checkCanonicalSchemaVersion(`${major}.${minor + 1}.${patch}`, CONTRACT.semantic.schema_version);
  checkCanonicalSchemaVersion(
    `${major}.${Math.max(minor - 1, 0)}.${patch}`,
    CONTRACT.semantic.schema_version,
  );
  expectRejection(
    () => checkCanonicalSchemaVersion(`${major + 1}.0.0`, CONTRACT.semantic.schema_version),
    'unknown major version',
  );
  expectRejection(
    () => checkCanonicalSchemaVersion('2.0', CONTRACT.semantic.schema_version),
    'malformed version',
  );
  expectRejection(
    () => checkCanonicalSchemaVersion(undefined, CONTRACT.semantic.schema_version),
    'non-string version',
  );

  validateCanonicalOperation({
    kind: 'add',
    name: 'Copied Total Sales',
    payload: {
      expression: { kind: 'measure_ref', name: 'Total Sales' },
      name: 'Copied Total Sales',
    },
    target: 'metric',
  });
  expectRejection(
    () => validateCanonicalOperation({ kind: 'add', name: 'x', target: 'dashboard' }),
    'unknown operation target',
  );
  expectRejection(
    () => validateCanonicalOperation({ kind: 'remove', name: 'x', payload: {}, target: 'metric' }),
    'remove op with payload',
  );
  expectRejection(
    () => validateCanonicalOperation({
      kind: 'update',
      name: 'x',
      payload: { name: 'other' },
      target: 'metric',
    }),
    'payload name mismatch and missing expression',
  );

  validateCanonicalPresentationIR({
    doc_id: 'self-check',
    pages: [
      {
        page_id: 'p1',
        name: 'p1',
        visuals: [
          {
            visual_id: 'v1',
            intent: 'bar',
            bindings: [{ binding_id: 'b1', field_name: 'Region', role: 'x_axis' }],
          },
        ],
      },
    ],
    schema_version: CONTRACT.presentation.schema_version,
  });
  expectRejection(
    () => validateCanonicalPresentationIR({
      schema_version: `${Number(CANONICAL_CONTRACT.presentation.schema_version.split('.')[0]) + 1}.0.0`,
    }),
    'PresentationIR unknown major',
  );
  expectRejection(
    () => validateCanonicalPresentationIR({
      pages: [{ page_id: 'p1', name: 'p1', visuals: [{ visual_id: 'v1', intent: 'hologram' }] }],
      schema_version: CONTRACT.presentation.schema_version,
    }),
    'unknown presentation intent',
  );

  validateCanonicalInteractionIR({
    doc_id: 'self-check',
    pages: [
      {
        page_id: 'p1',
        selections: [{ selection_id: 's1', source_visual_id: 'v1', target_field: 'Region' }],
        filters: [{ filter_id: 'f1', target_field: 'Region', operator: 'equals', values: ['West'] }],
        cross_filters: [{ rule_id: 'c1', source_visual_id: 'v1' }],
        drills: [{ drill_id: 'd1', target_visual_id: 'v1' }],
        tooltips: [{ tooltip_id: 't1', source_visual_id: 'v1' }],
        navigations: [{ action_id: 'a1', trigger_source_id: 'v1', kind: 'page_nav' }],
      },
    ],
    schema_version: CONTRACT.interaction.schema_version,
  });
  expectRejection(
    () => validateCanonicalInteractionIR({
      pages: [{ page_id: 'p1', filters: [{ filter_id: 'f1', target_field: 'Region', operator: 'nope' }] }],
      schema_version: CONTRACT.interaction.schema_version,
    }),
    'unknown interaction filter operator',
  );

  // Malicious parity vectors & fail-closed remote ops checks
  expectRejection(
    () => validateRemotePresentationOperation({ extra: 1, kind: 'remove', name: 'p1', target: 'page' }),
    'unknown field in presentation operation',
  );
  expectRejection(
    () => validateRemotePresentationOperation({ kind: 'remove', name: 'p1', payload: {}, target: 'page' }),
    'remove presentation op with payload',
  );
  expectRejection(
    () => validateRemotePresentationOperation({ kind: 'remove', name: 'v1', target: 'visual' }),
    'visual presentation op missing page_id',
  );
  expectRejection(
    () => validateRemotePresentationOperation({ kind: 'remove', name: 'p1', page_id: 'p1', target: 'page' }),
    'page presentation op carrying page_id',
  );
  expectRejection(
    () => validateRemotePresentationOperation({
      kind: 'add',
      name: 'v1',
      page_id: 'p1',
      payload: { intent: 'bar', visual_id: 'v2' },
      target: 'visual',
    }),
    'visual presentation op name mismatch',
  );
  expectRejection(
    () => validateRemotePresentationOperation({
      kind: 'add',
      name: 'p1',
      payload: { name: 'P2', page_id: 'p2' },
      target: 'page',
    }),
    'page presentation op name mismatch',
  );

  expectRejection(
    () => validateRemoteInteractionOperation({ extra: 1, kind: 'remove', name: 'f1', target: 'global_filter' }),
    'unknown field in interaction operation',
  );
  expectRejection(
    () => validateRemoteInteractionOperation({ kind: 'remove', name: 'f1', payload: {}, target: 'global_filter' }),
    'remove interaction op with payload',
  );
  expectRejection(
    () => validateRemoteInteractionOperation({
      kind: 'add',
      name: 'f1',
      payload: { filter_id: 'f2', operator: 'equals', target_field: 'Region' },
      target: 'global_filter',
    }),
    'global_filter interaction op name mismatch',
  );
  expectRejection(
    () => validateRemoteInteractionOperation({
      kind: 'add',
      name: 'p1',
      payload: { page_id: 'p2' },
      target: 'page',
    }),
    'page interaction op name mismatch',
  );

  // Forbidden carrier properties
  for (const carrier of ['raw_sql', 'query', 'secret', 'password', 'token', 'connection_string', 'dsn', 'sql']) {
    expectRejection(
      () => validateCanonicalVisual({ intent: 'bar', properties: { [carrier]: 'evil' }, visual_id: 'v1' }),
      `forbidden carrier "${carrier}" in visual properties`,
    );
    expectRejection(
      () => validateCanonicalPagePresentation({ name: 'p1', page_id: 'p1', properties: { [carrier]: 'evil' } }),
      `forbidden carrier "${carrier}" in page properties`,
    );
    expectRejection(
      () => validateCanonicalPresentationIR({
        metadata: { [carrier]: 'evil' },
        schema_version: CONTRACT.presentation.schema_version,
      }),
      `forbidden carrier "${carrier}" in presentation metadata`,
    );
    expectRejection(
      () => validateCanonicalInteractionIR({
        metadata: { [carrier]: 'evil' },
        schema_version: CONTRACT.interaction.schema_version,
      }),
      `forbidden carrier "${carrier}" in interaction metadata`,
    );
    expectRejection(
      () => validateCanonicalPageInteraction({
        navigations: [{ action_id: 'a1', trigger_source_id: 'v1', kind: 'page_nav', parameters: { [carrier]: 'evil' } }],
        page_id: 'p1',
      }),
      `forbidden carrier "${carrier}" in navigation parameters`,
    );
  }

  // Non-scalar property, metadata, and filter values
  expectRejection(
    () => validateCanonicalVisual({ intent: 'bar', properties: { nested: { sub: 1 } }, visual_id: 'v1' }),
    'nested object in visual properties',
  );
  // Custom visual spec: única estructura anidada admitida, validada fail-closed
  // cuando existe; un custom sin spec sigue siendo un fallback opaco válido.
  const validCustomSpec = {
    layers: [{ mark: 'line', x_role: 'x_axis', y_role: 'y_axis', show_points: true }],
    max_data_points: 100,
    schema_version: '1.0.0',
  };
  validateCanonicalVisual({
    bindings: [
      { binding_id: 'bx', field_name: 'Region', role: 'x_axis' },
      { binding_id: 'by', field_name: 'Sales', role: 'y_axis' },
    ],
    intent: 'custom_visual',
    properties: { custom_visual_spec: validCustomSpec },
    visual_id: 'v1',
  });
  expectRejection(
    () => validateCanonicalVisual({
      intent: 'custom_visual',
      properties: {
        custom_visual_spec: {
          ...validCustomSpec,
          layers: [{ mark: 'bar', x_role: 'x_axis', y_role: 'y_axis', color: 'red' }],
        },
      },
      visual_id: 'v1',
    }),
    'non-hex color in nested custom_visual_spec',
  );
  expectRejection(
    () => validateCanonicalVisual({
      intent: 'bar',
      properties: { custom_visual_spec: { ...validCustomSpec, extra: 1 } },
      visual_id: 'v1',
    }),
    'unknown key in nested custom_visual_spec',
  );
  expectRejection(
    () => validateCanonicalVisual({
      intent: 'bar',
      properties: { custom_visual_spec: validCustomSpec },
      visual_id: 'v1',
    }),
    'custom_visual_spec on a non-custom intent',
  );
  validateCanonicalVisual({ intent: 'custom_visual', visual_id: 'v1' });
  expectRejection(
    () => validateCanonicalPresentationIR({
      metadata: { nested: { sub: 1 } },
      schema_version: CONTRACT.presentation.schema_version,
    }),
    'nested object in presentation metadata',
  );
  expectRejection(
    () => validateCanonicalInteractionIR({
      metadata: { nested: { sub: 1 } },
      schema_version: CONTRACT.interaction.schema_version,
    }),
    'nested object in interaction metadata',
  );
  expectRejection(
    () => validateCanonicalPageInteraction({
      navigations: [{ action_id: 'a1', trigger_source_id: 'v1', kind: 'page_nav', parameters: { nested: { sub: 1 } } }],
      page_id: 'p1',
    }),
    'nested object in navigation parameters',
  );
  expectRejection(
    () => validateCanonicalInteractionFilter({
      filter_id: 'f1',
      operator: 'equals',
      target_field: 'Region',
      values: [{ complex: true }],
    }),
    'complex object in interaction filter values',
  );

  // Acceptance: metadata escalar y parámetros de navegación escalares siguen siendo válidos.
  validateCanonicalPresentationIR({
    metadata: { locale: 'es', migrated_from: 'tableau' },
    schema_version: CONTRACT.presentation.schema_version,
  });
  validateCanonicalInteractionIR({
    metadata: { origin: 'sample-superstore' },
    schema_version: CONTRACT.interaction.schema_version,
  });
  validateCanonicalPageInteraction({
    navigations: [{ action_id: 'a1', trigger_source_id: 'v1', kind: 'page_nav', parameters: { delay_ms: 250 } }],
    page_id: 'p1',
  });

  // Unknown keys in visual and page presentations
  expectRejection(
    () => validateCanonicalVisual({ intent: 'bar', unknown_extra_key: true, visual_id: 'v1' }),
    'unknown key in visual presentation',
  );
  expectRejection(
    () => validateCanonicalPagePresentation({ name: 'p1', page_id: 'p1', unknown_extra_key: true }),
    'unknown key in page presentation',
  );
  expectRejection(
    () => validateCanonicalVisual({ geometry: { bad_key: 1, x: 0 }, intent: 'bar', visual_id: 'v1' }),
    'unknown key in visual geometry',
  );
}

// ---------------------------------------------------------------------------
// Contrato wire del modo Studio remoto (CMVP-04B)
//
// Derivado de apps/dataviz_service/server.py + storage/postgres.py. El cliente
// transporta SOLO operaciones tipadas atadas a base_version + base_checksum;
// nunca computa hashes, nunca persiste autoridad paralela en el navegador y
// nunca activa publicaciones. Las mutaciones llevan Idempotency-Key para un
// replay seguro ante respuestas perdidas.
// ---------------------------------------------------------------------------

export interface RemoteDurableHead {
  id: string;
  version_number: number;
  checksum: string;
  semantic: SemanticModel;
  presentation: PresentationIR;
  interaction: InteractionIR;
  parent_version_id: string | null;
  created_by: string | null;
  message: string | null;
  created_at: string;
}

export interface RemoteDurableHistoryEntry {
  id: string;
  version_number: number;
  checksum: string;
  parent_version_id: string | null;
  created_by: string | null;
  message: string | null;
  created_at: string;
  semantic?: SemanticModel;
  presentation?: PresentationIR;
  interaction?: InteractionIR;
}

/** Respuesta de GET /api/projects/{project_id}/authoring. */
export interface RemoteAuthoringState {
  project_id: string;
  published_version: number | null;
  head: RemoteDurableHead;
  history: RemoteDurableHistoryEntry[];
}

/** Fila durable devuelta por drafts|rollback (POST, HTTP 201). */
export interface RemoteDurableVersion {
  id: string;
  project_id: string;
  version_number: number;
  checksum: string;
  is_active: boolean;
  created_at: string;
}

export interface RemotePresentationOperation {
  kind: 'add' | 'update' | 'remove';
  target: 'page' | 'visual';
  name: string;
  page_id?: string | null;
  payload: CanonicalPagePresentation | CanonicalVisualPresentation | null;
}

export function validateRemotePresentationOperation(payload: unknown): RemotePresentationOperation {
  if (!isRecord(payload)) {throw new Error('Presentation operation must be an object');}
  const allowedKeys = new Set(['kind', 'target', 'name', 'page_id', 'payload']);
  for (const k of Object.keys(payload)) {
    if (!allowedKeys.has(k)) {
      throw new Error(`unknown field "${k}" in presentation operation`);
    }
  }
  const kind = requireEnum(payload.kind, CONTRACT.operations.presentation.kinds, 'PresentationOperation.kind') as 'add' | 'update' | 'remove';
  const target = requireEnum(payload.target, CONTRACT.operations.presentation.targets, 'PresentationOperation.target') as 'page' | 'visual';
  const name = requireString(payload.name, 'PresentationOperation.name');
  if (!name.trim()) {throw new Error('PresentationOperation.name must be non-empty');}
  if (target === 'visual' && (!payload.page_id || typeof payload.page_id !== 'string')) {
    throw new Error('visual presentation ops require page_id');
  }
  if (target === 'page' && payload.page_id !== undefined && payload.page_id !== null) {
    throw new Error('page presentation ops must not carry page_id');
  }
  if (kind === 'remove') {
    if (payload.payload !== undefined && payload.payload !== null) {
      throw new Error('remove presentation ops must not carry a payload');
    }
    return {
      kind,
      name,
      page_id: (payload.page_id as string | null) ?? null,
      payload: null,
      target,
    };
  }
  if (!isRecord(payload.payload)) {
    throw new Error(`${kind} presentation ops require a payload object`);
  }
  if (target === 'visual') {
    validateCanonicalVisual(payload.payload);
    const payloadId = (payload.payload as Record<string, unknown>).visual_id;
    if (payloadId !== name) {
      throw new Error(`payload id "${String(payloadId)}" must equal presentation op name "${name}"`);
    }
  } else if (target === 'page') {
    validateCanonicalPagePresentation(payload.payload);
    const payloadId = (payload.payload as Record<string, unknown>).page_id;
    if (payloadId !== name) {
      throw new Error(`payload id "${String(payloadId)}" must equal presentation op name "${name}"`);
    }
  }
  return {
    kind,
    name,
    page_id: (payload.page_id as string | null) ?? null,
    payload: payload.payload as unknown as CanonicalPagePresentation | CanonicalVisualPresentation,
    target,
  };
}

export interface RemoteInteractionOperation {
  kind: 'add' | 'update' | 'remove';
  target: 'page' | 'global_filter';
  name: string;
  payload: unknown;
}

export function validateRemoteInteractionOperation(payload: unknown): RemoteInteractionOperation {
  if (!isRecord(payload)) {throw new Error('Interaction operation must be an object');}
  const allowedKeys = new Set(['kind', 'target', 'name', 'payload']);
  for (const k of Object.keys(payload)) {
    if (!allowedKeys.has(k)) {
      throw new Error(`unknown field "${k}" in interaction operation`);
    }
  }
  const kind = requireEnum(payload.kind, CONTRACT.operations.interaction.kinds, 'InteractionOperation.kind') as 'add' | 'update' | 'remove';
  const target = requireEnum(payload.target, CONTRACT.operations.interaction.targets, 'InteractionOperation.target') as 'page' | 'global_filter';
  const name = requireString(payload.name, 'InteractionOperation.name');
  if (!name.trim()) {throw new Error('InteractionOperation.name must be non-empty');}
  if (kind === 'remove') {
    if (payload.payload !== undefined && payload.payload !== null) {
      throw new Error('remove interaction ops must not carry a payload');
    }
    return {
      kind,
      name,
      payload: null,
      target,
    };
  }
  if (!isRecord(payload.payload)) {
    throw new Error(`${kind} interaction ops require a payload object`);
  }
  if (target === 'global_filter') {
    validateCanonicalInteractionFilter(payload.payload);
    const payloadId = (payload.payload as Record<string, unknown>).filter_id;
    if (payloadId !== name) {
      throw new Error(`payload id "${String(payloadId)}" must equal interaction op name "${name}"`);
    }
  } else if (target === 'page') {
    validateCanonicalPageInteraction(payload.payload);
    const payloadId = (payload.payload as Record<string, unknown>).page_id;
    if (payloadId !== name) {
      throw new Error(`payload id "${String(payloadId)}" must equal interaction op name "${name}"`);
    }
  }
  return {
    kind,
    name,
    payload: payload.payload,
    target,
  };
}

/** Cuerpo de POST /api/projects/{project_id}/authoring/drafts. */
export interface RemoteDraftRequest {
  base_version: number;
  base_checksum: string;
  semantic_ops: Operation[];
  presentation_ops: RemotePresentationOperation[];
  interaction_ops: RemoteInteractionOperation[];
  message: string;
}

/** Cuerpo de POST /api/projects/{project_id}/authoring/rollback. */
export interface RemoteRollbackRequest {
  base_version: number;
  base_checksum: string;
  target_version: number;
  message: string;
}

/** Candidato durable devuelto por POST /api/projects/{project_id}/publication-requests. */
export interface RemotePublicationCandidate {
  id: string;
  project_id: string;
  target_version: number;
  expected_published_version: number | null;
  kind: string;
  target_checksum: string;
  diff_sha256: string;
  requested_by: string;
  requested_at: string;
}

/**
 * Estado de sincronización del modo Studio remoto. 'ready' es la ÚNICA puerta
 * para mutar: la autoridad durable vive en el servicio, no en el navegador.
 */
export type RemoteSaveState = 'loading' | 'ready' | 'saving' | 'stale-conflict' | 'server-error';

// Run boundary self-checks at load time to verify fail-closed invariants
runCanonicalContractSelfCheck();
