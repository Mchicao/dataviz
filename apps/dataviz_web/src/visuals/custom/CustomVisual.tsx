import React, { useEffect, useRef, useState } from 'react';
import type { Scalar } from '../../runtime/types';
import type { VisualProps } from '../base/types';
import { columnForRef, formatScalar } from '../base/data';
import { displayTitle } from '../base/title';
import { CUSTOM_RENDER_BUDGET_UNITS, estimateActualCost } from './spec';
import type { CustomVisualLayer, CustomVisualSpec } from './spec';

/**
 * Visual combinado declarativo: superpone capas cerradas (bar, line, area,
 * point) sobre los roles del visual según `custom_spec`.
 *
 * El costo se estima con los datos reales ya limitados a `max_data_points`;
 * si supera el presupuesto de render no se dibuja ninguna marca y se muestra
 * un alert con la estimación y un botón de override ligado a la spec y costo
 * observados. Un cambio de datos/spec o un remount vuelve al estado bloqueado.
 */

const FALLBACK_PLOT = { height: 300, width: 480 };
const LAYER_COLORS = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc949'];

/** Medición del marco en píxeles CSS (jsdom/primer frame usan la caja clásica). */
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

interface LayerSeries {
  layer: CustomVisualLayer;
  index: number;
  xs: Scalar[];
  ys: Scalar[];
}

/** Marcas SVG que aporta una capa con n puntos de datos. */
function layerMarkCount(layer: CustomVisualLayer, points: number): number {
  if (layer.mark === 'bar' || layer.mark === 'point') {return points;}
  return 1 + (layer.show_points ? points : 0);
}

function numericValue(value: Scalar): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/** Techo "bonito" para la escala Y (múltiplos de 5·10^k/2). */
function niceCeil(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {return 1;}
  const power = 10 ** Math.floor(Math.log10(value));
  const step = power / 2;
  return Math.ceil(value / step) * step;
}

const layerColor = (index: number, layer: CustomVisualLayer): string => (
  layer.color ?? LAYER_COLORS[index % LAYER_COLORS.length]
);

function truncateLabel(label: string, maxChars: number): string {
  return label.length <= maxChars ? label : `${label.slice(0, Math.max(1, maxChars - 1))}…`;
}

