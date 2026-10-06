/**
 * DataVIZ runtime client.
 *
 * Effect owns asynchronous transport, cancellation and schema validation. A
 * Promise facade remains for React and historical callers while Effect-native
 * code can consume `fetchRuntimeEffect` directly.
 */

// La clase de error y el cliente comparten módulo por diseño (algebra de
// fallo del transporte); separarlas fragmenta la API pública.
// oxlint-disable max-classes-per-file

import { Effect, Schema } from 'effect';

import { requestJsonEffect } from './http';
import type { HttpRequestError, RuntimeFetch } from './http';
import {
  RENDER_PLAN_SCHEMA_VERSION,
  RUNTIME_RESULTS_SCHEMA_VERSION,
} from './types';

export type { RuntimeFetch, RuntimeFetchInit, RuntimeResponse } from './http';

const RUNTIME_ENDPOINT_PREFIX = '/api/versions';
const RUNTIME_ENDPOINT_SUFFIX = '/runtime';

export interface CredentialFinding {
  readonly kind: string;
  readonly severity: string;
  readonly source: string;
  readonly line: number | null;
  readonly message: string;
}

export interface CredentialScanReport {
  readonly status: string;
  readonly has_credential_risk: boolean;
  readonly finding_count: number;
  readonly redaction_count: number;
  readonly scanned_file_count: number;
  readonly sanitized: boolean;
  readonly findings: readonly CredentialFinding[];
}

const RUNTIME_ERROR_CODES = [
  'config',
  'network',
  'aborted',
  'auth',
  'not_found',
  'server',
  'client',
  'response',
] as const;

export type RuntimeErrorCode = (typeof RUNTIME_ERROR_CODES)[number];

/** Stable domain error algebra at both the Effect and Promise boundaries. */
export class RuntimeClientError extends Schema.TaggedError<RuntimeClientError>()('RuntimeClientError', {
  cause: Schema.optional(Schema.Defect()),
  code: Schema.Literals(RUNTIME_ERROR_CODES),
  endpoint: Schema.optional(Schema.String),
  message: Schema.String,
  status: Schema.optional(Schema.Number),
}) {}

export function runtimeEndpointFor(versionId: string, baseUrl = ''): string {
  const trimmedBase = baseUrl.replace(/\/+$/, '');
  return `${trimmedBase}${RUNTIME_ENDPOINT_PREFIX}/${encodeURIComponent(versionId)}${RUNTIME_ENDPOINT_SUFFIX}`;
}

export interface FetchRuntimeOptions {
  readonly endpoint?: string;
  readonly versionId?: string;
  readonly baseUrl?: string;
  readonly authToken?: string;
  readonly signal?: AbortSignal;
  readonly fetchImpl?: RuntimeFetch;
  readonly headers?: Record<string, string>;
}

const ScalarSchema = Schema.Union([Schema.Number, Schema.String, Schema.Boolean, Schema.Null]);
const RowSchema = Schema.Record(Schema.String, ScalarSchema);
const QueryResultSchema = Schema.Union([
  ScalarSchema,
  Schema.Array(ScalarSchema),
  Schema.Array(RowSchema),
]);
const QueryResultsSchema = Schema.Record(Schema.String, QueryResultSchema);
const ForecastSpecSchema = Schema.Struct({
  confidence_level: Schema.Number,
  fill_missing: Schema.Boolean,
  horizon: Schema.Number,
  ignore_last: Schema.Number,
  period: Schema.Literals(['day', 'week', 'month', 'quarter', 'year']),
  prediction_intervals: Schema.Boolean,
  seasonal: Schema.Boolean,
  time_field: Schema.NonEmptyString,
  value_field: Schema.NonEmptyString,
});
const VisualSpecSchema = Schema.Struct({
  data_roles: Schema.optional(Schema.Record(Schema.String, Schema.String)),
  forecast: Schema.optional(Schema.NullOr(ForecastSpecSchema)),
  geometry: Schema.optional(Schema.Record(
    Schema.String,
    Schema.Union([Schema.Number, Schema.String]),
  )),
  kind: Schema.NonEmptyString,
  liveness_policy: Schema.optional(Schema.Literals([
    'requires_marks',
    'allows_empty_state',
    'decorative',
  ])),
  name: Schema.NonEmptyString,
  page: Schema.optional(Schema.String),
  query: Schema.optional(Schema.NullOr(Schema.Record(Schema.String, Schema.Unknown))),
  title: Schema.optional(Schema.String),
});
const InteractionSpecSchema = Schema.Struct({
  data_role: Schema.optional(Schema.String),
  kind: Schema.NonEmptyString,
  name: Schema.NonEmptyString,
  source: Schema.NonEmptyString,
  target: Schema.NonEmptyString,
  values: Schema.optional(Schema.Array(Schema.Unknown)),
});
const CredentialFindingSchema = Schema.Struct({
  kind: Schema.String,
  line: Schema.NullOr(Schema.Number),
  message: Schema.String,
  severity: Schema.String,
  source: Schema.String,
});
const CredentialScanReportSchema = Schema.Struct({
  finding_count: Schema.Number,
  findings: Schema.Array(CredentialFindingSchema),
  has_credential_risk: Schema.Boolean,
  redaction_count: Schema.Number,
  sanitized: Schema.Boolean,
  scanned_file_count: Schema.Number,
  status: Schema.String,
});
const RuntimePayloadSchema = Schema.Struct({
  credential_scan: Schema.optional(CredentialScanReportSchema),
  plan: Schema.Struct({
    schema_version: Schema.Literals([RENDER_PLAN_SCHEMA_VERSION]),
    visuals: Schema.Array(VisualSpecSchema),
    interactions: Schema.optional(Schema.Array(InteractionSpecSchema)),
    description: Schema.optional(Schema.String),
    metadata: Schema.optional(Schema.Record(Schema.String, Schema.Unknown)),
  }),
  results: Schema.Struct({
    schema_version: Schema.Literals([RUNTIME_RESULTS_SCHEMA_VERSION]),
    visuals: Schema.Record(Schema.String, QueryResultsSchema),
    forecast_models: Schema.optional(Schema.Record(
      Schema.String,
      Schema.Record(Schema.String, Schema.Unknown),
    )),
  }),
});
export type RuntimePayload = Schema.Schema.Type<typeof RuntimePayloadSchema>;
const decodeRuntimePayload = Schema.decodeUnknownEffect(RuntimePayloadSchema);

