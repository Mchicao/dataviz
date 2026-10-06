import { INTERACTION_IR_SCHEMA_VERSION } from './types';
import type { AllowlistedQueryPredicate, CrossFilterRule, FilterCondition, FilterOperator, InteractionIR, InteractionState, NavigationAction, QueryAllowlistPolicy, SelectionMode } from './types';

/** Standard allowed filter operators per Interaction & Query AST spec. */
export const ALLOWED_FILTER_OPERATORS: readonly FilterOperator[] = [
  'equals',
  'not_equals',
  'in',
  'not_in',
  'greater_than',
  'less_than',
  'between',
  'contains',
  'not_contains',
  'starts_with',
  'not_starts_with',
  'ends_with',
  'not_ends_with',
  'top_n',
] as const;

/**
 * Deterministic Interaction Engine for Interaction IR.
 *
 * Manages interactive state (filters, selections, cross-filtering, tooltips,
 * hierarchy drills, and page navigation) and generates allowlisted query predicates.
 */
export class InteractionEngine {
  private ir: InteractionIR;
  private policy: QueryAllowlistPolicy;
  private state: InteractionState;

  constructor(
    ir: InteractionIR,
    initialPageId?: string,
    policy: QueryAllowlistPolicy = {},
  ) {
    if (!ir || ir.schema_version !== INTERACTION_IR_SCHEMA_VERSION) {
      const received = ir?.schema_version;
      throw new Error(
        `Incompatible Interaction IR schema_version: expected "${INTERACTION_IR_SCHEMA_VERSION}", got "${received}"`,
      );
    }

    this.ir = ir;
    this.policy = policy;

    const pages = ir.pages ?? [];
    const currentPageId = initialPageId ?? pages[0]?.page_id ?? 'default';

    this.state = {
      activeDrills: {},
      activeFilters: {},
      activeSelections: {},
      activeTooltip: null,
      currentPageId,
      navigationHistory: [currentPageId],
    };

    this.initializeGlobalFilters();
    this.initializePageInteractions(currentPageId);
  }

  /** Gets a clean copy of the current state. */
  public getState(): InteractionState {
    return {
      activeDrills: { ...this.state.activeDrills },
      activeFilters: { ...this.state.activeFilters },
      activeSelections: { ...this.state.activeSelections },
      activeTooltip: this.state.activeTooltip
        ? { ...this.state.activeTooltip }
        : null,
      currentPageId: this.state.currentPageId,
      navigationHistory: [...this.state.navigationHistory],
    };
  }

  /** Initialize global workbook filters. */
  private initializeGlobalFilters(): void {
    if (this.ir.global_filters) {
      for (const filter of this.ir.global_filters) {
        this.validateFilterPolicy(filter);
        this.state.activeFilters[filter.filter_id] = {
          ...filter,
          scope: filter.scope ?? 'workbook',
        };
      }
    }
  }

  /** Initialize page-specific selections, drills, and filters. */
  private initializePageInteractions(pageId: string): void {
    const page = this.ir.pages?.find((p) => p.page_id === pageId);
    if (!page) {return;}

    if (page.filters) {
      for (const filter of page.filters) {
        this.validateFilterPolicy(filter);
        this.state.activeFilters[filter.filter_id] = {
          ...filter,
          scope: filter.scope ?? 'page',
        };
      }
    }

    if (page.selections) {
      for (const sel of page.selections) {
        this.state.activeSelections[sel.source_visual_id] = {
          is_cleared_on_deselect: sel.is_cleared_on_deselect ?? true,
          mode: sel.mode ?? 'single',
          selected_values: sel.selected_values ? [...sel.selected_values] : [],
          selection_id: sel.selection_id,
          source_visual_id: sel.source_visual_id,
          target_field: sel.target_field,
        };
      }
    }

    if (page.drills) {
      for (const drill of page.drills) {
        this.state.activeDrills[drill.target_visual_id] = {
          allow_drill_down: drill.allow_drill_down ?? true,
          allow_drill_up: drill.allow_drill_up ?? true,
          current_level_index: drill.current_level_index ?? 0,
          drill_id: drill.drill_id,
          drill_through_page_id: drill.drill_through_page_id ?? null,
          hierarchy_levels: drill.hierarchy_levels
            ? [...drill.hierarchy_levels]
            : [],
          target_visual_id: drill.target_visual_id,
        };
      }
    }
  }

