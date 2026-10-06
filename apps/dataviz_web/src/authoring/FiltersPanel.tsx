import React, { useState } from 'react';
import type { Filter, SemanticFilterOperator, SemanticModel } from './types';

interface FiltersPanelProps {
  model: SemanticModel;
  selectedVisualId: string | null;
  activePageId: string | null;
  canAuthor: boolean;
  onAddFilter: (filter: Filter) => void;
  onRemoveFilter: (name: string) => void;
  onUpdateFilter: (filter: Filter) => void;
}

const OPERATORS: readonly SemanticFilterOperator[] = ['eq', 'ne', 'contains', 'starts_with', 'gt', 'lt'];

const SECTION_HEADINGS = {
  page: 'Filtros de esta página',
  report: 'Filtros de todas las páginas',
  visual: 'Filtros de este visual',
} as const;

type Scope = keyof typeof SECTION_HEADINGS;

const scopePrefix: Record<Scope, string | null> = { page: 'page', report: null, visual: 'visual' };

/** Filtros cuyo alcance coincide con la sección (report = sin applies_to). */
const filtersForScope = (filters: readonly Filter[], scope: Scope, scopeId: string | null): Filter[] => {
  if (scope === 'report') {
    return filters.filter((filter) => !filter.applies_to || filter.applies_to.length === 0);
  }
  const key = `${scopePrefix[scope]}:${scopeId}`;
  return filters.filter((filter) => (filter.applies_to ?? []).includes(key));
};

/**
 * Panel de filtros con alcance visual/página/report y permisos por filtro:
 * el ojo decide si el visualizador lo ve y el candado si puede modificarlo.
 */
export const FiltersPanel: React.FC<FiltersPanelProps> = ({
  model,
  selectedVisualId,
  activePageId,
  canAuthor,
  onAddFilter,
  onRemoveFilter,
  onUpdateFilter,
}) => {
  const [fieldKey, setFieldKey] = useState('sales.Region');
  const [operator, setOperator] = useState<SemanticFilterOperator>('eq');
  const [value, setValue] = useState('');

  const fields = model.entities.flatMap((entity) => entity.fields.map((field) => ({
    entity: entity.name,
    key: `${entity.name}.${field.name}`,
    label: field.name,
    name: field.name,
  })));
  const currentField = fields.find((field) => field.key === fieldKey) ?? fields[0];
  const scopeIds: Record<Scope, string | null> = { page: activePageId, report: null, visual: selectedVisualId };
  const takenNames = new Set(model.filters.map((filter) => filter.name));
  const addFilter = (scope: Scope) => {
    if (!currentField || !canAuthor) {return;}
    const prefix = scopePrefix[scope];
    const scopeId = scopeIds[scope];
    const base = `${currentField.name}_${operator}_${scope}`;
    let name = base;
    let counter = 2;
    while (takenNames.has(name)) {name = `${base}_${counter++}`;}
    const filter: Filter = {
      applies_to: prefix && scopeId ? [`${prefix}:${scopeId}`] : [],
      name,
      operator,
      target: { entity: currentField.entity, kind: 'field_ref', name: currentField.name },
      values: value.trim() === '' ? [] : [value.trim()],
    };
    onAddFilter(filter);
    setValue('');
  };
  const toggle = (filter: Filter, flag: 'viewer_hidden' | 'viewer_locked') => {
    onUpdateFilter({ ...filter, [flag]: !filter[flag] });
  };

  return (
    <div className="dv-filters-panel">
      <div className="dv-filter-form">
        <select aria-label="Campo del filtro" disabled={!canAuthor} onChange={(event) => setFieldKey(event.target.value)} value={currentField?.key ?? ''}>
          {model.entities.map((entity) => (
            <optgroup key={entity.name} label={entity.name}>
              {entity.fields.map((field) => (
                <option key={`${entity.name}.${field.name}`} value={`${entity.name}.${field.name}`}>{field.name}</option>
              ))}
            </optgroup>
          ))}
        </select>
        <div className="dv-filter-form-row">
          <select aria-label="Operador del filtro" disabled={!canAuthor} onChange={(event) => setOperator(event.target.value as SemanticFilterOperator)} value={operator}>
            {OPERATORS.map((op) => <option key={op} value={op}>{op}</option>)}
          </select>
          <input
            aria-label="Valor del filtro"
            disabled={!canAuthor}
            onChange={(event) => setValue(event.target.value)}
            placeholder="Valor"
            type="text"
            value={value}
          />
        </div>
      </div>
      {(Object.keys(SECTION_HEADINGS) as Scope[]).map((scope) => {
        const scopeId = scopeIds[scope];
        const unavailable = scope !== 'report' && !scopeId;
        const scopedFilters = unavailable ? [] : filtersForScope(model.filters, scope, scopeId);
        return (
          <details className="dv-filter-scope" key={scope} open>
            <summary>{SECTION_HEADINGS[scope]} <span>{scopedFilters.length}</span></summary>
            {unavailable ? (
              <p className="dv-filter-empty">Sin {scope === 'visual' ? 'visual' : 'página'} activo.</p>
            ) : (
              <ul className="dv-filter-list">
                {scopedFilters.length === 0 && <li className="dv-filter-empty">Sin filtros.</li>}
                {scopedFilters.map((filter) => (
                  <li className="dv-filter-row" key={filter.name}>
                    <span className="dv-filter-name" title={filter.name}>{filter.name}</span>
                    <button
                      aria-label={`Visibilidad del filtro ${filter.name}`}
                      aria-pressed={!filter.viewer_hidden}
                      className={`dv-filter-toggle${filter.viewer_hidden ? ' is-off' : ''}`}
                      disabled={!canAuthor}
                      onClick={() => toggle(filter, 'viewer_hidden')}
                      title={filter.viewer_hidden ? 'Oculto para visualizadores' : 'Visible para visualizadores'}
                      type="button"
                    >{filter.viewer_hidden ? '◌' : '👁'}</button>
                    <button
                      aria-label={`Bloqueo del filtro ${filter.name}`}
                      aria-pressed={!!filter.viewer_locked}
                      className={`dv-filter-toggle${filter.viewer_locked ? ' is-locked' : ''}`}
                      disabled={!canAuthor}
                      onClick={() => toggle(filter, 'viewer_locked')}
                      title={filter.viewer_locked ? 'Bloqueado para visualizadores' : 'Editable por visualizadores'}
                      type="button"
                    >{filter.viewer_locked ? '🔒' : '🔓'}</button>
                    <button
                      aria-label={`Eliminar filtro ${filter.name}`}
                      className="dv-filter-remove"
                      disabled={!canAuthor}
                      onClick={() => onRemoveFilter(filter.name)}
                      title="Eliminar filtro"
                      type="button"
                    >×</button>
                  </li>
                ))}
              </ul>
            )}
            <button
              className="dv-filter-add"
              disabled={!canAuthor || !currentField || unavailable}
              onClick={() => addFilter(scope)}
              type="button"
            >Agregar campos de datos…</button>
          </details>
        );
      })}
    </div>
  );
};
