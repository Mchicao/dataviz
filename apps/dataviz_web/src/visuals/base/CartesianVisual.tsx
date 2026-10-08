import React from 'react';
import type { InterpretedRole, Scalar } from '../../runtime/types';
import type { VisualProps } from './types';
import { columnForRef, formatScalar } from './data';
import { displayTitle } from './title';
import { planAxisDensity, truncateLabel } from './axisDensity';
import type { AxisDensityPlan } from './axisDensity';
import {
  ChartFrame,
  EmptyVisual,
  FALLBACK_PLOT,
  compactCategoryLabel,
  formatAxis,
  isRatioRef,
  niceMax,
  seriesColor,
  typeScaleFor,
  useMeasuredSize,
} from './chartPrimitives';

/** Cartesian variants rendered by this component. */
export type CartesianVariant =
  | 'bar'
  | 'stacked_bar'
  | 'percent_stacked_bar'
  | 'column'
  | 'stacked_column'
  | 'percent_stacked_column'
  | 'line'
  | 'area'
  | 'stacked_area'
  | 'scatter'
  | 'lollipop'
  | 'waterfall'
  | 'ribbon';

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

/**
 * Tableau calls every rectangular mark a bar. Shelf semantics disambiguate the
 * orientation: a measure on x is horizontal; a measure on y is vertical.
 */
