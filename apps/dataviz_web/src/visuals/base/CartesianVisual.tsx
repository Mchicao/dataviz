import React, { useEffect, useRef, useState } from 'react';
import type { InterpretedRole, Scalar } from '../../runtime/types';
import type { VisualProps } from './types';
import { columnForRef, formatScalar } from './data';
import { displayTitle } from './title';
import { planAxisDensity, truncateLabel } from './axisDensity';
import type { AxisDensityPlan } from './axisDensity';

/** Cartesian variants rendered by this component. */
export type CartesianVariant = 'bar' | 'column' | 'line' | 'area' | 'scatter';

export interface CartesianVisualProps extends VisualProps {
  variant: CartesianVariant;
}

interface Point {
  x: Scalar;
  y: Scalar;
  series: Scalar;
  label: Scalar;
  i: number;
}

interface DensityResult {
  points: Point[];
  mode: 'none' | 'sampled' | 'sampled_top_series';
  sourceCount: number;
  visibleSeries: number;
}

/** Default full-fidelity boundary; above D040 high-density territory sampling is explicit. */
const TEMPORAL_DENSITY_LIMIT = 5000;
/** Distinct series above which only the top-ranked series are rendered. */
const SERIES_REDUCTION_THRESHOLD = 8;
/** Series kept when the reduction above applies. */
const RETAINED_SERIES_COUNT = 5;
/** Fallback render box before the container is measured (also the jsdom size). */
const FALLBACK_PLOT = { height: 300, width: 480 };

/**
 * Mide el marco del visual en píxeles CSS para dibujar el SVG a escala 1:1.
 * Sin medición disponible (jsdom, primer frame) conserva la caja clásica 480×300.
 */
