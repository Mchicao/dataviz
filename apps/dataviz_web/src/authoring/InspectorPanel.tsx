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
  ['card', 'KPI'], ['bar', 'Barras'], ['column', 'Columnas'], ['line', 'Línea'],
  ['area', 'Área'], ['pie', 'Torta'], ['donut', 'Dona'], ['scatter', 'Dispersión'],
  ['table', 'Tabla'],
] as const;

interface FieldWell {
  role: string;
  label: string;
  source: 'fields' | 'values';
  optional?: boolean;
}

const FIELD_WELLS: Record<string, readonly FieldWell[]> = {
  area: [
    { role: 'category', label: 'Eje X · periodo', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'series', label: 'Serie', source: 'fields', optional: true },
  ],
  bar: [
    { role: 'category', label: 'Eje X · categoría', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'series', label: 'Serie · leyenda', source: 'fields', optional: true },
  ],
  card: [{ role: 'value', label: 'Valores', source: 'values' }],
  column: [
    { role: 'category', label: 'Eje X · categoría', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'series', label: 'Serie · leyenda', source: 'fields', optional: true },
  ],
  donut: [
    { role: 'category', label: 'Categoría · anillos', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
  kpi: [{ role: 'value', label: 'Valores', source: 'values' }],
  line: [
    { role: 'category', label: 'Eje X · periodo', source: 'fields' },
    { role: 'value', label: 'Eje Y · valores', source: 'values' },
    { role: 'series', label: 'Serie', source: 'fields', optional: true },
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
  table: [
    { role: 'category', label: 'Filas · categorías', source: 'fields' },
    { role: 'value', label: 'Valores', source: 'values' },
  ],
};

/** Iconos minimalistas de la galería de visuales (16×16). */
const VISUAL_ICONS: Record<string, React.ReactNode> = {
  area: <><polygon points="2,14 7,6 11,10 15,3 15,14" transform="translate(0 0)" /></>,
  bar: <><rect height="4" width="10" x="3" y="3" /><rect height="4" width="7" x="3" y="9" /><rect height="4" width="12" x="3" y="15" /></>,
  card: <rect height="12" rx="1.5" width="12" x="2" y="2" />,
  column: <><rect height="8" width="4" x="2" y="8" /><rect height="12" width="4" x="8" y="4" /><rect height="6" width="4" x="14" y="10" transform="translate(-2 0)" /></>,
  donut: <><circle cx="8" cy="8" fill="none" r="6" strokeWidth="4" /></>,
  line: <polyline fill="none" points="2,14 7,7 11,10 15,3" strokeWidth="2" transform="translate(0 1)" />,
  pie: <path d="M8 8 L8 1 A7 7 0 1 1 1.5 11 Z" transform="translate(0.5 0.5)" />,
  scatter: <><circle cx="4" cy="12" r="1.8" /><circle cx="9" cy="6" r="1.8" /><circle cx="13" cy="10" r="1.8" /></>,
  table: <><rect fill="none" height="12" rx="1" strokeWidth="1.5" width="12" x="2" y="2" /><line x1="2" x2="14" y1="6" y2="6" /><line x1="8" x2="8" y1="6" y2="14" /></>,
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
