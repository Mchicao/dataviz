import React from 'react';
import type { VisualProps } from './types';
import { columnForRef } from './data';
import { displayTitle, friendlyRoleName } from './title';

const DISPLAY_ROW_LIMIT = 100;

/**
 * Table visual: renders one column per data role, aligned into rows.
 *
 * Each role contributes a column whose values come from its resolved query
 * result (`columnForRef`). Columns are padded to the longest column so partial
 * results still align. Accessibility: a real `<table>` with `<caption>`,
 * `<thead>` and `<th scope="col">` header cells.
 */
export const TableVisual: React.FC<VisualProps> = ({ visual, selectedFieldRef, selectedValue, onDataSelect }) => {
  const title = displayTitle(visual.title, visual.name);
  const cols = Object.entries(visual.roles).map(([roleName, role]) => ({
    key: roleName,
    name: friendlyRoleName(roleName),
    ref: role.ref,
    values: columnForRef(role.ref, role.data),
  }));
  const zoneHeight = Number(visual.geometry.height ?? Number.POSITIVE_INFINITY);
  if (zoneHeight < 32) {
    return <div className="dv-table dv-table--compact" role="note" style={compactLabelStyle}>{title}</div>;
  }
  if (zoneHeight < 80) {
    const values = cols
      .map((column) => column.values.find((value) => value !== null && value !== undefined))
      .filter((value): value is string | number => value !== undefined && String(value) !== title)
      .slice(0, 2);
    return (
      <div
        className="dv-table dv-table--compact dv-table--metric"
        role="figure"
        aria-label={`${title}: ${values.map(formatCell).join(', ')}`}
        style={compactMetricStyle}
      >
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</span>
        {values.map((value, index) => (
          <strong key={index} style={{ flexShrink: 0, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
            {formatCell(value)}
          </strong>
        ))}
      </div>
    );
  }

  const selectableColumn = onDataSelect
    ? cols.find((column) => column.ref.startsWith('field:') && ['label', 'category', 'detail', 'y_axis'].includes(column.key))
      ?? cols.find((column) => column.ref.startsWith('field:'))
    : undefined;
  const maxLen = cols.reduce((m, c) => Math.max(m, c.values.length), 0);
  const rowCount = maxLen;
  const displayedRowCount = Math.min(rowCount, DISPLAY_ROW_LIMIT);

  return (
    <figure
      className="dv-table"
      style={tableFrameStyle}
    >
      <table style={tableStyle}>
        <caption style={captionStyle}>
          {title}
        </caption>
        {cols.length === 0 ? (
          <tbody>
            <tr>
              <td>No data roles bound.</td>
            </tr>
          </tbody>
        ) : (
          <>
            <thead>
              <tr>
                {cols.map((c) => (
                  <th
                    key={c.key}
                    scope="col"
                    style={headerCellStyle}
                  >
                    {c.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rowCount === 0 ? (
                <tr>
                  {cols.map((c) => (
                    <td key={c.key} style={cellStyle}>
                      {'\u2014'}
                    </td>
                  ))}
                </tr>
              ) : (
                Array.from({ length: displayedRowCount }, (_, i) => {
                  const selectedColumn = selectedFieldRef
                    ? cols.find((column) => column.ref === selectedFieldRef)
                    : undefined;
                  const isSelected = selectedColumn && selectedValue !== undefined
                    ? String(selectedColumn.values[i]) === String(selectedValue)
                    : false;
                  const selectableValue = selectableColumn?.values[i];
                  const activate = selectableColumn && selectableValue !== null && selectableValue !== undefined
                    ? () => onDataSelect?.(visual, selectableColumn.ref, selectableValue as string | number, i)
                    : undefined;
                  return (
                  <tr
                    aria-selected={isSelected || undefined}
                    className={isSelected ? 'dv-table-row--selected' : undefined}
                    data-selectable={activate ? 'true' : undefined}
                    key={i}
                    onClick={activate}
                    onKeyDown={activate ? (event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        activate();
                      }
                    } : undefined}
                    tabIndex={activate ? 0 : undefined}
                  >
                    {cols.map((c) => (
                      <td key={c.key} style={cellStyle}>
                        {formatCell(c.values[i])}
                      </td>
                    ))}
                  </tr>
                  );
                })
              )}
            </tbody>
          </>
        )}
      </table>
      {rowCount > displayedRowCount && (
        <figcaption style={footerStyle}>
          {`Showing ${displayedRowCount.toLocaleString('en-US')} of ${rowCount.toLocaleString('en-US')} rows`}
        </figcaption>
      )}
    </figure>
  );
};

const compactLabelStyle: React.CSSProperties = {
  alignItems: 'center',
  boxSizing: 'border-box',
  display: 'flex',
  fontSize: 'clamp(0.66rem, 0.62rem + 0.16vw, 0.76rem)',
  height: '100%',
  lineHeight: 1.2,
  minWidth: 0,
  overflow: 'hidden',
  padding: 'clamp(0.12rem, 0.4vw, 0.2rem) clamp(0.3rem, 0.8vw, 0.45rem)',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
  width: '100%',
};

const compactMetricStyle: React.CSSProperties = {
  alignItems: 'baseline',
  boxSizing: 'border-box',
  display: 'flex',
  fontSize: 'clamp(0.68rem, 0.64rem + 0.18vw, 0.8rem)',
  gap: 'clamp(0.35rem, 1vw, 0.75rem)',
  height: '100%',
  justifyContent: 'space-between',
  lineHeight: 1.15,
  minWidth: 0,
  overflow: 'hidden',
  padding: 'clamp(0.25rem, 0.8vw, 0.5rem)',
  width: '100%',
};

const tableFrameStyle: React.CSSProperties = {
  backgroundColor: 'var(--dv-card-bg, #ffffff)',
  border: '1px solid var(--dv-card-border, #d8e0e7)',
  borderRadius: 6,
  boxSizing: 'border-box',
  fontSize: 'clamp(0.68rem, 0.63rem + 0.18vw, 0.8rem)',
  height: '100%',
  margin: 0,
  minHeight: 0,
  minWidth: 0,
  overflow: 'auto',
  overscrollBehavior: 'contain',
  padding: 'clamp(0.3rem, 0.7vw, 0.55rem)',
  width: '100%',
};

const tableStyle: React.CSSProperties = {
  borderCollapse: 'collapse',
  fontVariantNumeric: 'tabular-nums',
  minWidth: '100%',
  tableLayout: 'auto',
  width: 'max-content',
};

const captionStyle: React.CSSProperties = {
  fontSize: 'clamp(0.72rem, 0.67rem + 0.18vw, 0.84rem)',
  fontWeight: 600,
  lineHeight: 1.2,
  overflow: 'hidden',
  padding: '0 clamp(0.25rem, 0.6vw, 0.4rem) clamp(0.3rem, 0.7vw, 0.45rem)',
  textAlign: 'left',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

const headerCellStyle: React.CSSProperties = {
  borderBottom: '1px solid var(--dv-card-border, #d8e0e7)',
  lineHeight: 1.2,
  padding: 'clamp(0.28rem, 0.65vw, 0.4rem) clamp(0.35rem, 0.8vw, 0.5rem)',
  textAlign: 'left',
  verticalAlign: 'middle',
  whiteSpace: 'nowrap',
};

const cellStyle: React.CSSProperties = {
  fontVariantNumeric: 'tabular-nums',
  lineHeight: 1.25,
  maxWidth: 'clamp(8rem, 24vw, 18rem)',
  overflow: 'hidden',
  padding: 'clamp(0.25rem, 0.55vw, 0.35rem) clamp(0.35rem, 0.8vw, 0.5rem)',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
};

const footerStyle: React.CSSProperties = {
  color: '#78889a',
  fontSize: 'clamp(0.64rem, 0.61rem + 0.12vw, 0.72rem)',
  padding: 'clamp(0.3rem, 0.7vw, 0.45rem) clamp(0.25rem, 0.6vw, 0.4rem) 0',
};

/** Render a single cell: missing -> em-dash, number -> locale, else string. */
function formatCell(value: ReturnType<typeof columnForRef>[number]): string {
  if (value === null || value === undefined) {return '\u2014';}
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value.toLocaleString('en-US') : String(value);
  }
  return String(value);
}
