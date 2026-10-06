import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { InteractionEngine, INTERACTION_IR_SCHEMA_VERSION, derivePageDrills, drillPathsFromMetadata, hierarchyLevelsForVisual, hierarchyNavigationContractsFromMetadata, hierarchyFilterValue, pageNavWindow, visualAtHierarchyLevel } from './interactions';
import type { InteractionIR } from './interactions';
import { CredentialWarningBanner } from './security/CredentialWarningBanner';
import { describeRuntimeError, fetchRuntime, isRuntimeAbortedError, runtimeEndpointFor } from './runtime/client';
import type { RuntimePayload } from './runtime/client';
import { refreshVisuals as refreshVisualQueries } from './runtime/query';
import type {
  InterpretedVisual,
  RenderPlan,
  VisualSpec,
} from './runtime/types';
import { renderPlan, displayTitle } from './visuals/base';
import { HierarchyNavigatorVisual } from './visuals/advanced/HierarchyNavigatorVisual';
import type { HierarchyNavigatorSelection } from './visuals/advanced/HierarchyNavigatorVisual';

// Re-exported for historical import sites (e.g. tests/e2e/web/runtime_e2e.test.tsx).
export type { RuntimePayload };
export { queryEndpointForRuntime, resultsFromDataset } from './runtime/query';

/** Ancho mÃ­nimo lÃ³gico reservado por botÃ³n de pÃ¡gina en la navegaciÃ³n. */
const PAGE_BUTTON_MIN_WIDTH = 128;
/** Respaldo del ancho disponible cuando el entorno no expone mediciÃ³n (SSR/jsdom). */
const DEFAULT_NAV_VIEWPORT_WIDTH = 1024;

export interface AppProps {
  initialPayload?: RuntimePayload | null;
  versionId?: string;
  endpoint?: string;
  authToken?: string;
  /**
   * Ancho lÃ³gico (px) disponible para la navegaciÃ³n de pÃ¡ginas; usado para
   * derivar el overflow. Omitido: se mide con ResizeObserver con respaldo en
   * `DEFAULT_NAV_VIEWPORT_WIDTH`.
   */
  navViewportWidth?: number;
}

interface GeometryBounds {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface PageVisual<T> {
  spec: VisualSpec;
  element: T;
}

function visualRect(spec: VisualSpec): GeometryBounds | null {
  const geometry = spec.geometry ?? {};
  const left = Number(geometry.x);
  const top = Number(geometry.y);
  const width = Number(geometry.width);
  const height = Number(geometry.height);
  if (![left, top, width, height].every(Number.isFinite) || width <= 0 || height <= 0) {return null;}
  return { height, left, top, width };
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) {return value.map(stableValue);}
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, nested]) => [key, stableValue(nested)]),
    );
  }
  return value;
}

function visualContentSignature(spec: VisualSpec): string {
  return JSON.stringify(stableValue({
    data_roles: spec.data_roles ?? {},
    kind: spec.kind,
    query: spec.query ?? null,
    title: spec.title ?? '',
  }));
}

/**
 * Suppress only technical compact replicas of a larger, semantically identical zone.
 * Equal-size repeats and visually distinct specs are preserved.
 */
export function deduplicateCompactReplicas<T>(visuals: PageVisual<T>[]): PageVisual<T>[] {
  const byName = new Map(visuals.map((visual) => [visual.spec.name, visual]));
  return visuals.filter((candidate) => {
    const replicaMatch = candidate.spec.name.match(/^(.*)::\d+$/);
    if (!replicaMatch) {return true;}
    const primary = byName.get(replicaMatch[1]);
    if (!primary || visualContentSignature(primary.spec) !== visualContentSignature(candidate.spec)) {
      return true;
    }
    const primaryRect = visualRect(primary.spec);
    const candidateRect = visualRect(candidate.spec);
    if (!primaryRect || !candidateRect) {return true;}
    const primaryArea = primaryRect.width * primaryRect.height;
    const candidateArea = candidateRect.width * candidateRect.height;
    return primaryArea < candidateArea * 4;
  });
}

