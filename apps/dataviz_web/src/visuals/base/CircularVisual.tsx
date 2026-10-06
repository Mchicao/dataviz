import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';

export interface CircularVisualProps extends VisualProps {
  variant: 'pie' | 'donut';
}

/** Native SVG pie/donut renderer for the neutral circular visual intents. */
export const CircularVisual: React.FC<CircularVisualProps> = ({ visual, variant }) => {
  const title = displayTitle(visual.title, visual.name);
  const categoryRole = getRole(visual, ['category', 'x_axis', 'x', 'label', 'color']);
  const valueRole = getRole(visual, ['value', 'y_axis', 'y', 'size', 'x_axis']);
  const labels = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const values = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const slices = values
    .map((value, index) => ({
      label: labels[index] ?? index,
      value: typeof value === 'number' && Number.isFinite(value) ? Math.max(0, value) : 0,
    }))
    .filter((slice) => slice.value > 0);
  const sid = `dv-${  visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  if (slices.length === 0) {
    return (
      <section className="dv-circular dv-circular--empty" role="img" aria-labelledby={`${sid}-title`} style={frameStyle}>
        <span id={`${sid}-title`} style={{ color: '#718096' }}>{title}: no data</span>
      </section>
    );
  }

  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  let cursor = -Math.PI / 2;
  const radius = 72;
  const cx = 100;
  const cy = 100;

  return (
    <section className="dv-circular" style={frameStyle}>
      <svg preserveAspectRatio="xMidYMid meet" role="img" style={{ display: 'block', height: '100%', minHeight: 0, minWidth: 0, width: '100%' }} viewBox="0 0 200 200" aria-labelledby={`${sid}-title ${sid}-desc`}>
        <title id={`${sid}-title`}>{`${title} (${variant})`}</title>
        <desc id={`${sid}-desc`}>{`${variant} chart with ${slices.length} slices totaling ${formatScalar(total)}.`}</desc>
        {slices.map((slice, index) => {
          const start = cursor;
          const end = cursor + (slice.value / total) * Math.PI * 2;
          cursor = end;
          const share = (slice.value / total) * 100;
          return (
            <path
              key={`${String(slice.label)}-${index}`}
              d={arcPath(cx, cy, radius, start, end)}
              fill={sliceColor(index)}
              stroke="#ffffff"
              strokeWidth={1}
              aria-label={`${String(slice.label)}: ${formatScalar(slice.value)}`}
            >
              <title>{`${String(slice.label)}: ${formatScalar(slice.value)} (${share.toFixed(1)}%)`}</title>
            </path>
          );
        })}
        {variant === 'donut' && <circle cx={cx} cy={cy} r={34} fill="#ffffff" />}
      </svg>
    </section>
  );
};

const COLORS = ['#3182ce', '#805ad5', '#38a169', '#dd6b20', '#d53f8c', '#718096'];
const sliceColor = (index: number): string => (
  index === 0 ? 'var(--dv-accent, #3182ce)' : COLORS[index % COLORS.length]
);
const frameStyle: React.CSSProperties = {
  backgroundColor: 'var(--dv-card-bg, #ffffff)',
  border: '1px solid var(--dv-card-border, #d8e0e7)',
  borderRadius: 6,
  boxSizing: 'border-box',
  display: 'flex',
  height: '100%',
  minHeight: 0,
  minWidth: 0,
  overflow: 'hidden',
  padding: 'clamp(0.35rem, 0.9vw, 0.65rem)',
  width: '100%',
};

function point(cx: number, cy: number, radius: number, angle: number): [number, number] {
  return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
}

function arcPath(cx: number, cy: number, radius: number, start: number, end: number): string {
  const [sx, sy] = point(cx, cy, radius, start);
  const [ex, ey] = point(cx, cy, radius, end);
  const largeArc = end - start > Math.PI ? 1 : 0;
  return `M ${cx} ${cy} L ${sx} ${sy} A ${radius} ${radius} 0 ${largeArc} 1 ${ex} ${ey} Z`;
}