export const CustomVisual: React.FC<VisualProps> = ({ visual }) => {
  const frame = useMeasuredSize();
  const [approvedCostSignature, setApprovedCostSignature] = useState<string | null>(null);
  const spec: CustomVisualSpec | undefined = visual.custom_spec;
  const title = displayTitle(visual.title, visual.name);
  const sid = `dvc-${  visual.name.replaceAll(/[^a-zA-Z0-9_-]/g, '_')}`;

  if (!spec) {
    return (
      <section
        ref={frame.ref}
        className="dv-custom dv-custom--invalid"
        role="alert"
        style={frameStyle()}
      >
        <h3 style={headingStyle()}>{title}</h3>
        <p>El visual combinado requiere una spec válida en <code>custom_visual_spec</code>.</p>
      </section>
    );
  }

  const series: LayerSeries[] = spec.layers.map((layer, index) => {
    const xRole = visual.roles[layer.x_role];
    const yRole = visual.roles[layer.y_role];
    return {
      index,
      layer,
      xs: xRole ? columnForRef(xRole.ref, xRole.data) : [],
      ys: yRole ? columnForRef(yRole.ref, yRole.data) : [],
    };
  });
  const sourceCount = Math.max(0, ...series.map((item) => Math.max(item.xs.length, item.ys.length)));
  const limit = spec.max_data_points;
  const dataPoints = Math.min(sourceCount, limit);
  const truncated = sourceCount > limit;
  const visible = series.map((item) => ({
    ...item,
    xs: item.xs.slice(0, limit),
    ys: item.ys.slice(0, limit),
  }));
  const cost = estimateActualCost(spec, dataPoints, sourceCount);
  const costSignature = JSON.stringify([spec, dataPoints, cost.render_units]);
  const override = approvedCostSignature === costSignature;
  const markCount = visible.reduce((sum, item) => (
    sum + layerMarkCount(item.layer, Math.max(item.xs.length, item.ys.length))
  ), 0);
  const hasData = visible.some((item) => item.xs.length > 0 || item.ys.length > 0);

  if (!cost.within_render_budget && !override) {
    return (
      <section
        ref={frame.ref}
        className="dv-custom dv-custom--blocked"
        role="alert"
        data-cost-render-units={cost.render_units}
        data-cost-query-cells={cost.query_cells}
        data-render-budget={CUSTOM_RENDER_BUDGET_UNITS}
        style={frameStyle()}
      >
        <h3 style={headingStyle()}>{title}</h3>
        <p>
          {`Visual combinado de alto costo: ${cost.render_units} unidades de render estimadas con ${cost.data_points} puntos en ${spec.layers.length} capa(s) (presupuesto ${CUSTOM_RENDER_BUDGET_UNITS}). No se dibujaron marcas.`}
        </p>
        <button onClick={() => setApprovedCostSignature(costSignature)} type="button">Renderizar de todas formas</button>
      </section>
    );
  }

  if (!hasData) {
    return (
      <section
        ref={frame.ref}
        className="dv-custom dv-custom--empty"
        role="img"
        aria-labelledby={`${sid}-title ${sid}-desc`}
        style={frameStyle()}
      >
        <span id={`${sid}-title`} style={{ color: '#94a3b8' }}>{title}: no data</span>
        <span id={`${sid}-desc`} style={{ display: 'none' }}>0 points</span>
      </section>
    );
  }

  const typeScale = Math.min(
    1.25,
    Math.max(0.6, Math.min(frame.width / FALLBACK_PLOT.width, frame.height / FALLBACK_PLOT.height)),
  );

  // Escala Y compartida por todas las capas, con línea base en 0.
  const yNumbers = visible.flatMap((item) => item.ys.map(numericValue)).filter((value): value is number => value !== null);
  const [rawYLo, rawYHi] = yNumbers.reduce(
    ([lo, hi], value) => [Math.min(lo, value), Math.max(hi, value)],
    [0, 0],
  );
  const yLo = rawYLo;
  const yHi = niceCeil(rawYHi);
  const span = yHi - yLo || 1;

  const right = 16;
  const top = 14;
  const bottom = 44;
  const left = Math.min(64, Math.max(38, frame.width * 0.12));
  const innerWidth = Math.max(1, frame.width - left - right);
  const innerHeight = Math.max(1, frame.height - top - bottom);
  const baseline = frame.height - bottom;
  const bandCount = Math.max(...visible.map((item) => item.xs.length), 1);
  const xAt = (index: number) => left + ((index + 0.5) / bandCount) * innerWidth;
  const yAt = (value: number) => baseline - ((value - yLo) / span) * innerHeight;

  const tickCount = 4;
  const ticks = Array.from({ length: tickCount + 1 }, (_, index) => yLo + (span * index) / tickCount);
  const labelSeries = visible.find((item) => item.xs.length > 0);
  const labelStride = Math.max(1, Math.ceil((labelSeries?.xs.length ?? 0) / 10));

  return (
    <section
      ref={frame.ref}
      className="dv-custom"
      data-custom-layers={spec.layers.length}
      data-rendered-mark-count={markCount}
      data-source-mark-count={sourceCount}
      data-cost-render-units={cost.render_units}
      data-cost-query-cells={cost.query_cells}
      data-truncated={truncated ? 'true' : 'false'}
      style={frameStyle()}
    >
      <h3 style={headingStyle(typeScale)}>
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</span>
        {truncated && (
          <span className="dv-custom-limit">{`Límite · ${dataPoints} de ${sourceCount} filas`}</span>
        )}
      </h3>
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${frame.width} ${frame.height}`}
        role="img"
        style={{ display: 'block', flex: '1 1 auto', minHeight: 0, minWidth: 0 }}
        aria-labelledby={`${sid}-title ${sid}-desc`}
      >
        <title id={`${sid}-title`}>{`${title} (custom)`}</title>
        <desc id={`${sid}-desc`}>
          {`${spec.layers.length} capa(s) (${spec.layers.map((layer) => layer.mark).join(', ')}) sobre ${visible.map((item) => item.layer.y_role).join(' / ')} por ${labelSeries?.layer.x_role ?? 'índice'}; ${dataPoints} puntos${truncated ? `, truncados de ${sourceCount} filas` : ''}, rango ${formatScalar(yLo)} a ${formatScalar(yHi)}, costo ${cost.render_units} unidades de render.`}
        </desc>
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={left}
              x2={frame.width - right}
              y1={yAt(tick)}
              y2={yAt(tick)}
              stroke="var(--dv-grid, #e2e7ec)"
              strokeDasharray={tick === 0 ? undefined : '3 3'}
            />
            <text
              x={left - 6}
              y={yAt(tick) + 3.5}
              textAnchor="end"
              fill="var(--dv-axis-text, #6b7784)"
              fontSize={Math.max(7, 10 * typeScale)}
            >
              {formatScalar(tick)}
            </text>
          </g>
        ))}
        <line x1={left} x2={frame.width - right} y1={baseline} y2={baseline} stroke="var(--dv-axis-line, #b9c2cb)" />
        {labelSeries?.xs.map((label, index) => (
          index % labelStride === 0 && label !== null
            ? (
              <text
                key={index}
                x={xAt(index)}
                y={baseline + 16}
                textAnchor="middle"
                fill="var(--dv-axis-text, #6b7784)"
                fontSize={Math.max(7, 10 * typeScale)}
              >
                {truncateLabel(String(label), 12)}
              </text>
            )
            : null
        ))}
        {visible.map((item) => renderLayer(item, xAt, yAt, innerWidth, bandCount))}
        {renderLayerLegend(visible, frame.width, baseline)}
      </svg>
    </section>
  );
};

function renderLayer(
  item: LayerSeries,
  xAt: (index: number) => number,
  yAt: (value: number) => number,
  innerWidth: number,
  bandCount: number,
) {
  const { layer, index, xs, ys } = item;
  const color = layerColor(index, layer);
  const points = Math.max(xs.length, ys.length);
  const valueAt = (i: number) => numericValue(ys[i] ?? null) ?? 0;
  const tipAt = (i: number) => (
    `${String(xs[i] ?? i)}: ${formatScalar(ys[i] ?? null)} (${layer.y_role})`
  );

  if (layer.mark === 'bar') {
    const barWidth = Math.max(3, Math.min(52, (innerWidth / Math.max(bandCount, 1)) * 0.62));
    return (
      <g key={`layer-${index}`} data-layer-mark="bar">
        {Array.from({ length: points }, (_, i) => {
          const y = yAt(valueAt(i));
          const zero = yAt(0);
          const top = Math.min(y, zero);
          const height = Math.max(0.5, Math.abs(zero - y));
          return (
            <rect
              className="dv-bar"
              fill={color}
              fillOpacity="0.85"
              height={height}
              key={i}
              rx="2"
              width={barWidth}
              x={xAt(i) - barWidth / 2}
              y={top}
            >
              <title>{tipAt(i)}</title>
            </rect>
          );
        })}
      </g>
    );
  }

  if (layer.mark === 'point') {
    return (
      <g key={`layer-${index}`} data-layer-mark="point">
        {Array.from({ length: points }, (_, i) => (
          <circle
            cx={xAt(i)}
            cy={yAt(valueAt(i))}
            fill={color}
            key={i}
            r="3.5"
          >
            <title>{tipAt(i)}</title>
          </circle>
        ))}
      </g>
    );
  }

  const path = Array.from({ length: points }, (_, i) => `${xAt(i)},${yAt(valueAt(i))}`).join(' ');
  return (
    <g key={`layer-${index}`} data-layer-mark={layer.mark}>
      {layer.mark === 'area' && points > 0 && (
        <polygon
          fill={color}
          fillOpacity="0.22"
          points={`${xAt(0)},${yAt(0)} ${path} ${xAt(points - 1)},${yAt(0)}`}
        />
      )}
      {points > 1 && (
        <polyline
          fill="none"
          points={path}
          stroke={color}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
        />
      )}
      {layer.show_points && Array.from({ length: points }, (_, i) => (
        <circle
          cx={xAt(i)}
          cy={yAt(valueAt(i))}
          fill={color}
          key={i}
          r="3"
        >
          <title>{tipAt(i)}</title>
        </circle>
      ))}
    </g>
  );
}

function renderLayerLegend(visible: LayerSeries[], width: number, baseline: number) {
  return (
    <g aria-label="Capas" transform={`translate(0 ${baseline + 30})`}>
      {visible.map((item, index) => {
        const x = 8 + index * (Math.min(width, 420) / Math.max(visible.length, 1));
        return (
          <g key={`legend-${index}`}>
            <rect fill={layerColor(index, item.layer)} height="8" rx="2" width="8" x={x} y="0" />
            <text fill="var(--dv-axis-text, #6b7784)" fontSize="9" x={x + 12} y="8">
              {truncateLabel(item.layer.y_role, 16)}
            </text>
          </g>
        );
      })}
    </g>
  );
}

const frameStyle = (): React.CSSProperties => ({
  backgroundColor: 'var(--dv-card-bg, #ffffff)',
  border: '1px solid var(--dv-card-border, #d8e0e7)',
  borderRadius: 6,
  boxSizing: 'border-box',
  display: 'flex',
  flexDirection: 'column',
  gap: '3px',
  height: '100%',
  minHeight: 0,
  minWidth: 0,
  overflow: 'hidden',
  padding: '6px',
  width: '100%',
});

const headingStyle = (typeScale = 1): React.CSSProperties => ({
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
