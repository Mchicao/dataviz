import React from 'react';
import type { InterpretedVisual, Row } from '../../runtime/types';
import type { MatrixPivotResult, MatrixCell } from './types';
import { displayTitle } from '../base/title';

/**
 * Extract field name from a neutral reference string (e.g. 'entity:sales.region' -> 'region', 'measure:sales' -> 'sales').
 */
export function fieldFromRef(ref: string | undefined, fallback: string): string {
  if (!ref) {return fallback;}
  const parts = ref.split(/[:.]/);
  const last = parts.at(-1);
  return last && last.trim() ? last.trim() : fallback;
}

/**
 * Helper to pivot a flat dataset (Row[]) into a matrix layout.
 *
 * @param rows Input tabular rows
 * @param rowField Field name representing matrix rows (e.g. 'region')
 * @param colField Field name representing matrix columns (e.g. 'year')
 * @param valueField Field name representing cell measure value (e.g. 'sales')
 */
export function buildMatrixPivot(
  rows: Row[],
  rowField: string,
  colField: string,
  valueField: string,
): MatrixPivotResult {
  if (!rows || rows.length === 0) {
    return {
      cells: [],
      colHeaders: [],
      colTotals: {},
      grandTotal: 0,
      rowHeaders: [],
      rowTotals: {},
    };
  }

  const rowSet = new Set<string>();
  const colSet = new Set<string>();

  for (const r of rows) {
    const rVal = r[rowField] !== undefined && r[rowField] !== null ? String(r[rowField]) : 'Unknown';
    const cVal = r[colField] !== undefined && r[colField] !== null ? String(r[colField]) : 'Unknown';
    rowSet.add(rVal);
    colSet.add(cVal);
  }

  const rowHeaders = [...rowSet];
  const colHeaders = [...colSet];

  // Map for fast lookup: `${rowKey}:::${colKey}` -> cell value
  const valueMap = new Map<string, number | string | null>();
  for (const r of rows) {
    const rVal = r[rowField] !== undefined && r[rowField] !== null ? String(r[rowField]) : 'Unknown';
    const cVal = r[colField] !== undefined && r[colField] !== null ? String(r[colField]) : 'Unknown';
    const key = `${rVal}:::${cVal}`;
    const v = r[valueField];
    valueMap.set(key, (v as number | string | null) ?? null);
  }

  const cells: MatrixCell[][] = [];
  const rowTotals: Record<string, number> = {};
  const colTotals: Record<string, number> = {};
  let grandTotal = 0;

  for (const c of colHeaders) {
    colTotals[c] = 0;
  }

  for (let rIdx = 0; rIdx < rowHeaders.length; rIdx++) {
    const rHeader = rowHeaders[rIdx];
    cells[rIdx] = [];
    let rSum = 0;

    for (let cIdx = 0; cIdx < colHeaders.length; cIdx++) {
      const cHeader = colHeaders[cIdx];
      const rawVal = valueMap.get(`${rHeader}:::${cHeader}`) ?? null;

      let numVal = 0;
      let formattedValue = '-';
      if (typeof rawVal === 'number') {
        numVal = rawVal;
        formattedValue = rawVal.toLocaleString();
        rSum += numVal;
        colTotals[cHeader] = (colTotals[cHeader] || 0) + numVal;
        grandTotal += numVal;
      } else if (typeof rawVal === 'string' && !isNaN(Number(rawVal))) {
        numVal = Number(rawVal);
        formattedValue = rawVal;
        rSum += numVal;
        colTotals[cHeader] = (colTotals[cHeader] || 0) + numVal;
        grandTotal += numVal;
      } else if (rawVal !== null) {
        formattedValue = String(rawVal);
      }

      cells[rIdx][cIdx] = {
        colKey: cHeader,
        formattedValue,
        rowKey: rHeader,
        value: rawVal,
      };
    }
    rowTotals[rHeader] = rSum;
  }

  return {
    cells,
    colHeaders,
    colTotals,
    grandTotal,
    rowHeaders,
    rowTotals,
  };
}

export interface MatrixVisualProps {
  visual: InterpretedVisual;
  rowField?: string;
  colField?: string;
  valueField?: string;
  showSubtotals?: boolean;
}

/**
 * Pivot Matrix Visual Component for DataVIZ Web Engine.
 *
 * Fulfills Corpus Gap 1: Multi-dimensional matrix pivoting with row/col headers,
 * subtotals, grand total, and full ARIA grid accessibility.
 */
