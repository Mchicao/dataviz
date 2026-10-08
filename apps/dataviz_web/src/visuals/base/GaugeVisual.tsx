import React from 'react';
import type { VisualProps } from './types';
import { columnForRef, formatScalar, getRole } from './data';
import { displayTitle } from './title';
import {
  ChartFrame,
  EmptyVisual,
  formatAxis,
  niceMax,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

/** Indicador radial (PBI gauge): semicírculo 0→techo con arco de valor y marca de objetivo. */
export const GaugeVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const valueRole = getRole(visual, ['value', 'y_axis', 'y']);
  const targetRole = getRole(visual, ['target_metric', 'target', 'comparison_metric']);
  const valueColumn = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const targetColumn = targetRole ? columnForRef(targetRole.ref, targetRole.data) : [];
  const value = valueColumn.map((cell) => (typeof cell === 'number' && Number.isFinite(cell) ? cell : 0))
    .reduce((sum, cell) => sum + cell, 0);
  const target = targetColumn.map((cell) => (typeof cell === 'number' && Number.isFinite(cell) ? cell : 0))
    .reduce((sum, cell) => sum + cell, 0);

  if (valueColumn.length === 0 || !Number.isFinite(value)) {
    return (
      <EmptyVisual className="dv-gauge" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const valueRef = valueRole?.ref ?? '';
  const ceiling = niceMax(Math.max(value, Math.max(target, 0)) * 1.08);
  const ratio = Math.min(1, Math.max(0, value / ceiling));
  const cx = frame.width / 2;
  const cy = frame.height * 0.86;
  const radius = Math.min(frame.width / 2 - 24, frame.height * 0.72);
  const pointAt = (share: number) => {
    const angle = Math.PI * (1 - share);
    return { x: cx + radius * Math.cos(angle), y: cy - radius * Math.sin(angle) };
  };
  const start = pointAt(0);
  const valuePoint = pointAt(ratio);
  const targetPoint = target > 0 ? pointAt(Math.min(1, target / ceiling)) : null;
  const largeArc = ratio > 0.5 ? 1 : 0;
  const onTarget = target > 0 && value >= target;

  return (
    <ChartFrame
      className="dv-gauge"
      desc={`gauge of ${valueRef}: ${formatScalar(value)} of ${formatScalar(ceiling)}${target > 0 ? `, target ${formatScalar(target)}` : ''}.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{
        'data-gauge-ceiling': ceiling,
        'data-gauge-target': target,
        'data-gauge-value': value,
      }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      <path
        d={`M${start.x},${start.y} A${radius},${radius} 0 0 1 ${cx + radius},${cy}`}
        fill="none"
        stroke="var(--dv-grid, #e2e7ec)"
        strokeWidth={Math.max(10, radius * 0.16)}
        strokeLinecap="round"
      />
      <path
        className="dv-gauge-arc"
        d={`M${start.x},${start.y} A${radius},${radius} 0 ${largeArc} 1 ${valuePoint.x},${valuePoint.y}`}
        fill="none"
        stroke={onTarget ? '#59a14f' : 'var(--dv-accent, #4e79a7)'}
        strokeWidth={Math.max(10, radius * 0.16)}
        strokeLinecap="round"
      >
        <title>{`${formatAxis(value, valueRef)} of ${formatAxis(ceiling, valueRef)}${target > 0 ? ` · objetivo ${formatAxis(target, valueRef)}` : ''}`}</title>
      </path>
      {targetPoint && (
        <g className="dv-gauge-target" data-gauge-target-mark="true">
          <line
            stroke="var(--dv-axis-text, #37474f)"
            strokeLinecap="round"
            strokeWidth={Math.max(2, radius * 0.035)}
            x1={targetPoint.x}
            x2={targetPoint.x}
            y1={targetPoint.y - radius * 0.12}
            y2={targetPoint.y + radius * 0.12}
          />
        </g>
      )}
      <text textAnchor="middle" x={cx} y={cy - radius * 0.28} fill="var(--dv-text, #172536)" fontSize={Math.max(14, radius * 0.34)} fontWeight="600">
        {formatAxis(value, valueRef)}
      </text>
      {target > 0 && (
        <text textAnchor="middle" x={cx} y={cy - radius * 0.06} fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(9, radius * 0.15)}>
          {`objetivo ${formatAxis(target, valueRef)} · ${Math.round((value / target) * 100)}%`}
        </text>
      )}
    </ChartFrame>
  );
};