function useMeasuredSize(): { ref: React.RefObject<HTMLElement | null>; width: number; height: number } {
  const ref = useRef<HTMLElement | null>(null);
  const [size, setSize] = useState(FALLBACK_PLOT);
  useEffect(() => {
    const node = ref.current;
    if (!node || typeof ResizeObserver === 'undefined') {return;}
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect || rect.width < 1 || rect.height < 1) {return;}
      setSize((prev) => (
        Math.abs(prev.width - rect.width) < 1 && Math.abs(prev.height - rect.height) < 1
          ? prev
          : { height: rect.height, width: rect.width }
      ));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return { height: size.height, ref, width: size.width };
}

/**
 * Small dependency-free SVG renderer for neutral cartesian intents.
 *
 * Tableau calls every rectangular mark a bar. Shelf semantics disambiguate the
 * orientation: a measure on x is horizontal; a measure on y is vertical.
 */
export const CartesianVisual: React.FC<CartesianVisualProps> = ({ visual, variant, onDataSelect }) => {
  const frame = useMeasuredSize();
  const typeScale = Math.min(
    1.25,
    Math.max(0.6, Math.min(frame.width / FALLBACK_PLOT.width, frame.height / FALLBACK_PLOT.height)),
  );
  const exactRole = (names: readonly string[]) => {
    for (const name of names) {
      const role = visual.roles[name];
      if (role) {return role;}
    }
    return;
  };
  const measureRole = (names: readonly string[]) => {
    const role = exactRole(names);
    return role?.ref.startsWith('measure:') ? role : undefined;
  };
  const fieldRole = (names: readonly string[]) => {
    for (const name of names) {
      const role = visual.roles[name];
      if (role?.ref.startsWith('field:')) {return role;}
    }
    return;
  };

  const horizontal = variant === 'bar'
    && Boolean(measureRole(['x_axis']) || (!measureRole(['y_axis']) && !measureRole(['value', 'y'])));
  const labelRole = exactRole(['label']);
  const valRole = measureRole(['value', 'y'])
    ?? (horizontal ? measureRole(['x_axis']) : measureRole(['y_axis']))
    ?? Object.entries(visual.roles).find(
      ([name, role]) => name !== 'label' && role.ref.startsWith('measure:'),
    )?.[1]
    ?? labelRole;
  const seriesRole = fieldRole(['series', 'color', 'x_axis_2', 'y_axis_2']);
  const categoryCandidates = horizontal
    ? [exactRole(['category']), exactRole(['y_axis']), exactRole(['y_axis_2'])]
    : [exactRole(['category']), exactRole(['x_axis']), exactRole(['x_axis_2'])];
  const fieldRoles = Object.values(visual.roles).filter(
    (role) => role !== valRole && role !== seriesRole && role.ref.startsWith('field:'),
  );
  const catRole = [...categoryCandidates, ...fieldRoles].find(hasDisplayValues)
    ?? [...categoryCandidates, ...fieldRoles].find(Boolean);

  const xs = catRole ? columnForRef(catRole.ref, catRole.data) : [];
  const ys = valRole ? columnForRef(valRole.ref, valRole.data) : [];
  const series = seriesRole ? columnForRef(seriesRole.ref, seriesRole.data) : [];
  const labels = labelRole ? columnForRef(labelRole.ref, labelRole.data) : [];
  const rawPoints = buildPoints(xs, ys, series, labels);
  const density = reduceTemporalDensity(rawPoints);
  const {points} = density;
  const { yMin, yMax } = extent(points.map((point) => point.y));
  const sid = `dv-${  visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;
  const title = displayTitle(visual.title, visual.name);
  const orientation = horizontal ? 'horizontal' : 'vertical';
  const selectionRole = variant === 'scatter'
    ? fieldRole(['detail', 'detail_2']) ?? (catRole?.ref.startsWith('field:') ? catRole : undefined)
    : (catRole?.ref.startsWith('field:') ? catRole : undefined);
  const selectionRef = selectionRole?.ref ?? '';
  const selectionValues = selectionRole ? columnForRef(selectionRole.ref, selectionRole.data) : [];
  const selectionValueForPoint = (point: Point): Scalar => (
    variant === 'scatter' ? selectionValues[point.i] ?? point.x : point.x
  );
  const selectPoint = selectionRef && onDataSelect
    ? (point: Point) => {
        const selectedValue = selectionValueForPoint(point);
        if (typeof selectedValue === 'string' || typeof selectedValue === 'number') {
          onDataSelect(visual, selectionRef, selectedValue, point.i);
        }
      }
    : undefined;

  if (points.length === 0) {
    return (
      <section
        ref={frame.ref}
        className="dv-cartesian dv-cartesian--empty"
        role="img"
        aria-labelledby={`${sid}-title ${sid}-desc`}
        style={frameStyle(typeScale)}
      >
        <span id={`${sid}-title`} style={{ color: '#94a3b8' }}>{title}: no data</span>
        <span id={`${sid}-desc`} style={{ display: 'none' }}>0 points</span>
      </section>
    );
  }

  return (
    <section
      ref={frame.ref}
      className={`dv-cartesian dv-cartesian--${orientation}`}
      data-density-mode={density.mode}
      data-rendered-mark-count={points.length}
      data-source-mark-count={density.sourceCount}
      data-visible-series-count={density.visibleSeries}
      style={frameStyle(typeScale)}
    >
      <h3 id={`${sid}-heading`} style={headingStyle(typeScale)}>
        <span style={headingTitleStyle}>{title}</span>
        {density.mode === 'sampled' && (
          <span className="dv-density-note">Sampled · {points.length} of {density.sourceCount} points</span>
        )}
        {density.mode === 'sampled_top_series' && (
          <span className="dv-density-note">
            Sampled · top {density.visibleSeries} series · {points.length} of {density.sourceCount} points
          </span>
        )}
      </h3>
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${frame.width} ${frame.height}`}
        role="img"
        style={chartStyle}
        aria-labelledby={`${sid}-title ${sid}-desc`}
      >
        <title id={`${sid}-title`}>{`${title} (${variant})`}</title>
        <desc id={`${sid}-desc`}>
          {`${variant} chart of ${valRole?.ref ?? 'value'} by ${catRole?.ref ?? 'index'}; ${points.length} points${density.mode === 'sampled' ? `, sampled from ${density.sourceCount} source points` : ''}${density.mode === 'sampled_top_series' ? `, top ${density.visibleSeries} series by magnitude, sampled from ${density.sourceCount} source points` : ''}, range ${formatScalar(yMin)} to ${formatScalar(yMax)}.`}
        </desc>
        {horizontal
          ? renderHorizontalBars(points, valRole?.ref ?? '', labelRole?.ref ?? '', selectPoint, frame.width, frame.height, typeScale)
          : renderVerticalChart(
              points,
              catRole?.ref ?? '',
              valRole?.ref ?? '',
              variant,
              selectPoint,
              selectionValueForPoint,
              frame.width,
              frame.height,
              typeScale,
            )}
      </svg>
    </section>
  );
};