export const CartesianVisual: React.FC<CartesianVisualProps> = ({ visual, variant, onDataSelect }) => {
  const frame = useMeasuredSize();
  const typeScale = typeScaleFor(frame.width, frame.height);
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

  // Familia barras (PBI barChart / Tableau barras horizontales): horizontal por
  // defecto; sólo es vertical cuando la medida vive explícitamente en y_axis.
  const horizontal = (variant === 'bar' || variant === 'stacked_bar' || variant === 'percent_stacked_bar')
    && !measureRole(['y_axis']);
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
      <EmptyVisual
        className="dv-cartesian"
        detail="0 points"
        reason="no data"
        ref={frame.ref}
        sid={sid}
        title={title}
        typeScale={typeScale}
      />
    );
  }

  return (
    <ChartFrame
      className={`dv-cartesian dv-cartesian--${orientation}`}
      desc={`${variant} chart of ${valRole?.ref ?? 'value'} by ${catRole?.ref ?? 'index'}; ${points.length} points${density.mode === 'sampled' ? `, sampled from ${density.sourceCount} source points` : ''}${density.mode === 'sampled_top_series' ? `, top ${density.visibleSeries} series by magnitude, sampled from ${density.sourceCount} source points` : ''}, range ${formatScalar(yMin)} to ${formatScalar(yMax)}.`}
      height={frame.height}
      note={
        density.mode === 'sampled' ? `Sampled · ${points.length} of ${density.sourceCount} points`
        : density.mode === 'sampled_top_series' ? `Sampled · top ${density.visibleSeries} series · ${points.length} of ${density.sourceCount} points`
        : undefined
      }
      ref={frame.ref}
      sectionProps={{
        'data-density-mode': density.mode,
        'data-rendered-mark-count': points.length,
        'data-source-mark-count': density.sourceCount,
        'data-visible-series-count': density.visibleSeries,
      }}
      sid={sid}
      svgTitle={`${title} (${variant})`}
      title={title}
      typeScale={typeScale}
      width={frame.width}
    >
      {horizontal
        ? renderHorizontalBars(points, valRole?.ref ?? '', labelRole?.ref ?? '', variant, selectPoint, frame.width, frame.height, typeScale)
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
    </ChartFrame>
  );
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
  variant: CartesianVariant,
  onPointSelect?: (point: Point) => void,
  width = FALLBACK_PLOT.width,
  height = FALLBACK_PLOT.height,
  typeScale = 1,
) {
  if ((variant === 'stacked_bar' || variant === 'percent_stacked_bar')
    && points.some((point) => point.series !== null && String(point.series).trim() !== '')) {
    return renderStackedHorizontalBars(points, valueRef, width, height, typeScale, onPointSelect, variant === 'percent_stacked_bar');
  }
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

/** Barras horizontales apiladas por serie (stacked_bar con serie presente). */
function renderStackedHorizontalBars(
  points: Point[],
  valueRef: string,
  width = FALLBACK_PLOT.width,
  height = FALLBACK_PLOT.height,
  typeScale = 1,
  onPointSelect?: (point: Point) => void,
  percent = false,
) {
  const seriesNames = [...new Set(points.map((point) => String(point.series ?? '')).filter((name) => name.trim().length > 0))].sort();
  const categories = [...new Set(points.map((point) => String(point.x ?? '')).filter(Boolean))];
  const totals = new Map(categories.map((category) => [
    category,
    points
      .filter((point) => String(point.x ?? '') === category)
      .reduce((sum, point) => sum + numericY(point), 0),
  ]));
  const orderedCategories = [...categories].sort((left, right) => (totals.get(right) ?? 0) - (totals.get(left) ?? 0));
  const rawMax = Math.max(...orderedCategories.map((category) => totals.get(category) ?? 0), 0);
  const max = percent ? 1 : niceMax(rawMax);
  const scaleOf = (category: string) => (percent ? 1 / Math.max(totals.get(category) ?? 0, 1e-9) : 1);
  const top = 14;
  const bottom = 32;
  const band = (height - top - bottom) / Math.max(orderedCategories.length, 1);
  const barHeight = Math.max(3, Math.min(42, band * 0.62));
  const labelFontSize = Math.max(9, Math.min(13, band * 0.3));
  const longest = Math.max(...orderedCategories.map((category) => category.length), 4);
  const left = Math.min(Math.max(64, Math.min(longest, 18) * labelFontSize * 0.52 + 16), width * 0.4);
  const right = 16;
  const innerWidth = width - left - right;
  const tickCount = Math.max(2, Math.min(6, Math.floor(innerWidth / 70)));
  const ticks = Array.from({ length: tickCount }, (_, index) => (max * index) / (tickCount - 1));
  const markCount = points.filter((point) => numericY(point) > 0).length;

  return (
    <g data-rendered-mark-count={markCount}>
      {ticks.map((tick) => {
        const x = left + (tick / max) * innerWidth;
        return (
          <g key={tick}>
            <line x1={x} y1={top} x2={x} y2={height - bottom} stroke="var(--dv-grid, #dde3e9)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
            <text x={x} y={height - 10} textAnchor="middle" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * typeScale)}>
              {percent ? `${Math.round(tick * 100)}%` : formatAxis(tick, valueRef)}
            </text>
          </g>
        );
      })}
      {orderedCategories.map((category, categoryIndex) => {
        const y = top + categoryIndex * band + (band - barHeight) / 2;
        let cursor = 0;
        return (
          <g key={category}>
            <text x={left - 8} y={y + barHeight / 2 + labelFontSize * 0.34} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, labelFontSize)}>
              <title>{category}</title>
              {truncateLabel(category, 18)}
            </text>
            {seriesNames.map((seriesName) => {
              const point = points.find((candidate) => (
                String(candidate.x ?? '') === category && String(candidate.series ?? '') === seriesName
              ));
              const value = point ? numericY(point) : 0;
              const scaledCursor = cursor * scaleOf(category);
              const segmentWidth = (Math.max(0, value) * scaleOf(category) / max) * innerWidth;
              const segmentLeft = left + (scaledCursor / max) * innerWidth;
              cursor += value;
              if (!point || segmentWidth <= 0) {return null;}
              return (
                <rect
                  className={percent ? 'dv-bar dv-bar--percent' : 'dv-bar'}
                  data-percent-share={percent ? `${Math.round(value * scaleOf(category) * 100)}%` : undefined}
                  fill={seriesColor(Math.max(0, seriesNames.indexOf(seriesName)))}
                  height={barHeight}
                  key={`${category}-${seriesName}`}
                  onClick={onPointSelect ? () => onPointSelect(point) : undefined}
                  rx="1.5"
                  width={segmentWidth}
                  x={segmentLeft}
                  y={y}
                >
                  <title>{`${category} / ${seriesName}: ${formatAxis(value, valueRef)}`}</title>
                </rect>
              );
            })}
            <text fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 9.5 * typeScale)} textAnchor="start" x={Math.min(left + (cursor / max) * innerWidth + 5, width - right - 4)} y={y + barHeight / 2 + 3}>
              {percent ? '100%' : formatAxis(totals.get(category) ?? 0, valueRef)}
            </text>
          </g>
        );
      })}
      <line x1={left} y1={height - bottom} x2={width - right} y2={height - bottom} stroke="var(--dv-grid, #c9ced3)" />
      {renderSeriesLegend(seriesNames, height, Math.max(7, 9.5 * typeScale))}
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

  if (variant === 'stacked_column' || variant === 'percent_stacked_column') {
    return renderStackedColumns(ordered, valueRef, {
      axes, baseline, bottom, bottomLabels, height, innerHeight, innerWidth, left, onPointSelect,
      right, top, typeScale, width, xAt,
    }, variant === 'percent_stacked_column');
  }

  if (variant === 'ribbon') {
    return renderRibbon(ordered, valueRef, {
      axes, axisPlan, baseline, bottom, bottomLabels, height, innerHeight, innerWidth, left,
      onPointSelect, right, top, typeScale, width, xAt,
    });
  }

  if (variant === 'waterfall') {
    return renderWaterfall(ordered, valueRef, {
      axisPlan, baseline, bottom, bottomLabels, height, innerHeight, innerWidth, left, onPointSelect,
      right, top, typeScale, width, xAt,
    });
  }

  if (variant === 'stacked_area') {
    return renderStackedArea(ordered, valueRef, {
      axes, axisPlan, baseline, bottom, bottomLabels, height, innerHeight, innerWidth, left,
      onPointSelect, right, top, typeScale, width, xAt,
    });
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

  if (variant === 'lollipop') {
    const stemWidth = 2.5;
    const barWidthMax = Math.max(3, Math.min(52, (innerWidth / Math.max(ordered.length, 1)) * 0.62));
    return (
      <g>
        {axes}
        {ordered.map((point, index) => {
          const x = xAt(index);
          const value = numericY(point);
          const y = yAt(value);
          return (
            <g
              aria-label={onPointSelect ? `Select ${String(point.x)}` : undefined}
              key={point.i}
              onClick={onPointSelect ? () => onPointSelect(point) : undefined}
              role={onPointSelect ? 'button' : undefined}
              style={onPointSelect ? { cursor: 'pointer' } : undefined}
              tabIndex={onPointSelect ? 0 : undefined}
            >
              <line stroke="var(--dv-accent, #4e79a7)" strokeLinecap="round" strokeWidth={stemWidth} x1={x} x2={x} y1={y} y2={baseline}>
                <title>{`${String(point.x)}: ${formatAxis(value, valueRef)}`}</title>
              </line>
              <circle className="dv-lollipop-head" cx={x} cy={y} fill={seriesColor(0)} r={Math.max(3.5, Math.min(6, barWidthMax / 4))}>
                <title>{`${String(point.x)}: ${formatAxis(value, valueRef)}`}</title>
              </circle>
              {!useSeriesTicks && renderBottomAxisLabel(bottomLabels[index] ?? '', index, x, baseline, axisPlan, Math.max(7, 10 * typeScale))}
            </g>
          );
        })}
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

function formatPercent(value: Scalar): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) {return formatScalar(value);}
  return `${(value * 100).toFixed(1).replace('.', ',')} %`;
}

/** Barras verticales apiladas por serie (stacked_column con serie presente). */
function renderStackedColumns(
  ordered: Point[],
  valueRef: string,
  layout: {
    axes: React.ReactNode;
    baseline: number;
    bottom: number;
    bottomLabels: Scalar[];
    height: number;
    innerHeight: number;
    innerWidth: number;
    left: number;
    onPointSelect?: (point: Point) => void;
    right: number;
    top: number;
    typeScale: number;
    width: number;
    xAt: (index: number) => number;
  },
  percent = false,
) {
  const seriesNames = [...new Set(ordered.map((point) => String(point.series ?? '')).filter((name) => name.trim().length > 0))].sort();
  const categories = [...new Set(ordered.map((point) => String(point.x ?? '')).filter(Boolean))];
  const totals = new Map(categories.map((category) => [
    category,
    ordered
      .filter((point) => String(point.x ?? '') === category)
      .reduce((sum, point) => sum + numericY(point), 0),
  ]));
  const rawMax = Math.max(...categories.map((category) => totals.get(category) ?? 0), 0);
  const max = percent ? 1 : niceMax(rawMax);
  const scaleOf = (category: string) => (percent ? 1 / Math.max(totals.get(category) ?? 0, 1e-9) : 1);
  const yStack = (value: number) => layout.baseline - (Math.max(0, value) / max) * layout.innerHeight;
  const barWidth = Math.max(3, Math.min(52, (layout.innerWidth / Math.max(ordered.length, 1)) * 0.62));
  const categoryStride = Math.max(1, Math.ceil(categories.length / 10));
  const markCount = ordered.filter((point) => numericY(point) > 0).length;

  const percentSteps = Math.max(2, Math.min(5, Math.floor(layout.innerHeight / 30)));
  const percentTicks: number[] = percent
    ? Array.from({ length: percentSteps }, (_, index) => index / (percentSteps - 1))
    : [];
  return (
    <g data-rendered-mark-count={markCount}>
      {percent
        ? percentTicks.map((tick) => {
            const y = layout.baseline - tick * layout.innerHeight;
            return (
              <g key={tick}>
                <line x1={layout.left} y1={y} x2={layout.width - layout.right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray={tick === 0 ? undefined : '3 3'} />
                <text x={layout.left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * layout.typeScale)}>
                  {`${Math.round(tick * 100)}%`}
                </text>
              </g>
            );
          })
        : layout.axes}
      {ordered.map((point, index) => {
        const select = layout.onPointSelect;
        const category = String(point.x ?? '');
        const seriesIndex = Math.max(0, seriesNames.indexOf(String(point.series ?? '')));
        const peers = ordered.filter((candidate) => String(candidate.x ?? '') === category && numericY(candidate) > 0);
        const before = peers.slice(0, peers.indexOf(point)).reduce((sum, peer) => sum + numericY(peer), 0);
        const value = numericY(point);
        if (value <= 0) {return null;}
        const categoryScale = scaleOf(category);
        const yTop = yStack((before + value) * categoryScale);
        const barHeight = Math.max(0, layout.baseline - yTop);
        return (
          <g key={point.i}>
            <path
              className={percent ? 'dv-bar dv-bar--percent' : 'dv-bar'}
              data-percent-share={percent ? `${Math.round(value * categoryScale * 100)}%` : undefined}
              d={topRoundedBar(layout.xAt(index) - barWidth / 2, yTop, barWidth, barHeight, 2)}
              fill={seriesColor(seriesIndex)}
              onClick={select ? () => select(point) : undefined}
            >
              <title>{`${category}${point.series === null ? '' : ` / ${String(point.series)}`}: ${formatAxis(value, valueRef)}${percent ? ` (${Math.round(value * categoryScale * 100)}%)` : ''}`}</title>
            </path>
          </g>
        );
      })}
      {categories.map((category, categoryIndex) => {
        if (categoryIndex % categoryStride !== 0) {return null;}
        const indexes = ordered.flatMap((point, index) => String(point.x ?? '') === category ? [index] : []);
        const center = indexes.reduce((sum, index) => sum + layout.xAt(index), 0) / Math.max(indexes.length, 1);
        return (
          <text key={category} x={center} y={layout.top - 4} textAnchor="middle" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(8, 10 * layout.typeScale)}>
            <title>{`${category}: ${formatAxis(totals.get(category) ?? 0, valueRef)}`}</title>
            {compactCategoryLabel(category)}
          </text>
        );
      })}
      {renderSeriesLegend(seriesNames, layout.height, Math.max(7, 9.5 * layout.typeScale))}
    </g>
  );
}

