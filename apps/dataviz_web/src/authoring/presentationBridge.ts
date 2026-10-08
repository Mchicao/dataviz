import type {
  CanonicalDataBinding,
  CanonicalThemeConfig,
  CanonicalVisualPresentation,
  PresentationFieldRole,
  PresentationIR,
  PresentationSnapshot,
  PresentationVisualIntent,
  VisualFormatSettings,
  VisualLayoutSpec,
} from './types';
import { parseCustomVisualSpec } from '../visuals/custom/spec';
import type { CustomVisualSpec } from '../visuals/custom/spec';

export const PRESENTATION_IR_SCHEMA_VERSION = '1.0.0' as const;

const DEFAULT_PALETTE = ['#2563EB', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6'];
const KIND_TO_INTENT: Record<string, PresentationVisualIntent> = {
  area: 'area',
  bar: 'bar',
  box_plot: 'box_plot',
  bullet: 'bullet',
  card: 'kpi_card',
  column: 'column',
  combo: 'combo',
  custom_visual: 'custom_visual',
  donut: 'donut',
  funnel: 'funnel',
  gantt: 'gantt',
  gauge: 'gauge',
  heatmap: 'heatmap',
  histogram: 'histogram',
  image: 'image',
  kpi: 'kpi_card',
  line: 'line',
  lollipop: 'lollipop',
  map: 'map',
  matrix: 'pivot_matrix',
  packed_bubbles: 'packed_bubbles',
  pareto: 'pareto',
  percent_stacked_bar: 'percent_stacked_bar',
  percent_stacked_column: 'percent_stacked_column',
  ribbon: 'ribbon',
  pie: 'pie',
  scatter: 'scatter',
  slicer: 'slicer_filter',
  stacked_area: 'stacked_area',
  stacked_bar: 'stacked_bar',
  stacked_column: 'stacked_column',
  table: 'table',
  text_box: 'text_box',
  treemap: 'treemap',
  waterfall: 'waterfall',
};

const EDITOR_ROLE_TO_CANONICAL: Record<string, PresentationFieldRole> = {
  category: 'x_axis',
  color: 'color',
  column: 'column',
  comparison_metric: 'comparison_metric',
  filter_target: 'filter_target',
  label: 'label',
  row: 'row',
  series: 'series',
  size: 'size',
  target_metric: 'target_metric',
  tooltip: 'tooltip',
  value: 'value',
  x_axis: 'x_axis',
  y_axis: 'y_axis',
};

function theme(overrides: Partial<CanonicalThemeConfig> = {}): CanonicalThemeConfig {
  return {
    background_color: '#FFFFFF',
    border_color: '#E5E7EB',
    border_radius_px: 0,
    border_width_px: 0,
    card_background_color: '#FFFFFF',
    color_palette: [...DEFAULT_PALETTE],
    font_family: 'Segoe UI, sans-serif',
    font_size_pt: 10,
    primary_color: '#111827',
    shadow_enabled: false,
    text_color: '#1F2937',
    ...overrides,
  };
}

const accessibility = () => ({
  alt_text: '',
  aria_label: '',
  high_contrast_aware: true,
  screen_reader_summary: '',
  tab_index: 0,
});

function bindingFor(ref: string, role: string, visualId: string, index: number): CanonicalDataBinding | null {
  const canonicalRole = EDITOR_ROLE_TO_CANONICAL[role];
  if (!canonicalRole) {return null;}
  const separator = ref.indexOf(':');
  const fieldName = separator === -1 ? ref : ref.slice(separator + 1);
  if (!fieldName) {return null;}
  return {
    aggregation: null,
    binding_id: `${visualId}::${role}_${index}`,
    display_name: fieldName,
    field_name: fieldName,
    format_string: null,
    role: canonicalRole,
    sort_order: null,
  };
}

export function visualToIR(visual: VisualLayoutSpec): CanonicalVisualPresentation {
  const bindings = Object.entries(visual.data_roles)
    .map(([role, ref], index) => bindingFor(ref, role, visual.id, index))
    .filter((binding): binding is CanonicalDataBinding => binding !== null);
  return {
    accessibility: accessibility(),
    bindings,
    geometry: {
      ...visual.geometry,
      layout_mode: 'floating',
      padding: [0, 0, 0, 0],
      z_index: 0,
    },
    intent: KIND_TO_INTENT[visual.kind] ?? 'custom_visual',
    is_visible: true,
    properties: {
      editor_data_roles: { ...visual.data_roles },
      editor_kind: visual.kind,
      editor_name: visual.name,
      editor_show_title: visual.format_settings.show_title,
      ...(visual.custom_spec ? { custom_visual_spec: visual.custom_spec } : {}),
    },
    style: theme({
      primary_color: visual.format_settings.accent_color,
      background_color: visual.format_settings.background_color,
      card_background_color: visual.format_settings.background_color,
      text_color: visual.format_settings.text_color,
    }),
    subtitle: '',
    title: visual.title,
    visual_id: visual.id,
  };
}

function readStringRecord(value: unknown): Record<string, string> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {return null;}
  const entries = Object.entries(value);
  if (!entries.every(([key, item]) => key && typeof item === 'string')) {return null;}
  return Object.fromEntries(entries) as Record<string, string>;
}