/** Padding/gap del marco escalados al tamaño medido del contenedor. */
const frameStyle = (typeScale: number): React.CSSProperties => ({
  backgroundColor: 'var(--dv-card-bg, #ffffff)',
  border: '1px solid var(--dv-card-border, #d8e0e7)',
  borderRadius: 6,
  boxSizing: 'border-box',
  display: 'flex',
  flexDirection: 'column',
  gap: `${3 * typeScale}px`,
  height: '100%',
  minHeight: 0,
  minWidth: 0,
  overflow: 'hidden',
  padding: `${6 * typeScale}px`,
  width: '100%',
});

const headingStyle = (typeScale: number): React.CSSProperties => ({
  alignItems: 'center',
  display: 'flex',
  flex: '0 0 auto',
  fontSize: `${Math.max(9, 13 * typeScale)}px`,
  fontWeight: 600,
  gap: '0.5rem',
  justifyContent: 'space-between',
  lineHeight: 1.2,
  margin: 0,
  minWidth: 0,
  overflow: 'hidden',
  whiteSpace: 'nowrap',
});

const headingTitleStyle: React.CSSProperties = {
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
};

const chartStyle: React.CSSProperties = {
  display: 'block',
  flex: '1 1 auto',
  minHeight: 0,
  minWidth: 0,
};

/**
 * Sample dense temporal series without altering any point value.
 *
 * The aggregation semantics of the value measure (sum, average, min, ratio...)
 * are not provable from the rendered data, so no value is ever combined: every
 * emitted mark is an original source point with its original x, y, series,
 * label and row index. Dense series are bounded by a uniform stride that keeps
 * the first and last point of each series; with more than
 * `SERIES_REDUCTION_THRESHOLD` series only the `RETAINED_SERIES_COUNT` series
 * with the largest total absolute value are kept. Points without a series
 * never compete for retention. The approximation is declared via `mode`,
 * `sourceCount` and `visibleSeries`; it hides source points but never changes
 * the meaning of the rendered ones.
 */
export function reduceTemporalDensity(points: Point[], limit = TEMPORAL_DENSITY_LIMIT): DensityResult {
  const densityLimit = Math.max(1, Math.floor(limit));
  if (points.length <= densityLimit) {
    return { mode: 'none', points, sourceCount: points.length, visibleSeries: 0 };
  }
  if (!points.every((point) => (
    typeof point.x === 'string'
    && /^\d{4}-\d{2}-\d{2}/.test(point.x)
  ))) {
    return { mode: 'none', points, sourceCount: points.length, visibleSeries: 0 };
  }

  const seriesTotals = new Map<string, number>();
  for (const point of points) {
    const series = String(point.series ?? '').trim();
    if (series) {seriesTotals.set(series, (seriesTotals.get(series) ?? 0) + Math.abs(numericY(point)));}
  }
  const rankedSeries = [...seriesTotals.entries()]
    .sort((left, right) => right[1] - left[1])
    .map(([name]) => name);
  const useSeriesReduction = rankedSeries.length > SERIES_REDUCTION_THRESHOLD;
  const retainedSeries = new Set(rankedSeries.slice(0, RETAINED_SERIES_COUNT));

  const bySeries = new Map<string, Point[]>();
  for (const point of points) {
    const seriesKey = String(point.series ?? '').trim();
    if (useSeriesReduction && seriesKey && !retainedSeries.has(seriesKey)) {continue;}
    const group = bySeries.get(seriesKey);
    if (group) {group.push(point);}
    else {bySeries.set(seriesKey, [point]);}
  }

  const kept: Point[] = [];
  for (const group of bySeries.values()) {
    const stride = Math.ceil(group.length / densityLimit);
    for (let index = 0; index < group.length; index += stride) {
      kept.push(group[index]);
    }
    if ((group.length - 1) % stride !== 0) {
      kept.push(group[group.length - 1]);
    }
  }
  return {
    mode: useSeriesReduction ? 'sampled_top_series' : 'sampled',
    points: kept,
    sourceCount: points.length,
    visibleSeries: useSeriesReduction
      ? Math.min(RETAINED_SERIES_COUNT, rankedSeries.length)
      : rankedSeries.length,
  };
}

function hasDisplayValues(role: InterpretedRole | undefined): role is InterpretedRole {
  if (!role) {return false;}
  return columnForRef(role.ref, role.data).some(
    (value) => value !== null && String(value).trim().length > 0,
  );
}