/** Gráfico de cascada: aportes flotantes sobre el acumulado, con soporte de negativos. */
function renderWaterfall(
  ordered: Point[],
  valueRef: string,
  layout: {
    axisPlan: AxisDensityPlan;
    baseline: number;
    bottom: number;
    bottomLabels: Scalar[];
    height: number;
    innerHeight: number;
    innerWidth: number;
    left: number;
    onPointSelect?: (point: Point) => void;
    right: number;
    top: number;
    typeScale: number;
    width: number;
    xAt: (index: number) => number;
  },
) {
  let cumulative = 0;
  const steps = ordered.map((point) => {
    const value = numericY(point);
    const start = cumulative;
    cumulative += value;
    return { end: cumulative, point, start, value };
  });
  const yMax = niceMax(Math.max(0, ...steps.map((step) => Math.max(step.start, step.end))));
  const yMin = Math.min(0, ...steps.map((step) => Math.min(step.start, step.end)));
  const span = Math.max(yMax - yMin, 1);
  const yAt = (value: number) => layout.baseline - ((value - yMin) / span) * layout.innerHeight;
  const zeroLine = yAt(0);
  const tickCount = Math.max(2, Math.min(6, Math.floor(layout.innerHeight / 26)));
  const ticks = Array.from({ length: tickCount }, (_, index) => yMin + (span * index) / (tickCount - 1));
  const barWidth = Math.max(4, Math.min(52, (layout.innerWidth / Math.max(ordered.length, 1)) * 0.62));

  return (
    <g data-rendered-mark-count={steps.length} data-waterfall-min={Math.round(yMin)}>
      {ticks.map((tick) => {
        const y = yAt(tick);
        return (
          <g key={tick}>
            <line x1={layout.left} y1={y} x2={layout.width - layout.right} y2={y} stroke="var(--dv-grid, #e2e7ec)" strokeDasharray={Math.abs(tick) < 1e-9 ? undefined : '3 3'} />
            <text x={layout.left - 6} y={y + 3.5} textAnchor="end" fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * layout.typeScale)}>
              {formatAxis(tick, valueRef)}
            </text>
          </g>
        );
      })}
      <line x1={layout.left} y1={zeroLine} x2={layout.width - layout.right} y2={zeroLine} stroke="var(--dv-axis-line, #b9c2cb)" />
      {steps.map((step, index) => {
        const select = layout.onPointSelect;
        const x = layout.xAt(index);
        const yTop = yAt(Math.max(step.start, step.end));
        const barHeight = Math.max(1, Math.abs(yAt(step.start) - yAt(step.end)));
        const positive = step.value >= 0;
        const label = String(step.point.x ?? '');
        return (
          <g
            aria-label={select ? `Select ${label}` : undefined}
            key={step.point.i}
            onClick={select ? () => select(step.point) : undefined}
            onKeyDown={select ? (event) => {
              if (event.key === 'Enter' || event.key === ' ') {select(step.point);}
            } : undefined}
            role={select ? 'button' : undefined}
            style={select ? { cursor: 'pointer' } : undefined}
            tabIndex={select ? 0 : undefined}
          >
            <rect
              className="dv-bar"
              data-waterfall-sign={positive ? 'up' : 'down'}
              fill={positive ? 'var(--dv-accent, #4e79a7)' : '#e15759'}
              height={barHeight}
              rx="1.5"
              width={barWidth}
              x={x - barWidth / 2}
              y={yTop}
            >
              <title>{`${label}: ${formatAxis(step.value, valueRef)} (acumulado ${formatAxis(step.end, valueRef)})`}</title>
            </rect>
            {index < steps.length - 1 && (
              <line
                stroke="var(--dv-grid, #9aa6b2)"
                strokeDasharray="2 2"
                x1={x + barWidth / 2}
                x2={layout.xAt(index + 1) - barWidth / 2}
                y1={yAt(step.end)}
                y2={yAt(step.end)}
              />
            )}
            {renderBottomAxisLabel(layout.bottomLabels[index] ?? label, index, x, layout.baseline, layout.axisPlan, Math.max(7, 10 * layout.typeScale))}
          </g>
        );
      })}
    </g>
  );
}

