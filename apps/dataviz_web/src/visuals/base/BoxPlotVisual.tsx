import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';
import { truncateLabel } from './axisDensity';
import {
  ChartFrame,
  EmptyVisual,
  formatAxis,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

interface BoxStats {
  label: string;
  max: number;
  median: number;
  min: number;
  outliers: number[];
  q1: number;
  q3: number;
  whiskerHigh: number;
  whiskerLow: number;
}

/** Cuantil por interpolación lineal (mismo convención que NumPy 'linear'). */
function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) {return 0;}
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  const next = sorted[base + 1];
  if (next === undefined) {return sorted[base] ?? 0;}
  return (sorted[base] ?? 0) + rest * (next - (sorted[base] ?? 0));
}

function boxStats(label: string, values: number[]): BoxStats | null {
  const sorted = [...values].sort((left, right) => left - right);
  if (sorted.length === 0) {return null;}
  const q1 = quantile(sorted, 0.25);
  const median = quantile(sorted, 0.5);
  const q3 = quantile(sorted, 0.75);
  const iqr = q3 - q1;
  const lowFence = q1 - 1.5 * iqr;
  const highFence = q3 + 1.5 * iqr;
  const inliers = sorted.filter((value) => value >= lowFence && value <= highFence);
  return {
    label,
    max: sorted[sorted.length - 1] ?? 0,
    median,
    min: sorted[0] ?? 0,
    outliers: sorted.filter((value) => value < lowFence || value > highFence),
    q1,
    q3,
    whiskerHigh: inliers.length > 0 ? inliers[inliers.length - 1] ?? 0 : q3,
    whiskerLow: inliers.length > 0 ? inliers[0] ?? 0 : q1,
  };
}

/** Caja y bigotes (Tableau box-and-whisker): cuartiles por categoría con outliers 1.5×IQR. */
export const BoxPlotVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const categoryRole = getRole(visual, ['category', 'x_axis', 'detail']);
  const valueRole = getRole(visual, ['value', 'y_axis', 'y']);
  const labels = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const column = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const values = column.map((cell) => (typeof cell === 'number' && Number.isFinite(cell) ? cell : null));

  const groups = new Map<string, number[]>();
  const single: number[] = [];
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === null) {continue;}
    const label = labels.length > 0 ? String(labels[index] ?? '') : '';
    if (label) {
      const group = groups.get(label) ?? [];
      group.push(value);
      groups.set(label, group);
    } else {
      single.push(value);
    }
  }
  const stats = labels.length > 0
    ? [...groups.entries()].map(([label, groupValues]) => boxStats(label, groupValues))
    : [boxStats('', single)];
  const boxes = stats.filter((box): box is BoxStats => box !== null);

  if (boxes.length === 0) {
    return (
      <EmptyVisual className="dv-boxplot" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const valueRef = valueRole?.ref ?? '';
  const allValues = boxes.flatMap((box) => [...box.outliers, box.whiskerLow, box.whiskerHigh]);
  const yMin = Math.min(...allValues);
  const yMax = Math.max(...allValues);
  const span = Math.max(yMax - yMin, 1);
  const left = Math.min(Math.max(48, formatAxis(yMax, valueRef).length * 6.2 + 16), frame.width * 0.3);
  const right = 14;
  const top = 16;
  const bottom = 34;
  const innerWidth = frame.width - left - right;
  const innerHeight = frame.height - top - bottom;
  const baseline = frame.height - bottom;
  const yAt = (value: number) => top + ((yMax - value) / span) * innerHeight;
  const bandWidth = innerWidth / boxes.length;
  const boxWidth = Math.max(8, Math.min(64, bandWidth * 0.5));
  const tickCount = Math.max(2, Math.min(6, Math.floor(innerHeight / 26)));
  const ticks = Array.from({ length: tickCount }, (_, index) => yMin + (span * index) / (tickCount - 1));

  return (
    <ChartFrame
      className="dv-boxplot"
      desc={`box plot of ${valueRef} across ${boxes.length} group(s), overall range ${formatScalar(yMin)} to ${formatScalar(yMax)}; whiskers at 1.5x IQR.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-boxplot-groups': boxes.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {ticks.map((tick) => {
        const y = yAt(tick);
        return (
          <g key={tick}>
            <line x1={left} y1={y} x2={frame.width - right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray="3 3" />
            <text x={left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {formatAxis(tick, valueRef)}
            </text>
          </g>
        );
      })}
      {boxes.map((box, index) => {
        const cx = left + index * bandWidth + bandWidth / 2;
        const label = box.label || 'Total';
        return (
          <g data-boxplot-group={label} key={label}>
            <line stroke="var(--dv-axis-text, #6b7784)" strokeWidth="1.2" x1={cx} x2={cx} y1={yAt(box.whiskerLow)} y2={yAt(box.q1)} />
            <line stroke="var(--dv-axis-text, #6b7784)" strokeWidth="1.2" x1={cx} x2={cx} y1={yAt(box.q3)} y2={yAt(box.whiskerHigh)} />
            <line stroke="var(--dv-axis-text, #6b7784)" strokeWidth="1.2" x1={cx - boxWidth * 0.22} x2={cx + boxWidth * 0.22} y1={yAt(box.whiskerLow)} y2={yAt(box.whiskerLow)} />
            <line stroke="var(--dv-axis-text, #6b7784)" strokeWidth="1.2" x1={cx - boxWidth * 0.22} x2={cx + boxWidth * 0.22} y1={yAt(box.whiskerHigh)} y2={yAt(box.whiskerHigh)} />
            <rect
              className="dv-boxplot-box"
              fill="var(--dv-accent, #4e79a7)"
              fillOpacity="0.55"
              height={Math.max(2, Math.abs(yAt(box.q1) - yAt(box.q3)))}
              rx="2"
              width={boxWidth}
              x={cx - boxWidth / 2}
              y={Math.min(yAt(box.q1), yAt(box.q3))}
            >
              <title>{`${label}: mediana ${formatScalar(box.median)}, IQR ${formatScalar(box.q1)}–${formatScalar(box.q3)}, bigotes ${formatScalar(box.whiskerLow)}–${formatScalar(box.whiskerHigh)}, outliers ${box.outliers.length}`}</title>
            </rect>
            <line data-boxplot-median={label} stroke="#172536" strokeWidth="2" x1={cx - boxWidth / 2} x2={cx + boxWidth / 2} y1={yAt(box.median)} y2={yAt(box.median)} />
            {box.outliers.map((outlier, outlierIndex) => (
              <circle className="dv-boxplot-outlier" cx={cx} cy={yAt(outlier)} fill="none" key={outlierIndex} r="2.5" stroke="#e15759" strokeWidth="1.2">
                <title>{`${label}: outlier ${formatScalar(outlier)}`}</title>
              </circle>
            ))}
            <text textAnchor="middle" x={cx} y={baseline + 14} fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              <title>{label}</title>
              {truncateLabel(label, 12)}
            </text>
          </g>
        );
      })}
      <line x1={left} y1={baseline} x2={frame.width - right} y2={baseline} stroke="var(--dv-axis-line, #b9c2cb)" />
    </ChartFrame>
  );
};
