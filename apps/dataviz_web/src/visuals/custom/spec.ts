/**
 * Contrato declarativo de custom visuals (capas cerradas) y costo estructural.
 *
 * El contrato viaja serializado en `properties.custom_visual_spec` del IR de
 * presentación y se expone como `custom_spec` en editor y runtime. Es un módulo
 * puro sin dependencias: el mismo parser valida en la frontera canónica
 * (authoring/types), en el intérprete del plan y en los estimadores de costo
 * del agente local y remoto. Fail-closed: cualquier clave extra, tipo
 * incorrecto o color no hex rechaza la spec completa.
 */

/** Versión exacta del contrato de custom visuals (SemVer, coincidencia exacta). */
export const CUSTOM_VISUAL_SPEC_SCHEMA_VERSION = '1.0.0';

/** Máximo de puntos declarables por visual. */
export const CUSTOM_MAX_DATA_POINTS = 50_000;
/** Máximo de capas por visual. */
export const CUSTOM_MAX_LAYERS = 6;
/** Presupuesto de unidades de render por visual. */
export const CUSTOM_RENDER_BUDGET_UNITS = 5000;
/** Presupuesto de celdas de consulta por visual. */
export const CUSTOM_QUERY_CELL_BUDGET = 20_000;

/** Marcas cerradas que una capa puede dibujar. */
export const CUSTOM_LAYER_MARKS = ['bar', 'line', 'area', 'point'] as const;
export type CustomLayerMark = (typeof CUSTOM_LAYER_MARKS)[number];

function isCustomLayerMark(value: unknown): value is CustomLayerMark {
  return typeof value === 'string'
    && (CUSTOM_LAYER_MARKS as readonly string[]).includes(value);
}

export interface CustomVisualLayer {
  mark: CustomLayerMark;
  /** Nombre del rol del visual usado como eje X compartido (p. ej. `x_axis`). */
  x_role: string;
  /** Nombre del rol del visual usado como valor (p. ej. `y_axis`, `y_axis_2`). */
  y_role: string;
  /** Color de la capa en formato `#RRGGBB`. */
  color?: string;
  /** Dibuja puntos sobre líneas/áreas (sólo afecta a `line`/`area`). */
  show_points?: boolean;
}

export interface CustomVisualSpec {
  schema_version: typeof CUSTOM_VISUAL_SPEC_SCHEMA_VERSION;
  /** Límite de filas que el visual puede dibujar (1..50000). */
  max_data_points: number;
  /** 1..6 capas cerradas. */
  layers: CustomVisualLayer[];
}

const HEX_COLOR = /^#[0-9a-fA-F]{6}$/;

/**
 * Vocabulario canónico de roles de binding (espejo exacto de `FieldRole` en
 * `core/contracts/presentation_ir.py` y de `presentation.field_roles` del
 * CANONICAL_CONTRACT). Este módulo es hoja y no puede importar el contrato de
 * authoring sin crear un ciclo, por eso el vocabulario se embebe aquí.
 */
export const CANONICAL_LAYER_ROLES = [
  'color',
  'column',
  'comparison_metric',
  'filter_target',
  'label',
  'row',
  'series',
  'size',
  'target_metric',
  'tooltip',
  'value',
  'x_axis',
  'y_axis',
] as const;

export type CanonicalLayerRole = (typeof CANONICAL_LAYER_ROLES)[number];