  /** Validate a filter condition against policy rules. */
  private validateFilterPolicy(filter: FilterCondition): void {
    this.validateField(filter.target_field);
    this.validateOperator(filter.operator);
  }

  /** Validate field against allowlist and denylist policy. */
  private validateField(field: string): void {
    if (!field || typeof field !== 'string') {
      throw new Error('Field name must be a non-empty string');
    }

    if (
      this.policy.deniedFields &&
      this.policy.deniedFields.includes(field)
    ) {
      throw new Error(`Field '${field}' is explicitly denied by policy`);
    }

    if (
      this.policy.allowedFields &&
      this.policy.allowedFields.length > 0 &&
      !this.policy.allowedFields.includes(field)
    ) {
      throw new Error(`Field '${field}' is not in the allowlist policy`);
    }
  }

  /** Validate operator against allowlist policy. */
  private validateOperator(operator: FilterOperator): void {
    if (!ALLOWED_FILTER_OPERATORS.includes(operator)) {
      throw new Error(`Filter operator '${operator}' is not allowed`);
    }

    if (
      this.policy.allowedOperators &&
      this.policy.allowedOperators.length > 0 &&
      !this.policy.allowedOperators.includes(operator)
    ) {
      throw new Error(`Operator '${operator}' is not permitted by policy`);
    }
  }

  /**
   * Perform deterministic visual selection.
   * Updates selection state and derived cross-filtering rules.
   */
  public select(
    sourceVisualId: string,
    targetField: string,
    values: unknown[],
    mode: SelectionMode = 'single',
    selectionId = `sel_${sourceVisualId}`,
  ): InteractionState {
    this.validateField(targetField);

    const existing = this.state.activeSelections[sourceVisualId];
    const isClearedOnDeselect = existing?.is_cleared_on_deselect ?? true;
    let nextValues: unknown[] = [];

    switch (mode) {
      case 'single': {
        const val = values[0];
        if (
          isClearedOnDeselect &&
          existing &&
          existing.selected_values.length === 1 &&
          existing.selected_values[0] === val
        ) {
          nextValues = [];
        } else {
          nextValues = val === undefined ? [] : [val];
        }
        break;
      }
      case 'multi': {
        const currentSet = new Set(existing ? existing.selected_values : []);
        for (const val of values) {
          if (currentSet.has(val)) {
            currentSet.delete(val);
          } else {
            currentSet.add(val);
          }
        }
        nextValues = [...currentSet];
        break;
      }
      case 'range': {
        if (values.length !== 2) {
          throw new Error('Range selection mode requires exactly 2 values [min, max]');
        }
        nextValues = [...values];
        break;
      }
      case 'literal_list': {
        nextValues = [...values];
        break;
      }
      default: {
        throw new Error(`Unsupported selection mode '${mode}'`);
      }
    }

    this.state.activeSelections[sourceVisualId] = {
      is_cleared_on_deselect: isClearedOnDeselect,
      mode,
      selected_values: nextValues,
      selection_id: selectionId,
      source_visual_id: sourceVisualId,
      target_field: targetField,
    };

    return this.getState();
  }

  /** Clear active selection for a source visual. */
  public clearSelection(sourceVisualId: string): InteractionState {
    if (this.state.activeSelections[sourceVisualId]) {
      this.state.activeSelections[sourceVisualId].selected_values = [];
    }
    return this.getState();
  }

  /** Apply or update a filter condition deterministically. */
  public applyFilter(filter: FilterCondition): InteractionState {
    this.validateFilterPolicy(filter);
    this.state.activeFilters[filter.filter_id] = {
      ...filter,
      scope: filter.scope ?? 'page',
    };
    return this.getState();
  }