/** Zip category/value columns into points, falling back to index on gaps. */
function buildPoints(
  xs: Scalar[],
  ys: Scalar[],
  series: Scalar[] = [],
  labels: Scalar[] = [],
): Point[] {
  const n = Math.max(xs.length, ys.length, series.length, labels.length);
  return Array.from({ length: n }, (_, i) => ({
    i,
    label: labels[i] ?? null,
    series: series[i] ?? null,
    x: xs[i] ?? (n === 1 ? '' : i),
    y: ys[i] ?? null,
  }));
}

/** Min/max of the numeric values in `vals`; empty -> [0, 0]. */
function extent(vals: Scalar[]): { yMin: number; yMax: number } {
  const nums = vals.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
  if (nums.length === 0) {return { yMin: 0, yMax: 0 };}
  return { yMax: Math.max(...nums), yMin: Math.min(...nums) };
}

function renderHorizontalBars(
  points: Point[],
  valueRef: string,
  labelRef: string,
  onPointSelect?: (point: Point) => void,
  width = FALLBACK_PLOT.width,
  height = FALLBACK_PLOT.height,
  typeScale = 1,
) {
  const MAX_VISIBLE_BARS = 30;
  const visiblePoints = points.length > MAX_VISIBLE_BARS
    ? [...points].sort((leftPoint, rightPoint) => numericY(rightPoint) - numericY(leftPoint)).slice(0, MAX_VISIBLE_BARS)
    : points;
  const right = 16;
  const top = points.length > visiblePoints.length ? 26 : 12;
  const bottom = 32;
  const max = niceMax(Math.max(...visiblePoints.map(numericY)));
  const band = (height - top - bottom) / Math.max(visiblePoints.length, 1);
  const barHeight = Math.max(3, Math.min(42, band * 0.62));
  const labelFontSize = Math.max(9, Math.min(13, band * 0.3));
  const longestLabel = Math.max(...visiblePoints.map((point) => {
    const series = String(point.series ?? '').trim();
    return `${String(point.x || 'Total')}${series ? ` / ${series}` : ''}`.length;
  }), 4);
  const left = Math.min(
    Math.max(64, Math.min(longestLabel, 18) * labelFontSize * 0.52 + 16),
    width * 0.4,
  );
  const innerWidth = width - left - right;
  const tickCount = Math.max(2, Math.min(6, Math.floor(innerWidth / 70)));
  const ticks = Array.from({ length: tickCount }, (_, index) => (max * index) / (tickCount - 1));

  return (
    <g data-rendered-mark-count={visiblePoints.length} data-omitted-mark-count={points.length - visiblePoints.length}>
      {points.length > visiblePoints.length && (
        <text x={left} y="14" fill="var(--dv-axis-text, #5f6b76)" fontSize={Math.max(8, 10 * typeScale)}>
          {`Top ${visiblePoints.length} of ${points.length} by value`}
        </text>
      )}
      {ticks.map((tick) => {
        const x = left + (tick / max) * innerWidth;
        return (
          <g key={tick}>
            <line x1={x} y1={top} x2={x} y2={height - bottom} stroke="var(--dv-grid, #dde3e9)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
            <text x={x} y={height - 10} textAnchor="middle" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {formatAxis(tick, valueRef)}
            </text>
          </g>
        );
      })}
      {visiblePoints.map((point, index) => {
        const y = top + index * band + (band - barHeight) / 2;
        const value = numericY(point);
        const barWidth = (Math.max(0, value) / max) * innerWidth;
        const label = point.label === null ? '' : formatPercent(point.label);
        const categoryLabel = point.series === null || String(point.series).trim() === ''
          ? String(point.x || 'Total')
          : `${String(point.x || 'Total')} / ${String(point.series)}`;
        return (
          <g
            aria-label={onPointSelect ? `Select ${categoryLabel}` : undefined}
            key={point.i}
            onClick={onPointSelect ? () => onPointSelect(point) : undefined}
            onKeyDown={onPointSelect ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {onPointSelect(point);}
            } : undefined}
            role={onPointSelect ? 'button' : undefined}
            style={onPointSelect ? { cursor: 'pointer' } : undefined}
            tabIndex={onPointSelect ? 0 : undefined}
          >
            <text x={left - 8} y={y + barHeight / 2 + labelFontSize * 0.34} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, labelFontSize)}>
              <title>{categoryLabel}</title>
              {truncateLabel(categoryLabel, 18)}
            </text>
            <rect className="dv-bar" x={left} y={y} width={barWidth} height={barHeight} fill="var(--dv-accent, #4e79a7)" rx="2">
              <title>{`${categoryLabel}: ${formatAxis(value, valueRef)}${label ? ` (${label})` : ''}`}</title>
            </rect>
            {label && (
              <text
                x={left + Math.max(4, barWidth - 5)}
                y={y + barHeight / 2 + 3}
                textAnchor={barWidth > 54 ? 'end' : 'start'}
                fill="#ffffff"
                fontSize={Math.max(8, 10 * typeScale)}
                fontWeight="600"
              >
                {labelRef ? label : ''}
              </text>
            )}
          </g>
        );
      })}
      <line x1={left} y1={height - bottom} x2={width - right} y2={height - bottom} stroke="var(--dv-grid, #c9ced3)" />
    </g>
  );
}

