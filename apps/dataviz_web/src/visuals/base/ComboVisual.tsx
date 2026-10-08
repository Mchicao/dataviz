import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, getRole } from './data';
import { displayTitle } from './title';
import { planAxisDensity, truncateLabel } from './axisDensity';
import {
  ChartFrame,
  EmptyVisual,
  formatAxis,
  niceMax,
  seriesColor,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

/** Combinado (PBI line and column combo / Tableau dual combination): columnas + línea con eje derecho propio. */
export const ComboVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const categoryRole = getRole(visual, ['category', 'x_axis']);
  const barRole = getRole(visual, ['value', 'y_axis', 'y']);
  const lineRole = getRole(visual, ['comparison_metric', 'comparison', 'value_2', 'y_axis_2']);
  const labels = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const barValues = barRole ? columnForRef(barRole.ref, barRole.data).map((cell) => (typeof cell === 'number' ? cell : 0)) : [];
  const lineValues = lineRole && lineRole.ref !== barRole?.ref
    ? columnForRef(lineRole.ref, lineRole.data).map((cell) => (typeof cell === 'number' ? cell : 0))
    : [];

  const n = Math.max(labels.length, barValues.length, lineValues.length);
  if (n === 0 || barValues.length === 0) {
    return (
      <EmptyVisual className="dv-combo" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const barRef = barRole?.ref ?? '';
  const lineRef = lineRole?.ref ?? '';
  const maxBar = niceMax(Math.max(...barValues, 0));
  const maxLine = niceMax(Math.max(...lineValues, 0));
  const left = Math.min(Math.max(38, formatAxis(maxBar, barRef).length * 6.2 + 16), frame.width * 0.26);
  const right = Math.max(30, formatAxis(maxLine, lineRef).length * 6.2 + 12);
  const top = 16;
  const bottom = 34;
  const innerWidth = frame.width - left - right;
  const innerHeight = frame.height - top - bottom;
  const baseline = frame.height - bottom;
  const barWidth = Math.max(4, Math.min(52, (innerWidth / n) * 0.62));
  const xAt = (index: number) => left + ((index + 0.5) / n) * innerWidth;
  const yBar = (value: number) => baseline - (Math.max(0, value) / maxBar) * innerHeight;
  const yLine = (value: number) => top + (1 - Math.max(0, value) / maxLine) * innerHeight;
  const tickCount = Math.max(2, Math.min(5, Math.floor(innerHeight / 30)));
  const barTicks = Array.from({ length: tickCount }, (_, index) => (maxBar * index) / (tickCount - 1));
  const lineTicks = Array.from({ length: tickCount }, (_, index) => (maxLine * index) / (tickCount - 1));
  const axisPlan = planAxisDensity(labels.map((label) => String(label ?? '')), innerWidth, Math.max(0, Math.min(52, innerHeight * 0.2)));
  const linePath = lineValues.map((value, index) => `${xAt(index)},${yLine(value)}`).join(' ');
  const legend = [
    { color: 'var(--dv-accent, #4e79a7)', name: barRef.replace(/^(measure|field):/, '') },
    ...(lineValues.length > 0 ? [{ color: seriesColor(1), name: lineRef.replace(/^(measure|field):/, '') }] : []),
  ];

  return (
    <ChartFrame
      className="dv-combo"
      desc={`combination chart of ${barRef} as columns and ${lineRef || 'secondary measure'} as a line, ${n} categories, dual y axes.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-combo-categories': n }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {barTicks.map((tick, index) => {
        const y = yBar(tick);
        const lineTick = lineTicks[index] ?? 0;
        return (
          <g key={tick}>
            <line x1={left} y1={y} x2={frame.width - right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
            <text x={left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {formatAxis(tick, barRef)}
            </text>
            {lineValues.length > 0 && (
              <text x={frame.width - right + 6} y={yLine(lineTick) + 3.5} fill={seriesColor(1)} fontSize={Math.max(7, 9.5 * typeScale)}>
                {formatAxis(lineTick, lineRef)}
              </text>
            )}
          </g>
        );
      })}
      {barValues.map((value, index) => (
        <g key={index}>
          <rect
            className="dv-bar"
            fill="var(--dv-accent, #4e79a7)"
            height={Math.max(1, baseline - yBar(value))}
            rx="1.5"
            width={barWidth}
            x={xAt(index) - barWidth / 2}
            y={yBar(value)}
          >
            <title>{`${String(labels[index] ?? index)} · ${barRef}: ${formatAxis(value, barRef)}${lineValues.length > 0 ? ` · ${lineRef}: ${formatAxis(lineValues[index] ?? 0, lineRef)}` : ''}`}</title>
          </rect>
          {index % axisPlan.stride === 0 && (
            <text textAnchor="middle" x={xAt(index)} y={baseline + 14} fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              <title>{String(labels[index] ?? '')}</title>
              {truncateLabel(String(labels[index] ?? ''), axisPlan.mode === 'rotated' ? 14 : axisPlan.maxChars)}
            </text>
          )}
        </g>
      ))}
      {lineValues.length > 0 && (
        <g data-combo-line="true">
          <polyline fill="none" points={linePath} stroke={seriesColor(1)} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          {lineValues.map((value, index) => (
            <circle cx={xAt(index)} cy={yLine(value)} fill={seriesColor(1)} key={index} r="3" />
          ))}
        </g>
      )}
      <line x1={left} y1={baseline} x2={frame.width - right} y2={baseline} stroke="var(--dv-axis-line, #b9c2cb)" />
      <g aria-label="Series legend">
        {legend.map((entry, index) => (
          <g key={entry.name}>
            <rect fill={entry.color} height="9" rx="2" width="9" x={left + index * 150} y={frame.height - 14} />
            <text fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 9.5 * typeScale)} x={left + index * 150 + 14} y={frame.height - 6}>
              {truncateLabel(entry.name, 16)}
            </text>
          </g>
        ))}
      </g>
    </ChartFrame>
  );
};
