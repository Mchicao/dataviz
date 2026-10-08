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

interface Bin {
  count: number;
  from: number;
  to: number;
}

/** Cantidad de bins determinista: sqrt del tamaño de muestra, acotado a 4..16 (Sturges sobre-muestra poco). */
function binCount(n: number): number {
  return Math.max(4, Math.min(16, Math.ceil(Math.sqrt(n))));
}

function buildBins(values: number[]): Bin[] {
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (max === min) {
    return [{ count: values.length, from: min - 0.5, to: max + 0.5 }];
  }
  const width = (max - min) / binCount(values.length);
  const bins: Bin[] = Array.from({ length: binCount(values.length) }, (_, index) => ({
    count: 0,
    from: min + index * width,
    to: min + (index + 1) * width,
  }));
  for (const value of values) {
    const index = Math.min(bins.length - 1, Math.floor((value - min) / width));
    const bin = bins[index];
    if (bin) {bin.count += 1;}
  }
  return bins;
}

/** Histograma (Tableau histogram): binning local de una columna numérica cruda, sin inventar agregaciones. */
export const HistogramVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
  const title = displayTitle(visual.title, visual.name);
  const sid = `dv-${visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  const valueRole = getRole(visual, ['value', 'y_axis', 'y', 'x_axis']);
  const column = valueRole ? columnForRef(valueRole.ref, valueRole.data) : [];
  const values = column.filter((cell): cell is number => typeof cell === 'number' && Number.isFinite(cell));

  if (values.length === 0) {
    return (
      <EmptyVisual className="dv-histogram" reason="no data" ref={frame.ref} sid={sid} title={title} typeScale={typeScale} />
    );
  }

  const bins = buildBins(values);
  const binValueWidth = bins.length > 1 && bins[1] ? bins[1].to - bins[1].from : (bins[0]?.to ?? 1) - (bins[0]?.from ?? 0);
  const valueRef = valueRole?.ref ?? '';
  const max = niceMax(Math.max(...bins.map((bin) => bin.count)));
  const left = Math.max(38, Math.max(...bins.map((bin) => String(bin.count).length), 2) * 6.2 + 16);
  const right = 14;
  const top = 14;
  const bottom = 34;
  const innerWidth = frame.width - left - right;
  const innerHeight = frame.height - top - bottom;
  const binWidth = innerWidth / bins.length;
  const yAt = (count: number) => frame.height - bottom - (count / max) * innerHeight;
  const baseline = frame.height - bottom;
  const tickCount = Math.max(2, Math.min(6, Math.floor(innerHeight / 26)));
  const ticks = Array.from({ length: tickCount }, (_, index) => (max * index) / (tickCount - 1));
  const labelStride = Math.max(1, Math.ceil(bins.length / Math.floor(innerWidth / 64)));

  return (
    <ChartFrame
      className="dv-histogram"
      desc={`histogram of ${valueRef}: ${values.length} values in ${bins.length} bins of width ${formatScalar(binValueWidth)}, range ${formatScalar(Math.min(...values))} to ${formatScalar(Math.max(...values))}.`}
      height={frame.height}
      ref={frame.ref}
      sectionProps={{ 'data-bin-count': bins.length }}
      sid={sid}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {ticks.map((tick) => {
        const y = yAt(tick);
        return (
          <g key={tick}>
            <line x1={left} y1={y} x2={frame.width - right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
            <text x={left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {formatScalar(tick)}
            </text>
          </g>
        );
      })}
      {bins.map((bin, index) => {
        const x = left + index * binWidth;
        const barHeight = baseline - yAt(bin.count);
        return (
          <g key={`${bin.from}-${index}`}>
            <rect
              className="dv-histogram-bin"
              data-histogram-bin={index}
              fill="var(--dv-accent, #4e79a7)"
              height={Math.max(bin.count > 0 ? 1 : 0, barHeight)}
              rx="1.5"
              width={Math.max(1, binWidth - 2)}
              x={x + 1}
              y={yAt(bin.count)}
            >
              <title>{`${formatScalar(Math.round(bin.from * 100) / 100)} – ${formatScalar(Math.round(bin.to * 100) / 100)}: ${bin.count} valores`}</title>
            </rect>
            {index % labelStride === 0 && (
              <text
                fill="var(--dv-axis-text, #6b7784)"
                fontSize={Math.max(7, 9.5 * typeScale)}
                textAnchor="middle"
                x={x + binWidth / 2}
                y={baseline + 14}
              >
                {formatAxis(bin.from, valueRef)}
              </text>
            )}
          </g>
        );
      })}
      <line x1={left} y1={baseline} x2={frame.width - right} y2={baseline} stroke="var(--dv-axis-line, #b9c2cb)" />
    </ChartFrame>
  );
};
