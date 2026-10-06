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
  const value = kind === 'card' || kind === 'kpi' ? 'measure:Sales' : 'measure:Sales';
  const roles: Record<string, string> = kind === 'card' || kind === 'kpi'
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
      width: kind === 'card' || kind === 'kpi' ? 364 : 752,
      height: kind === 'card' || kind === 'kpi' ? 168 : 240,
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
    card: 'Ventas totales',
    column: 'Ventas por categoría',
    donut: 'Distribución de ventas',
    kpi: 'Indicador principal',
    line: 'Evolución de ventas',
    pie: 'Participación por región',
    scatter: 'Ventas y utilidad',
    table: 'Detalle de resultados',
  };
  return labels[kind] ?? 'Nuevo visual';
}
