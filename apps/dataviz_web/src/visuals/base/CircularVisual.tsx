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
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const properties = (visual as { properties?: Record<string, unknown> }).properties ?? {};
  // Si no está explícitamente en false, se muestran por defecto estilo Power BI
  const showDataLabels = properties.show_data_labels !== false;
  const showCategoryLabels = properties.show_category_labels !== false;

  if (slices.length === 0) {
    return (
      <section className="dv-circular dv-circular--empty" role="img" aria-labelledby={`${sid}-title`} style={frameStyle}>
        <span id={`${sid}-title`} style={{ color: '#718096' }}>{title}: no data</span>
      </section>
    );
  }

  const total = slices.reduce((sum, slice) => sum + slice.value, 0);
  const centerLabel = total >= 10_000
    ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, notation: 'compact' }).format(total)
    : formatScalar(total);
  let cursor = -Math.PI / 2;
  const radius = 72;
  const cx = 100;
  const cy = 100;
  const donutHole = 34;

  return (
    <section className="dv-circular" style={frameStyle}>
      {showCategoryLabels && (
        <div
          className="dv-circular-legend"
          data-testid="circular-legend"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '4px 10px',
            justifyContent: 'center',
            alignItems: 'center',
            marginBottom: '4px',
            fontSize: '11px',
            color: 'var(--dv-axis-text, #4b5563)',
            flexShrink: 0,
            lineHeight: 1.2,
          }}
        >
          {slices.map((slice, index) => {
            const share = total > 0 ? (slice.value / total) * 100 : 0;
            const compactValue = slice.value >= 10_000
              ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, notation: 'compact' }).format(slice.value)
              : formatScalar(slice.value);
            return (
              <span
                key={`${String(slice.label)}-legend-${index}`}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap' }}
                title={`${String(slice.label)}: ${formatScalar(slice.value)} (${share.toFixed(1)}%)`}
              >
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    backgroundColor: sliceColor(index),
                    flexShrink: 0,
                  }}
                />
                <span style={{ fontWeight: 550, color: 'var(--dv-text, #172536)' }}>{String(slice.label)}</span>
                {showDataLabels && <span style={{ opacity: 0.8, fontSize: '10px' }}>(${compactValue} · ${share.toFixed(0)}%)</span>}
              </span>
            );
          })}
        </div>
      )}
      <div style={{ flex: '1 1 auto', minHeight: 0, minWidth: 0, width: '100%', position: 'relative' }}>
        <svg
          preserveAspectRatio="xMidYMid meet"
          role="img"
          style={{ display: 'block', height: '100%', minHeight: 0, minWidth: 0, width: '100%' }}
          viewBox="0 0 200 200"
          aria-labelledby={`${sid}-title ${sid}-desc`}
        >
          <title id={`${sid}-title`}>{`${title} (${variant})`}</title>
          <desc id={`${sid}-desc`}>{`${variant} chart with ${slices.length} slices totaling ${formatScalar(total)}.`}</desc>
          {slices.map((slice, index) => {
            const start = cursor;
            const end = cursor + (slice.value / total) * Math.PI * 2;
            cursor = end;
            const share = (slice.value / total) * 100;
            const mid = (start + end) / 2;
            const labelRadius = variant === 'donut' ? (radius + donutHole) / 2 : radius * 0.65;
            const [lx, ly] = point(cx, cy, labelRadius, mid);

            return (
              <g key={`${String(slice.label)}-${index}`}>
                <path
                  d={arcPath(cx, cy, radius, start, end)}
                  fill={sliceColor(index)}
                  stroke="#ffffff"
                  strokeWidth={1}
                  aria-label={`${String(slice.label)}: ${formatScalar(slice.value)}`}
                >
                  <title>{`${String(slice.label)}: ${formatScalar(slice.value)} (${share.toFixed(1)}%)`}</title>
                </path>
                {showDataLabels && share >= 6 && (
                  <text
                    x={lx}
                    y={ly}
                    dominantBaseline="central"
                    fill="#ffffff"
                    fontSize={share >= 15 ? '8.5' : '7.5'}
                    fontWeight="700"
                    style={{ pointerEvents: 'none', textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}
                    textAnchor="middle"
                  >
                    {share >= 16
                      ? `${slice.value >= 10_000 ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 1, notation: 'compact' }).format(slice.value) : formatScalar(slice.value)} (${share.toFixed(0)}%)`
                      : `${share.toFixed(0)}%`}
                  </text>
                )}
              </g>
            );
          })}
          {variant === 'donut' && (
            <>
              <circle cx={cx} cy={cy} r={donutHole} fill="#ffffff" />
              <text
                data-donut-total={formatScalar(total)}
                dominantBaseline="middle"
                fill="var(--dv-text, #172536)"
                fontSize="15"
                fontWeight="600"
                textAnchor="middle"
                x={cx}
                y={cy - 4}
              >
                {centerLabel}
              </text>
              <text
                dominantBaseline="middle"
                fill="var(--dv-axis-text, #6b7784)"
                fontSize="9"
                textAnchor="middle"
                x={cx}
                y={cy + 10}
              >
                total
              </text>
            </>
          )}
        </svg>
      </div>
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
  flexDirection: 'column',
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
