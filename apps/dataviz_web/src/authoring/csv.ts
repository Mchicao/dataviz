import type { Scalar } from '../runtime/types';
import {
  addEntity,
  addMetric,
  removeEntity,
  removeFilter,
  removeMetric,
  removeParameter,
  removeRelationship,
  removeRLSIntent,
} from './operations';
import type { DataType, Metric, Operation, SemanticModel } from './types';

export const LOCAL_DATASET_SCHEMA_VERSION = '1.0.0';
export const DEFAULT_CSV_LIMITS = Object.freeze({
  maxBytes: 2 * 1024 * 1024,
  maxColumns: 100,
  maxRows: 5_000,
});

/** Un solo locale de case-folding para headers, identificadores y validación. */
const CASE_FOLD_LOCALE = 'en';
const CONTROL_CHARS = /[\u0000-\u001F\u007F]/;

export interface CsvLimits {
  maxBytes: number;
  maxRows: number;
  maxColumns: number;
}

export interface LocalDatasetColumn {
  name: string;
  source_name: string;
  data_type: DataType;
  nullable: boolean;
}

export interface LocalDataset {
  schema_version: typeof LOCAL_DATASET_SCHEMA_VERSION;
  name: string;
  source_filename: string;
  imported_at: string;
  columns: LocalDatasetColumn[];
  rows: Record<string, Scalar>[];
}

/** Parse a local CSV into inert, bounded tabular data. CSV cells are never executed. */
export function parseLocalCsv(
  text: string,
  filename: string,
  limits: CsvLimits = DEFAULT_CSV_LIMITS,
): LocalDataset {
  assertLimits(limits);
  if (utf8ByteLength(text) > limits.maxBytes) {
    throw new Error(`El CSV supera el límite de ${formatBytes(limits.maxBytes)}.`);
  }

  const records = parseRecords(text);
  if (records.length === 0) {throw new Error('El CSV está vacío.');}
  const rawHeaders = records[0].map((header, index) => (
    index === 0 ? header.replace(/^\uFEFF/, '').trim() : header.trim()
  ));
  if (rawHeaders.length > limits.maxColumns) {
    throw new Error(`El CSV supera el límite de ${limits.maxColumns} columnas.`);
  }
  if (rawHeaders.some((header) => header.length === 0)) {
    throw new Error('El CSV contiene encabezados vacíos.');
  }
  if (rawHeaders.some((header) => CONTROL_CHARS.test(header))) {
    throw new Error('El CSV contiene caracteres de control en los encabezados.');
  }
  const foldedHeaders = rawHeaders.map((header) => header.toLocaleLowerCase(CASE_FOLD_LOCALE));
  if (new Set(foldedHeaders).size !== foldedHeaders.length) {
    throw new Error('El CSV contiene encabezados duplicados.');
  }

  const dataRecords = records
    .slice(1)
    .map((record, index) => ({ line: index + 2, record }))
    .filter((entry) => !isBlankRecord(entry.record));
  if (dataRecords.length > limits.maxRows) {
    throw new Error(`El CSV supera el límite de ${limits.maxRows} filas.`);
  }
  for (const { record, line } of dataRecords) {
    if (record.length !== rawHeaders.length) {
      throw new Error(
        `La fila ${line} tiene ${record.length} columnas; se esperaban ${rawHeaders.length}.`,
      );
    }
  }

  const names = uniqueIdentifiers(rawHeaders);
  const rawColumns = rawHeaders.map((_, columnIndex) => (
    dataRecords.map(({ record }) => normalizeCell(record[columnIndex]))
  ));
  const columns: LocalDatasetColumn[] = rawHeaders.map((sourceName, index) => ({
    data_type: inferColumnType(rawColumns[index]),
    name: names[index],
    nullable: rawColumns[index].some((value) => value === null),
    source_name: sourceName.slice(0, 180),
  }));
  const rows = dataRecords.map(({ record }) => Object.fromEntries(columns.map((column, index) => [
    column.name,
    coerceCell(normalizeCell(record[index]), column.data_type),
  ])) as Record<string, Scalar>);

  return {
    columns,
    imported_at: new Date().toISOString(),
    name: datasetNameFrom(filename),
    rows,
    schema_version: LOCAL_DATASET_SCHEMA_VERSION,
    source_filename: safeFilename(filename),
  };
}

