import React, { useEffect, useId, useMemo, useState } from 'react';
import type { VisualProps } from './types';
import { columnForRef, getRole, scalarForRef } from './data';
import { displayTitle } from './title';

const COMPACT_SELECT_LIMIT = 80;
const SEARCH_SUGGESTION_LIMIT = 50;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export type SlicerMode = 'select' | 'date' | 'search';

/** Elige un control acotado para evitar miles de opciones DOM en filtros densos. */
export function slicerMode(values: string[]): SlicerMode {
  if (values.length <= COMPACT_SELECT_LIMIT) {return 'select';}
  if (values.every((value) => ISO_DATE.test(value))) {return 'date';}
  return 'search';
}

/** Neutral filter control; query execution remains outside the render plan. */
export const SlicerVisual: React.FC<VisualProps> = ({ visual, filterValue = '', onFilterChange }) => {
  const title = displayTitle(visual.title, visual.name);
  const role = getRole(visual, ['filter_target', 'value']);
  const values = role ? columnForRef(role.ref, role.data) : [];
  const parameterValue = role?.ref.startsWith('parameter:')
    ? scalarForRef(role.ref, role.data)
    : null;
  const effectiveValue = filterValue || (parameterValue === null ? '' : String(parameterValue));
  const options = useMemo(
    () => [...new Set(values.filter((value) => value !== null).map(String))],
    [values],
  );
  const mode = slicerMode(options);
  const enabled = Boolean(onFilterChange && role?.ref.startsWith('field:'));
  const [draft, setDraft] = useState(effectiveValue);
  const listId = useId();

  useEffect(() => setDraft(effectiveValue), [effectiveValue]);

  const suggestions = useMemo(() => {
    if (mode !== 'search') {return [];}
    const needle = draft.trim().toLocaleLowerCase();
    return options
      .filter((value) => !needle || value.toLocaleLowerCase().includes(needle))
      .slice(0, SEARCH_SUGGESTION_LIMIT);
  }, [draft, mode, options]);
  const dateBounds = useMemo(
    () => mode === 'date' ? [...options].sort((left, right) => left.localeCompare(right)) : [],
    [mode, options],
  );

  const commitSearch = () => {
    const value = draft.trim();
    if (!value || options.includes(value)) {onFilterChange?.(visual, value);}
  };

  const unavailableReason = role?.ref.startsWith('field:')
    ? undefined
    : 'Los parámetros requieren un binding de autoría.';

  if (mode === 'date') {
    return (
      <label className="dv-slicer dv-slicer--date" aria-label={title}>
        <span>{title}</span>
        <input
          aria-label={title}
          disabled={!enabled}
          max={dateBounds.at(-1)}
          min={dateBounds[0]}
          onChange={(event) => onFilterChange?.(visual, event.currentTarget.value)}
          title={unavailableReason}
          type="date"
          value={effectiveValue}
        />
      </label>
    );
  }

  if (mode === 'search') {
    return (
      <label className="dv-slicer dv-slicer--search" aria-label={title}>
        <span>{title}</span>
        <input
          aria-label={`${title}. Buscar entre ${options.length} valores`}
          disabled={!enabled}
          list={listId}
          onBlur={commitSearch}
          onChange={(event) => setDraft(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {commitSearch();}
            if (event.key === 'Escape') {setDraft(filterValue);}
          }}
          placeholder={`Buscar entre ${options.length} valores`}
          title={unavailableReason}
          type="search"
          value={draft}
        />
        <datalist id={listId}>
          {suggestions.map((value) => <option key={value} value={value} />)}
        </datalist>
      </label>
    );
  }

  return (
    <label className="dv-slicer" aria-label={title}>
      <span>{title}</span>
      <select
        value={effectiveValue}
        disabled={!enabled}
        onChange={(event) => onFilterChange?.(visual, event.currentTarget.value)}
        title={unavailableReason}
      >
        <option value="">Todos</option>
        {options.map((value) => (
          <option key={value} value={value}>
            {value}
          </option>
        ))}
      </select>
    </label>
  );
};
