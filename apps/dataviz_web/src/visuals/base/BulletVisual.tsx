import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';
import { truncateLabel } from './axisDensity';
import {
  ChartFrame,
  EmptyVisual,
  formatAxis,
  niceMax,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

interface BulletRow {
  label: string;
  target: number;
  value: number;
}

const BANDS = [
  { fill: '#e9eef4', share: 0.6 },
  { fill: '#d7e0ea', share: 0.8 },
  { fill: '#c2cfdd', share: 1 },
];

/** Bullet graph (Tableau Show Me): barra de avance + objetivo + bandas cualitativas 60/80/100% de la escala. */
export const BulletVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const categoryRole = getRole(visual, ['category', 'x_axis']);
  const valueRole = getRole(visual, ['value', 'y_axis', 'y']);
  const targetRole = getRole(visual, ['comparison_metric', 'target_metric', 'target']);
  const labels = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const values = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const targets = targetRole && targetRole.ref !== valueRole?.ref
    ? columnForRef(targetRole.ref, targetRole.data)
    : [];

  const rowValue = (column: typeof values, index: number) => {
    const cell = column[index];
    return typeof cell === 'number' && Number.isFinite(cell) ? cell : null;
  };
  const hasCategory = labels.length > 0;
  const rows: BulletRow[] = [];
  const groups = new Map<string, { sum: number; target: number }>();
  for (let index = 0; index < Math.max(values.length, 1); index += 1) {
    const value = rowValue(values, index);
    if (value === null) {continue;}
    if (!hasCategory) {
      rows.push({ label: 'Total', target: rowValue(targets, 0) ?? 0, value });
      break;
    }
    const label = String(labels[index] ?? '');
    if (!label) {continue;}
    const group = groups.get(label) ?? { sum: 0, target: rowValue(targets, index) ?? 0 };
    group.sum += value;
    if (group.target === 0) {group.target = rowValue(targets, index) ?? 0;}
    groups.set(label, group);
  }
  if (hasCategory) {
    for (const [label, group] of groups) {
      rows.push({ label, target: group.target, value: group.sum });
    }
  }

  if (rows.every((row) => row.value <= 0)) {
    return (
      <EmptyVisual className="dv-bullet" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const valueRef = valueRole?.ref ?? '';
  const scaleMax = niceMax(Math.max(...rows.map((row) => Math.max(row.value, row.target)), 0));
  const top = 14;
  const bottom = 26;
  const longest = Math.max(...rows.map((row) => row.label.length), 4);
  const left = Math.min(Math.max(48, Math.min(longest, 16) * 6.4 + 12), frame.width * 0.3);
  const right = 14;
  const innerWidth = frame.width - left - right;
  const band = (frame.height - top - bottom) / rows.length;
  const barHeight = Math.max(4, Math.min(30, band * 0.42));
  const valueAt = (value: number) => left + (Math.max(0, value) / scaleMax) * innerWidth;
  const tickCount = Math.max(2, Math.min(6, Math.floor(innerWidth / 70)));
  const ticks = Array.from({ length: tickCount }, (_, index) => (scaleMax * index) / (tickCount - 1));

  return (
    <ChartFrame
      className="dv-bullet"
      desc={`bullet chart of ${valueRef} against target across ${rows.length} row(s), scale up to ${formatScalar(scaleMax)}.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-bullet-rows': rows.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {ticks.map((tick) => {
        const x = valueAt(tick);
        return (
          <g key={tick}>
            <line x1={x} y1={top} x2={x} y2={frame.height - bottom} stroke="var(--dv-grid, #dde3e9)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
            <text x={x} y={frame.height - 10} textAnchor="middle" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {formatAxis(tick, valueRef)}
            </text>
          </g>
        );
      })}
      {rows.map((row, index) => {
        const y = top + index * band + (band - barHeight) / 2;
        const onTarget = row.target > 0 && row.value >= row.target;
        return (
          <g data-bullet-row={row.label} key={row.label}>
            <text x={left - 8} y={y + barHeight / 2 + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              <title>{row.label}</title>
              {truncateLabel(row.label, 16)}
            </text>
            {BANDS.map((bandStyle) => (
              <rect
                fill={bandStyle.fill}
                height={barHeight}
                key={bandStyle.share}
                rx="1.5"
                width={valueAt(scaleMax * bandStyle.share) - left}
                x={left}
                y={y}
              />
            ))}
            <rect
              className="dv-bullet-bar"
              data-bullet-on-target={onTarget ? 'true' : 'false'}
              fill={onTarget ? '#59a14f' : 'var(--dv-accent, #4e79a7)'}
              height={barHeight * 0.55}
              rx="1.5"
              width={Math.max(1, valueAt(row.value) - left)}
              x={left}
              y={y + barHeight * 0.225}
            >
              <title>{`${row.label}: ${formatAxis(row.value, valueRef)}${row.target > 0 ? ` de objetivo ${formatAxis(row.target, valueRef)} (${Math.round((row.value / row.target) * 100)}%)` : ''}`}</title>
            </rect>
            {row.target > 0 && (
              <line
                className="dv-bullet-target"
                data-bullet-target={row.label}
                stroke="#172536"
                strokeLinecap="round"
                strokeWidth="2.5"
                x1={valueAt(row.target)}
                x2={valueAt(row.target)}
                y1={y - 2}
                y2={y + barHeight + 2}
              />
            )}
            <text fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 9.5 * typeScale)} textAnchor="start" x={valueAt(row.value) + 5} y={y + barHeight / 2 + 3}>
              {formatAxis(row.value, valueRef)}
            </text>
          </g>
        );
      })}
      <line x1={left} y1={frame.height - bottom} x2={frame.width - right} y2={frame.height - bottom} stroke="var(--dv-grid, #c9ced3)" />
    </ChartFrame>
  );
};