function renderVerticalChart(
  points: Point[],
  categoryRef: string,
  valueRef: string,
  variant: CartesianVariant,
  onPointSelect?: (point: Point) => void,
  selectionValueForPoint: (point: Point) => Scalar = (point) => point.x,
  width = FALLBACK_PLOT.width,
  height = FALLBACK_PLOT.height,
  typeScale = 1,
) {
  const ordered = variant === 'bar' || variant === 'column' ? orderGroupedPoints(points) : points;
  const ratio = isRatioRef(valueRef);
  const maxValue = Math.max(...ordered.map(numericY));
  const max = ratio ? Math.max(0.01, Math.ceil(maxValue * 100) / 100) : niceMax(maxValue);
  const seriesNames = [...new Set(ordered.map((point) => String(point.series ?? '')).filter((name) => name.trim().length > 0))].sort();
  const useSeriesTicks = (variant === 'bar' || variant === 'column')
    && ordered.some((point) => point.series !== null && String(point.series).trim() !== '');
  const bottomLabels = ordered.map((point) => compactCategoryLabel(
    useSeriesTicks ? point.series ?? point.x ?? '' : point.x ?? '',
  ));

  // Márgenes derivados del contenido: el eje Y nunca recorta su etiqueta más
  // larga, el eje X reserva altura sólo cuando hay etiquetas o leyenda, y la
  // cantidad de ticks cabe en la altura disponible (nada se solapa en marcos
  // pequeños).
  const right = 16;
  const top = useSeriesTicks ? 26 : 14;
  const bottomEstimate = useSeriesTicks ? 54 : 34;
  const innerHeightEstimate = height - top - bottomEstimate;
  const tickBudget = Math.max(2, Math.min(6, Math.floor(innerHeightEstimate / 26)));
  const tickCount = ratio && max <= 0.3
    ? Math.max(2, Math.min(Math.round(max / 0.02) + 1, tickBudget * 2))
    : tickBudget;
  const ticks = ratio && max <= 0.3
    ? Array.from({ length: tickCount }, (_, index) => (index * 0.02 * max) / ((tickCount - 1) * 0.02))
    : Array.from({ length: tickCount }, (_, index) => (max * index) / (tickCount - 1));
  const maxTickChars = Math.max(...ticks.map((tick) => formatAxis(tick, valueRef).length), 2);
  const left = Math.min(Math.max(38, maxTickChars * 6.2 + 16), width * 0.35);
  // Piso de trazado: en marcos muy bajos se recorta el espacio del eje X
  // (rotado → truncado) antes que aplastar el gráfico.
  const PLOT_FLOOR = 44;
  const rotatedSpace = useSeriesTicks ? 0 : Math.max(0, Math.min(52, height - top - PLOT_FLOOR));
  const axisPlan = planAxisDensity(bottomLabels, width - left - right, rotatedSpace);
  const desiredBottom = useSeriesTicks ? 54 : axisPlan.mode === 'rotated' ? rotatedSpace + 8 : 34;
  const bottom = Math.min(desiredBottom, Math.max(24, height - top - PLOT_FLOOR));
  const innerHeight = height - top - bottom;
  const innerWidth = width - left - right;
  const xAt = (index: number) => left + ((index + 0.5) / Math.max(ordered.length, 1)) * innerWidth;
  const yAt = (value: number) => height - bottom - (Math.max(0, value) / max) * innerHeight;
  const baseline = height - bottom;

  const axes = (
    <>
      {ticks.map((tick) => {
        const y = yAt(tick);
        return (
          <g key={tick}>
            <line x1={left} y1={y} x2={width - right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
            <text x={left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {formatAxis(tick, valueRef)}
            </text>
          </g>
        );
      })}
      <line x1={left} y1={baseline} x2={width - right} y2={baseline} stroke="var(--dv-axis-line, #b9c2cb)" />
    </>
  );

  if (variant === 'scatter') {
    const numericXs = ordered.map(numericX);
    const minX = Math.min(...numericXs);
    const maxX = Math.max(...numericXs);
    const spanX = Math.max(maxX - minX, 1);
    const numericYs = ordered.map(numericY);
    const minScatterY = Math.min(...numericYs, 0);
    const maxScatterY = Math.max(...numericYs, 0);
    const spanScatterY = Math.max(maxScatterY - minScatterY, 1);
    const scatterX = (point: Point) => left + ((numericX(point) - minX) / spanX) * innerWidth;
    const scatterY = (point: Point) => (
      top + ((maxScatterY - numericY(point)) / spanScatterY) * innerHeight
    );
    const xTicks = Array.from({ length: 6 }, (_, index) => minX + (spanX * index) / 5);
    const yTicks = Array.from(
      { length: 6 },
      (_, index) => minScatterY + (spanScatterY * index) / 5,
    );
    return (
      <g>
        {yTicks.map((tick) => {
          const y = top + ((maxScatterY - tick) / spanScatterY) * innerHeight;
          return (
            <g key={tick}>
              <line x1={left} y1={y} x2={width - right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray="3 3" />
              <text x={left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
                {formatAxis(tick, valueRef)}
              </text>
            </g>
          );
        })}
        <line
          x1={left}
          y1={top + ((maxScatterY - Math.max(0, minScatterY)) / spanScatterY) * innerHeight}
          x2={width - right}
          y2={top + ((maxScatterY - Math.max(0, minScatterY)) / spanScatterY) * innerHeight}
          stroke="var(--dv-axis-text, #6b7784)"
        />
        {xTicks.map((tick) => (
          <text
            fill="var(--dv-axis-text, #6b7784)"
            fontSize={Math.max(7, 10 * typeScale)}
            key={tick}
            textAnchor="middle"
            x={left + ((tick - minX) / spanX) * innerWidth}
            y={height - bottom + 20}
          >
            {formatAxis(tick, categoryRef)}
          </text>
        ))}
        {ordered.map((point) => {
          const seriesIndex = Math.max(0, seriesNames.indexOf(String(point.series ?? '')));
          return (
            <circle
              aria-label={onPointSelect ? `Select ${String(selectionValueForPoint(point))}` : undefined}
              key={point.i}
              cx={scatterX(point)}
              cy={scatterY(point)}
              r="3.5"
              fill={seriesColor(seriesIndex)}
              onClick={onPointSelect ? () => onPointSelect(point) : undefined}
              role={onPointSelect ? 'button' : undefined}
              style={onPointSelect ? { cursor: 'pointer' } : undefined}
              tabIndex={onPointSelect ? 0 : undefined}
            >
              <title>{`${formatAxis(numericX(point), categoryRef)}, ${formatAxis(numericY(point), valueRef)}${point.series === null ? '' : ` / ${String(point.series)}`}`}</title>
            </circle>
          );
        })}
        {renderSeriesLegend(seriesNames, height, Math.max(7, 9.5 * typeScale))}
      </g>
    );
  }

  if (variant === 'line' || variant === 'area') {
    const path = ordered.map((point, index) => `${xAt(index)},${yAt(numericY(point))}`).join(' ');
    return (
      <g>
        {axes}
        {variant === 'area' && <polygon points={`${left},${baseline} ${path} ${xAt(ordered.length - 1)},${baseline}`} fill="var(--dv-accent, #4e79a7)" fillOpacity="0.22" />}
        <polyline points={path} fill="none" stroke="var(--dv-accent, #4e79a7)" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        {renderBottomAxisLabels(bottomLabels, xAt, baseline, axisPlan, Math.max(7, 10 * typeScale))}
        {onPointSelect && ordered.map((point, index) => (
          <circle
            aria-label={`Select ${String(point.x)}`}
            key={point.i}
            cx={xAt(index)}
            cy={yAt(numericY(point))}
            r="4"
            fill="var(--dv-accent, #4e79a7)"
            onClick={() => onPointSelect(point)}
            role="button"
            style={{ cursor: 'pointer' }}
            tabIndex={0}
          />
        ))}
      </g>
    );
  }

  const barWidth = Math.max(3, Math.min(52, (innerWidth / Math.max(ordered.length, 1)) * 0.62));
  const categories = ordered.some((point) => point.series !== null)
    ? [...new Set(ordered.map((point) => String(point.x ?? '')))].filter(Boolean)
    : [];
  const categoryStride = Math.max(1, Math.ceil(categories.length / 10));
  return (
    <g>
      {axes}
      {categories.map((category, categoryIndex) => {
        if (categoryIndex % categoryStride !== 0) {return null;}
        const indexes = ordered.flatMap((point, index) => String(point.x ?? '') === category ? [index] : []);
        const center = indexes.reduce((sum, index) => sum + xAt(index), 0) / indexes.length;
        return <text key={category} x={center} y="15" textAnchor="middle" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(8, 10 * typeScale)}>{compactCategoryLabel(category)}</text>;
      })}
      {ordered.map((point, index) => {
        const x = xAt(index);
        const value = numericY(point);
        const y = yAt(value);
        const barHeight = Math.max(0, baseline - y);
        const seriesIndex = Math.max(0, seriesNames.indexOf(String(point.series ?? '')));
        const bottomLabel = bottomLabels[index] ?? '';
        return (
          <g
            aria-label={onPointSelect ? `Select ${String(point.x)}` : undefined}
            key={point.i}
            onClick={onPointSelect ? () => onPointSelect(point) : undefined}
            onKeyDown={onPointSelect ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {onPointSelect(point);}
            } : undefined}
            role={onPointSelect ? 'button' : undefined}
            style={onPointSelect ? { cursor: 'pointer' } : undefined}
            tabIndex={onPointSelect ? 0 : undefined}
          >
            <path
              className="dv-bar"
              d={topRoundedBar(x - barWidth / 2, y, barWidth, barHeight, 3)}
              fill={seriesColor(seriesIndex)}
            >
              <title>{`${String(point.x)}${point.series === null ? '' : ` / ${String(point.series)}`}: ${formatScalar(value)}${isRatioRef(valueRef) ? ` (${formatAxis(value, valueRef)})` : ''}`}</title>
            </path>
            {!useSeriesTicks && renderBottomAxisLabel(bottomLabel, index, x, baseline, axisPlan, Math.max(7, 10 * typeScale))}
          </g>
        );
      })}
      {useSeriesTicks && renderSeriesLegend(seriesNames, height, Math.max(7, 9.5 * typeScale))}
    </g>
  );
}

/** Path de barra vertical con esquinas superiores redondeadas y base recta. */
function topRoundedBar(x: number, y: number, barWidth: number, barHeight: number, radius: number): string {
  const r = Math.min(radius, barWidth / 2, barHeight);
  const left = x;
  const right = x + barWidth;
  const base = y + barHeight;
  return [
    `M${left},${base}`,
    `L${left},${y + r}`,
    `Q${left},${y} ${left + r},${y}`,
    `L${right - r},${y}`,
    `Q${right},${y} ${right},${y + r}`,
    `L${right},${base}`,
    'Z',
  ].join(' ');
}

function renderSeriesLegend(series: string[], height: number, fontSize = 9.5) {
  const allSeries = series.filter((name) => name.trim().length > 0);
  const visible = allSeries.slice(0, 6);
  if (visible.length === 0) {return null;}
  const hiddenCount = allSeries.length - visible.length;
  const legendWidth = 424;
  const slotCount = visible.length + (hiddenCount > 0 ? 1 : 0);
  const itemWidth = legendWidth / slotCount;
  const maxChars = Math.max(6, Math.floor((itemWidth - 14) / 4.4));
  return (
    <g aria-label="Series legend">
      {visible.map((name, index) => {
        const x = 44 + index * itemWidth;
        return (
          <g key={name}>
            <rect x={x} y={height - 19} width="9" height="9" rx="2" fill={seriesColor(index)} />
            <text x={x + 14} y={height - 11} fill="var(--dv-axis-text, #6b7784)" fontSize={fontSize}>
              <title>{name}</title>
              {truncateLabel(name, maxChars)}
            </text>
          </g>
        );
      })}
      {hiddenCount > 0 && (
        <text
          x={44 + visible.length * itemWidth}
          y={height - 11}
          fill="var(--dv-axis-text, #6b7784)"
          fontSize={fontSize}
        >
          {`+${hiddenCount} more`}
        </text>
      )}
    </g>
  );
}

function renderBottomAxisLabels(
  labels: Scalar[],
  xAt: (index: number) => number,
  baseline: number,
  plan: AxisDensityPlan,
  fontSize = 10,
) {
  return labels.map((label, index) => (
    <React.Fragment key={`axis-label-${index}`}>
      {renderBottomAxisLabel(label, index, xAt(index), baseline, plan, fontSize)}
    </React.Fragment>
  ));
}

function renderBottomAxisLabel(
  label: Scalar,
  index: number,
  x: number,
  baseline: number,
  plan: AxisDensityPlan,
  fontSize = 10,
) {
  if (index % plan.stride !== 0) {return null;}
  const rendered = plan.mode === 'truncated' ? truncateLabel(label, plan.maxChars) : String(label ?? '');
  const rotated = plan.mode === 'rotated';
  const y = baseline + (rotated ? 12 : 16);
  return (
    <text
      data-axis-label="true"
      data-axis-label-mode={plan.mode}
      x={x}
      y={y}
      transform={rotated ? `rotate(38 ${x} ${y})` : undefined}
      textAnchor={rotated ? 'start' : 'middle'}
      fill="var(--dv-axis-text, #6b7784)"
      fontSize={fontSize}
    >
      {rendered}
    </text>
  );
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function compactCategoryLabel(label: Scalar): Scalar {
  if (typeof label !== 'string') {return label;}
  const quarter = label.match(/^(\d{4})-Q([1-4])$/);
  if (quarter) {return `Q${quarter[2]} '${quarter[1].slice(-2)}`;}
  const isoDate = label.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/);
  if (!isoDate) {return label;}
  const month = MONTH_LABELS[Number(isoDate[2]) - 1];
  return month ? `${month} '${isoDate[1].slice(-2)}` : label;
}

function orderGroupedPoints(points: Point[]): Point[] {
  if (!points.some((point) => point.series !== null)) {return points;}
  const categories = [...new Set(points.map((point) => String(point.x ?? '')))].filter(Boolean);
  const series = [...new Set(points.map((point) => String(point.series ?? '')))].sort();
  return categories.flatMap((category) => series.flatMap((seriesName) =>
    points.filter((point) => String(point.x ?? '') === category && String(point.series ?? '') === seriesName),
  ));
}

function numericY(point: Point): number {
  return typeof point.y === 'number' && Number.isFinite(point.y) ? point.y : 0;
}

function numericX(point: Point): number {
  return typeof point.x === 'number' && Number.isFinite(point.x) ? point.x : 0;
}

function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {return 1;}
  const rawStep = value / 5;
  const power = 10 ** Math.floor(Math.log10(rawStep));
  const fraction = rawStep / power;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  const step = niceFraction * power;
  return Math.ceil(value / step) * step;
}

function isRatioRef(ref: string): boolean {
  return /(discount|percent|percentage|share|ratio|margin|%)/i.test(ref);
}

/** Formato compacto sin ceros triviales: $2.5M, $500K (el valor exacto vive en el tooltip). */
function compactNumber(value: number, options: Intl.NumberFormatOptions): string {
  const text = new Intl.NumberFormat('en-US', options).format(value);
  return text.replaceAll(/\.0(?=[KMB])/g, '');
}

/** Formato de eje: compacto para magnitudes grandes (el valor exacto vive en el tooltip). */
function formatAxis(value: number, ref: string): string {
  if (isRatioRef(ref)) {return `${Math.round(value * 100)}%`;}
  if (/(sales|revenue|amount|cost)/i.test(ref)) {
    const options: Intl.NumberFormatOptions = { currency: 'USD', maximumFractionDigits: 0, style: 'currency' };
    if (Math.abs(value) >= 10_000) {
      return compactNumber(value, { ...options, maximumFractionDigits: 1, notation: 'compact' });
    }
    return new Intl.NumberFormat('en-US', options).format(value);
  }
  if (Math.abs(value) >= 100_000) {
    return compactNumber(value, { maximumFractionDigits: 1, notation: 'compact' });
  }
  return formatScalar(value);
}

function formatPercent(value: Scalar): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {return formatScalar(value);}
  return `${(value * 100).toFixed(1).replace('.', ',')} %`;
}

const BAR_COLORS = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc949'];

function seriesColor(index: number): string {
  return index === 0 ? 'var(--dv-accent, #4e79a7)' : BAR_COLORS[index % BAR_COLORS.length];
}
