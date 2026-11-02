import type { Scalar } from "../../runtime/types";

/**
 * Heurística genérica de densidad de ejes para los renderers cartesianos.
 *
 * Decide cuántas etiquetas de categoría caben legiblemente en un ancho dado y
 * cómo presentarlas (íntegras, rotadas o con stride limpio), sin conocer la demo:
 * sólo el número de puntos, el ancho disponible y la longitud de las etiquetas.
 */

/** Modo de presentación decidido para el eje de categorías. */
export type LabelMode = "horizontal" | "rotated" | "truncated";

export interface AxisDensityPlan {
  /** Índices de puntos que reciben tick propio (uno de cada `stride`). */
  readonly stride: number;
  /** Cómo se dibuja cada etiqueta. */
  readonly mode: LabelMode;
  /** Máximo de caracteres visibles por etiqueta antes de `.` (0 = sin tope). */
  readonly maxChars: number;
}

/** Ancho mínimo razonable por carácter horizontal a ~8-9px. */
const CHARS_PER_PIXEL = 1 / 6.2;
/** Espacio horizontal mínimo por tick horizontal para evitar colisiones: ~54px. */
const HORIZONTAL_MIN_PX_PER_TICK = 54;
/** Un tick rotado ocupa ~24px de ancho por punto. */
const ROTATED_MIN_PX_PER_TICK = 24;
/** Truncado: nunca menos de 4 caracteres visibles. */
const MIN_TRUNCATED_CHARS = 4;

function maxLabelLength(labels: Scalar[]): number {
  let longest = 0;
  for (const label of labels) {
    const len = String(label ?? "").length;
    if (len > longest) {longest = len;}
  }
  return longest;
}

/**
 * Calcula el plan de ticks/etiquetas.
 *
 * @param labels valores del eje de categorías
 * @param availableWidth px disponibles para el eje (innerWidth)
 * @param rotatedSpace altura reservada a etiquetas rotadas (px); si es 0 no se
 *   puede rotar y se trunca en su lugar.
 */
export function planAxisDensity(
  labels: Scalar[],
  availableWidth: number,
  rotatedSpace = 60,
): AxisDensityPlan {
  const count = Math.max(labels.length, 1);
  const longest = maxLabelLength(labels);
  const labelWidth = Math.max(HORIZONTAL_MIN_PX_PER_TICK, longest * 7.5 + 8);
  const maxHorizontalTicks = Math.max(2, Math.floor(availableWidth / labelWidth));

  if (count <= maxHorizontalTicks) {
    return { maxChars: 0, mode: "horizontal", stride: 1 };
  }

  const canRotate = rotatedSpace >= 30;
  const maxRotatedTicks = Math.max(2, Math.floor(availableWidth / ROTATED_MIN_PX_PER_TICK));
  if (canRotate && count <= maxRotatedTicks) {
    return { maxChars: 0, mode: "rotated", stride: 1 };
  }

  // Caso denso (e.g. series de tiempo de 30 a 90 días en gráficos de línea/área):
  // Calculamos una cantidad óptima y espaciada de ticks (entre 4 y 8 ticks) con espaciado uniforme:
  const targetTicks = Math.max(3, Math.min(8, Math.floor(availableWidth / 75)));
  const stride = Math.max(1, Math.ceil(count / targetTicks));
  const shown = Math.ceil(count / stride);
  const slotWidth = availableWidth / shown;
  const chars = Math.max(MIN_TRUNCATED_CHARS, Math.floor(slotWidth * CHARS_PER_PIXEL));

  return { maxChars: longest > chars ? chars : 0, mode: "horizontal", stride };
}

/** Trunca `label` a `maxChars` caracteres con elipsis final. */
export function truncateLabel(label: Scalar, maxChars: number): string {
  const text = String(label ?? "");
  if (maxChars <= 0 || text.length <= maxChars) {return text;}
  if (maxChars <= 1) {return text.slice(0, Math.max(maxChars, 1));}
  return `${text.slice(0, maxChars - 1).trimEnd()}.`;
}
