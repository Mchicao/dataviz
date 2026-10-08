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
  seriesColor,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

/** Pareto (composición documentada de Tableau): barras ordenadas descendentes + % acumulado en eje derecho, guía 80%. */
export const ParetoVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const categoryRole = getRole(visual, ['category', 'x_axis']);
  const valueRole = getRole(visual, ['value', 'y_axis', 'y']);
  const labels = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const values = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const entries = labels
    .map((label, index) => ({ label: String(label ?? index), value: typeof values[index] === 'number' ? values[index] as number : 0 }))
    .filter((entry) => entry.value > 0)
    .sort((left, right) => right.value - left.value);

  if (entries.length === 0) {
    return (
      <EmptyVisual className="dv-pareto" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const total = entries.reduce((sum, entry) => sum + entry.value, 0);
  const cumulative = entries.map((_, index) => {
    const partial = entries.slice(0, index + 1).reduce((sum, entry) => sum + entry.value, 0);
    return partial / total;
  });
  const valueRef = valueRole?.ref ?? '';
  const max = niceMax(entries[0]?.value ?? 1);
  const left = Math.min(Math.max(38, formatAxis(max, valueRef).length * 6.2 + 16), frame.width * 0.3);
  const right = 34;
  const top = 16;
  const bottom = 34;
  const innerWidth = frame.width - left - right;
  const innerHeight = frame.height - top - bottom;
  const baseline = frame.height - bottom;
  const barWidth = Math.max(4, Math.min(48, (innerWidth / entries.length) * 0.62));
  const xAt = (index: number) => left + ((index + 0.5) / entries.length) * innerWidth;
  const yBar = (value: number) => baseline - (value / max) * innerHeight;
  const yCum = (share: number) => top + (1 - share) * innerHeight;
  const tickCount = Math.max(2, Math.min(5, Math.floor(innerHeight / 30)));
  const ticks = Array.from({ length: tickCount }, (_, index) => (max * index) / (tickCount - 1));
  const cumTicks = [0, 0.25, 0.5, 0.75, 1];
  const cumPath = cumulative.map((share, index) => `${xAt(index)},${yCum(share)}`).join(' ');

  return (
    <ChartFrame
      className="dv-pareto"
      desc={`pareto of ${valueRef}: ${entries.length} categories sorted by value, cumulative share reaching 100% at ${entries[entries.length - 1]?.label ?? ''}.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-pareto-categories': entries.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {ticks.map((tick) => {
        const y = yBar(tick);
        return (
          <g key={tick}>
            <line x1={left} y1={y} x2={frame.width - right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
            <text x={left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {formatAxis(tick, valueRef)}
            </text>
          </g>
        );
      })}
      {cumTicks.map((tick) => (
        <text key={tick} x={frame.width - right + 6} y={yCum(tick) + 3.5} fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 9.5 * typeScale)}>
          {`${Math.round(tick * 100)}%`}
        </text>
      ))}
      <line data-pareto-guide="80" strokeDasharray="4 3" stroke="#f28e2b" x1={left} x2={frame.width - right} y1={yCum(0.8)} y2={yCum(0.8)} />
      {entries.map((entry, index) => {
        const x = xAt(index);
        const barHeight = baseline - yBar(entry.value);
        return (
          <g key={entry.label}>
            <rect
              className="dv-bar"
              fill="var(--dv-accent, #4e79a7)"
              height={Math.max(1, barHeight)}
              rx="1.5"
              width={barWidth}
              x={x - barWidth / 2}
              y={yBar(entry.value)}
            >
              <title>{`${entry.label}: ${formatAxis(entry.value, valueRef)} · ${Math.round(cumulative[index]! * 100)}% acumulado`}</title>
            </rect>
            <text textAnchor="middle" x={x} y={baseline + 14} fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              <title>{entry.label}</title>
              {truncateLabel(entry.label, 10)}
            </text>
          </g>
        );
      })}
      <polyline className="dv-pareto-line" data-pareto-cumulative="true" fill="none" points={cumPath} stroke={seriesColor(1)} strokeWidth="2" strokeLinejoin="round">
        <title>{`${formatScalar(total)} total · línea de % acumulado`}</title>
      </polyline>
      {cumulative.map((share, index) => (
        <circle cx={xAt(index)} cy={yCum(share)} fill={seriesColor(1)} key={index} r="2.5" />
      ))}
      <line x1={left} y1={baseline} x2={frame.width - right} y2={baseline} stroke="var(--dv-axis-line, #b9c2cb)" />
    </ChartFrame>
  );
};
