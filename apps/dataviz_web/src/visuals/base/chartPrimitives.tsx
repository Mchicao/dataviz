import React, { useEffect, useRef, useState } from 'react';
import type { Scalar } from '../../runtime/types';
import { formatScalar } from './data';

/** Fallback render box before the container is measured (also the jsdom size). */
export const FALLBACK_PLOT = { height: 300, width: 480 };

/** Escala tipográfica compartida: se deriva del marco medido, no del zoom. */
export function typeScaleFor(width: number, height: number): number {
  return Math.min(
    1.25,
    Math.max(0.6, Math.min(width / FALLBACK_PLOT.width, height / FALLBACK_PLOT.height)),
  );
}

/**
 * Mide el marco del visual en píxeles CSS para dibujar el SVG a escala 1:1.
 * Sin medición disponible (jsdom, primer frame) conserva la caja clásica 480×300.
 */
export function useMeasuredSize(): { ref: React.RefObject<HTMLElement | null>; width: number; height: number } {
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

/** Padding/gap del marco escalados al tamaño medido del contenedor. */
export function frameStyle(typeScale: number): React.CSSProperties {
  return {
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
  };
}

export function headingStyle(typeScale: number): React.CSSProperties {
  return {
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
  };
}

export const headingTitleStyle: React.CSSProperties = {
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
};

export const chartStyle: React.CSSProperties = {
  display: 'block',
  flex: '1 1 auto',
  minHeight: 0,
  minWidth: 0,
};

interface ChartFrameProps {
  sid: string;
  title: string;
  className: string;
  typeScale: number;
  width: number;
  height: number;
  /** Contenido del `<desc>` para lectores de pantalla. */
  desc: string;
  /** Título accesible del svg; por defecto el mismo del encabezado. */
  svgTitle?: string;
  /** Nota opcional junto al título (p. ej. muestreo de densidad). */
  note?: string;
  /** Atributos extra del `<section>` (data-* de densidad/marcas). */
  sectionProps?: Record<string, string | number>;
  children: React.ReactNode;
}

interface ChartFrameHandle {
  ref: React.RefObject<HTMLElement | null>;
}

/**
 * Esqueleto compartido de visual: section medible + título + svg accesible
 * (role="img" con `<title>`/`<desc>`), con estado vacío explícito.
 */
export function ChartFrame({
  sid,
  title,
  className,
  typeScale,
  width,
  height,
  desc,
  note,
  sectionProps,
  children,
  ref,
}: ChartFrameProps & ChartFrameHandle): React.ReactElement {
  return (
    <section
      className={className}
      ref={ref}
      style={frameStyle(typeScale)}
      {...sectionProps}
    >
      <h3 id={`${sid}-heading`} style={headingStyle(typeScale)}>
        <span style={headingTitleStyle}>{title}</span>
        {note ? <span className="dv-density-note">{note}</span> : null}
      </h3>
      <svg
        width="100%"
        height="100%"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        style={chartStyle}
        aria-labelledby={`${sid}-title ${sid}-desc`}
      >
        <title id={`${sid}-title`}>{title}</title>
        <desc id={`${sid}-desc`}>{desc}</desc>
        {children}
      </svg>
    </section>
  );
}

/** Estado vacío compartido: título + razón, sin marcas. */
export function EmptyVisual({
  sid,
  title,
  className,
  reason,
  detail = '0 marks',
  ref,
  typeScale,
}: {
  sid: string;
  title: string;
  className: string;
  reason: string;
  detail?: string;
  typeScale: number;
  ref: React.RefObject<HTMLElement | null>;
}): React.ReactElement {
  return (
    <section
      className={`${className} ${className}--empty`}
      ref={ref}
      role="img"
      aria-labelledby={`${sid}-title ${sid}-desc`}
      style={frameStyle(typeScale)}
    >
      <span id={`${sid}-title`} style={{ color: '#94a3b8' }}>{title}: {reason}</span>
      <span id={`${sid}-desc`} style={{ display: 'none' }}>{detail}</span>
    </section>
  );
}

/** Min/max "bonito" para ejes: techo redondo cercano (piso 1 si no hay positivos). */
export function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) {return 1;}
  const rawStep = value / 5;
  const power = 10 ** Math.floor(Math.log10(rawStep));
  const fraction = rawStep / power;
  const niceFraction = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  const step = niceFraction * power;
  return Math.ceil(value / step) * step;
}

export function isRatioRef(ref: string): boolean {
  return /(discount|percent|percentage|share|ratio|margin|%)/i.test(ref);
}

/** Formato compacto sin ceros triviales: $2.5M, $500K (el valor exacto vive en el tooltip). */
function compactNumber(value: number, options: Intl.NumberFormatOptions): string {
  const text = new Intl.NumberFormat('en-US', options).format(value);
  return text.replaceAll(/\.0(?=[KMB])/g, '');
}

/** Formato de eje: compacto para magnitudes grandes (el valor exacto vive en el tooltip). */
export function formatAxis(value: number, ref: string): string {
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

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Compacta fechas ISO y trimestres a la forma "Jan '26" / "Q1 '26". */
export function compactCategoryLabel(label: Scalar): Scalar {
  if (typeof label !== 'string') {return label;}
  const quarter = label.match(/^(\d{4})-Q([1-4])$/);
  if (quarter) {return `Q${quarter[2]} '${quarter[1].slice(-2)}`;}
  const isoDate = label.match(/^(\d{4})-(\d{2})(?:-(\d{2}))?/);
  if (!isoDate) {return label;}
  const month = MONTH_LABELS[Number(isoDate[2]) - 1];
  return month ? `${month} '${isoDate[1].slice(-2)}` : label;
}

export const BAR_COLORS = ['#4e79a7', '#f28e2b', '#e15759', '#76b7b2', '#59a14f', '#edc949'];

export function seriesColor(index: number): string {
  return index === 0 ? 'var(--dv-accent, #4e79a7)' : BAR_COLORS[index % BAR_COLORS.length];
}

/** Escalar → número finito (0 para no numéricos), para escalas y acumulados. */
export function numericOf(value: Scalar): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}

/** Interpolación lineal entre dos hex de color, para escalas continuas (heatmap). */
export function interpolateColor(ratio: number, from: string, to: string): string {
  const clamp = Math.min(1, Math.max(0, Number.isFinite(ratio) ? ratio : 0));
  const parse = (hex: string) => {
    const value = hex.replaceAll('#', '');
    return [
      Number.parseInt(value.slice(0, 2), 16),
      Number.parseInt(value.slice(2, 4), 16),
      Number.parseInt(value.slice(4, 6), 16),
    ];
  };
  const [r1, g1, b1] = parse(from);
  const [r2, g2, b2] = parse(to);
  const mix = (a: number, b: number) => Math.round(a + (b - a) * clamp);
  return `rgb(${mix(r1, r2)}, ${mix(g1, g2)}, ${mix(b1, b2)})`;
}
