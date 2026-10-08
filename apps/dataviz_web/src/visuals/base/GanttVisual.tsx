import React from 'react';
import type { Scalar } from '../../runtime/types';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';
import { truncateLabel } from './axisDensity';
import {
  ChartFrame,
  EmptyVisual,
  compactCategoryLabel,
  numericOf,
  seriesColor,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

interface Bar {
  category: string;
  end: number;
  label: string;
  seriesIndex: number;
  start: number;
  value: number;
}

/** Milisegundos de un escalar de fecha; null si no es parseable como fecha. */
function timeOf(value: Scalar): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {return value;}
  if (typeof value !== 'string') {return null;}
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

function formatTick(ms: number): string {
  return compactCategoryLabel(new Date(ms).toISOString().slice(0, 10)) as string;
}

/**
 * Gantt (Tableau Gantt Bars): barra por fila desde la fecha de inicio con largo
 * proporcional a la duración. La duración es numérica en la unidad que traiga el
 * campo (días por convención); la interpretación vive en el tooltip.
 */
export const GanttVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const categoryRole = getRole(visual, ['category', 'y_axis', 'row']);
  const startRole = getRole(visual, ['x_axis', 'start', 'date', 'category_2']);
  const durationRole = getRole(visual, ['value', 'duration', 'size']);
  const seriesRole = getRole(visual, ['series', 'color']);
  const categories = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const starts = startRole ? columnForRef(startRole.ref, startRole.data) : [];
  const durations = durationRole ? columnForRef(durationRole.ref, durationRole.data) : [];
  const series = seriesRole ? columnForRef(seriesRole.ref, seriesRole.data) : [];

  const seriesNames = [...new Set(series.map((entry) => String(entry ?? '')).filter((name) => name.trim().length > 0))].sort();
  const rows = new Map<string, Bar[]>();
  const n = Math.max(categories.length, starts.length, durations.length);
  let parsedBars = 0;
  for (let index = 0; index < n; index += 1) {
    const start = timeOf(starts[index] ?? null);
    if (start === null) {continue;}
    const duration = numericOf(durations[index] ?? 0);
    const category = String(categories[index] ?? '');
    const rowKey = category || String(starts[index] ?? `fila ${index}`);
    parsedBars += 1;
    const bar: Bar = {
      category: rowKey,
      end: start + Math.max(duration, 0),
      label: rowKey,
      seriesIndex: Math.max(0, seriesNames.indexOf(String(series[index] ?? ''))),
      start,
      value: duration,
    };
    const row = rows.get(rowKey) ?? [];
    row.push(bar);
    rows.set(rowKey, row);
  }

  if (parsedBars === 0 || rows.size === 0) {
    return (
      <EmptyVisual className="dv-gantt" reason="sin fechas válidas" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const allBars = [...rows.values()].flat();
  const minTime = Math.min(...allBars.map((bar) => bar.start));
  const maxTime = Math.max(...allBars.map((bar) => bar.end));
  const span = Math.max(maxTime - minTime, 1);
  const orderedRows = [...rows.keys()];
  const top = 16;
  const bottom = 26;
  const left = Math.min(Math.max(48, Math.max(...orderedRows.map((row) => row.length), 4) * 6.4 + 12), frame.width * 0.28);
  const right = 14;
  const innerWidth = frame.width - left - right;
  const band = (frame.height - top - bottom) / Math.max(orderedRows.length, 1);
  const barHeight = Math.max(4, Math.min(26, band * 0.5));
  const xAt = (time: number) => left + ((time - minTime) / span) * innerWidth;
  const tickCount = Math.max(2, Math.min(6, Math.floor(innerWidth / 80)));
  const ticks = Array.from({ length: tickCount }, (_, index) => minTime + (span * index) / (tickCount - 1));

  return (
    <ChartFrame
      className="dv-gantt"
      desc={`gantt of ${durationRole?.ref ?? 'duration'} from ${startRole?.ref ?? 'start'}: ${parsedBars} bars across ${orderedRows.length} rows, ${formatScalar(Math.round(span / 86_400_000))} days span.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-gantt-bars': parsedBars, 'data-gantt-rows': orderedRows.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {ticks.map((tick) => (
        <g key={tick}>
          <line x1={xAt(tick)} y1={top} x2={xAt(tick)} y2={frame.height - bottom} stroke="var(--dv-grid, #dde3e9)" strokeDasharray={tick === minTime ? undefined : '3 3'} />
          <text x={xAt(tick)} y={frame.height - 10} textAnchor="middle" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
            {formatTick(tick)}
          </text>
        </g>
      ))}
      {orderedRows.map((row, rowIndex) => (
        <g key={row}>
          <text x={left - 8} y={top + rowIndex * band + band / 2 + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
            <title>{row}</title>
            {truncateLabel(row, 16)}
          </text>
          {(rows.get(row) ?? []).map((bar, barIndex) => {
            const y = top + rowIndex * band + (band - barHeight) / 2 + barIndex * (barHeight + 2);
            const barStart = xAt(bar.start);
            const barEnd = xAt(bar.end);
            return (
              <rect
                className="dv-gantt-bar"
                data-gantt-row={row}
                fill={seriesColor(bar.seriesIndex)}
                height={barHeight}
                key={`${row}-${barIndex}`}
                rx="2"
                width={Math.max(2, barEnd - barStart)}
                x={barStart}
                y={y}
              >
                <title>{`${row}: inicio ${formatTick(bar.start)}, duración ${formatScalar(Math.round(bar.value))}`}</title>
              </rect>
            );
          })}
        </g>
      ))}
      <line x1={left} y1={frame.height - bottom} x2={frame.width - right} y2={frame.height - bottom} stroke="var(--dv-grid, #c9ced3)" />
    </ChartFrame>
  );
};
