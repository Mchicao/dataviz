import React, { useState } from 'react';
import type { Filter, Metric, PresentationSnapshot, SemanticModel, VisualLayoutSpec } from './types';

interface InspectorPanelProps {
  canvas: PresentationSnapshot['canvas'];
  model: SemanticModel;
  selected: VisualLayoutSpec | null;
  onAddVisual: (kind: string) => void;
  onUpdateVisual: (visual: VisualLayoutSpec, message: string) => void;
  onAddMetric: (metric: Metric) => void;
  onRemoveMetric: (name: string) => void;
  onAddFilter: (filter: Filter) => void;
  onRemoveFilter: (name: string) => void;
  onCollapse?: () => void;
}

const VISUAL_KINDS = [
  ['card', 'KPI'], ['bar', 'Barras'], ['stacked_bar', 'Barras apiladas'],
  ['percent_stacked_bar', 'Barras 100%'], ['column', 'Columnas'],
  ['stacked_column', 'Columnas apiladas'], ['percent_stacked_column', 'Columnas 100%'],
  ['line', 'Línea'], ['area', 'Área'], ['stacked_area', 'Área apilada'],
  ['combo', 'Combinado'], ['ribbon', 'Cintas'], ['pie', 'Torta'], ['donut', 'Dona'],
  ['scatter', 'Dispersión'], ['lollipop', 'Paleta'], ['table', 'Tabla'], ['matrix', 'Matriz'],
  ['treemap', 'Treemap'], ['heatmap', 'Mapa de calor'], ['histogram', 'Histograma'],
  ['box_plot', 'Caja y bigotes'], ['bullet', 'Bullet'], ['pareto', 'Pareto'],
  ['waterfall', 'Cascada'], ['funnel', 'Embudo'], ['gauge', 'Indicador'],
  ['packed_bubbles', 'Burbujas'], ['gantt', 'Gantt'],
  ['map', 'Mapa'], ['slicer', 'Segmentador'],
] as const;

interface FieldWell {
  role: string;
  label: string;
  source: 'fields' | 'values';
  optional?: boolean;
}

const cartesianWells = (xLabel: string, yLabel: string): readonly FieldWell[] => [
  { role: 'category', label: xLabel, source: 'fields' },
  { role: 'value', label: yLabel, source: 'values' },
  { role: 'series', label: 'Serie · leyenda', source: 'fields', optional: true },
];

