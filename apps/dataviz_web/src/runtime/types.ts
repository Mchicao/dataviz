/**
 * Canonical DataVIZ RenderPlan contract (TypeScript mirror).
 *
 * Mirrors the Python contract in `core/compilers/render_plan.py`. The runtime
 * consumes the JSON form of a RenderPlan plus results isolated per visual; it
 * never reads Tableau (.twb/.twbx) or Power BI (.pbip/.pbir) artifacts.
 */
import type { CustomVisualSpec } from '../visuals/custom/spec';

/** Schema version of the canonical RenderPlan contract (SemVer, exact match). */
export const RENDER_PLAN_SCHEMA_VERSION = '2.1.0';

/** Schema version emitted by the Python runtime materializer. */
export const RUNTIME_RESULTS_SCHEMA_VERSION = '2.0.0';

/**
 * Visual kinds this runtime can render. Closed set.
 *
 * This set mirrors the neutral visual intents emitted by the Python bridge.
 * The runtime keeps the intent names stable so the same plan can target PBIP
 * or the web renderer without leaking a vendor component name.
 */
export const SUPPORTED_VISUAL_KINDS = [
  'card',
  'kpi',
  'table',
  'matrix',
  'bar',
  'stacked_bar',
  'percent_stacked_bar',
  'column',
  'stacked_column',
  'percent_stacked_column',
  'line',
  'area',
  'stacked_area',
  'pie',
  'donut',
  'scatter',
  'treemap',
  'heatmap',
  'funnel',
  'histogram',
  'box_plot',
  'bullet',
  'pareto',
  'lollipop',
  'combo',
  'ribbon',
  'packed_bubbles',
  'gantt',
  'map',
  'waterfall',
  'gauge',
  'text_box',
  'slicer',
  'custom_visual',
] as const;

/** A kind this runtime can render directly. */
export type SupportedVisualKind = (typeof SUPPORTED_VISUAL_KINDS)[number];

/** A primitive cell value produced by a query. */
export type Scalar = number | string | boolean | null;

/** A tabular row: column name -> cell value. */
export type Row = Record<string, Scalar>;

/**
 * Result for a single neutral reference.
 *
 * - `Scalar`: a single headline value (e.g. a measure total).
 * - `Scalar[]`: a flat column of values, keyed by a field reference.
 * - `Row[]`: a tabular dataset, keyed by an entity reference.
 */
export type QueryResult = Scalar | readonly Scalar[] | readonly Row[];

/**
 * External query results keyed by neutral reference.
 *
 * Keys match the values in `VisualSpec.data_roles`, e.g.
 * `"measure:total_amount"` -> `1234.5` or `"entity:sales"` -> `Row[]`.
 */
export type QueryResults = Record<string, QueryResult>;

/** Executed results are isolated by visual name to avoid cross-query collisions. */
export interface RuntimeResults {
  schema_version: typeof RUNTIME_RESULTS_SCHEMA_VERSION;
  visuals: Record<string, QueryResults>;
  // Passthrough opaco: el wire se valida con Schema en client.ts.
  // oxlint-disable-next-line anti-slop/no-unsafe-dictionary-type
  forecast_models?: Record<string, Record<string, unknown>>;
}

/** Neutral forecast instruction mirrored from Python `ForecastSpec`. */
export interface ForecastSpec {
  time_field: string;
  value_field: string;
  period: 'day' | 'week' | 'month' | 'quarter' | 'year';
  horizon: number;
  confidence_level: number;
  ignore_last: number;
  fill_missing: boolean;
  seasonal: boolean;
  prediction_intervals: boolean;
}

/** Canonical visual spec (mirrors Python `VisualSpec.to_dict()`). */
export interface VisualSpec {
  name: string;
  kind: string;
  data_roles?: Record<string, string>;
  page?: string;
  title?: string;
  geometry?: Record<string, number | string>;
  // Passthrough opaco: el wire se valida con Schema en client.ts.
  // oxlint-disable-next-line anti-slop/no-unsafe-dictionary-type
  query?: Record<string, unknown> | null;
  forecast?: ForecastSpec | null;
  liveness_policy?: LivenessPolicy;
  /**
   * Spec declarativa de capas para `custom_visual`. Debe superar el parser
   * fail-closed (`parseCustomVisualSpec`); se descarta en caso contrario.
   */
  custom_spec?: CustomVisualSpec | null;
}

/** Canonical interaction spec (mirrors Python `InteractionSpec.to_dict()`). */
export interface InteractionSpec {
  name: string;
  source: string;
  target: string;
  kind: string;
  data_role?: string;
  values?: readonly unknown[];
}

/** Canonical render plan (mirrors Python `RenderPlan.to_dict()`). */
export interface RenderPlan {
  schema_version: string;
  visuals: readonly VisualSpec[];
  interactions?: readonly InteractionSpec[];
  description?: string;
  // Passthrough opaco: el wire se valida con Schema en client.ts.
  // oxlint-disable-next-line anti-slop/no-unsafe-dictionary-type
  metadata?: Record<string, unknown>;
}

export type LivenessPolicy = 'requires_marks' | 'allows_empty_state' | 'decorative';

/** Runtime evidence emitted per displayed visual. */
export interface VisualLivenessRecord {
  visual_id: string;
  policy: LivenessPolicy;
  row_count: number;
  mark_count: number;
  error: string;
}

/** A resolved data role: its neutral reference and the query data (if any). */
export interface InterpretedRole {
  ref: string;
  data: QueryResult | undefined;
}

/** A visual after interpretation: ready to hand to a renderer. */
export interface InterpretedVisual {
  name: string;
  kind: string;
  /** True when the runtime has a native renderer for `kind`. */
  supported: boolean;
  /** Human-readable title derived from `name`. */
  title: string;
  geometry: Record<string, number | string>;
  /** Resolved data roles, keyed by role name. */
  roles: Record<string, InterpretedRole>;
  /** Spec declarativa validada; presente sólo para custom_visual con spec válida. */
  custom_spec?: CustomVisualSpec;
}
