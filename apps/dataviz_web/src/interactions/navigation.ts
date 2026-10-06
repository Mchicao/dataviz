/**
 * Navigation derived from source-declared hierarchy metadata and report geometry.
 */

import type { VisualSpec } from '../runtime/types';
import type { DrillSpec } from './types';

const LEVEL_ROLE_PATTERN = /^(x|y)_axis(?:_(\d+))?$/;

export interface DeclaredDrillPath {
  readonly name?: string;
  readonly levels: readonly string[];
  readonly datasource?: string;
}

/** Read source-declared drill paths carried in RenderPlan metadata. */
export function drillPathsFromMetadata(metadata?: Record<string, unknown>): DeclaredDrillPath[] {
  const raw = metadata?.drill_paths;
  if (!Array.isArray(raw)) {return [];}
  const paths: DeclaredDrillPath[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') {continue;}
    const record = item as Record<string, unknown>;
    if (!Array.isArray(record.levels)) {continue;}
    const levels = record.levels.filter((level): level is string => typeof level === 'string' && level.length > 0);
    if (levels.length < 2) {continue;}
    paths.push({
      datasource: typeof record.datasource === 'string' ? record.datasource : undefined,
      levels,
      name: typeof record.name === 'string' ? record.name : undefined,
    });
  }
  return paths;
}

function axisFieldLevels(spec: VisualSpec): string[] {
  interface Level {
    family: 'x' | 'y';
    index: number;
    field: string;
  }
  const levels: Level[] = [];
  for (const [role, ref] of Object.entries(spec.data_roles ?? {})) {
    const match = role.match(LEVEL_ROLE_PATTERN);
    if (!match || !String(ref).startsWith('field:')) {continue;}
    levels.push({
      family: match[1] === 'x' ? 'x' : 'y',
      field: String(ref).slice('field:'.length),
      index: Number(match[2] ?? 0),
    });
  }
  if (levels.length < 2) {return [];}
  const xLevels = levels.filter((level) => level.family === 'x');
  const yLevels = levels.filter((level) => level.family === 'y');
  const chosen = yLevels.length >= xLevels.length ? yLevels : xLevels;
  return [...new Set(
    [...chosen].sort((left, right) => left.index - right.index).map((level) => level.field),
  )];
}

function belongsToDeclaredPath(
  fields: readonly string[],
  path: DeclaredDrillPath,
  datasource: string,
): boolean {
  if (path.datasource && datasource && path.datasource !== datasource) {return false;}
  let cursor = -1;
  for (const field of fields) {
    cursor = path.levels.indexOf(field, cursor + 1);
    if (cursor < 0) {return false;}
  }
  return true;
}

/**
 * Return ordered hierarchy levels only when the source declared a matching drill path.
 * Composite shelves without a declared hierarchy are intentionally not drillable.
 */
export function hierarchyLevelsForVisual(
  spec: VisualSpec,
  declaredPaths: readonly DeclaredDrillPath[] = [],
): string[] {
  const fields = axisFieldLevels(spec);
  if (fields.length < 2 || declaredPaths.length === 0) {return [];}
  const datasource = String((spec.query as Record<string, unknown> | null | undefined)?.from_datasource ?? '');
  return declaredPaths.some((path) => belongsToDeclaredPath(fields, path, datasource)) ? fields : [];
}

/** Derive one deterministic DrillSpec per visual backed by a declared hierarchy. */
export function derivePageDrills(
  pageId: string,
  visuals: readonly VisualSpec[],
  declaredPaths: readonly DeclaredDrillPath[] = [],
): DrillSpec[] {
  const drills: DrillSpec[] = [];
  for (const spec of visuals) {
    const hierarchyLevels = hierarchyLevelsForVisual(spec, declaredPaths);
    if (hierarchyLevels.length < 2) {continue;}
    drills.push({
      current_level_index: 0,
      drill_id: `${pageId}:derived-drill:${spec.name}`,
      hierarchy_levels: hierarchyLevels,
      target_visual_id: spec.name,
    });
  }
  return drills;
}