/** Bounds of the authored zones on a page; null enables the safe grid fallback. */
export function geometryBounds(visuals: { spec: VisualSpec }[]): GeometryBounds | null {
  const rects = visuals.map(({ spec }) => visualRect(spec));
  if (rects.length === 0 || rects.some((rect) => rect === null)) {return null;}
  const valid = rects as GeometryBounds[];
  const left = Math.min(...valid.map((rect) => rect.left));
  const top = Math.min(...valid.map((rect) => rect.top));
  const right = Math.max(...valid.map((rect) => rect.left + rect.width));
  const bottom = Math.max(...valid.map((rect) => rect.top + rect.height));
  if (right <= left || bottom <= top) {return null;}
  return { height: bottom - top, left, top, width: right - left };
}

function canvasSize(visuals: { spec: VisualSpec }[]): React.CSSProperties {
  const bounds = geometryBounds(visuals);
  if (bounds) {
    return {
      aspectRatio: `${bounds.width} / ${bounds.height}`,
      display: 'block',
      position: 'relative',
      width: '100%',
    };
  }
  return {
    display: 'grid',
    gap: '16px',
    gridAutoRows: 'minmax(180px, auto)',
    gridTemplateColumns: 'repeat(12, minmax(0, 1fr))',
    width: '100%',
  };
}

function layoutRole(spec: VisualSpec): 'hero' | 'support' | 'compact' {
  const geometry = spec.geometry ?? {};
  const width = Number(geometry.width ?? 320);
  const height = Number(geometry.height ?? 180);
  if (width >= 600 && height >= 240) {return 'hero';}
  if (width >= 250 && height >= 180) {return 'support';}
  return 'compact';
}

function geometryStyle(
  visual: VisualSpec,
  visuals: { spec: VisualSpec }[],
): React.CSSProperties {
  const bounds = geometryBounds(visuals);
  const rect = visualRect(visual);
  if (bounds && rect) {
    const compactZone = rect.height < 72 || rect.width < 180;
    return {
      borderRadius: compactZone ? 4 : 8,
      height: `${(rect.height / bounds.height) * 100}%`,
      left: `${((rect.left - bounds.left) / bounds.width) * 100}%`,
      padding: compactZone ? 4 : 8,
      position: 'absolute',
      top: `${((rect.top - bounds.top) / bounds.height) * 100}%`,
      width: `${(rect.width / bounds.width) * 100}%`,
      zIndex: visual.kind === 'slicer' || visual.kind === 'text_box' ? 2 : 1,
    };
  }
  const role = layoutRole(visual);
  const supportVisuals = visuals.filter(({ spec }) => layoutRole(spec) === 'support');
  const supportIndex = supportVisuals.findIndex(({ spec }) => spec.name === visual.name);
  if (role === 'hero') {
    return { gridColumn: '5 / -1', gridRow: '1 / span 2', minHeight: 392 };
  }
  if (role === 'support') {
    return { gridColumn: '1 / 5', gridRow: `${supportIndex + 1}`, minHeight: 188 };
  }
  return { gridColumn: 'span 4', minHeight: 156 };
}

/** Build the executable interaction subset carried by the public RenderPlan. */
export function interactionIRFromPlan(plan: RenderPlan): InteractionIR {
  const pageIds = [...new Set(plan.visuals.map((visual) => visual.page || 'Overview'))];
  const visualPages = new Map(plan.visuals.map((visual) => [visual.name, visual.page || 'Overview']));
  const visualsByPage = new Map<string, VisualSpec[]>();
  const declaredDrillPaths = drillPathsFromMetadata(plan.metadata);
  for (const visual of plan.visuals) {
    const page = visual.page || 'Overview';
    const pageVisuals = visualsByPage.get(page) ?? [];
    pageVisuals.push(visual);
    visualsByPage.set(page, pageVisuals);
  }
  return {
    doc_id: String(plan.metadata?.source_name ?? ''),
    pages: pageIds.map((page_id) => {
      const grouped = new Map<string, { behavior: 'filter' | 'highlight'; targets: string[] }>();
      for (const interaction of plan.interactions ?? []) {
        if (visualPages.get(interaction.source) !== page_id) {continue;}
        if (interaction.kind !== 'filter' && interaction.kind !== 'highlight') {continue;}
        const current = grouped.get(interaction.source) ?? {
          behavior: interaction.kind,
          targets: [],
        };
        current.targets.push(interaction.target);
        grouped.set(interaction.source, current);
      }
      // JerarquÃ­as derivadas del payload: sÃ³lo visuales con >= 2 niveles reales.
      const drills = derivePageDrills(page_id, visualsByPage.get(page_id) ?? [], declaredDrillPaths);
      return {
        page_id,
        cross_filters: [...grouped.entries()].map(([source_visual_id, value], index) => ({
          rule_id: `${page_id}:render-plan:${index}`,
          source_visual_id,
          target_visual_ids: [...new Set(value.targets)],
          behavior: value.behavior,
        })),
        ...(drills.length > 0 ? { drills } : {}),
      };
    }),
    schema_version: INTERACTION_IR_SCHEMA_VERSION,
  };
}

