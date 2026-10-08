import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';
import { truncateLabel } from './axisDensity';
import {
  ChartFrame,
  EmptyVisual,
  formatAxis,
  numericOf,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

interface Stage {
  label: string;
  value: number;
}

/** Embudo de etapas (PBI funnel chart): bandas centradas decrecientes con % de la primera etapa. */
export const FunnelVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const categoryRole = getRole(visual, ['category', 'stage', 'x_axis']);
  const valueRole = getRole(visual, ['value', 'y_axis', 'y', 'size']);
  const labels = categoryRole ? columnForRef(categoryRole.ref, categoryRole.data) : [];
  const values = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const stages: Stage[] = labels
    .map((label, index) => ({ label: String(label ?? index), value: numericOf(values[index] ?? 0) }))
    .filter((stage) => stage.value > 0)
    .sort((left, right) => right.value - left.value);

  if (stages.length === 0) {
    return (
      <EmptyVisual className="dv-funnel" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const max = stages[0]?.value ?? 1;
  const top = 14;
  const bottom = 26;
  const longest = Math.max(...stages.map((stage) => stage.label.length), 4);
  const left = Math.min(Math.max(48, Math.min(longest, 16) * 6.4 + 12), frame.width * 0.32);
  const right = 12;
  const funnelCenter = left + (frame.width - left - right) / 2;
  const funnelMaxHalf = (frame.width - left - right) / 2;
  const band = (frame.height - top - bottom) / stages.length;
  const valueRef = valueRole?.ref ?? '';

  return (
    <ChartFrame
      className="dv-funnel"
      desc={`funnel of ${valueRef} across ${stages.length} stages, from ${formatScalar(stages[0]?.value ?? 0)} down to ${formatScalar(stages[stages.length - 1]?.value ?? 0)}.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-funnel-stages': stages.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {stages.map((stage, index) => {
        const yTop = top + index * band;
        const yBottom = yTop + band;
        const nextValue = stages[index + 1]?.value ?? 0;
        const halfTop = (stage.value / max) * funnelMaxHalf;
        const halfBottom = (Math.max(nextValue, 0) / max) * funnelMaxHalf;
        const share = stage.value / max;
        return (
          <polygon
            className="dv-funnel-stage"
            data-funnel-stage={stage.label}
            fill="var(--dv-accent, #4e79a7)"
            fillOpacity={0.55 + 0.45 * share}
            key={stage.label}
            points={`${funnelCenter - halfTop},${yTop} ${funnelCenter + halfTop},${yTop} ${funnelCenter + halfBottom},${yBottom} ${funnelCenter - halfBottom},${yBottom}`}
            rx="2"
          >
            <title>{`${stage.label}: ${formatAxis(stage.value, valueRef)} (${Math.round(share * 100)}% of ${formatScalar(max)})`}</title>
          </polygon>
        );
      })}
      {stages.map((stage, index) => {
        const yMid = top + index * band + band / 2;
        const share = max > 0 ? stage.value / max : 0;
        return (
          <g key={`label-${stage.label}`}>
            <text x={left - 8} y={yMid + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              <title>{stage.label}</title>
              {truncateLabel(stage.label, 16)}
            </text>
            <text x={frame.width - right} y={yMid + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {`${formatAxis(stage.value, valueRef)} · ${Math.round(share * 100)}%`}
            </text>
          </g>
        );
      })}
    </ChartFrame>
  );
};