function expressionFieldName(value: unknown): string {
  if (!value || typeof value !== 'object') {return '';}
  const expression = value as Record<string, unknown>;
  return expression.kind === 'field_ref' && typeof expression.name === 'string' ? expression.name : '';
}

function selectFieldName(value: unknown): string {
  if (!value || typeof value !== 'object') {return '';}
  return expressionFieldName((value as Record<string, unknown>).expression);
}

/**
 * Rewrite a visual query to the active hierarchy level while preserving measures,
 * filters, parameters, and non-hierarchy grouping dimensions.
 */
export function visualAtHierarchyLevel(
  spec: VisualSpec,
  hierarchyLevels: readonly string[],
  levelIndex: number,
): VisualSpec {
  if (!spec.query || hierarchyLevels.length < 2) {return spec;}
  const activeLevel = hierarchyLevels[Math.max(0, Math.min(levelIndex, hierarchyLevels.length - 1))];
  const query = spec.query as Record<string, unknown>;
  const groupBy = Array.isArray(query.group_by) ? query.group_by : [];
  const select = Array.isArray(query.select) ? query.select : [];
  const hierarchy = new Set(hierarchyLevels);

  const activeGroup = groupBy.find((expression) => expressionFieldName(expression) === activeLevel);
  const activeSelect = select.find((item) => selectFieldName(item) === activeLevel);
  const activeExpression = activeGroup
    ?? (activeSelect && typeof activeSelect === 'object'
      ? (activeSelect as Record<string, unknown>).expression
      : undefined);
  if (!activeExpression || !activeSelect) {return spec;}

  const retainedGroup = groupBy.filter((expression) => !hierarchy.has(expressionFieldName(expression)));
  const retainedSelect = select.filter((item) => !hierarchy.has(selectFieldName(item)));
  return {
    ...spec,
    query: {
      ...query,
      group_by: [...retainedGroup, structuredClone(activeExpression)],
      select: [...retainedSelect, structuredClone(activeSelect)],
    },
  };
}



export interface HierarchyNavigatorDataBindings {
  readonly idField: string;
  readonly labelField: string;
  readonly parentField: string;
  readonly pathFields: readonly string[];
}

export interface HierarchyNavigatorParameterBindings {
  readonly id?: string;
  readonly label?: string;
  readonly path: readonly string[];
  readonly depth?: string;
}

export interface HierarchyNavigatorSpec {
  readonly kind: 'hierarchy';
  readonly navigationId: string;
  readonly pageId: string;
  readonly sourceVisualId: string;
  readonly mode: 'recursive' | 'flat';
  readonly pathSeparator: string;
  readonly geometry: Record<string, number | string>;
  readonly data: HierarchyNavigatorDataBindings;
  readonly selectionParameters: HierarchyNavigatorParameterBindings;
  readonly filter?: {
    readonly targetField: string;
    readonly valueField: string;
  };
  readonly sourceSelection: {
    readonly emitField?: string;
    readonly listen: boolean;
  };
  readonly searchable: boolean;
}