/**
 * Fingerprint estable que vincula un dataset importado con la versión del modelo que produjo.
 * Cubre identidad, columnas tipadas, filas y un hash del contenido para detectar re-imports editados.
 */
export function datasetFingerprint(dataset: LocalDataset): string {
  const columns = dataset.columns.map((column) => `${column.name}:${column.data_type}`).join(',');
  return `csv1|${dataset.name}|${columns}|${dataset.rows.length}|${hashRows(dataset.rows)}`;
}

export function semanticModelForDataset(dataset: LocalDataset): SemanticModel {
  const entity = dataset.name;
  const metrics = dataset.columns
    .filter((column) => column.data_type === 'integer' || column.data_type === 'decimal')
    .map((column) => sumMetric(entity, column));
  return {
    description: `Modelo local importado desde ${dataset.source_filename}.`,
    entities: [{
      name: entity,
      description: `Datos locales de ${dataset.source_filename}; no se enviaron a un servidor.`,
      fields: dataset.columns.map((column) => ({
        name: column.name,
        source_column: column.source_name,
        data_type: column.data_type,
        nullable: column.nullable,
      })),
    }],
    filters: [],
    metrics,
    name: dataset.name,
    parameters: [],
    relationships: [],
    rls_intents: [],
    schema_version: '2.0.0',
  };
}

/** Build a complete replacement that materializes atomically against the current model. */
export function semanticReplacementOps(
  current: SemanticModel,
  replacement: SemanticModel,
): Operation[] {
  return [
    ...current.relationships.map((item) => removeRelationship(item.name)),
    ...current.rls_intents.map((item) => removeRLSIntent(item.name)),
    ...current.filters.map((item) => removeFilter(item.name)),
    ...current.parameters.map((item) => removeParameter(item.name)),
    ...current.metrics.map((item) => removeMetric(item.name)),
    ...current.entities.map((item) => removeEntity(item.name)),
    ...replacement.entities.map(addEntity),
    ...replacement.metrics.map(addMetric),
  ];
}

function parseRecords(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  let afterQuote = false;

  const pushField = () => {
    row.push(field);
    field = '';
    afterQuote = false;
  };
  const pushRow = () => {
    pushField();
    rows.push(row);
    row = [];
  };

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
          afterQuote = true;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (afterQuote && char !== ',' && char !== '\r' && char !== '\n') {
      if (/\s/.test(char)) {continue;}
      throw new Error(`CSV inválido: carácter inesperado después de una comilla en posición ${index + 1}.`);
    }
    if (char === '"') {
      if (field.length > 0) {
        throw new Error(`CSV inválido: comilla inesperada en posición ${index + 1}.`);
      }
      quoted = true;
    } else if (char === ',') {
      pushField();
    } else if (char === '\n' || char === '\r') {
      if (char === '\r' && text[index + 1] === '\n') {index += 1;}
      pushRow();
    } else {
      field += char;
    }
  }
  if (quoted) {throw new Error('CSV inválido: falta cerrar una comilla.');}
  if (field.length > 0 || row.length > 0 || afterQuote) {pushRow();}
  return rows;
}