function endpointEffect(options: FetchRuntimeOptions) {
  if (options.endpoint) {return Effect.succeed(options.endpoint);}
  if (options.versionId) {return Effect.succeed(runtimeEndpointFor(options.versionId, options.baseUrl));}
  return Effect.fail(new RuntimeClientError({
    code: 'config',
    message: 'runtime client requires either an endpoint URL or a versionId',
  }));
}

function runtimeErrorFromHttp(error: HttpRequestError): RuntimeClientError {
  if (error.kind === 'config' || error.kind === 'network' || error.kind === 'aborted' || error.kind === 'response') {
    return new RuntimeClientError({
      cause: error.cause,
      code: error.kind,
      endpoint: error.endpoint,
      message: error.message,
    });
  }

  const status = error.status ?? 0;
  const code: RuntimeErrorCode = status === 401 || status === 403
    ? 'auth'
    : status === 404
      ? 'not_found'
      : status >= 500
        ? 'server'
        : 'client';
  return new RuntimeClientError({
    code,
    endpoint: error.endpoint,
    message: error.message,
    status,
  });
}

/** Effect-native runtime loader with typed failures. */
export const fetchRuntimeEffect = Effect.fn('DataViz.fetchRuntime')((options: FetchRuntimeOptions) =>
  Effect.gen(function* runFetchRuntime() {
    const endpoint = yield* endpointEffect(options);
    const headers = {
      Accept: 'application/json',
      ...options.headers,
      ...(options.authToken && { Authorization: `Bearer ${options.authToken}` }),
    };

    const body = yield* requestJsonEffect({
      endpoint,
      fetchImpl: options.fetchImpl,
      headers,
      method: 'GET',
      signal: options.signal,
    }).pipe(Effect.mapError(runtimeErrorFromHttp));

    return yield* decodeRuntimePayload(body).pipe(
      Effect.mapError((cause) => new RuntimeClientError({
        cause,
        code: 'response',
        endpoint,
        message: `invalid runtime payload: ${cause.message}`,
      })),
    );
  }),
);

/** Promise boundary retained for React and existing callers. */
export function fetchRuntime(options: FetchRuntimeOptions): Promise<RuntimePayload> {
  return Effect.runPromise(fetchRuntimeEffect(options));
}

export interface RuntimeClientConfig {
  readonly baseUrl?: string;
  readonly authToken?: string;
  readonly fetchImpl?: RuntimeFetch;
  readonly headers?: Record<string, string>;
}

export interface GetRuntimeOptions {
  readonly signal?: AbortSignal;
  readonly endpoint?: string;
  readonly authToken?: string;
}

// La clase de error y el cliente comparten módulo por diseño (algebra de
// fallo del transporte); separarlas fragmenta la API pública.
// oxlint-disable-next-line max-classes-per-file
export class RuntimeClient {
  private readonly config: RuntimeClientConfig;

  constructor(config: RuntimeClientConfig = {}) {
    this.config = config;
  }

  getRuntime(versionId: string, options: GetRuntimeOptions = {}): Promise<RuntimePayload> {
    return fetchRuntime({
      authToken: options.authToken ?? this.config.authToken,
      baseUrl: this.config.baseUrl,
      endpoint: options.endpoint,
      fetchImpl: this.config.fetchImpl,
      headers: this.config.headers,
      signal: options.signal,
      versionId,
    });
  }
}

// `unknown` es deliberado: helpers públicos de diagnóstico que aceptan
// cualquier valor capturado antes de estrecharlo a la algebra de error.
// oxlint-disable-next-line anti-slop/no-unknown-parameters
export function isRuntimeAbortedError(error: unknown): boolean {
  return error instanceof RuntimeClientError && error.code === 'aborted';
}

// oxlint-disable-next-line anti-slop/no-unknown-parameters
export function describeRuntimeError(error: unknown): string {
  if (error instanceof RuntimeClientError) {
    switch (error.code) {
      case 'aborted': {
        return '';
      }
      case 'auth': {
        return 'Se requiere autenticación o el alcance es insuficiente.';
      }
      case 'not_found': {
        return 'No se encontró la versión solicitada.';
      }
      case 'server': {
        return 'El servicio de runtime no está disponible. Intenta de nuevo más tarde.';
      }
      case 'network': {
        return 'No se pudo contactar el servicio de runtime.';
      }
      case 'response': {
        return 'El runtime devolvió una respuesta inválida.';
      }
      case 'client': {
        return 'La solicitud al runtime fue rechazada por el servicio.';
      }
      case 'config': {
        return 'El cliente de runtime está mal configurado.';
      }
      default: {
        return error.message;
      }
    }
  }
  return error instanceof Error ? error.message : String(error);
}