  /** Remove a filter condition by filter_id. */
  public removeFilter(filterId: string): InteractionState {
    delete this.state.activeFilters[filterId];
    return this.getState();
  }

  /** Hierarchy Drill-down operation. */
  public drillDown(targetVisualId: string): InteractionState {
    const drill = this.state.activeDrills[targetVisualId];
    if (!drill) {
      throw new Error(`No drill specification found for visual '${targetVisualId}'`);
    }
    if (!drill.allow_drill_down) {
      throw new Error(`Drill down is disabled for visual '${targetVisualId}'`);
    }
    if (drill.current_level_index >= drill.hierarchy_levels.length - 1) {
      return this.getState(); // Max level reached, deterministic no-op
    }

    drill.current_level_index += 1;
    return this.getState();
  }

  /** Hierarchy Drill-up operation. */
  public drillUp(targetVisualId: string): InteractionState {
    const drill = this.state.activeDrills[targetVisualId];
    if (!drill) {
      throw new Error(`No drill specification found for visual '${targetVisualId}'`);
    }
    if (!drill.allow_drill_up) {
      throw new Error(`Drill up is disabled for visual '${targetVisualId}'`);
    }
    if (drill.current_level_index <= 0) {
      return this.getState(); // Min level reached, deterministic no-op
    }

    drill.current_level_index -= 1;
    return this.getState();
  }

  /** Drill-through to another page. */
  public drillThrough(
    sourceVisualId: string,
    targetPageId?: string,
  ): InteractionState {
    const drill = this.state.activeDrills[sourceVisualId];
    const pageId = targetPageId ?? drill?.drill_through_page_id;
    if (!pageId) {
      throw new Error(`Drill-through target page not specified for visual '${sourceVisualId}'`);
    }
    return this.navigate({
      action_id: `nav_drill_${sourceVisualId}`,
      kind: 'page_nav',
      target_page_id: pageId,
      trigger_source_id: sourceVisualId,
    });
  }

  /** Show tooltip for visual. */
  public showTooltip(
    sourceVisualId: string,
    dataPoint?: Record<string, unknown>,
  ): InteractionState {
    const page = this.ir.pages?.find((p) => p.page_id === this.state.currentPageId);
    const spec = page?.tooltips?.find((t) => t.source_visual_id === sourceVisualId);

    this.state.activeTooltip = {
      dataPoint,
      fields: spec?.fields ? [...spec.fields] : [],
      kind: spec?.kind ?? 'default',
      source_visual_id: sourceVisualId,
      target_page_id: spec?.target_page_id ?? null,
      tooltip_id: spec?.tooltip_id ?? `tt_${sourceVisualId}`,
    };

    return this.getState();
  }

  /** Hide tooltip. */
  public hideTooltip(): InteractionState {
    this.state.activeTooltip = null;
    return this.getState();
  }

