import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';
import {
  ChartFrame,
  EmptyVisual,
  formatAxis,
  numericOf,
  seriesColor,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

interface Bubble {
  colorIndex: number;
  label: string;
  radius: number;
  value: number;
  x: number;
  y: number;
}

/**
 * Burbujas empaquetadas (Tableau Packed Bubbles): círculos con área proporcional
 * al valor, dispuestos en filas envolventes de forma determinista (sin física).
 * ponytail: layout por filas con ajuste de radio; el packing real (d3-hierarchy)
 * es el upgrade cuando haya más de ~40 burbujas.
 */
export const PackedBubblesVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const categoryRole = getRole(visual, ['category', 'label', 'detail']);
  const seriesRole = getRole(visual, ['series', 'color']);
  const valueRole = getRole(visual, ['value', 'size', 'y_axis']);
  const labels = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const series = seriesRole ? columnForRef(seriesRole.ref, seriesRole.data) : [];
  const values = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];

  const totals = new Map<string, { colorIndex: number; value: number }>();
  const n = Math.max(labels.length, values.length);
  for (let index = 0; index < n; index += 1) {
    const label = String(labels[index] ?? '');
    const value = numericOf(values[index] ?? 0);
    if (!label || value <= 0) {continue;}
    const existing = totals.get(label);
    const colorIndex = series[index] !== undefined && series[index] !== null
      ? Math.abs(String(series[index]).split('').reduce((hash, char) => (hash * 31 + char.charCodeAt(0)) | 0, 7)) % 6
      : 0;
    totals.set(label, {
      colorIndex: existing ? Math.max(existing.colorIndex, colorIndex) : colorIndex,
      value: (existing?.value ?? 0) + value,
    });
  }
  const entries = [...totals.entries()].sort((left, right) => right[1].value - left[1].value);

  if (entries.length === 0) {
    return (
      <EmptyVisual className="dv-packed-bubbles" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const valueRef = valueRole?.ref ?? '';
  const maxValue = entries[0]?.[1].value ?? 1;
  const minDim = Math.min(frame.width, frame.height - 22);
  const maxRadius = Math.max(10, minDim * 0.26);
  const radiusOf = (value: number) => Math.max(5, Math.sqrt(value / maxValue) * maxRadius);
  const bubbles: Bubble[] = [];
  let cursorX = 0;
  let rowMaxRadius = 0;
  let rowY = 0;
  for (const [label, entry] of entries) {
    const radius = radiusOf(entry.value);
    if (cursorX > 0 && cursorX + radius * 2 > frame.width) {
      cursorX = 0;
      rowY += rowMaxRadius * 2 + 4;
      rowMaxRadius = 0;
    }
    bubbles.push({
      colorIndex: entry.colorIndex,
      label,
      radius,
      value: entry.value,
      x: cursorX + radius,
      y: rowY + radius,
    });
    cursorX += radius * 2 + 4;
    rowMaxRadius = Math.max(rowMaxRadius, radius);
  }
  const contentHeight = rowY + rowMaxRadius * 2;
  const contentWidth = Math.max(...bubbles.map((bubble) => bubble.x + bubble.radius), 1);
  const fit = Math.min(1, (frame.width - 8) / contentWidth, (frame.height - 30) / contentHeight);

  return (
    <ChartFrame
      className="dv-packed-bubbles"
      desc={`packed bubbles of ${valueRef}: ${bubbles.length} categories, largest ${formatScalar(maxValue)}.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-bubble-count': bubbles.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      <g transform={`translate(${4} ${10}) scale(${fit})`} transform-origin="0 0">
        {bubbles.map((bubble) => (
          <circle
            className="dv-bubble"
            cx={bubble.x}
            cy={bubble.y}
            data-bubble={bubble.label}
            fill={seriesColor(bubble.colorIndex)}
            fillOpacity="0.78"
            key={bubble.label}
            r={bubble.radius}
            stroke="#ffffff"
            strokeWidth="1"
          >
            <title>{`${bubble.label}: ${formatAxis(bubble.value, valueRef)}`}</title>
          </circle>
        ))}
        {bubbles.filter((bubble) => bubble.radius > 24).map((bubble) => (
          <text
            fill="#ffffff"
            fontSize={Math.max(8, bubble.radius * 0.28)}
            key={`label-${bubble.label}`}
            textAnchor="middle"
            x={bubble.x}
            y={bubble.y + bubble.radius * 0.1}
          >
            {bubble.label.length > 14 ? `${bubble.label.slice(0, 13)}…` : bubble.label}
          </text>
        ))}
      </g>
    </ChartFrame>
  );
};