function isCanonicalRole(value: string): value is CanonicalLayerRole {
  return (CANONICAL_LAYER_ROLES as readonly string[]).includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireNonEmptyString(value: unknown, label: string): string {
  if (typeof value !== 'string' || value.trim() === '') {
    throw new Error(`${label} debe ser un string no vacío, recibido ${JSON.stringify(value)}`);
  }
  return value;
}

/**
 * Parser/validator fail-closed del contrato de custom visuals.
 * Rechaza claves extra, tipos incorrectos, marcas fuera del enum,
 * colores no hex y límites fuera de rango.
 */
export function parseCustomVisualSpec(payload: unknown): CustomVisualSpec {
  if (!isRecord(payload)) {
    throw new Error('custom_visual_spec debe ser un objeto');
  }
  for (const key of Object.keys(payload)) {
    if (key !== 'schema_version' && key !== 'max_data_points' && key !== 'layers') {
      throw new Error(`clave no permitida en custom_visual_spec: "${key}"`);
    }
  }
  if (payload.schema_version !== CUSTOM_VISUAL_SPEC_SCHEMA_VERSION) {
    throw new Error(
      `custom_visual_spec.schema_version debe ser "${CUSTOM_VISUAL_SPEC_SCHEMA_VERSION}", recibido ${JSON.stringify(payload.schema_version)}`,
    );
  }
  const maxDataPoints = payload.max_data_points;
  if (
    typeof maxDataPoints !== 'number' || !Number.isInteger(maxDataPoints)
    || maxDataPoints < 1 || maxDataPoints > CUSTOM_MAX_DATA_POINTS
  ) {
    throw new Error(
      `custom_visual_spec.max_data_points debe ser un entero entre 1 y ${CUSTOM_MAX_DATA_POINTS}`,
    );
  }
  const rawLayers = payload.layers;
  if (!Array.isArray(rawLayers) || rawLayers.length < 1 || rawLayers.length > CUSTOM_MAX_LAYERS) {
    throw new Error(
      `custom_visual_spec.layers debe ser un arreglo de 1 a ${CUSTOM_MAX_LAYERS} capas`,
    );
  }
  return {
    layers: rawLayers.map((layer, index) => parseCustomVisualLayer(layer, index)),
    max_data_points: maxDataPoints,
    schema_version: CUSTOM_VISUAL_SPEC_SCHEMA_VERSION,
  };
}

function parseCustomVisualLayer(payload: unknown, index: number): CustomVisualLayer {
  if (!isRecord(payload)) {
    throw new Error(`custom_visual_spec.layers[${index}] debe ser un objeto`);
  }
  for (const key of Object.keys(payload)) {
    if (key !== 'mark' && key !== 'x_role' && key !== 'y_role' && key !== 'color' && key !== 'show_points') {
      throw new Error(`clave no permitida en custom_visual_spec.layers[${index}]: "${key}"`);
    }
  }
  const {mark} = payload;
  if (!isCustomLayerMark(mark)) {
    throw new Error(
      `custom_visual_spec.layers[${index}].mark debe ser una de [${CUSTOM_LAYER_MARKS.join(', ')}], recibido ${JSON.stringify(mark)}`,
    );
  }
  const xRole = requireNonEmptyString(payload.x_role, `custom_visual_spec.layers[${index}].x_role`);
  const yRole = requireNonEmptyString(payload.y_role, `custom_visual_spec.layers[${index}].y_role`);
  for (const [key, role] of [['x_role', xRole], ['y_role', yRole]] as const) {
    if (!isCanonicalRole(role)) {
      throw new Error(
        `custom_visual_spec.layers[${index}].${key} "${role}" no es un rol canónico de binding`,
      );
    }
  }
  // Igual que el parser Python: `null` se trata como ausente (get() → None).
  if (payload.color !== undefined && payload.color !== null
    && (typeof payload.color !== 'string' || !HEX_COLOR.test(payload.color))) {
    throw new Error(
      `custom_visual_spec.layers[${index}].color debe ser un color hex #RRGGBB, recibido ${JSON.stringify(payload.color)}`,
    );
  }
  if (payload.show_points !== undefined && payload.show_points !== null
    && typeof payload.show_points !== 'boolean') {
    throw new Error(`custom_visual_spec.layers[${index}].show_points debe ser boolean`);
  }
  const layer: CustomVisualLayer = { mark, x_role: xRole, y_role: yRole };
  if (payload.color !== undefined && payload.color !== null) {layer.color = payload.color;}
  if (payload.show_points !== undefined && payload.show_points !== null) {layer.show_points = payload.show_points;}
  return layer;
}

/** Roles únicos referenciados por las capas (dominio del costo de consulta). */
export function uniqueRolesForSpec(spec: CustomVisualSpec): string[] {
  return [...new Set(spec.layers.flatMap((layer) => [layer.x_role, layer.y_role]))];
}

/** Unidades de render que aporta una capa por punto de datos. */
export function renderUnitsPerRow(layer: CustomVisualLayer): number {
  const pointsExtra = (layer.mark === 'line' || layer.mark === 'area') && layer.show_points ? 1 : 0;
  return 1 + pointsExtra;
}

export interface CustomCostReport {
  /** `structural`: desde max_data_points declarado; `actual`: desde filas reales. */
  basis: 'structural' | 'actual';
  data_points: number;
  unique_roles: number;
  query_cells: number;
  render_units: number;
  within_query_budget: boolean;
  within_render_budget: boolean;
  /** Sólo `actual`: los datos superaban max_data_points y se truncaron. */
  truncated: boolean;
  warnings: string[];
}

function buildReport(
  basis: 'structural' | 'actual',
  dataPoints: number,
  uniqueRoles: number,
  queryCells: number,
  renderUnits: number,
  truncated: boolean,
): CustomCostReport {
  const withinQuery = queryCells <= CUSTOM_QUERY_CELL_BUDGET;
  const withinRender = renderUnits <= CUSTOM_RENDER_BUDGET_UNITS;
  const warnings: string[] = [];
  if (basis === 'structural') {
    warnings.push(
      `Costo estructural: hasta ${renderUnits} unidades de render (presupuesto ${CUSTOM_RENDER_BUDGET_UNITS}) `
      + `y ${queryCells} celdas de consulta (presupuesto ${CUSTOM_QUERY_CELL_BUDGET}) `
      + `para ${dataPoints} puntos declarados en ${uniqueRoles} roles.`,
    );
  }
  if (!withinRender) {
    warnings.push(
      `Excede el presupuesto de render: ${renderUnits} unidades sobre un máximo de ${CUSTOM_RENDER_BUDGET_UNITS}.`,
    );
  }
  if (!withinQuery) {
    warnings.push(
      `Excede el presupuesto de celdas de consulta: ${queryCells} sobre un máximo de ${CUSTOM_QUERY_CELL_BUDGET}.`,
    );
  }
  if (truncated) {
    warnings.push('Los datos se truncaron al límite de puntos declarado (muestreo por límite).');
  }
  return {
    basis,
    data_points: dataPoints,
    query_cells: queryCells,
    render_units: renderUnits,
    truncated,
    unique_roles: uniqueRoles,
    warnings,
    within_query_budget: withinQuery,
    within_render_budget: withinRender,
  };
}

/** Costo con el máximo declarado (peor caso, para propuestas y validación). */
export function estimateStructuralCost(spec: CustomVisualSpec): CustomCostReport {
  const uniqueRoles = uniqueRolesForSpec(spec).length;
  const dataPoints = spec.max_data_points;
  const renderUnits = spec.layers.reduce((sum, layer) => sum + renderUnitsPerRow(layer), 0) * dataPoints;
  return buildReport(
    'structural',
    dataPoints,
    uniqueRoles,
    dataPoints * uniqueRoles,
    renderUnits,
    false,
  );
}

/**
 * Costo con datos reales: `dataPoints` son las filas efectivamente dibujadas
 * (ya limitadas por `max_data_points`); `sourceCount` permite declarar el
 * truncamiento cuando la fuente traía más filas que el límite.
 */
export function estimateActualCost(
  spec: CustomVisualSpec,
  dataPoints: number,
  sourceCount?: number,
): CustomCostReport {
  const uniqueRoles = uniqueRolesForSpec(spec).length;
  const points = Math.max(0, Math.floor(dataPoints));
  const renderUnits = spec.layers.reduce((sum, layer) => sum + renderUnitsPerRow(layer), 0) * points;
  const truncated = sourceCount !== undefined && sourceCount > points;
  return buildReport(
    'actual',
    points,
    uniqueRoles,
    points * uniqueRoles,
    renderUnits,
    truncated,
  );
}

/**
 * Vista mínima de una operación de visual suficiente para calcular costo sin
 * acoplar este módulo a los contratos de authoring.
 */
export interface CustomVisualOpLike {
  kind: string;
  payload?: { kind?: string; title?: string; custom_spec?: unknown } | undefined;
}

/**
 * Warnings de costo estructural para cada custom visual de un lote de
 * operaciones (agente local y cliente remoto). Las specs inválidas se ignoran:
 * nunca se confía en la entrada para describir su propio costo.
 */
export function customVisualOpsCostWarnings(visualOps: readonly CustomVisualOpLike[]): string[] {
  const warnings: string[] = [];
  for (const op of visualOps) {
    if (op.kind === 'remove' || op.payload?.kind !== 'custom_visual') {continue;}
    if (op.payload.custom_spec === undefined || op.payload.custom_spec === null) {continue;}
    let spec: CustomVisualSpec;
    try {
      spec = parseCustomVisualSpec(op.payload.custom_spec);
    } catch {
      continue;
    }
    const label = op.payload.title?.trim() || 'custom_visual';
    warnings.push(`«${label}»: ${estimateStructuralCost(spec).warnings[0]}`, ...budgetExceededWarnings(spec));
  }
  return warnings;
}

/** True cuando alguna operación custom excede el presupuesto estructural. */
export function customVisualOpsRequireConfirmation(
  visualOps: readonly CustomVisualOpLike[],
): boolean {
  return visualOps.some((op) => {
    if (op.kind === 'remove' || op.payload?.kind !== 'custom_visual') {return false;}
    try {
      const report = estimateStructuralCost(parseCustomVisualSpec(op.payload.custom_spec));
      return !report.within_query_budget || !report.within_render_budget;
    } catch {
      return false;
    }
  });
}

/** Sólo los warnings de presupuesto excedido de una spec (sin el resumen). */
export function budgetExceededWarnings(spec: CustomVisualSpec): string[] {
  return estimateStructuralCost(spec).warnings.filter((warning) => warning.startsWith('Excede'));
}

/**
 * Contrasta el `cost_report` declarado por un modelo remoto contra el cálculo
 * local del mismo lote de operaciones. Acepta el formato plano
 * (`render_units`/`query_cells`) o el reporte durable del servicio
 * (`totals.estimated_render_units`/`totals.estimated_query_cells`). Devuelve
 * `null` cuando coincide (puede ignorarse) o cuando el modelo no envió nada;
 * si difiere, devuelve el warning que explica que se usa el cálculo local.
 */
export function compareRemoteCostReport(
  costReport: unknown,
  visualOps: readonly CustomVisualOpLike[],
): string | null {
  if (!isRecord(costReport)) {return null;}
  let renderUnits = 0;
  let queryCells = 0;
  for (const op of visualOps) {
    if (op.kind === 'remove' || op.payload?.kind !== 'custom_visual') {continue;}
    if (op.payload.custom_spec === undefined || op.payload.custom_spec === null) {continue;}
    try {
      const report = estimateStructuralCost(parseCustomVisualSpec(op.payload.custom_spec));
      renderUnits += report.render_units;
      queryCells += report.query_cells;
    } catch {
      continue;
    }
  }
  const totals = isRecord(costReport.totals) ? costReport.totals : {};
  const readNumber = (value: unknown): number | null => (
    typeof value === 'number' && Number.isFinite(value) ? value : null
  );
  const reportedRender = readNumber(costReport.render_units)
    ?? readNumber(totals.estimated_render_units);
  const reportedCells = readNumber(costReport.query_cells)
    ?? readNumber(totals.estimated_query_cells);
  if (reportedRender === renderUnits && reportedCells === queryCells) {return null;}
  return 'El costo reportado por el modelo '
    + `(${reportedRender ?? '—'} unidades de render, ${reportedCells ?? '—'} celdas) no coincide con el cálculo local `
    + `(${renderUnits} unidades, ${queryCells} celdas); se usa el cálculo local.`;
}