interface ColumnLayout {
  axisPlan: AxisDensityPlan;
  axes?: React.ReactNode;
  baseline: number;
  bottom: number;
  bottomLabels: Scalar[];
  height: number;
  innerHeight: number;
  innerWidth: number;
  left: number;
  onPointSelect?: (point: Point) => void;
  right: number;
  top: number;
  typeScale: number;
  width: number;
  xAt: (index: number) => number;
}

/** Segmento de una categoría: serie, valor y posición apilada (rank descendente). */
interface RibbonSegment {
  after: number;
  before: number;
  series: string;
  value: number;
}

/**
 * Gráfico de cintas (PBI ribbon): columnas apiladas con segmentos ordenados por
 * rank dentro de cada categoría y bandas que conectan la misma serie entre
 * categorías adyacentes. Versión estática: la animación de PBI no aplica.
 */
function renderRibbon(ordered: Point[], valueRef: string, layout: ColumnLayout) {
  const seriesNames = [...new Set(ordered.map((point) => String(point.series ?? '')).filter((name) => name.trim().length > 0))].sort();
  const categories = [...new Set(ordered.map((point) => String(point.x ?? '')).filter(Boolean))];
  const valueOf = (category: string, series: string) => {
    const point = ordered.find((candidate) => String(candidate.x ?? '') === category && String(candidate.series ?? '') === series);
    return point ? numericY(point) : 0;
  };
  const segmentsByCategory = categories.map((category) => {
    const ranked = seriesNames
      .map((series) => ({ series, value: valueOf(category, series) }))
      .sort((left, right) => right.value - left.value);
    let cursor = 0;
    const segments: RibbonSegment[] = ranked.map((entry) => {
      const segment = { after: cursor + entry.value, before: cursor, series: entry.series, value: entry.value };
      cursor += entry.value;
      return segment;
    });
    return { category, segments, total: cursor };
  });
  const max = niceMax(Math.max(...segmentsByCategory.map((entry) => entry.total), 0));
  const yAt = (value: number) => layout.baseline - (Math.max(0, value) / max) * layout.innerHeight;
  const columnWidth = Math.max(10, Math.min(56, (layout.innerWidth / Math.max(categories.length, 1)) * 0.42));
  const xCenter = (categoryIndex: number) => layout.left + ((categoryIndex + 0.5) / Math.max(categories.length, 1)) * layout.innerWidth;
  const segmentOf = (categoryIndex: number, series: string) => (
    segmentsByCategory[categoryIndex]?.segments.find((segment) => segment.series === series)
  );
  let bandCount = 0;

  return (
    <g data-ribbon-categories={categories.length} data-rendered-mark-count={ordered.filter((point) => numericY(point) > 0).length}>
      {layout.axes}
      {categories.slice(0, -1).map((category, categoryIndex) => {
        const leftX = xCenter(categoryIndex) + columnWidth / 2;
        const rightX = xCenter(categoryIndex + 1) - columnWidth / 2;
        return seriesNames.map((series) => {
          const leftSegment = segmentOf(categoryIndex, series);
          const rightSegment = segmentOf(categoryIndex + 1, series);
          if (!leftSegment && !rightSegment) {return null;}
          const leftBefore = leftSegment?.before ?? 0;
          const leftAfter = leftSegment?.after ?? 0;
          const rightBefore = rightSegment?.before ?? 0;
          const rightAfter = rightSegment?.after ?? 0;
          bandCount += 1;
          return (
            <polygon
              className="dv-ribbon-band"
              data-ribbon-series={series}
              fill={seriesColor(Math.max(0, seriesNames.indexOf(series)))}
              fillOpacity="0.3"
              key={`${category}-${series}`}
              points={`${leftX},${yAt(leftBefore)} ${rightX},${yAt(rightBefore)} ${rightX},${yAt(rightAfter)} ${leftX},${yAt(leftAfter)}`}
            >
              <title>{`${category} → ${categories[categoryIndex + 1] ?? ''} · ${series}: ${formatAxis(leftSegment?.value ?? 0, valueRef)} → ${formatAxis(rightSegment?.value ?? 0, valueRef)}`}</title>
            </polygon>
          );
        });
      })}
      {segmentsByCategory.map((entry, categoryIndex) => {
        const x = xCenter(categoryIndex);
        return (
          <g key={entry.category}>
            {entry.segments.map((segment) => {
              if (segment.value <= 0) {return null;}
              const yTop = yAt(segment.after);
              const barHeight = Math.max(1, layout.baseline - yTop);
              return (
                <rect
                  className="dv-bar dv-ribbon-column"
                  fill={seriesColor(Math.max(0, seriesNames.indexOf(segment.series)))}
                  height={barHeight}
                  key={segment.series}
                  width={columnWidth}
                  x={x - columnWidth / 2}
                  y={yTop}
                >
                  <title>{`${entry.category} / ${segment.series}: ${formatAxis(segment.value, valueRef)}`}</title>
                </rect>
              );
            })}
            <text textAnchor="middle" x={x} y={layout.baseline + 14} fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * layout.typeScale)}>
              <title>{entry.category}</title>
              {truncateLabel(compactCategoryLabel(entry.category), 12)}
            </text>
          </g>
        );
      })}
      {renderSeriesLegend(seriesNames, layout.height, Math.max(7, 9.5 * layout.typeScale))}
      <g data-ribbon-bands={bandCount} />
    </g>
  );
}