/** Parse source-agnostic hierarchy navigation contracts from a RenderPlan. */
export function hierarchyNavigationContractsFromMetadata(metadata?: Record<string, unknown>): HierarchyNavigatorSpec[] {
  const raw = metadata?.navigation_contracts;
  if (!Array.isArray(raw)) {return [];}
  const navigators: HierarchyNavigatorSpec[] = [];
  for (const value of raw) {
    if (!value || typeof value !== 'object') {continue;}
    const record = value as Record<string, unknown>;
    const {data} = record;
    const parameters = record.selection_parameters;
    const sourceSelection = record.source_selection;
    if (record.kind !== 'hierarchy' || !data || typeof data !== 'object') {continue;}
    if (!parameters || typeof parameters !== 'object' || !sourceSelection || typeof sourceSelection !== 'object') {continue;}
    const d = data as Record<string, unknown>;
    const p = parameters as Record<string, unknown>;
    const selection = sourceSelection as Record<string, unknown>;
    const filter = record.filter && typeof record.filter === 'object'
      ? record.filter as Record<string, unknown>
      : null;
    const mode = record.mode === 'flat' ? 'flat' : record.mode === 'recursive' ? 'recursive' : null;
    if (!mode || typeof record.page_id !== 'string' || typeof record.source_visual_id !== 'string') {continue;}
    navigators.push({
      kind: 'hierarchy',
      navigationId: typeof record.navigation_id === 'string' ? record.navigation_id : '',
      pageId: record.page_id,
      sourceVisualId: record.source_visual_id,
      mode,
      pathSeparator: typeof record.path_separator === 'string' ? record.path_separator : '|',
      geometry: record.geometry && typeof record.geometry === 'object'
        ? Object.fromEntries(Object.entries(record.geometry as Record<string, unknown>)
          .filter(([, item]) => typeof item === 'number' || typeof item === 'string')) as Record<string, number | string>
        : {},
      data: {
        idField: typeof d.id_field === 'string' ? d.id_field : '',
        labelField: typeof d.label_field === 'string' ? d.label_field : '',
        parentField: typeof d.parent_field === 'string' ? d.parent_field : '',
        pathFields: Array.isArray(d.path_fields) ? d.path_fields.filter((item): item is string => typeof item === 'string') : [],
      },
      selectionParameters: {
        ...(typeof p.id === 'string' && p.id ? { id: p.id } : {}),
        ...(typeof p.label === 'string' && p.label ? { label: p.label } : {}),
        path: Array.isArray(p.path) ? p.path.filter((item): item is string => typeof item === 'string') : [],
        ...(typeof p.depth === 'string' && p.depth ? { depth: p.depth } : {}),
      },
      ...(filter && typeof filter.target_field === 'string' && typeof filter.value_field === 'string' ? {
        filter: { targetField: filter.target_field, valueField: filter.value_field },
      } : {}),
      sourceSelection: {
        ...(typeof selection.emit_field === 'string' && selection.emit_field ? { emitField: selection.emit_field } : {}),
        listen: Boolean(selection.listen),
      },
      searchable: Boolean(record.searchable),
    });
  }
  return navigators;
}

export interface HierarchySelectionValue {
  readonly id: string;
  readonly label: string;
  readonly path: readonly string[];
}

/** Resolve a hierarchy navigation filter value from its source visual results. */
export function hierarchyFilterValue(
  navigator: HierarchyNavigatorSpec,
  selection: HierarchySelectionValue,
  results: Record<string, unknown>,
): unknown {
  const filterField = navigator.filter?.valueField;
  if (!filterField) {return undefined;}
  if (filterField === navigator.data.idField) {return selection.id;}
  if (filterField === navigator.data.labelField) {return selection.label;}

  const filterValues = results[`field:${filterField}`];
  if (!Array.isArray(filterValues)) {return filterValues;}
  const ids = results[`field:${navigator.data.idField}`];
  if (!Array.isArray(ids)) {return undefined;}
  const index = ids.findIndex((value) => String(value) === selection.id);
  return index === -1 ? undefined : filterValues[index];
}

/** Calculated state of the visible report-page navigation window. */
export interface PageNavWindow {
  readonly visible: readonly string[];
  readonly hasOverflow: boolean;
  readonly canPrev: boolean;
  readonly canNext: boolean;
  readonly position: number;
  readonly total: number;
}

export function pageNavWindow(
  pageNames: readonly string[],
  selectedPage: string,
  visibleCount: number,
): PageNavWindow {
  const total = pageNames.length;
  const selectedIndex = Math.max(0, pageNames.indexOf(selectedPage));
  const windowSize = Math.max(1, Math.min(Math.floor(visibleCount), total));
  let start = selectedIndex - Math.floor((windowSize - 1) / 2);
  start = Math.max(0, Math.min(start, total - windowSize));
  const hasOverflow = total > windowSize;
  return {
    canNext: hasOverflow && selectedIndex < total - 1,
    canPrev: hasOverflow && selectedIndex > 0,
    hasOverflow,
    position: selectedIndex + 1,
    total,
    visible: pageNames.slice(start, start + windowSize),
  };
}