export function visualFromIR(visual: CanonicalVisualPresentation): VisualLayoutSpec {
  const properties = visual.properties ?? {};
  const storedRoles = readStringRecord(properties.editor_data_roles);
  const dataRoles = storedRoles ?? Object.fromEntries(visual.bindings.map((binding) => [
    binding.role === 'x_axis' ? 'category' : binding.role,
    `field:${binding.field_name}`,
  ]));
  const kind = typeof properties.editor_kind === 'string'
    ? properties.editor_kind
    : visual.intent === 'kpi_card'
      ? 'kpi'
      : visual.intent === 'pivot_matrix'
        ? 'matrix'
        : visual.intent === 'slicer_filter'
          ? 'slicer'
          : visual.intent === 'custom_visual'
            ? 'custom_visual'
            : visual.intent;
  const format: VisualFormatSettings = {
    accent_color: visual.style.primary_color,
    background_color: visual.style.background_color,
    show_title: typeof properties.editor_show_title === 'boolean'
      ? properties.editor_show_title
      : true,
    text_color: visual.style.text_color,
  };
  // La spec anidada se revalida fail-closed: una spec corrupta en el IR no
  // llega al runtime (el visual degrada a estado explícito sin spec).
  let custom_spec: CustomVisualSpec | undefined;
  if (properties.custom_visual_spec !== undefined && properties.custom_visual_spec !== null) {
    try {
      custom_spec = parseCustomVisualSpec(properties.custom_visual_spec);
    } catch {
      custom_spec = undefined;
    }
  }
  return {
    data_roles: dataRoles,
    format_settings: format,
    geometry: {
      height: visual.geometry.height,
      width: visual.geometry.width,
      x: visual.geometry.x,
      y: visual.geometry.y,
    },
    id: visual.visual_id,
    kind,
    name: typeof properties.editor_name === 'string' ? properties.editor_name : visual.visual_id,
    title: visual.title,
    ...(custom_spec ? { custom_spec } : {}),
  };
}

export function presentationToIR(
  presentation: PresentationSnapshot,
  docId: string,
  title: string = '',
  baseIR?: PresentationIR | null,
): PresentationIR {
  if (!docId) {throw new Error('PresentationIR doc_id is required');}
  // LUNA1 P1: cuando existe un IR canónico de referencia se preserva su
  // estructura multipágina, page_ids y título; la conversión desde un snapshot
  // plano nunca debe reconstruir una sola página 'canvas' sobre un doc ya rico.
  if (baseIR && baseIR.pages.length > 0) {
    return updatePresentationIRFromSnapshot(baseIR, presentation, docId);
  }
  return {
    doc_id: docId,
    metadata: { authoring_surface: 'studio' },
    pages: [{
      page_id: 'canvas',
      name: 'Canvas',
      display_name: 'Canvas',
      is_hidden: false,
      width: presentation.canvas.width,
      height: presentation.canvas.height,
      theme: theme({ background_color: presentation.canvas.background_color }),
      accessibility: accessibility(),
      visuals: presentation.visuals.map(visualToIR),
      properties: {},
    }],
    schema_version: PRESENTATION_IR_SCHEMA_VERSION,
    theme: theme({ background_color: presentation.canvas.background_color }),
    title: (title || baseIR?.title) ?? '',
  };
}

export function presentationFromIR(ir: PresentationIR, activePageId?: string | null): PresentationSnapshot {
  if (ir.schema_version !== PRESENTATION_IR_SCHEMA_VERSION) {
    throw new Error(`Incompatible PresentationIR schema_version: ${ir.schema_version}`);
  }
  if (!ir.pages || ir.pages.length === 0) {
    return {
      canvas: {
        background_color: '#FFFFFF',
        height: 760,
        width: 1200,
      },
      visuals: [],
    };
  }
  const page = (activePageId ? ir.pages.find((p) => p.page_id === activePageId) : null) ?? ir.pages[0];
  return {
    canvas: {
      background_color: page.theme?.background_color ?? ir.theme?.background_color ?? '#FFFFFF',
      height: page.height ?? 760,
      width: page.width ?? 1200,
    },
    visuals: page.visuals.map(visualFromIR),
  };
}

export function updatePresentationIRFromSnapshot(
  baseIR: PresentationIR | null | undefined,
  snapshot: PresentationSnapshot,
  docId: string,
  activePageId?: string | null,
): PresentationIR {
  if (!baseIR) {
    return presentationToIR(snapshot, docId);
  }
  const targetPageId = activePageId ?? (baseIR.pages.length > 0 ? baseIR.pages[0].page_id : 'canvas');
  const existingPageIndex = baseIR.pages.findIndex((p) => p.page_id === targetPageId);

  const updatedVisuals = snapshot.visuals.map(visualToIR);
  const existingPage = existingPageIndex === -1 ? null : baseIR.pages[existingPageIndex];
  const updatedPage = {
    accessibility: existingPage ? existingPage.accessibility : accessibility(),
    display_name: existingPage ? existingPage.display_name : 'Canvas',
    height: snapshot.canvas.height,
    is_hidden: existingPage ? existingPage.is_hidden : false,
    name: existingPage ? existingPage.name : 'Canvas',
    page_id: targetPageId,
    properties: existingPage ? (existingPage.properties ?? {}) : {},
    theme: theme({
      ...(existingPage ? existingPage.theme : {}),
      background_color: snapshot.canvas.background_color,
    }),
    visuals: updatedVisuals,
    width: snapshot.canvas.width,
  };

  let newPages: typeof baseIR.pages;
  if (existingPageIndex !== -1) {
    newPages = baseIR.pages.map((p, idx) => (idx === existingPageIndex ? updatedPage : p));
  } else if (baseIR.pages.length === 0) {
    newPages = [updatedPage];
  } else {
    newPages = [...baseIR.pages, updatedPage];
  }

  return {
    ...baseIR,
    doc_id: baseIR.doc_id || docId,
    pages: newPages,
    title: baseIR.title ?? '',
  };
}