type RuntimeDataState = 'loading' | 'ready' | 'refreshing' | 'error';

const DATA_STATE_ES: Record<RuntimeDataState, string> = {
  error: 'con error',
  loading: 'cargando',
  ready: 'listos',
  refreshing: 'actualizando',
};

export const App: React.FC<AppProps> = ({
  initialPayload = null,
  versionId,
  endpoint,
  authToken,
  navViewportWidth,
}) => {
  const [payload, setPayload] = useState<RuntimePayload | null>(initialPayload);
  const [dataState, setDataState] = useState<RuntimeDataState>(initialPayload ? 'ready' : 'loading');
  const [error, setError] = useState('');
  const [activePage, setActivePage] = useState('');
  const [filterError, setFilterError] = useState('');
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [, setInteractionRevision] = useState(0);
  /** Nivel de drill vigente por visual, sincronizado con el InteractionEngine. */
  const [drillLevels, setDrillLevels] = useState<Record<string, number>>({});
  const [parameterValues, setParameterValues] = useState<Record<string, unknown>>({});
  const [navigatorSelections, setNavigatorSelections] = useState<Record<string, string>>({});
  const [navigatorMarkSelections, setNavigatorMarkSelections] = useState<Record<string, { fieldRef: string; value: string | number }>>({});
  const [measuredNavWidth, setMeasuredNavWidth] = useState<number | null>(null);
  const filterRun = useRef(0);
  const filterController = useRef<AbortController | null>(null);
  const navRef = useRef<HTMLElement | null>(null);
  const runtimeEndpoint = endpoint ?? (versionId ? runtimeEndpointFor(versionId) : undefined);
  const interactionEngine = useMemo(
    () => payload ? new InteractionEngine(interactionIRFromPlan(payload.plan)) : null,
    [payload?.plan],
  );

  // Mide el ancho real de la barra de pÃ¡ginas cuando el entorno lo permite;
  // sin mediciÃ³n (jsdom/SSR) cae al ancho lÃ³gico por defecto.
  useEffect(() => {
    if (navViewportWidth !== undefined || typeof ResizeObserver === 'undefined') {return;}
    const node = navRef.current;
    if (!node) {return;}
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (width > 0) {setMeasuredNavWidth(width);}
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [navViewportWidth, payload?.plan]);

  const refreshVisuals = useCallback(async (
    candidates: VisualSpec[],
    runtimeParameters: Record<string, unknown> = parameterValues,
  ) => {
    if (!payload || !runtimeEndpoint || !interactionEngine) {return;}
    const parameterizedCandidates = candidates.map((visual) => visual.query ? ({
      ...visual,
      query: {
        ...visual.query,
        parameters: {
          ...((visual.query.parameters && typeof visual.query.parameters === 'object')
            ? visual.query.parameters as Record<string, unknown>
            : {}),
          ...runtimeParameters,
        },
      },
    }) : visual);
    filterController.current?.abort();
    const controller = new AbortController();
    filterController.current = controller;
    const run = ++filterRun.current;
    setDataState('refreshing');
    try {
      const updates = await refreshVisualQueries({
        authToken,
        predicatesFor: (visualName) => interactionEngine.generateAllowlistedQueries(visualName)
          // El predicado de drill del IR iguala el campo a su propio nombre y no
          // es ejecutable como filtro; hasta definir el contrato de consulta de
          // granularidad se excluye para no vaciar los visuales.
          // ponytail: techo deliberado â€” upgrade: predicado de drill real en QuerySpec.
          .filter((predicate) => predicate.source !== 'drill'),
        runtimeEndpoint,
        signal: controller.signal,
        visuals: parameterizedCandidates,
      });
      if (run !== filterRun.current) {return;}
      setPayload((current) => current ? {
        ...current,
        results: {
          ...current.results,
          visuals: { ...current.results.visuals, ...Object.fromEntries(updates) },
        },
      } : current);
      setFilterError('');
      setDataState('ready');
    } catch (error: unknown) {
      if (controller.signal.aborted || run !== filterRun.current) {return;}
      setFilterError(error instanceof Error ? error.message : 'filter query failed');
      setDataState('error');
    }
  }, [authToken, interactionEngine, parameterValues, payload, runtimeEndpoint]);

  const handleFilterChange = useCallback(async (sourceVisual: InterpretedVisual, value: string) => {
    if (!payload || !interactionEngine) {return;}
    const targetRole = sourceVisual.roles.filter_target;
    if (!targetRole?.ref.startsWith('field:')) {return;}
    const fieldName = targetRole.ref.slice('field:'.length);
    const sourceSpec = payload.plan.visuals.find((visual) => visual.name === sourceVisual.name);
    const sourceDatasource = String(sourceSpec?.query?.from_datasource ?? '');
    if (!sourceDatasource) {return;}

    const explicitTargets = new Set(
      (payload.plan.interactions ?? [])
        .filter((rule) => rule.kind === 'filter' && rule.source === sourceVisual.name)
        .map((rule) => rule.target),
    );
    const candidates = payload.plan.visuals.filter((visual) => (
      visual.query
      && String(visual.query.from_datasource ?? '') === sourceDatasource
      && (explicitTargets.size === 0 || explicitTargets.has(visual.name))
    ));
    if (value) {
      interactionEngine.applyFilter({
        filter_id: sourceVisual.name,
        is_interactive_slicer: true,
        name: sourceVisual.title,
        operator: 'equals',
        scope: 'page',
        target_field: fieldName,
        target_visual_ids: candidates.map((visual) => visual.name),
        values: [value],
      });
    } else {
      interactionEngine.removeFilter(sourceVisual.name);
    }
    setFilterValues((current) => ({ ...current, [sourceVisual.name]: value }));
    await refreshVisuals(candidates);
  }, [interactionEngine, payload, refreshVisuals]);

  const handleDataSelect = useCallback(async (
    sourceVisual: InterpretedVisual,
    fieldRef: string,
    value: string | number,
    rowIndex?: number,
  ) => {
    if (!payload || !interactionEngine || !fieldRef.startsWith('field:')) {return;}
    const fieldName = fieldRef.slice('field:'.length);
    const sourceSpec = payload.plan.visuals.find((visual) => visual.name === sourceVisual.name);
    if (!sourceSpec) {return;}
    const listenerNavigator = hierarchyNavigationContractsFromMetadata(payload.plan.metadata).find((navigator) => {
      if (!navigator.sourceSelection.listen) {return false;}
      return sourceSpec.name === navigator.sourceVisualId;
    });
    if (listenerNavigator) {
      const sourceResults = payload.results.visuals[sourceSpec.name] ?? {};
      const selectedColumn = sourceResults[fieldRef];
      const inferredIndex = rowIndex ?? (Array.isArray(selectedColumn)
        ? selectedColumn.findIndex((item) => String(item) === String(value))
        : 0);
      const ids = sourceResults[`field:${listenerNavigator.data.idField}`];
      const labels = sourceResults[`field:${listenerNavigator.data.labelField}`];
      const id = Array.isArray(ids) ? ids[inferredIndex] : ids;
      const label = Array.isArray(labels) ? labels[inferredIndex] : labels;
      if (id !== null && id !== undefined) {
        const humanLabel = label === null || label === undefined ? String(id) : String(label);
        handleNavigatorSelect(listenerNavigator, { id: String(id), label: humanLabel, path: [humanLabel] });
        return;
      }
    }
    const sourceDatasource = String(sourceSpec.query?.from_datasource ?? '');
    if (!sourceDatasource) {return;}
    const explicitTargets = new Set(
      (payload.plan.interactions ?? [])
        .filter((rule) => rule.kind === 'filter' && rule.source === sourceVisual.name)
        .map((rule) => rule.target),
    );
    if (explicitTargets.size === 0) {return;}
    const candidates = payload.plan.visuals.filter((visual) => (
      visual.query
      && String(visual.query.from_datasource ?? '') === sourceDatasource
      && explicitTargets.has(visual.name)
    ));
    interactionEngine.select(sourceVisual.name, fieldName, [value]);
    setInteractionRevision((value) => value + 1);
    await refreshVisuals(candidates);
  }, [interactionEngine, payload, refreshVisuals]);

  useEffect(() => () => filterController.current?.abort(), []);

  useEffect(() => {
    if (initialPayload) {
      setPayload(initialPayload);
      setDataState('ready');
      return;
    }
    if (!endpoint && !versionId) {
      setError('version id is required');
      setDataState('error');
      return;
    }

    setDataState('loading');
    const controller = new AbortController();
    void fetchRuntime({
      authToken,
      endpoint,
      signal: controller.signal,
      versionId,
    })
      .then((data) => {
        setPayload(data);
        setError('');
        setDataState('ready');
      })
      .catch((error: unknown) => {
        if (isRuntimeAbortedError(error)) {return;}
        setError(describeRuntimeError(error) || 'DataVIZ runtime unavailable');
        setDataState('error');
      })
;

    return () => controller.abort();
  }, [endpoint, initialPayload, versionId, authToken]);

  const { rendered, renderError } = useMemo(() => {
    if (!payload) {return { rendered: [], renderError: '' };}
    try {
      const dataSelectionSources = new Set(
        (payload.plan.interactions ?? [])
          .filter((rule) => rule.kind === 'filter' || rule.kind === 'highlight')
          .map((rule) => rule.source),
      );
      for (const navigator of hierarchyNavigationContractsFromMetadata(payload.plan.metadata)) {
        if (!navigator.sourceSelection.listen) {continue;}
        const source = payload.plan.visuals.find((visual) => visual.name === navigator.sourceVisualId);
        if (source) {dataSelectionSources.add(source.name);}
      }
      return { renderError: '', rendered: renderPlan(payload.plan, payload.results, (visual, value) => {
        void handleFilterChange(visual, value);
      }, filterValues, (visual, fieldRef, value, rowIndex) => {
        void handleDataSelect(visual, fieldRef, value, rowIndex);
      }, (visual) => dataSelectionSources.has(visual.name), (visual) => navigatorMarkSelections[visual.name]) };
    } catch (error: unknown) {
      return { rendered: [], renderError: String(error instanceof Error ? error.message : error) };
    }
  }, [filterValues, handleDataSelect, handleFilterChange, navigatorMarkSelections, payload]);

  const activeError = error || renderError;
  if (activeError) {return <main role="alert" className="dv-runtime-error">Runtime de DataVIZ no disponible: {activeError}</main>;}
  if (dataState === 'loading' || !payload) {return <main aria-busy="true" className="dv-runtime-loading">Cargando DataVIZ…</main>;}

  if (!payload.plan.visuals || payload.plan.visuals.length === 0) {
    return (
      <main className="dv-runtime-empty">
        <p>Sin visuales.</p>
      </main>
    );
  }

  const pages = new Map<string, PageVisual<React.ReactElement>[]>();
  payload.plan.visuals.forEach((spec, index) => {
    const page = spec.page || 'Overview';
    const items = pages.get(page) ?? [];
    items.push({ element: rendered[index], spec });
    pages.set(page, items);
  });
  const pageNames = [...pages.keys()];
  const selectedPage = pageNames.includes(activePage) ? activePage : pageNames[0];
  const reportContext = String(
    (payload.plan.metadata?.source as Record<string, unknown> | undefined)?.artifact_name
    || 'DataVIZ report',
  );
  const interactionState = interactionEngine?.getState();
  const activeFilterLabels = Object.values(interactionState?.activeFilters ?? {})
    .map((filter) => `${filter.target_field} ${filter.operator} ${filter.values ?? []}`);
  for (const selection of Object.values(interactionState?.activeSelections ?? {})) {
    if (selection.selected_values.length) {activeFilterLabels.push(`${selection.target_field} = ${selection.selected_values}`);}
  }
  const activeFilterCount = activeFilterLabels.length;
  const selectedPageVisuals = pages.get(selectedPage) ?? [];
  const selectedVisuals = deduplicateCompactReplicas(selectedPageVisuals);
  const selectedNavigators = hierarchyNavigationContractsFromMetadata(payload.plan.metadata)
    .filter((navigator) => navigator.pageId === selectedPage);
  const navigatorVisuals: PageVisual<React.ReactElement>[] = selectedNavigators.flatMap((navigator) => {
    const sourceSpec = payload.plan.visuals.find((visual) => visual.name === navigator.sourceVisualId);
    if (!sourceSpec) {return [];}
    const sourceResults = payload.results.visuals[sourceSpec.name] ?? {};
    const spec: VisualSpec = {
      geometry: navigator.geometry,
      kind: 'hierarchy_navigator',
      name: `${navigator.pageId}::hierarchy-navigator::${navigator.navigationId}`,
    };
    return [{
      element: (
        <HierarchyNavigatorVisual
          navigator={navigator}
          onSelect={(selection) => handleNavigatorSelect(navigator, selection)}
          results={sourceResults}
          selectedId={navigatorSelections[navigator.navigationId]}
          sourceRoles={sourceSpec.data_roles ?? {}}
        />
      ),
      spec,
    }];
  });
  const layoutVisuals = [...selectedVisuals, ...navigatorVisuals];
  const usesGeometryLayout = geometryBounds(layoutVisuals) !== null;
  const hasActiveFilters = activeFilterCount > 0;

  // La pÃ¡gina puede adquirir filtros si tiene slicers de campo o interacciones
  // de filtro propias; si no, el botÃ³n serÃ­a un control que jamÃ¡s puede actuar.
  const pageByVisual = new Map(
    payload.plan.visuals.map((visual) => [visual.name, visual.page || 'Overview']),
  );
  const selectedPageFilterCapable = (
    selectedVisuals.some(({ spec }) => spec.kind === 'slicer'
      && Object.values(spec.data_roles ?? {}).some((ref) => String(ref).startsWith('field:')))
    || (payload.plan.interactions ?? []).some((rule) => (
      rule.kind === 'filter' && pageByVisual.get(rule.source) === selectedPage
    ))
  );
  const showClearFilters = hasActiveFilters || selectedPageFilterCapable;
  const declaredDrillPaths = drillPathsFromMetadata(payload.plan.metadata);

  // Controles de drill sÃ³lo para visuales mostrados con jerarquÃ­a real.
  const selectedDrills = selectedVisuals
    .map((visual) => ({ levels: hierarchyLevelsForVisual(visual.spec, declaredDrillPaths), visual }))
    .filter((entry) => entry.levels.length >= 2);

  const availableNavWidth = navViewportWidth ?? measuredNavWidth ?? DEFAULT_NAV_VIEWPORT_WIDTH;
  const navVisibleCount = Math.max(1, Math.floor(availableNavWidth / PAGE_BUTTON_MIN_WIDTH));
  const navWindow = pageNavWindow(pageNames, selectedPage, navVisibleCount);

  const navigateToPage = (page: string) => {
    interactionEngine?.navigate({
      action_id: `page-nav:${page}`,
      kind: 'page_nav',
      target_page_id: page,
      trigger_source_id: 'page-nav',
    });
    setActivePage(page);
  };

  const clearFilters = () => {
    interactionEngine?.navigate({
      action_id: `clear-filters:${selectedPage}`,
      kind: 'clear_filters',
      trigger_source_id: 'clear-filters',
    });
    setFilterValues({});
    setInteractionRevision((value) => value + 1);
    void refreshVisuals(selectedVisuals.map(({ spec }) => spec).filter((spec) => spec.query));
  };

  function handleNavigatorSelect(
    navigator: ReturnType<typeof hierarchyNavigationContractsFromMetadata>[number],
    selection: HierarchyNavigatorSelection,
  ) {
    if (!payload) {return;}
    const updates: Record<string, unknown> = {};
    if (navigator.mode === 'recursive') {
      if (navigator.selectionParameters.id) {updates[navigator.selectionParameters.id] = selection.id;}
      if (navigator.selectionParameters.label) {updates[navigator.selectionParameters.label] = selection.label;}
    } else {
      navigator.selectionParameters.path.forEach((parameter, index) => {
        if (parameter) {updates[parameter] = selection.path[index] ?? 'Null';}
      });
      if (navigator.selectionParameters.depth) {updates[navigator.selectionParameters.depth] = selection.path.length;}
      if (navigator.selectionParameters.id) {updates[navigator.selectionParameters.id] = selection.id;}
      if (navigator.selectionParameters.label) {updates[navigator.selectionParameters.label] = selection.label;}
    }
    const nextParameters = { ...parameterValues, ...updates };
    setParameterValues(nextParameters);
    setNavigatorSelections((current) => ({ ...current, [navigator.navigationId]: selection.id }));
    const sourceSpec = payload.plan.visuals.find((visual) => visual.name === navigator.sourceVisualId);
    const sourceDatasource = String(sourceSpec?.query?.from_datasource ?? '');
    const candidates = payload.plan.visuals.filter((visual) => (
      visual.query
      && visual.page === navigator.pageId
      && (!sourceDatasource || String(visual.query.from_datasource ?? '') === sourceDatasource)
    ));
    if (navigator.sourceSelection.emitField && sourceSpec) {
      setNavigatorMarkSelections((current) => ({
        ...current,
        [sourceSpec.name]: {
          fieldRef: `field:${navigator.sourceSelection.emitField}`,
          value: selection.id,
        },
      }));
    }
    if (navigator.filter && interactionEngine && sourceSpec) {
      const filterValue = hierarchyFilterValue(
        navigator,
        selection,
        payload.results.visuals[sourceSpec.name] ?? {},
      );
      if (filterValue !== undefined && filterValue !== null) {
        interactionEngine.applyFilter({
          filter_id: `hierarchy-navigator:${navigator.navigationId}`,
          is_interactive_slicer: true,
          name: 'Hierarchy Navigator',
          operator: 'equals',
          scope: 'page',
          target_field: navigator.filter.targetField,
          target_visual_ids: candidates.map((visual) => visual.name),
          values: [filterValue],
        });
      }
    }
    setPayload((current) => {
      if (!current) {return current;}
      const changedResults = { ...current.results.visuals };
      for (const visual of current.plan.visuals) {
        const target = visual.data_roles?.filter_target;
        if (!target?.startsWith('parameter:')) {continue;}
        const parameter = target.slice('parameter:'.length);
        if (!(parameter in updates)) {continue;}
        changedResults[visual.name] = {
          ...changedResults[visual.name],
          [target]: updates[parameter] as string | number | boolean | null,
        };
      }
      return { ...current, results: { ...current.results, visuals: changedResults } };
    });
    void refreshVisuals(candidates, nextParameters);
  }

  const handleDrill = (visualName: string, direction: 'down' | 'up') => {
    if (!interactionEngine) {return;}
    const state = direction === 'down'
      ? interactionEngine.drillDown(visualName)
      : interactionEngine.drillUp(visualName);
    setDrillLevels(Object.fromEntries(
      Object.entries(state.activeDrills).map(([visualId, drill]) => [visualId, drill.current_level_index]),
    ));
    const drill = state.activeDrills[visualName];
    const sourceVisual = payload.plan.visuals.find((visual) => visual.name === visualName);
    if (drill && sourceVisual?.query) {
      const drilledVisual = visualAtHierarchyLevel(
        sourceVisual,
        drill.hierarchy_levels,
        drill.current_level_index,
      );
      void refreshVisuals([drilledVisual]);
    }
  };

  return (
    <main
      className="dv-runtime"
    >
      {payload.credential_scan && <CredentialWarningBanner report={payload.credential_scan} />}
      <p className="dv-report-status" role="status">
        <strong>{reportContext}</strong> | Datos {DATA_STATE_ES[dataState]} | {selectedPage} | Filtros {activeFilterCount}
        {activeFilterLabels.map((label) => <span key={label}>{label}</span>)}
      </p>
      {filterError && <p className="dv-filter-error" role="alert">Filtro no aplicado: {filterError}</p>}
      {showClearFilters && (
        <button
          className="dv-clear-filters"
          disabled={!hasActiveFilters}
          onClick={clearFilters}
          title={hasActiveFilters ? undefined : 'Sin filtros activos en esta página'}
          type="button"
        >
          Limpiar filtros
        </button>
      )}
      {pageNames.length > 1 && (
        <nav
          aria-label="Páginas del reporte"
          className="dv-page-nav"
          ref={navRef}
        >
          {navWindow.hasOverflow && (
            <button
              aria-label="Ir a la página anterior"
              disabled={!navWindow.canPrev}
              title={navWindow.canPrev ? undefined : 'Ya estás en la primera página'}
              onClick={() => {
                const target = pageNames[pageNames.indexOf(selectedPage) - 1];
                if (target) {navigateToPage(target);}
              }}
              type="button"
            >
              &lsaquo;
            </button>
          )}
          {navWindow.visible.map((page) => (
            <button
              aria-current={page === selectedPage ? 'page' : undefined}
              key={page}
              onClick={() => navigateToPage(page)}
              type="button"
            >
              {page}
            </button>
          ))}
          {navWindow.hasOverflow && (
            <>
              <button
                aria-label="Ir a la página siguiente"
                disabled={!navWindow.canNext}
                title={navWindow.canNext ? undefined : 'Ya estás en la última página'}
                onClick={() => {
                  const target = pageNames[pageNames.indexOf(selectedPage) + 1];
                  if (target) {navigateToPage(target);}
                }}
                type="button"
              >
                &rsaquo;
              </button>
              <span aria-live="polite" className="dv-page-nav-status">
                Página {navWindow.position} de {navWindow.total}
              </span>
            </>
          )}
        </nav>
      )}
      {selectedDrills.length > 0 && (
        <div aria-label="Navegación de jerarquía" className="dv-drill-bar" role="group">
          {selectedDrills.map(({ visual, levels }) => {
            const levelIndex = Math.min(drillLevels[visual.spec.name] ?? 0, levels.length - 1);
            const drillTitle = displayTitle(visual.spec.title ?? '', visual.spec.name);
            const canDown = levelIndex < levels.length - 1;
            const canUp = levelIndex > 0;
            return (
              <div
                className="dv-drill-control"
                key={visual.spec.name}
              >
                <span className="dv-drill-title">{drillTitle}</span>
                <button
                  aria-label={`Subir nivel ${drillTitle}`}
                  disabled={!canUp}
                  onClick={() => handleDrill(visual.spec.name, 'up')}
                  title={canUp ? undefined : 'Ya estás en el nivel superior'}
                  type="button"
                >
                  Subir nivel
                </button>
                <span aria-live="polite" className="dv-drill-level">
                  Nivel actual: {levels[levelIndex]}
                </span>
                <button
                  aria-label={`Bajar nivel ${drillTitle}`}
                  disabled={!canDown}
                  onClick={() => handleDrill(visual.spec.name, 'down')}
                  title={canDown ? undefined : 'Ya estás en el nivel más profundo'}
                  type="button"
                >
                  Bajar nivel
                </button>
              </div>
            );
          })}
        </div>
      )}
      <section className="dv-page" data-page={selectedPage}>
          <h1>{selectedPage}</h1>
          <div className="dv-canvas-viewport">
            <div
              className={`dv-canvas ${usesGeometryLayout ? 'dv-canvas--geometry' : 'dv-canvas--grid'}`}
              style={canvasSize(layoutVisuals)}
            >
            {selectedVisuals.map(({ spec, element }) => (
              <article
                className={`dv-visual dv-visual--${layoutRole(spec)}`}
                data-visual-kind={spec.kind}
                key={spec.name}
                style={geometryStyle(spec, layoutVisuals)}
              >
                {element}
              </article>
            ))}
            {navigatorVisuals.map(({ spec, element }) => (
              <article
                className="dv-visual dv-visual--hierarchy-navigator"
                data-visual-kind={spec.kind}
                key={spec.name}
                style={geometryStyle(spec, layoutVisuals)}
              >
                {element}
              </article>
            ))}
            </div>
          </div>
      </section>
    </main>
  );
};

export default App;