export const MatrixVisual: React.FC<MatrixVisualProps> = ({
  visual,
  rowField = 'row',
  colField = 'col',
  valueField = 'value',
  showSubtotals = true,
}) => {
  const title = displayTitle(visual.title, visual.name);
  let rowsData: Row[] = [];
  
  if (visual.roles.entity?.data && Array.isArray(visual.roles.entity.data)) {
    rowsData = visual.roles.entity.data as Row[];
  } else if (visual.roles.data?.data && Array.isArray(visual.roles.data.data)) {
    rowsData = visual.roles.data.data as Row[];
  } else {
    for (const roleKey of Object.keys(visual.roles)) {
      const d = visual.roles[roleKey]?.data;
      if (Array.isArray(d)) {
        rowsData = d as Row[];
        break;
      }
    }
  }

  const resolvedRowField = fieldFromRef(visual.roles.rows?.ref, rowField);
  const resolvedColField = fieldFromRef(visual.roles.columns?.ref, colField);
  const resolvedValueField = fieldFromRef(visual.roles.value?.ref, valueField);

  const matrix = buildMatrixPivot(rowsData, resolvedRowField, resolvedColField, resolvedValueField);

  if (matrix.rowHeaders.length === 0 || matrix.colHeaders.length === 0) {
    return (
      <div
        className="dataviz-matrix-empty"
        role="region"
        aria-label={`${title} - Empty Matrix`}
        tabIndex={0}
      >
        <h4>{title}</h4>
        <p>No matrix data available to render.</p>
      </div>
    );
  }

  return (
    <div className="dataviz-matrix-container" role="region" aria-label={title} tabIndex={0}>
      <h4 className="dataviz-matrix-title">{title}</h4>
      <table
        className="dataviz-matrix-table"
        role="grid"
        aria-label={`Pivot matrix for ${title}`}
      >
        <caption className="sr-only">{`Matrix visualization displaying ${resolvedRowField} by ${resolvedColField}`}</caption>
        <thead>
          <tr >
            <th role="columnheader" scope="col" className="dataviz-matrix-header-corner">
              {`${resolvedRowField} / ${resolvedColField}`}
            </th>
            {matrix.colHeaders.map((col) => (
              <th key={col} role="columnheader" scope="col" className="dataviz-matrix-col-header">
                {col}
              </th>
            ))}
            {showSubtotals && (
              <th role="columnheader" scope="col" className="dataviz-matrix-total-header">
                Total
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {matrix.rowHeaders.map((rHeader, rIdx) => (
            <tr key={rHeader} >
              <th role="rowheader" scope="row" className="dataviz-matrix-row-header">
                {rHeader}
              </th>
              {matrix.colHeaders.map((cHeader, cIdx) => {
                const cell = matrix.cells[rIdx][cIdx];
                return (
                  <td
                    key={`${rHeader}-${cHeader}`}
                    
                    className="dataviz-matrix-cell"
                    aria-label={`${rHeader}, ${cHeader}: ${cell.formattedValue}`}
                  >
                    {cell.formattedValue}
                  </td>
                );
              })}
              {showSubtotals && (
                <td
                  
                  className="dataviz-matrix-cell dataviz-matrix-row-total"
                  aria-label={`${rHeader} Total: ${matrix.rowTotals[rHeader].toLocaleString()}`}
                >
                  {`${matrix.rowTotals[rHeader].toLocaleString()}`}
                </td>
              )}
            </tr>
          ))}
        </tbody>
        {showSubtotals && (
          <tfoot>
            <tr  className="dataviz-matrix-footer-row">
              <th role="rowheader" scope="row" className="dataviz-matrix-total-header">
                Total
              </th>
              {matrix.colHeaders.map((col) => (
                <td
                  key={`total-${col}`}
                  
                  className="dataviz-matrix-cell dataviz-matrix-col-total"
                  aria-label={`Column Total for ${col}: ${matrix.colTotals[col].toLocaleString()}`}
                >
                  {`${matrix.colTotals[col].toLocaleString()}`}
                </td>
              ))}
              <td
                
                className="dataviz-matrix-cell dataviz-matrix-grand-total"
                aria-label={`Grand Total: ${matrix.grandTotal.toLocaleString()}`}
              >
                {`${matrix.grandTotal.toLocaleString()}`}
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
};