  /**
   * Deterministic page navigation and action execution.
   */
  public navigate(action: NavigationAction): InteractionState {
    if (!action || !action.kind) {
      throw new Error('NavigationAction must have a valid kind');
    }

    switch (action.kind) {
      case 'page_nav': {
        const targetPage = action.target_page_id;
        if (!targetPage) {
          throw new Error('page_nav requires target_page_id');
        }
        if (targetPage !== this.state.currentPageId) {
          this.state.currentPageId = targetPage;
          this.state.navigationHistory.push(targetPage);
          this.initializePageInteractions(targetPage);
        }
        break;
      }
      case 'url_nav': {
        const url = action.target_url;
        if (!url || typeof url !== 'string') {
          throw new Error('url_nav requires target_url string');
        }
        const lowerUrl = url.trim().toLowerCase();
        if (
          lowerUrl.startsWith('javascript:') ||
          lowerUrl.startsWith('data:') ||
          lowerUrl.startsWith('vbscript:')
        ) {
          throw new Error(`Unsafe URL target scheme rejected: ${url}`);
        }
        break;
      }
      case 'back': {
        if (this.state.navigationHistory.length > 1) {
          this.state.navigationHistory.pop(); // Remove current
          const previousPage =
            this.state.navigationHistory[this.state.navigationHistory.length - 1];
          this.state.currentPageId = previousPage;
        }
        break;
      }
      case 'clear_filters': {
        // Clear page filters and active selections, keep workbook global filters
        const activeFilters: Record<string, FilterCondition> = {};
        for (const [id, filter] of Object.entries(this.state.activeFilters)) {
          if (filter.scope === 'workbook') {
            activeFilters[id] = filter;
          }
        }
        this.state.activeFilters = activeFilters;
        for (const selKey of Object.keys(this.state.activeSelections)) {
          this.state.activeSelections[selKey].selected_values = [];
        }
        break;
      }
      case 'bookmark': {
        if (action.parameters?.page_id) {
          const pageId = String(action.parameters.page_id);
          this.state.currentPageId = pageId;
          this.state.navigationHistory.push(pageId);
        }
        break;
      }
      default: {
        throw new Error(`Unsupported navigation kind '${(action as NavigationAction).kind}'`);
      }
    }

    return this.getState();
  }

  /**
   * Generates allowlisted query predicates for a target visual based on active
   * filters, active selections (cross-filtering), and active hierarchy drills.
   */
  public generateAllowlistedQueries(
    targetVisualId: string,
  ): AllowlistedQueryPredicate[] {
    const predicates: AllowlistedQueryPredicate[] = [];

    // 1. Process active filters (global, or page-level targeting visualId)
    for (const filter of Object.values(this.state.activeFilters)) {
      if (
        filter.scope === 'workbook' ||
        !filter.target_visual_ids ||
        filter.target_visual_ids.length === 0 ||
        filter.target_visual_ids.includes(targetVisualId)
      ) {
        this.validateFilterPolicy(filter);
        predicates.push({
          field: filter.target_field,
          operator: filter.operator,
          source: 'filter',
          values: filter.values ? [...filter.values] : [],
        });
      }
    }

    // 2. Process active cross-filters based on CrossFilterRules on the current page
    const currentPage = this.ir.pages?.find(
      (p) => p.page_id === this.state.currentPageId,
    );
    const crossFilterRules = currentPage?.cross_filters ?? [];

    for (const selection of Object.values(this.state.activeSelections)) {
      if (
        selection.source_visual_id !== targetVisualId &&
        selection.selected_values.length > 0
      ) {
        // Find matching rule
        const rule = crossFilterRules.find(
          (r: CrossFilterRule) =>
            r.source_visual_id === selection.source_visual_id &&
            (r.target_visual_ids === undefined ||
              r.target_visual_ids.length === 0 ||
              r.target_visual_ids.includes(targetVisualId)),
        );

        const behavior = rule?.behavior ?? 'filter';
        if (behavior === 'filter') {
          this.validateField(selection.target_field);
          predicates.push({
            field: selection.target_field,
            operator: selection.mode === 'range' ? 'between' : 'in',
            source: 'cross_filter',
            values: [...selection.selected_values],
          });
        }
      }
    }

    // 3. Process active drill state
    const drill = this.state.activeDrills[targetVisualId];
    if (drill && drill.current_level_index > 0) {
      const activeLevelField = drill.hierarchy_levels[drill.current_level_index];
      if (activeLevelField) {
        this.validateField(activeLevelField);
        predicates.push({
          field: activeLevelField,
          operator: 'equals',
          source: 'drill',
          values: [activeLevelField],
        });
      }
    }

    // Sort predicates deterministically by field then operator
    return predicates.sort((a, b) => {
      const fieldCmp = a.field.localeCompare(b.field);
      if (fieldCmp !== 0) {return fieldCmp;}
      return a.operator.localeCompare(b.operator);
    });
  }
}
