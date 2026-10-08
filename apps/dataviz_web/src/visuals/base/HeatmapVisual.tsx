import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';
import { truncateLabel } from './axisDensity';
import {
  ChartFrame,
  EmptyVisual,
  formatAxis,
  interpolateColor,
  numericOf,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

/** Tabla de calor (Tableau highlight table / PBI matriz con escala de color): fila × columna con celdas agregadas. */
export const HeatmapVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const rowRole = getRole(visual, ['row', 'category', 'y_axis']);
  const columnRole = getRole(visual, ['column', 'series', 'x_axis', 'color']);
  const valueRole = getRole(visual, ['value', 'y', 'size']);
  const rows = rowRole ? columnForRef(rowRole.ref, rowRole.data) : [];
  const cols = columnRole ? columnForRef(columnRole.ref, columnRole.data) : [];
  const vals = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];

  const totals = new Map<string, number>();
  const n = Math.max(rows.length, cols.length, vals.length);
  for (let index = 0; index < n; index += 1) {
    const row = String(rows[index] ?? '');
    const col = String(cols[index] ?? '');
    const value = numericOf(vals[index] ?? 0);
    if (!row || !col) {continue;}
    const key = `${row}\u0000${col}`;
    const existing = totals.get(key) ?? 0;
    totals.set(key, existing + value);
  }
  const rowKeys = [...new Set(rows.map((row) => String(row ?? '')).filter(Boolean))];
  const colKeys = [...new Set(cols.map((col) => String(col ?? '')).filter(Boolean))];
  const rowTotals = new Map(rowKeys.map((rowKey) => [
    rowKey,
    colKeys.reduce((sum, colKey) => sum + (totals.get(`${rowKey}\u0000${colKey}`) ?? 0), 0),
  ]));
  const colTotals = new Map(colKeys.map((colKey) => [
    colKey,
    rowKeys.reduce((sum, rowKey) => sum + (totals.get(`${rowKey}\u0000${colKey}`) ?? 0), 0),
  ]));
  const orderedRows = [...rowKeys].sort((left, right) => (rowTotals.get(right) ?? 0) - (rowTotals.get(left) ?? 0));
  const orderedCols = [...colKeys].sort((left, right) => (colTotals.get(right) ?? 0) - (colTotals.get(left) ?? 0));
  const aggregated = orderedRows.flatMap((rowKey, rowIndex) => orderedCols.map((colKey, colIndex) => ({
    col: colIndex,
    key: `${rowKey}\u0000${colKey}`,
    row: rowIndex,
    rowKey,
    colKey,
    value: totals.get(`${rowKey}\u0000${colKey}`) ?? 0,
  })));
  const maxValue = Math.max(...aggregated.map((cell) => cell.value), 0);

  if (aggregated.length === 0 || maxValue <= 0) {
    return (
      <EmptyVisual className="dv-heatmap" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const valueRef = valueRole?.ref ?? '';
  const left = Math.min(
    Math.max(48, Math.max(...orderedRows.map((rowKey) => rowKey.length), 4) * 6.4 + 12),
    frame.width * 0.3,
  );
  const top = 26;
  const right = 12;
  const bottom = 18;
  const cellWidth = (frame.width - left - right) / Math.max(orderedCols.length, 1);
  const cellHeight = (frame.height - top - bottom) / Math.max(orderedRows.length, 1);
  const showCellText = cellWidth > 34 && cellHeight > 14;

  return (
    <ChartFrame
      className="dv-heatmap"
      desc={`heatmap of ${valueRef} by ${rowRole?.ref ?? 'row'} and ${columnRole?.ref ?? 'column'}: ${orderedRows.length} rows x ${orderedCols.length} columns, max ${formatScalar(maxValue)}.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-heatmap-cells': aggregated.length, 'data-heatmap-cols': orderedCols.length, 'data-heatmap-rows': orderedRows.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {orderedCols.map((colKey, colIndex) => (
        <text
          key={colKey}
          textAnchor="end"
          transform={`rotate(38 ${left + colIndex * cellWidth + cellWidth / 2} ${top - 8})`}
          x={left + colIndex * cellWidth + cellWidth / 2}
          y={top - 8}
          fill="var(--dv-axis-text, #6b7784)"
          fontSize={Math.max(7, 10 * typeScale)}
        >
          <title>{colKey}</title>
          {truncateLabel(colKey, 14)}
        </text>
      ))}
      {orderedRows.map((rowKey, rowIndex) => (
        <text
          key={rowKey}
          textAnchor="end"
          x={left - 6}
          y={top + rowIndex * cellHeight + cellHeight / 2 + 3.5}
          fill="var(--dv-axis-text, #6b7784)"
          fontSize={Math.max(7, 10 * typeScale)}
        >
          <title>{rowKey}</title>
          {truncateLabel(rowKey, 14)}
        </text>
      ))}
      {aggregated.map((cell) => {
        const x = left + cell.col * cellWidth;
        const y = top + cell.row * cellHeight;
        const share = cell.value / maxValue;
        const key = `${cell.rowKey}:${cell.colKey}`;
        return (
          <g key={key}>
            <rect
              className="dv-heatmap-cell"
              data-heatmap-cell={key}
              fill={interpolateColor(share, '#f4f8fc', '#2f5e8f')}
              height={Math.max(1, cellHeight - 1)}
              rx="1.5"
              width={Math.max(1, cellWidth - 1)}
              x={x}
              y={y}
            >
              <title>{`${cell.rowKey} / ${cell.colKey}: ${formatAxis(cell.value, valueRef)}`}</title>
            </rect>
            {showCellText && (
              <text
                fill={share > 0.55 ? '#ffffff' : 'var(--dv-axis-text, #37474f)'}
                fontSize={Math.max(7, 9.5 * typeScale)}
                textAnchor="middle"
                x={x + cellWidth / 2}
                y={y + cellHeight / 2 + 3}
              >
                {formatAxis(cell.value, valueRef)}
              </text>
            )}
          </g>
        );
      })}
    </ChartFrame>
  );
};