const FIELD_WELLS: Record<string, readonly FieldWell[]> = {
  area: [
    { role: 'category', label: 'Eje X · periodo', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'series', label: 'Serie', source: 'fields', optional: true },
  ],
  bar: cartesianWells('Eje X · categoría', 'Eje Y · valores'),
  box_plot: [
    { role: 'category', label: 'Eje X · categoría', source: 'fields', optional: true },
    { role: 'value', label: 'Valores · distribución', source: 'values' },
  ],
  bullet: [
    { role: 'category', label: 'Filas · categoría', source: 'fields', optional: true },
    { role: 'value', label: 'Valores', source: 'values' },
    { role: 'comparison_metric', label: 'Objetivo', source: 'values' },
  ],
  card: [{ role: 'value', label: 'Valores', source: 'values' }],
  column: cartesianWells('Eje X · categoría', 'Eje Y · valores'),
  combo: [
    { role: 'category', label: 'Eje X · categoría', source: 'fields' },
    { role: 'value', label: 'Columnas · valores', source: 'values' },
    { role: 'comparison_metric', label: 'Línea · valores', source: 'values' },
  ],
  donut: [
    { role: 'category', label: 'Categoría · anillos', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  funnel: [
    { role: 'category', label: 'Etapas', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  gantt: [
    { role: 'category', label: 'Filas · tareas', source: 'fields' },
    { role: 'x_axis', label: 'Inicio · fecha', source: 'fields' },
    { role: 'value', label: 'Duración · valores', source: 'values' },
  ],
  gauge: [
    { role: 'value', label: 'Valores', source: 'values' },
    { role: 'target_metric', label: 'Objetivo', source: 'values', optional: true },
  ],
  heatmap: [
    { role: 'row', label: 'Filas', source: 'fields' },
    { role: 'column', label: 'Columnas', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  histogram: [
    { role: 'value', label: 'Valores · numérico', source: 'values' },
  ],
  kpi: [{ role: 'value', label: 'Valores', source: 'values' }],
  line: [
    { role: 'category', label: 'Eje X · periodo', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'series', label: 'Serie', source: 'fields', optional: true },
  ],
  lollipop: cartesianWells('Eje X · categoría', 'Eje Y · valores'),
  map: [
    { role: 'category', label: 'Ubicación', source: 'fields' },
    { role: 'value', label: 'Tamaño · valores', source: 'values', optional: true },
  ],
  packed_bubbles: [
    { role: 'category', label: 'Detalle · etiquetas', source: 'fields' },
    { role: 'series', label: 'Color · agrupación', source: 'fields', optional: true },
    { role: 'value', label: 'Tamaño · valores', source: 'values' },
  ],
  percent_stacked_bar: cartesianWells('Eje Y · categoría', 'Eje X · valores'),
  percent_stacked_column: cartesianWells('Eje X · categoría', 'Eje Y · valores'),
  matrix: [
    { role: 'row', label: 'Filas', source: 'fields' },
    { role: 'column', label: 'Columnas', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  pareto: [
    { role: 'category', label: 'Eje X · categoría', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
  ],
  pie: [
    { role: 'category', label: 'Categoría · sectores', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  scatter: [
    { role: 'x_axis', label: 'Eje X · valores', source: 'values' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'category', label: 'Detalle por punto', source: 'fields', optional: true },
  ],
  ribbon: cartesianWells('Eje X · categoría', 'Eje Y · valores'),
  slicer: [{ role: 'category', label: 'Campo a filtrar', source: 'fields' }],
  stacked_area: [
    { role: 'category', label: 'Eje X · periodo', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'series', label: 'Serie', source: 'fields' },
  ],
  stacked_bar: cartesianWells('Eje Y · categoría', 'Eje X · valores'),
  stacked_column: cartesianWells('Eje X · categoría', 'Eje Y · valores'),
  table: [
    { role: 'category', label: 'Filas · categorías', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  treemap: [
    { role: 'category', label: 'Categorías', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  waterfall: [
    { role: 'category', label: 'Etapas · categoría', source: 'fields' },
    { role: 'value', label: 'Aportes · valores', source: 'values' },
  ],
};

/** Iconos minimalistas de la galería de visuales (16×16). */
const VISUAL_ICONS: Record<string, React.ReactNode> = {
  area: <><polygon points="2,14 7,6 11,10 15,3 15,14" transform="translate(0 0)" /></>,
  bar: <><rect height="4" width="10" x="3" y="3" /><rect height="4" width="7" x="3" y="9" /><rect height="4" width="12" x="3" y="15" /></>,
  box_plot: <><line x1="5" x2="5" y1="1" y2="15" /><rect fill="none" height="6" strokeWidth="1.4" width="6" x="2" y="5" /><line x1="11" x2="11" y1="3" y2="13" /><rect fill="none" height="5" strokeWidth="1.4" width="5" x="8.5" y="5" /></>,
  bullet: <><rect height="3" width="12" x="2" y="7" /><line x1="11" x2="11" y1="4" y2="13" strokeWidth="1.6" /></>,
  card: <rect height="12" rx="1.5" width="12" x="2" y="2" />,
  column: <><rect height="8" width="4" x="2" y="8" /><rect height="12" width="4" x="8" y="4" /><rect height="6" width="4" x="14" y="10" transform="translate(-2 0)" /></>,
  combo: <><rect height="9" width="4" x="3" y="7" /><rect height="5" width="4" x="10" y="11" /><polyline fill="none" points="2,10 6,5 10,8 15,2" strokeWidth="1.6" /></>,
  donut: <><circle cx="8" cy="8" fill="none" r="6" strokeWidth="4" /></>,
  funnel: <><polygon points="1,2 15,2 10,7 6,7" /><polygon points="6,9 10,9 9,14 7,14" /></>,
  gauge: <><path d="M2 12 A6 6 0 0 1 14 12" fill="none" strokeWidth="2" /><line x1="8" x2="11" y1="12" y2="8" strokeWidth="1.6" /></>,
  heatmap: <><rect height="3.5" width="3.5" x="1.5" y="1.5" /><rect fillOpacity="0.6" height="3.5" width="3.5" x="6" y="1.5" /><rect fillOpacity="0.3" height="3.5" width="3.5" x="10.5" y="1.5" /><rect fillOpacity="0.3" height="3.5" width="3.5" x="1.5" y="6" /><rect fillOpacity="0.6" height="3.5" width="3.5" x="6" y="6" /><rect height="3.5" width="3.5" x="10.5" y="6" /><rect fillOpacity="0.6" height="3.5" width="3.5" x="1.5" y="10.5" /><rect fillOpacity="0.3" height="3.5" width="3.5" x="6" y="10.5" /><rect height="3.5" width="3.5" x="10.5" y="10.5" /></>,
  histogram: <><rect height="5" width="3" x="1" y="11" /><rect height="8" width="3" x="4.5" y="8" /><rect height="12" width="3" x="8" y="4" /><rect height="9" width="3" x="11.5" y="7" /></>,
  line: <polyline fill="none" points="2,14 7,7 11,10 15,3" strokeWidth="2" transform="translate(0 1)" />,
  lollipop: <><line x1="5" x2="5" y1="15" y2="6" strokeWidth="1.6" /><circle cx="5" cy="4.5" r="1.8" /><line x1="11" x2="11" y1="15" y2="9" strokeWidth="1.6" /><circle cx="11" cy="7.5" r="1.8" /></>,
  map: <><path d="M8 1 C5.2 1 3 3.2 3 6 C3 10 8 15 8 15 C8 15 13 10 13 6 C13 3.2 10.8 1 8 1 Z" /><circle cx="8" cy="6" fill="#ffffff" r="2" /></>,
  matrix: <><rect fill="none" height="13" rx="1" strokeWidth="1.4" width="13" x="1.5" y="1.5" /><line x1="1.5" x2="14.5" y1="5.5" y2="5.5" strokeWidth="1.4" /><line x1="6" x2="6" y1="5.5" y2="14.5" /><line x1="10.5" x2="10.5" y1="5.5" y2="14.5" /></>,
  pareto: <><rect height="4" width="2.5" x="2" y="11" /><rect height="7" width="2.5" x="5" y="8" /><rect height="10" width="2.5" x="8" y="5" /><rect height="13" width="2.5" x="11" y="2" /><polyline fill="none" points="2,14 6,10 10,6 14,3" strokeWidth="1.3" strokeOpacity="0.55" /></>,
  pie: <path d="M8 8 L8 1 A7 7 0 1 1 1.5 11 Z" transform="translate(0.5 0.5)" />,
  scatter: <><circle cx="4" cy="12" r="1.8" /><circle cx="9" cy="6" r="1.8" /><circle cx="13" cy="10" r="1.8" /></>,
  slicer: <><rect fill="none" height="9" rx="1.5" strokeWidth="1.4" width="14" x="1" y="3.5" /><path d="M11 7 L13 9.5 L15 7" fill="none" strokeWidth="1.4" transform="translate(-1 0)" /><rect height="1.6" width="8" x="3" y="14" /></>,
  stacked_bar: <><rect height="3.4" width="12" x="2" y="2.5" /><rect fillOpacity="0.55" height="3.4" width="8" x="2" y="6.4" /><rect height="3.4" width="13" x="2" y="10.3" /></>,
  stacked_column: <><rect height="6" width="3.6" x="2" y="2.5" /><rect fillOpacity="0.55" height="6" width="3.6" x="2" y="9" /><rect height="4" width="3.6" x="7" y="4.5" /><rect fillOpacity="0.55" height="7" width="3.6" x="7" y="9" /><rect height="8" width="3.6" x="12" y="2.5" /><rect fillOpacity="0.55" height="3" width="3.6" x="12" y="11" /></>,
  table: <><rect fill="none" height="12" rx="1" strokeWidth="1.5" width="12" x="2" y="2" /><line x1="2" x2="14" y1="6" y2="6" /><line x1="8" x2="8" y1="6" y2="14" /></>,
  treemap: <><rect height="6" width="7" x="1.5" y="1.5" /><rect fillOpacity="0.55" height="6" width="6" x="9" y="1.5" /><rect fillOpacity="0.55" height="6" width="4" x="1.5" y="8.5" /><rect height="6" width="9" x="6.5" y="8.5" /></>,
  waterfall: <><rect height="3" width="3.2" x="1" y="2" /><rect fillOpacity="0.55" height="3" width="3.2" x="4.8" y="6" /><rect height="3" width="3.2" x="8.6" y="10" /><rect fillOpacity="0.55" height="3" width="3.2" x="12.4" y="5" /></>,
  percent_stacked_bar: <><rect height="3.4" width="14" x="1" y="2.5" /><rect fillOpacity="0.55" height="3.4" width="14" x="1" y="6.4" /><rect height="3.4" width="14" x="1" y="10.3" /></>,
  percent_stacked_column: <><rect height="13" width="3.4" x="2" y="1.5" /><rect fillOpacity="0.55" height="13" width="3.4" x="6.5" y="1.5" /><rect height="13" width="3.4" x="11" y="1.5" /></>,
  stacked_area: <><polygon points="1,14 1,8 6,4 11,7 15,2 15,14" /><polygon fillOpacity="0.5" points="1,14 1,11 6,9 11,10 15,7 15,14" /></>,
  ribbon: <><polygon points="1,3 5,3 9,5 13,5 13,8 9,8 5,6 1,6" /><polygon fillOpacity="0.5" points="1,7 5,7 9,9 13,9 13,13 9,13 5,11 1,11" /></>,
  packed_bubbles: <><circle cx="5" cy="6" r="3.6" /><circle cx="12" cy="4.5" r="2.4" /><circle cx="11" cy="10.5" r="2" /><circle cx="5.5" cy="12.5" r="1.5" /></>,
  gantt: <><rect height="2.6" width="8" x="2" y="3" /><rect fillOpacity="0.6" height="2.6" width="6" x="6" y="7" /><rect height="2.6" width="9" x="4" y="11" /><line x1="2" x2="2" y1="1" y2="15" strokeWidth="1.2" /></>,
};

const VisualIcon: React.FC<{ kind: string }> = ({ kind }) => (
  <svg aria-hidden="true" fill="currentColor" height="16" stroke="currentColor" viewBox="0 0 16 16" width="16">
    {VISUAL_ICONS[kind] ?? VISUAL_ICONS.table}
  </svg>
);

export const InspectorPanel: React.FC<InspectorPanelProps> = ({
  canvas,
  model,
  selected,
  onAddVisual,
  onUpdateVisual,
  onAddMetric,
  onRemoveMetric,
  onAddFilter,
  onRemoveFilter,
  onCollapse,
}) => {
  const [tab, setTab] = useState<'visual' | 'model'>('visual');
  const [metricName, setMetricName] = useState('');
  const [metricField, setMetricField] = useState('sales.Sales');
  const [filterName, setFilterName] = useState('');
  const [filterField, setFilterField] = useState('sales.Region');
  const [filterValue, setFilterValue] = useState('Norte');
  const fields = model.entities.flatMap((entity) => entity.fields.map((field) => ({
    dataType: field.data_type,
    entity: entity.name,
    key: `${entity.name}.${field.name}`,
    label: field.name,
    ref: `field:${field.name}`,
  })));
  const numericFields = fields.filter((field) => (
    field.dataType === 'integer' || field.dataType === 'decimal'
  ));
  const selectedMetricField = numericFields.find((field) => field.key === metricField)
    ?? numericFields[0];
  const selectedFilterField = fields.find((field) => field.key === filterField) ?? fields[0];
  const metrics = model.metrics.map((metric) => ({ label: metric.name, ref: `measure:${metric.name}` }));

  const patchSelected = (updates: Partial<VisualLayoutSpec>, message: string) => {
    if (selected) {onUpdateVisual({ ...selected, ...updates }, message);}
  };
  const patchGeometry = (name: keyof VisualLayoutSpec['geometry'], value: number) => {
    if (!selected || !Number.isFinite(value)) {return;}
    const limits = {
      height: Math.max(52, canvas.height - selected.geometry.y),
      width: Math.max(80, canvas.width - selected.geometry.x),
      x: Math.max(0, canvas.width - selected.geometry.width),
      y: Math.max(0, canvas.height - selected.geometry.height),
    };
    const minimum = name === 'width' ? 80 : name === 'height' ? 52 : 0;
    const bounded = Math.min(Math.max(minimum, value), limits[name]);
    patchSelected({ geometry: { ...selected.geometry, [name]: bounded } }, `Ajustar ${name}`);
  };
  const patchRole = (name: string, value: string) => {
    if (!selected) {return;}
    patchSelected({ data_roles: { ...selected.data_roles, [name]: value } }, `Asignar ${name}`);
  };
  const entities = model.entities.map((entity) => ({
    fields: fields.filter((field) => field.entity === entity.name),
    name: entity.name,
  }));
  const wellValue = (well: FieldWell) => (
    selected?.data_roles[well.role] ?? (well.source === 'values' ? metrics[0]?.ref ?? '' : '')
  );
  const renderWellOptions = (well: FieldWell) => {
    const unassigned = well.optional ? <option value="">— Sin asignar —</option> : null;
    if (well.source === 'fields') {
      return (
        <>
          {unassigned}
          {entities.map((entity) => (
            <optgroup key={entity.name} label={entity.name}>
              {entity.fields.map((option) => <option key={option.ref} value={option.ref}>{option.label}</option>)}
            </optgroup>
          ))}
        </>
      );
    }
    return (
      <>
        <optgroup label="Métricas">
          {metrics.map((option) => <option key={option.ref} value={option.ref}>{option.label}</option>)}
        </optgroup>
        <optgroup label="Campos numéricos">
          {numericFields.map((option) => <option key={option.ref} value={option.ref}>{option.label}</option>)}
        </optgroup>
      </>
    );
  };
  const selectTabFromKeyboard = (
    event: React.KeyboardEvent<HTMLButtonElement>,
    current: 'visual' | 'model',
  ) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {return;}
    event.preventDefault();
    const next = current === 'visual' ? 'model' : 'visual';
    setTab(next);
    document.getElementById(`dv-inspector-tab-${next}`)?.focus();
  };

  return (
    <aside className="dv-inspector" aria-label="Propiedades y modelo">
      <div className="dv-inspector-head">
        <span className="dv-pane-title"><span aria-hidden="true" className="dv-pane-icon">▤</span><strong>Visualizaciones</strong></span>
        {onCollapse && (
          <button aria-label="Minimizar visualizaciones" className="dv-pane-collapse" onClick={onCollapse} title="Minimizar panel" type="button">»</button>
        )}
      </div>
      <div className="dv-inspector-tabs" role="tablist" aria-label="Inspector">
        <button aria-controls="dv-inspector-panel-visual" aria-selected={tab === 'visual'} id="dv-inspector-tab-visual" onClick={() => setTab('visual')} onKeyDown={(event) => selectTabFromKeyboard(event, 'visual')} role="tab" tabIndex={tab === 'visual' ? 0 : -1} type="button">Visual</button>
        <button aria-controls="dv-inspector-panel-model" aria-selected={tab === 'model'} id="dv-inspector-tab-model" onClick={() => setTab('model')} onKeyDown={(event) => selectTabFromKeyboard(event, 'model')} role="tab" tabIndex={tab === 'model' ? 0 : -1} type="button">Modelo</button>
      </div>

      {tab === 'visual' && (
        <div aria-labelledby="dv-inspector-tab-visual" className="dv-inspector-body" id="dv-inspector-panel-visual" role="tabpanel">
          <section className="dv-property-section">
            <div className="dv-section-heading"><h2>Visualizaciones</h2></div>
            <div aria-label="Agregar visual" className="dv-visual-picker" role="group">
              {VISUAL_KINDS.map(([kind, label]) => (
                <button key={kind} onClick={() => onAddVisual(kind)} title={`Agregar ${label}`} type="button">
                  <VisualIcon kind={kind} />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          </section>
          {selected ? (
            <>
              <section className="dv-property-section">
                <h2>Contenido</h2>
                <label>
                  Título
                  <input
                    defaultValue={selected.title}
                    key={`${selected.id}-title-${selected.title}`}
                    onBlur={(event) => patchSelected({ title: event.currentTarget.value.trim() || selected.title }, 'Cambiar título')}
                  />
                </label>
                <label>
                  Tipo de gráfico
                  <select value={selected.kind} onChange={(event) => patchSelected({ kind: event.target.value }, 'Cambiar tipo de gráfico')}>
                    {VISUAL_KINDS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </label>
              </section>

              <section className="dv-property-section">
                <h2>Campos del visual</h2>
                {(FIELD_WELLS[selected.kind] ?? FIELD_WELLS.bar).map((well) => (
                  <label key={`${well.role}-${well.label}`}>
                    {well.label}
                    <select value={wellValue(well)} onChange={(event) => patchRole(well.role, event.target.value)}>
                      {renderWellOptions(well)}
                    </select>
                  </label>
                ))}
              </section>

              <section className="dv-property-section">
                <h2>Posición y tamaño</h2>
                <div className="dv-property-grid">
                  {(['x', 'y', 'width', 'height'] as const).map((name) => (
                    <label key={name}>
                      {name === 'width' ? 'Ancho' : name === 'height' ? 'Alto' : name.toUpperCase()}
                      <input
                        key={`${selected.id}-${name}-${selected.geometry[name]}`}
                        defaultValue={selected.geometry[name]}
                        min="0"
                        onBlur={(event) => patchGeometry(name, Number(event.currentTarget.value))}
                        type="number"
                      />
                    </label>
                  ))}
                </div>
              </section>

              <section className="dv-property-section">
                <h2>Apariencia</h2>
                <div className="dv-color-fields">
                  <label>Color principal<input aria-label="Color principal" type="color" value={selected.format_settings.accent_color} onChange={(event) => patchSelected({ format_settings: { ...selected.format_settings, accent_color: event.target.value } }, 'Cambiar color principal')} /></label>
                  <label>Fondo<input aria-label="Color de fondo" type="color" value={selected.format_settings.background_color} onChange={(event) => patchSelected({ format_settings: { ...selected.format_settings, background_color: event.target.value } }, 'Cambiar fondo')} /></label>
                </div>
                <label className="dv-check-row">
                  <input checked={selected.format_settings.show_title} onChange={(event) => patchSelected({ format_settings: { ...selected.format_settings, show_title: event.target.checked } }, 'Mostrar u ocultar título')} type="checkbox" />
                  Mostrar título
                </label>
              </section>
            </>
          ) : (
            <div className="dv-inspector-empty">
              <strong>Selecciona un visual</strong>
              <p>Haz clic en el canvas para editar tipo, datos, posición, tamaño y color.</p>
            </div>
          )}
        </div>
      )}

      {tab === 'model' && (
        <div aria-labelledby="dv-inspector-tab-model" className="dv-inspector-body" id="dv-inspector-panel-model" role="tabpanel">
          <section className="dv-property-section">
            <div className="dv-section-heading"><h2>Métricas</h2><span>{model.metrics.length}</span></div>
            <div className="dv-compact-form">
              <label>Nombre<input value={metricName} onChange={(event) => setMetricName(event.target.value)} placeholder="TicketPromedio" /></label>
              <label>Campo base<select value={selectedMetricField?.key ?? ''} onChange={(event) => setMetricField(event.target.value)}>{numericFields.map((field) => <option key={field.key} value={field.key}>{field.label}</option>)}</select></label>
              <button disabled={!metricName.trim() || !selectedMetricField} onClick={() => {
                if (!selectedMetricField) {return;}
                onAddMetric({ data_type: 'decimal', expression: { children: [{ kind: 'field_ref', entity: selectedMetricField.entity, name: selectedMetricField.label }], kind: 'agg', name: 'sum' }, format_string: '#,##0', name: metricName.trim() });
                setMetricName('');
              }} type="button">Crear métrica</button>
            </div>
            <ul className="dv-model-list">
              {model.metrics.map((metric) => <li key={metric.name}><span><strong>{metric.name}</strong><small>{metric.format_string || 'Sin formato'}</small></span><button aria-label={`Eliminar métrica ${metric.name}`} onClick={() => onRemoveMetric(metric.name)} type="button">Eliminar</button></li>)}
            </ul>
          </section>

          <section className="dv-property-section">
            <div className="dv-section-heading"><h2>Filtros</h2><span>{model.filters.length}</span></div>
            <div className="dv-compact-form">
              <label>Nombre<input value={filterName} onChange={(event) => setFilterName(event.target.value)} placeholder="SoloNorte" /></label>
              <label>Campo<select value={selectedFilterField?.key ?? ''} onChange={(event) => setFilterField(event.target.value)}>{fields.map((field) => <option key={field.key} value={field.key}>{field.label}</option>)}</select></label>
              <label>Valor<input value={filterValue} onChange={(event) => setFilterValue(event.target.value)} /></label>
              <button disabled={!filterName.trim() || !selectedFilterField} onClick={() => {
                if (!selectedFilterField) {return;}
                onAddFilter({ name: filterName.trim(), operator: 'eq', target: { entity: selectedFilterField.entity, kind: 'field_ref', name: selectedFilterField.label }, values: [filterValue] });
                setFilterName('');
              }} type="button">Crear filtro</button>
            </div>
            <ul className="dv-model-list">
              {model.filters.length === 0 && <li className="dv-list-empty">No hay filtros persistentes.</li>}
              {model.filters.map((filter) => <li key={filter.name}><span><strong>{filter.name}</strong><small>{filter.operator} · {String(filter.values?.[0] ?? '')}</small></span><button aria-label={`Eliminar filtro ${filter.name}`} onClick={() => onRemoveFilter(filter.name)} type="button">Eliminar</button></li>)}
            </ul>
          </section>
        </div>
      )}
    </aside>
  );
};
