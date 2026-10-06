/**
 * Rendering-side data helpers.
 *
 * The interpreter binds each role to a `QueryResult` (scalar or `Row[]`). These
 * helpers normalize that into the column-of-scalars shape the visuals consume,
 * using a documented convention for extracting a column from a row array.
 */

import type { InterpretedVisual, QueryResult, Row, Scalar } from '../../runtime/types';

/**
 * Normalize a role's query result into a column of scalars.
 *
 * Convention:
 * - `null`/`undefined` -> `[]` (missing data).
 * - scalar (number/string) -> single-element column `[scalar]`.
 * - `Scalar[]` -> returned as-is (a flat per-field column).
 * - `Row[]` -> one cell per row. The column key is the **last segment** of the
 *   neutral reference (e.g. ref `entity:sales.region` -> column `region`). If
 *   that key is absent from a row, the row's first value is used as a fallback
 *   so partial results still render.
 */
export function columnForRef(ref: string, data: QueryResult | undefined): Scalar[] {
  if (data === null || data === undefined) {return [];}
  if (typeof data === 'number' || typeof data === 'string') {return [data];}
  if (Array.isArray(data)) {
    if (data.length === 0) {return [];}
    const first = data[0];
    // Flat column of scalars (per-field query result).
    if (first === null || typeof first !== 'object') {return data as Scalar[];}
    // Row[]: extract the column named by the last segment of the reference.
    const col = (ref.split('.').pop() ?? '').trim();
    return (data as Row[]).map((row) => {
      if (row === null) {return null;}
      if (col && col in row) {return row[col];}
      const firstVal = Object.values(row)[0];
      return firstVal === undefined ? null : firstVal;
    });
  }
  return [];
}

/** Reduce a role's query result to a single scalar (first cell). */
export function scalarForRef(ref: string, data: QueryResult | undefined): Scalar {
  const col = columnForRef(ref, data);
  return col.length > 0 ? col[0] : null;
}

/** Format a scalar for display: em-dash for missing, locale grouping for numbers. */
export function formatScalar(value: Scalar): string {
  if (value === null || value === undefined) {return '\u2014';}
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value.toLocaleString('en-US') : String(value);
  }
  return String(value);
}

/**
 * Look up the first role present on a visual by candidate name, falling back to
 * the first declared role. Uses `Map.get` so absence is type-visible (safe under
 * strict index-access rules).
 */
export function getRole(
  visual: InterpretedVisual,
  names: readonly string[],
): { ref: string; data: QueryResult | undefined } | undefined {
  const byName = new Map(Object.entries(visual.roles));
  for (const n of names) {
    const r = byName.get(n);
    if (r) {return r;}
  }
  for (const r of byName.values()) {return r;}
  return undefined;
}