function normalizeCell(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

// ponytail: la inferencia es deliberadamente conservadora — `007` pasa a 7 (se pierden los
// ceros a la izquierda) y un único valor no finito (p.ej. `1e400`) demota toda la columna a
// string. Si algún dominio necesita preservar ceros o rechazar filas, hay que tipar columnas
// explícitamente antes de importar.
function inferColumnType(values: (string | null)[]): DataType {
  const present = values.filter((value): value is string => value !== null);
  if (present.length === 0) {return 'string';}
  if (present.every(isInteger)) {return 'integer';}
  if (present.every(isNumeric)) {return 'decimal';}
  if (present.every(isBoolean)) {return 'boolean';}
  if (present.every(isIsoDate)) {return 'date';}
  return 'string';
}

function coerceCell(value: string | null, type: DataType): Scalar {
  if (value === null) {return null;}
  if (type === 'integer' || type === 'decimal') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  if (type === 'boolean') {return value.toLocaleLowerCase('en') === 'true';}
  return value;
}

function isInteger(value: string): boolean {
  return /^[-+]?\d+$/.test(value) && Number.isSafeInteger(Number(value));
}

function isNumeric(value: string): boolean {
  return /^[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?$/.test(value)
    && Number.isFinite(Number(value));
}

function isBoolean(value: string): boolean {
  return /^(?:true|false)$/i.test(value);
}

function isIsoDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {return false;}
  const [year, month, day] = value.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCFullYear() === year
    && date.getUTCMonth() === month - 1
    && date.getUTCDate() === day;
}

function uniqueIdentifiers(headers: string[]): string[] {
  const used = new Set<string>();
  return headers.map((header) => {
    const base = sanitizeIdentifier(header);
    let candidate = base;
    let suffix = 2;
    while (used.has(candidate.toLocaleLowerCase(CASE_FOLD_LOCALE))) {
      candidate = `${base}_${suffix}`;
      suffix += 1;
    }
    used.add(candidate.toLocaleLowerCase(CASE_FOLD_LOCALE));
    return candidate;
  });
}

function sanitizeIdentifier(value: string): string {
  const normalized = value
    .normalize('NFKD')
    .replaceAll(/[\u0300-\u036F]/g, '')
    .replaceAll(/[^A-Za-z0-9_]+/g, '_')
    .replaceAll(/^_+|_+$/g, '')
    .slice(0, 64) || 'column';
  return /^\d/.test(normalized) ? `field_${normalized}` : normalized;
}

/** El nombre del dataset debe cumplir el límite de 64 chars de validateLocalDataset. */
function datasetNameFrom(filename: string): string {
  return sanitizeIdentifier(filename.replace(/\.[^.]+$/, '') || 'dataset').slice(0, 64);
}

/** FNV-1a 32 bits: barato y estable para detectar contenido editado; no es criográfico. */
function hashRows(rows: Record<string, Scalar>[]): string {
  let hash = 0x81_1c_9d_c5;
  const text = JSON.stringify(rows);
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01_00_01_93);
  }
  return (hash >>> 0).toString(16);
}

function safeFilename(filename: string): string {
  const leaf = filename.replaceAll('\\', '/').split('/').pop() ?? 'datos.csv';
  return leaf.replaceAll(/[\u0000-\u001F\u007F]/g, '').slice(0, 180) || 'datos.csv';
}

function sumMetric(entity: string, column: LocalDatasetColumn): Metric {
  return {
    data_type: column.data_type,
    description: `Suma de ${column.source_name}.`,
    expression: {
      children: [{ kind: 'field_ref', entity, name: column.name }],
      kind: 'agg',
      name: 'sum',
    },
    format_string: column.data_type === 'integer' ? '#,##0' : '#,##0.00',
    name: uniqueMetricName(column.name),
  };
}

function uniqueMetricName(fieldName: string): string {
  return `Sum_${fieldName}`.slice(0, 80);
}

function isBlankRecord(record: string[]): boolean {
  return record.every((cell) => cell.trim() === '');
}

function assertLimits(limits: CsvLimits): void {
  if (![limits.maxBytes, limits.maxRows, limits.maxColumns].every(Number.isSafeInteger)
    || limits.maxBytes <= 0 || limits.maxRows <= 0 || limits.maxColumns <= 0) {
    throw new Error('Los límites CSV deben ser enteros positivos.');
  }
}

function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}

function formatBytes(bytes: number): string {
  return bytes >= 1024 * 1024
    ? `${Math.floor(bytes / (1024 * 1024))} MiB`
    : `${Math.floor(bytes / 1024)} KiB`;
}
