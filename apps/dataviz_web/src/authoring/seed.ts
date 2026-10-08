import { initDocument } from './versioning';
import type { Document, SemanticModel, VisualLayoutSpec } from './types';

export const AUTHORING_STORAGE_KEY = 'dataviz.authoring.project.v1';

export const DEMO_FIELDS = [
  { label: 'Fecha de pedido', ref: 'field:OrderDate' },
  { label: 'Región', ref: 'field:Region' },
  { label: 'Categoría', ref: 'field:Category' },
  { label: 'Segmento', ref: 'field:Segment' },
] as const;

export const DEMO_METRICS = [
  { label: 'Ventas', ref: 'measure:Sales' },
  { label: 'Utilidad', ref: 'measure:Profit' },
  { label: 'Pedidos', ref: 'measure:Orders' },
] as const;

export function createSeedModel(): SemanticModel {
  return {
    description: 'Modelo inicial DataVIZ con datos sintéticos para autoría local.',
    entities: [
      {
        name: 'sales',
        description: 'Ventas sintéticas autorizadas para diseñar el dashboard.',
        fields: [
          { name: 'OrderDate', data_type: 'date' },
          { name: 'Region', data_type: 'string' },
          { name: 'Category', data_type: 'string' },
          { name: 'Segment', data_type: 'string' },
          { name: 'Sales', data_type: 'decimal' },
          { name: 'Profit', data_type: 'decimal' },
          { name: 'OrderId', data_type: 'string' },
        ],
      },
    ],
    filters: [],
    metrics: [
      metric('Sales', 'Sales', '#,##0'),
      metric('Profit', 'Profit', '#,##0'),
      metric('Orders', 'OrderId', '#,##0', 'count'),
    ],
    name: 'nuevo_dashboard',
    parameters: [],
    relationships: [],
    rls_intents: [],
    schema_version: '2.0.0',
  };
}

export function createAuthoringDocument(docId = 'dashboard-sin-titulo'): Document {
  return initDocument(createSeedModel(), {
    doc_id: docId,
    message: 'Proyecto DataVIZ creado',
    visuals: [],
  });
}

export function createVisual(
  kind: string,
  index: number,
  overrides: Partial<VisualLayoutSpec> = {},
): VisualLayoutSpec {
  const id = overrides.id ?? `visual-${index + 1}`;
  const compact = kind === 'card' || kind === 'kpi' || kind === 'gauge';
  const value = 'measure:Sales';
  const roles: Record<string, string> = compact
    ? { value }
    : kind === 'table'
      ? { category: 'field:Region', value }
      : { category: 'field:Region', value };
  return {
    data_roles: overrides.data_roles ?? roles,
    format_settings: overrides.format_settings ?? {
      accent_color: '#1677b8',
      background_color: '#ffffff',
      text_color: '#172536',
      show_title: true,
    },
    geometry: overrides.geometry ?? {
      x: 24 + (index % 2) * 388,
      y: 24 + Math.floor(index / 2) * 260,
      width: compact ? 364 : 752,
      height: compact ? 168 : 240,
    },
    id,
    kind,
    name: overrides.name ?? `Visual ${index + 1}`,
    title: overrides.title ?? visualTitle(kind),
    ...(overrides.custom_spec ? { custom_spec: overrides.custom_spec } : {}),
  };
}

function metric(name: string, field: string, format: string, aggregation = 'sum') {
  return {
    data_type: 'decimal' as const,
    expression: {
      children: [{ kind: 'field_ref' as const, entity: 'sales', name: field }],
      kind: 'agg' as const,
      name: aggregation,
    },
    format_string: format,
    name,
  };
}

function visualTitle(kind: string): string {
  const labels: Record<string, string> = {
    area: 'Tendencia acumulada',
    bar: 'Ventas por región',
    box_plot: 'Distribución por categoría',
    bullet: 'Avance contra objetivo',
    card: 'Ventas totales',
    column: 'Ventas por categoría',
    combo: 'Ventas y utilidad',
    donut: 'Distribución de ventas',
    funnel: 'Embudo de etapas',
    gantt: 'Cronograma por etapa',
    gauge: 'Cumplimiento del objetivo',
    heatmap: 'Mapa de calor por región y categoría',
    histogram: 'Distribución de valores',
    kpi: 'Indicador principal',
    line: 'Evolución de ventas',
    lollipop: 'Ranking por categoría',
    map: 'Distribución geográfica',
    matrix: 'Matriz de resumen',
    packed_bubbles: 'Proporción por burbujas',
    pareto: 'Pareto de ventas',
    percent_stacked_bar: 'Participación apilada por segmento',
    percent_stacked_column: 'Participación apilada por segmento',
    ribbon: 'Evolución de ranking por segmento',
    pie: 'Participación por región',
    scatter: 'Ventas y utilidad',
    slicer: 'Filtro de campo',
    stacked_area: 'Tendencia acumulada por segmento',
    stacked_bar: 'Ventas apiladas por segmento',
    stacked_column: 'Ventas apiladas por segmento',
    table: 'Detalle de resultados',
    treemap: 'Proporción por categoría',
    waterfall: 'Aporte al total',
  };
  return labels[kind] ?? 'Nuevo visual';
}
