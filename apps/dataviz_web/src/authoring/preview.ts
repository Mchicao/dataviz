import { RENDER_PLAN_SCHEMA_VERSION, RUNTIME_RESULTS_SCHEMA_VERSION } from '../runtime/types';
import type { RenderPlan, RuntimeResults, Scalar } from '../runtime/types';
import type { LocalDataset } from './csv';
import type { Expression, PresentationSnapshot, SemanticModel, VisualLayoutSpec } from './types';

const dates = ['2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01', '2026-05-01', '2026-06-01'];
const regions = ['Norte', 'Centro', 'Sur', 'Oriente', 'Norte', 'Centro'];
const categories = ['Tecnología', 'Oficina', 'Mobiliario', 'Servicios', 'Tecnología', 'Oficina'];

const samples: Record<string, readonly Scalar[] | Scalar> = {
  'field:Category': categories,
  'field:OrderDate': dates,
  'field:Region': regions,
  'field:Segment': ['Consumo', 'Empresa', 'Pyme', 'Gobierno', 'Consumo', 'Empresa'],
  'measure:Orders': [312, 377, 341, 429, 398, 476],
  'measure:Profit': [24000, 31000, 22000, 44000, 36000, 51000],
  'measure:ProfitMargin': 0.148,
  'measure:Sales': [182000, 224000, 198000, 276000, 241000, 309000],
};

export function renderPlanForPresentation(presentation: PresentationSnapshot): RenderPlan {
  return {
    description: 'Vista previa local del editor DataVIZ',
    schema_version: RENDER_PLAN_SCHEMA_VERSION,
    visuals: presentation.visuals.map((visual) => ({
      name: visual.name,
      kind: visual.kind,
      title: visual.title,
      data_roles: visual.data_roles,
      geometry: { ...visual.geometry },
      page: 'Canvas',
      ...(visual.custom_spec ? { custom_spec: visual.custom_spec } : {}),
    })),
  };
}

export function previewResultsFor(
  visuals: VisualLayoutSpec[],
  dataset?: LocalDataset | null,
  model?: SemanticModel | null,
): RuntimeResults {
  return {
    schema_version: RUNTIME_RESULTS_SCHEMA_VERSION,
    visuals: Object.fromEntries(visuals.map((visual) => [
      visual.name,
      groupedSeriesFor(visual, Object.fromEntries(Object.values(visual.data_roles).map((ref) => [
          ref,
          dataset && model ? datasetValue(ref, visual.kind, dataset, model) : scalarOrSeries(ref, visual.kind),
        ]))),
    ])),
  };
}

/**
 * Series de un visual con datos locales o sintéticos. Con categoría + medida, ambas series
 * se agregan por categoría única para que el chart y las tarjetas muestren los mismos totales.
 */
function groupedSeriesFor(
  visual: VisualLayoutSpec,
  base: Record<string, Scalar | readonly Scalar[]>,
): Record<string, Scalar | readonly Scalar[]> {
  const roles = Object.values(visual.data_roles);

  const categoryRef = roles.find((ref) => ref.startsWith('field:'));
  if (isScalarVisual(visual.kind) || !categoryRef
    || !roles.some((ref) => ref.startsWith('measure:'))) {
    return base;
  }

  const categories = base[categoryRef];
  if (!Array.isArray(categories)) {return base;}
  const firstIndexOfCategory = new Map<string, number>();
  for (let index = 0; index < categories.length; index += 1) {
    const category = categories[index];
    if (category === null) {continue;}
    const key = String(category);
    if (!firstIndexOfCategory.has(key)) {firstIndexOfCategory.set(key, index);}
  }

  const grouped: Record<string, Scalar | readonly Scalar[]> = {
    [categoryRef]: [...firstIndexOfCategory.keys()],
  };
  for (const ref of roles) {
    if (ref === categoryRef || !ref.startsWith('measure:')) {continue;}
    const values = base[ref];
    if (!Array.isArray(values)) { grouped[ref] = values; continue; }
    const totals = new Map<string, number>([...firstIndexOfCategory.keys()].map((key) => [key, 0]));
    for (let index = 0; index < categories.length; index += 1) {
      const category = categories[index];
      const value = values[index];
      if (category === null || typeof value !== 'number') {continue;}
      totals.set(String(category), (totals.get(String(category)) ?? 0) + value);
    }
    grouped[ref] = [...firstIndexOfCategory.keys()].map((key) => totals.get(key) ?? 0);
  }
  for (const ref of roles) {
    if (!ref.startsWith('field:') || ref === categoryRef) {continue;}
    const values = base[ref];
    grouped[ref] = Array.isArray(values)
      ? [...firstIndexOfCategory.values()].map((index) => values[index] ?? null) : values;
  }
  return grouped;
}

function datasetValue(
  ref: string,
  kind: string,
  dataset: LocalDataset,
  model: SemanticModel,
) {
  const scalarVisual = isScalarVisual(kind);
  if (ref.startsWith('field:')) {
    const values = fieldValues(ref.slice('field:'.length), dataset);
    return scalarVisual ? (values[0] ?? null) : values;
  }
  if (!ref.startsWith('measure:')) {return scalarVisual ? null : [];}
  const metricName = ref.slice('measure:'.length);
  const metric = model.metrics.find((candidate) => candidate.name === metricName);
  if (!metric) {return scalarVisual ? null : [];}
  const source = aggregateSourceField(metric.expression);
  if (!source) {return scalarVisual ? null : [];}
  const values = fieldValues(source, dataset);
  if (!scalarVisual) {return values;}
  return values.reduce<number>((total, value) => (
    total + (typeof value === 'number' ? value : 0)
  ), 0);
}

function isScalarVisual(kind: string): boolean {
  return kind === 'card' || kind === 'kpi';
}

function aggregateSourceField(expression: Expression): string | null {
  if (expression.kind !== 'agg' || expression.name?.toLocaleLowerCase('en') !== 'sum') {return null;}
  const child = expression.children?.[0];
  if (!child || child.kind !== 'field_ref' || !child.name) {return null;}
  return child.name;
}

function fieldValues(name: string, dataset: LocalDataset): Scalar[] {
  if (!dataset.columns.some((column) => column.name === name)) {return [];}
  return dataset.rows.map((row) => row[name] ?? null);
}

function scalarOrSeries(ref: string, kind: string) {
  const sample = samples[ref];
  if (sample === undefined) {return isScalarVisual(kind) ? null : [];}
  if (!isScalarVisual(kind)) {return sample;}
  if (!Array.isArray(sample)) {return sample;}
  return sample.reduce<number>((total, value) => (
    total + (typeof value === 'number' ? value : 0)
  ), 0);
}
