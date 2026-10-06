/**
 * Interaction Intermediate Representation (Interaction IR) TypeScript types.
 *
 * Mirrors the canonical Python contracts in `core/contracts/interaction_ir.py`.
 */

export const INTERACTION_IR_SCHEMA_VERSION = '1.1.0';

export type SelectionMode = 'single' | 'multi' | 'range' | 'literal_list';

export type FilterOperator =
  | 'equals'
  | 'not_equals'
  | 'in'
  | 'not_in'
  | 'greater_than'
  | 'less_than'
  | 'between'
  | 'contains'
  | 'not_contains'
  | 'starts_with'
  | 'not_starts_with'
  | 'ends_with'
  | 'not_ends_with'
  | 'top_n';

export type FilterScope = 'visual' | 'page' | 'workbook';

export type CrossFilterBehavior = 'filter' | 'highlight' | 'none';

export type TooltipKind = 'default' | 'custom_fields' | 'report_page';

export type NavigationKind =
  | 'page_nav'
  | 'url_nav'
  | 'back'
  | 'bookmark'
  | 'clear_filters';

export interface SelectionContract {
  selection_id: string;
  source_visual_id: string;
  target_field: string;
  mode?: SelectionMode;
  selected_values?: unknown[];
  is_cleared_on_deselect?: boolean;
}

export interface FilterCondition {
  filter_id: string;
  name?: string;
  target_field: string;
  operator: FilterOperator;
  values?: unknown[];
  scope?: FilterScope;
  target_visual_ids?: string[];
  is_interactive_slicer?: boolean;
}

export interface CrossFilterRule {
  rule_id: string;
  source_visual_id: string;
  target_visual_ids?: string[];
  behavior?: CrossFilterBehavior;
  bidirectional?: boolean;
}

export interface DrillSpec {
  drill_id: string;
  target_visual_id: string;
  hierarchy_levels?: string[];
  current_level_index?: number;
  allow_drill_down?: boolean;
  allow_drill_up?: boolean;
  drill_through_page_id?: string | null;
}

export interface TooltipSpec {
  tooltip_id: string;
  source_visual_id: string;
  kind?: TooltipKind;
  fields?: string[];
  target_page_id?: string | null;
  trigger?: string;
}

export interface NavigationAction {
  action_id: string;
  trigger_source_id: string;
  kind: NavigationKind;
  target_page_id?: string | null;
  target_url?: string | null;
  parameters?: Record<string, unknown>;
}

export interface PageInteractionSpec {
  page_id: string;
  selections?: SelectionContract[];
  filters?: FilterCondition[];
  cross_filters?: CrossFilterRule[];
  drills?: DrillSpec[];
  tooltips?: TooltipSpec[];
  navigations?: NavigationAction[];
}

export interface InteractionIR {
  schema_version: string;
  doc_id?: string;
  global_filters?: FilterCondition[];
  pages?: PageInteractionSpec[];
  metadata?: Record<string, unknown>;
}

/** Active selection state held by runtime engine. */
export interface ActiveSelection {
  selection_id: string;
  source_visual_id: string;
  target_field: string;
  mode: SelectionMode;
  selected_values: unknown[];
  is_cleared_on_deselect: boolean;
}

/** Active drill state per visual. */
export interface ActiveDrill {
  drill_id: string;
  target_visual_id: string;
  hierarchy_levels: string[];
  current_level_index: number;
  allow_drill_down: boolean;
  allow_drill_up: boolean;
  drill_through_page_id: string | null;
}

/** Active tooltip state. */
export interface ActiveTooltip {
  tooltip_id: string;
  source_visual_id: string;
  kind: TooltipKind;
  fields: string[];
  target_page_id: string | null;
  dataPoint?: Record<string, unknown>;
}

/** Allowlist policy for interactions and query generation. */
export interface QueryAllowlistPolicy {
  allowedFields?: string[];
  allowedOperators?: FilterOperator[];
  allowedDatasources?: string[];
  deniedFields?: string[];
}

/** Allowlisted query predicate output. */
export interface AllowlistedQueryPredicate {
  field: string;
  operator: FilterOperator;
  values: unknown[];
  source: 'filter' | 'cross_filter' | 'drill';
}

/** Deterministic Interaction State snapshot. */
export interface InteractionState {
  currentPageId: string;
  activeSelections: Record<string, ActiveSelection>;
  activeFilters: Record<string, FilterCondition>;
  activeDrills: Record<string, ActiveDrill>;
  activeTooltip: ActiveTooltip | null;
  navigationHistory: string[];
}
