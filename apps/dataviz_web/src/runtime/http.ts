import { Effect, Schema } from 'effect';

/** Minimal response shape consumed by the runtime transport. */
export interface RuntimeResponse {
  readonly ok: boolean;
  readonly status: number;
  readonly statusText: string;
// `unknown` es deliberado: frontera cruda del transporte; el caller valida el
// cuerpo con Schema (decodeRuntimePayload / decodeQueryDataset).
// oxlint-disable-next-line anti-slop/no-unknown-returns
  readonly json: () => Promise<unknown>;
}

/** Minimal request init emitted by the runtime transport. */
export interface RuntimeFetchInit {
  readonly method?: string;
  readonly headers?: Record<string, string>;
  readonly body?: string;
  readonly signal?: AbortSignal;
}

/** Injectable transport. The browser/Node global `fetch` is structurally compatible. */
export type RuntimeFetch = (input: string, init: RuntimeFetchInit) => Promise<RuntimeResponse>;

/** One transport error algebra for every DataVIZ runtime request. */
export class HttpRequestError extends Schema.TaggedError<HttpRequestError>()('HttpRequestError', {
  cause: Schema.optional(Schema.Defect()),
  endpoint: Schema.String,
  kind: Schema.Literals(['config', 'network', 'aborted', 'http', 'response']),
  message: Schema.String,
  status: Schema.optional(Schema.Number),
}) {}

export interface RequestJsonOptions {
  readonly endpoint: string;
  readonly method?: string;
  readonly headers?: Record<string, string>;
  readonly body?: string;
  readonly signal?: AbortSignal;
  readonly fetchImpl?: RuntimeFetch;
}

interface GlobalWithFetch { readonly fetch?: unknown }

/** Type guard: la única forma fiable de sondear si un valor es invocable. */
function isCallable<T extends (...args: never[]) => void>(value: unknown): value is T {
  // Sondeo de invocabilidad: es la herramienta correcta para feature detection.
  // oxlint-disable-next-line anti-slop/no-runtime-typeof
  return typeof value === 'function';
}

function resolveFetchImpl(provided: RuntimeFetch | undefined, endpoint: string) {
  if (provided) {return Effect.succeed(provided);}
  // SAFETY: globalThis se sondea como registro con fetch opcional; isCallable
  // estrecha antes de usarlo.
  const globalFetch = (globalThis as GlobalWithFetch).fetch;
  return isCallable<RuntimeFetch>(globalFetch)
    ? Effect.succeed(globalFetch)
    : Effect.fail(new HttpRequestError({
        endpoint,
        kind: 'config',
        message: 'no fetch implementation available; pass fetchImpl',
      }));
}

function isAbortError(cause: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) {return true;}
  return cause instanceof Error && cause.name === 'AbortError';
}

function describeCause(cause: unknown): string {
  return cause instanceof Error ? (cause.message || cause.name) : String(cause);
}

interface ComposedSignal {
  readonly signal: AbortSignal;
  readonly cleanup: () => void;
}

function requestSignal(effectSignal: AbortSignal, externalSignal?: AbortSignal): ComposedSignal {
  // No-op deliberado: las ramas sin listeners no requieren limpieza.
  // oxlint-disable-next-line no-empty-function
  const cleanupNoop = (): void => {};
  if (!externalSignal || externalSignal === effectSignal) {
    return { cleanup: cleanupNoop, signal: effectSignal };
  }

  // SAFETY: AbortSignal.any es opcional según runtime; se sondea antes de usar.
  const abortSignalConstructor = AbortSignal as typeof AbortSignal & {
    readonly any?: (signals: AbortSignal[]) => AbortSignal;
  };
  if (isCallable(abortSignalConstructor.any)) {
    return {
      cleanup: cleanupNoop,
      signal: abortSignalConstructor.any([effectSignal, externalSignal]),
    };
  }

  const controller = new AbortController();
  const abortFrom = (source: AbortSignal) => controller.abort(source.reason);
  const onEffectAbort = () => abortFrom(effectSignal);
  const onExternalAbort = () => abortFrom(externalSignal);
  if (effectSignal.aborted) {abortFrom(effectSignal);}
  else if (externalSignal.aborted) {abortFrom(externalSignal);}
  else {
    effectSignal.addEventListener('abort', onEffectAbort, { once: true });
    externalSignal.addEventListener('abort', onExternalAbort, { once: true });
  }
  return {
    cleanup: () => {
      effectSignal.removeEventListener('abort', onEffectAbort);
      externalSignal.removeEventListener('abort', onExternalAbort);
    },
    signal: controller.signal,
  };
}

/** Execute one JSON request with typed failures and interruption-aware Promise wrapping. */
export const requestJsonEffect = Effect.fn('DataViz.requestJson')((options: RequestJsonOptions) =>
  Effect.gen(function* runRequestJson() {
    const fetchImpl = yield* resolveFetchImpl(options.fetchImpl, options.endpoint);
    const response = yield* Effect.tryPromise({
      catch: (cause) => {
        const aborted = isAbortError(cause, options.signal);
        return new HttpRequestError({
          kind: aborted ? 'aborted' : 'network',
          message: aborted
            ? 'request aborted'
            : `request failed: ${describeCause(cause)}`,
          endpoint: options.endpoint,
          cause,
        });
      },
      try: (effectSignal) => {
        const composed = requestSignal(effectSignal, options.signal);
        try {
          return Promise.resolve(fetchImpl(options.endpoint, {
            method: options.method ?? 'GET',
            headers: options.headers,
            body: options.body,
            signal: composed.signal,
          })).finally(composed.cleanup);
        } catch (error) {
          composed.cleanup();
          throw error;
        }
      },
    });

    if (!response.ok) {
      const label = response.statusText
        ? `${response.status} ${response.statusText}`
        : String(response.status);
      return yield* new HttpRequestError({
        endpoint: options.endpoint,
        kind: 'http',
        message: `request failed: ${label}`,
        status: response.status,
      });
    }

    return yield* Effect.tryPromise({
      catch: (cause) => new HttpRequestError({
        kind: 'response',
        message: `response is not valid JSON: ${describeCause(cause)}`,
        endpoint: options.endpoint,
        cause,
      }),
      try: () => response.json(),
    });
  }),
);
