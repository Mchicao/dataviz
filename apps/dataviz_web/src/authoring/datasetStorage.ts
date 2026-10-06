import type { Scalar } from '../runtime/types';
import { DEFAULT_CSV_LIMITS, LOCAL_DATASET_SCHEMA_VERSION } from './csv';
import type { CsvLimits, LocalDataset } from './csv';
import type { DataType } from './types';

const ALLOWED_DATA_TYPES = new Set<DataType>([
  'string', 'integer', 'decimal', 'boolean', 'date', 'datetime', 'time',
  'binary', 'variant', 'unknown',
]);

export function validateLocalDataset(
  value: unknown,
  limits: CsvLimits = DEFAULT_CSV_LIMITS,
): asserts value is LocalDataset {
  if (!isRecord(value)) {throw new Error('El dataset local debe ser un objeto.');}
  if (value.schema_version !== LOCAL_DATASET_SCHEMA_VERSION) {
    throw new Error('Versión de dataset local no soportada.');
  }
  if (!isSafeText(value.name, 64) || !isSafeText(value.source_filename, 180)) {
    throw new Error('El dataset local no tiene una identidad válida.');
  }
  if (typeof value.imported_at !== 'string' || !Number.isFinite(Date.parse(value.imported_at))) {
    throw new TypeError('El dataset local no tiene una fecha de importación válida.');
  }
  if (!Array.isArray(value.columns) || value.columns.length === 0
    || value.columns.length > limits.maxColumns) {
    throw new Error('El dataset local tiene una cantidad de columnas inválida.');
  }
  if (!Array.isArray(value.rows) || value.rows.length > limits.maxRows) {
    throw new Error('El dataset local tiene una cantidad de filas inválida.');
  }

  const names = new Set<string>();
  for (const column of value.columns) {
    if (!isRecord(column)
      || !isSafeText(column.name, 80)
      || !isSafeText(column.source_name, 180)
      || typeof column.data_type !== 'string'
      || !ALLOWED_DATA_TYPES.has(column.data_type as DataType)
      || typeof column.nullable !== 'boolean') {
      throw new Error('El dataset local contiene una columna inválida.');
    }
    const folded = column.name.toLocaleLowerCase('en');
    if (names.has(folded)) {throw new Error('El dataset local contiene columnas duplicadas.');}
    names.add(folded);
  }

  const expected = new Set(value.columns.map((column) => column.name as string));
  for (const row of value.rows) {
    if (!isRecord(row)) {throw new Error('El dataset local contiene una fila inválida.');}
    const keys = Object.keys(row);
    if (keys.length !== expected.size || keys.some((key) => !expected.has(key))) {
      throw new Error('El dataset local contiene una fila con columnas inesperadas.');
    }
    for (const cell of Object.values(row)) {
      if (!isScalar(cell)) {throw new Error('El dataset local contiene un valor no escalar.');}
    }
  }
}

export function loadLocalDataset(
  storage: Storage,
  key: string,
  limits: CsvLimits = DEFAULT_CSV_LIMITS,
): LocalDataset | null {
  let raw: string | null;
  try {
    raw = storage.getItem(key);
  } catch {
    return null;
  }
  if (!raw) {return null;}
  try {
    const parsed: unknown = JSON.parse(raw);
    validateLocalDataset(parsed, limits);
    return cloneDataset(parsed);
  } catch {
    try {
      storage.setItem(`${key}.corrupt`, raw);
    } catch {
      // El almacenamiento puede estar bloqueado o lleno; la recuperación sigue segura.
    }
    return null;
  }
}

export function saveLocalDataset(
  storage: Storage,
  key: string,
  dataset: LocalDataset,
  limits: CsvLimits = DEFAULT_CSV_LIMITS,
): void {
  validateLocalDataset(dataset, limits);
  storage.setItem(key, JSON.stringify(cloneDataset(dataset)));
}

function cloneDataset(dataset: LocalDataset): LocalDataset {
  return JSON.parse(JSON.stringify(dataset)) as LocalDataset;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function isSafeText(value: unknown, maxLength: number): value is string {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= maxLength
    && !/[\u0000-\u001F\u007F]/.test(value);
}

function isScalar(value: unknown): value is Scalar {
  return value === null
    || typeof value === 'string'
    || typeof value === 'boolean'
    || (typeof value === 'number' && Number.isFinite(value));
}