/** Área apilada (PBI/Tableau stacked area): bandas acumuladas por serie sobre el eje temporal. */
function renderStackedArea(ordered: Point[], valueRef: string, layout: ColumnLayout) {
  const seriesNames = [...new Set(ordered.map((point) => String(point.series ?? '')).filter((name) => name.trim().length > 0))].sort();
  const categories = [...new Set(ordered.map((point) => String(point.x ?? '')).filter(Boolean))];
  const valueOf = (category: string, series: string) => {
    const point = ordered.find((candidate) => String(candidate.x ?? '') === category && String(candidate.series ?? '') === series);
    return point ? numericY(point) : 0;
  };
  const cumulative = categories.map((category) => {
    let cursor = 0;
    return seriesNames.map((series) => {
      cursor += valueOf(category, series);
      return cursor;
    });
  });
  const maxCum = niceMax(Math.max(...cumulative.map((row) => row[row.length - 1] ?? 0), 0));
  const yAt = (value: number) => layout.baseline - (Math.max(0, value) / maxCum) * layout.innerHeight;
  const xAt2 = (categoryIndex: number) => layout.left + ((categoryIndex + 0.5) / Math.max(categories.length, 1)) * layout.innerWidth;

  return (
    <g data-stacked-area-series={seriesNames.length} data-rendered-mark-count={ordered.length}>
      {layout.axes}
      {seriesNames.map((series, seriesIndex) => {
        const topPoints = categories.map((_, categoryIndex) => {
          const cum = cumulative[categoryIndex]?.[seriesIndex] ?? 0;
          return `${xAt2(categoryIndex)},${yAt(cum)}`;
        });
        const bottomPoints = categories.map((_, categoryIndex) => {
          const cumBelow = seriesIndex > 0 ? cumulative[categoryIndex]?.[seriesIndex - 1] ?? 0 : 0;
          return `${xAt2(categoryIndex)},${yAt(cumBelow)}`;
        });
        const polygon = `${topPoints.join(' ')} ${[...bottomPoints].reverse().join(' ')}`;
        return (
          <g key={series}>
            <polygon className="dv-stacked-area-band" data-stacked-area-series={series} fill={seriesColor(seriesIndex)} fillOpacity="0.55" points={polygon}>
              <title>{`${series}: máx ${formatAxis(Math.max(...categories.map((_, categoryIndex) => cumulative[categoryIndex]?.[seriesIndex] ?? 0)), valueRef)} acumulado`}</title>
            </polygon>
            <polyline fill="none" points={topPoints.join(' ')} stroke={seriesColor(seriesIndex)} strokeWidth="1.5" />
          </g>
        );
      })}
      {categories.map((category, categoryIndex) => (
        <text key={category} textAnchor="middle" x={xAt2(categoryIndex)} y={layout.baseline + 14} fill="var(--dv-axis-text, #6b7784)" fontSize={Math.max(7, 10 * layout.typeScale)}>
          <title>{category}</title>
          {truncateLabel(String(compactCategoryLabel(category)), 12)}
        </text>
      ))}
      {renderSeriesLegend(seriesNames, layout.height, Math.max(7, 9.5 * layout.typeScale))}
    </g>
  );
}

