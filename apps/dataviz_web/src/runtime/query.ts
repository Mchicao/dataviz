import { Effect, Schema } from 'effect';

import type { AllowlistedQueryPredicate, FilterOperator } from '../interactions';
import { requestJsonEffect } from './http';
import type { HttpRequestError, RuntimeFetch } from './http';
import type { QueryResults, Row, VisualSpec } from './types';

const MAX_CONCURRENT_QUERY_GROUPS = 8;

const QUERY_OPERATOR: Partial<Record<FilterOperator, string>> = {
  between: 'between',
  contains: 'contains',
  ends_with: 'ends_with',
  equals: 'eq',
  greater_than: 'gt',
  in: 'in',
  less_than: 'lt',
  not_contains: 'not_contains',
  not_ends_with: 'not_ends_with',
  not_equals: 'ne',
  not_in: 'not_in',
  not_starts_with: 'not_starts_with',
  starts_with: 'starts_with',
};

const ScalarSchema = Schema.Union([Schema.String, Schema.Number, Schema.Null]);
const RowSchema = Schema.Record(Schema.String, ScalarSchema);
const QueryDatasetSchema = Schema.Struct({
  rows: Schema.optionalKey(Schema.Array(RowSchema)),
});
const decodeQueryDataset = Schema.decodeUnknownEffect(QueryDatasetSchema);

export class QueryRefreshError extends Schema.TaggedError<QueryRefreshError>()('QueryRefreshError', {
  cause: Schema.optional(Schema.Defect()),
  message: Schema.String,
}) {}

export function queryEndpointForRuntime(runtimeEndpoint: string): string {
  return runtimeEndpoint.replace(/\/runtime(?:\?.*)?$/, '/query');
}

export function resultsFromDataset(visual: VisualSpec, rows: readonly Row[]): QueryResults {
  const resolved: QueryResults = {};
  for (const reference of Object.values(visual.data_roles ?? {})) {
    const [, name = ''] = reference.split(':', 2);
    if (!name) {continue;}
    const values = rows.map((row) => row[name] ?? null);
    resolved[reference] = visual.kind === 'card' || visual.kind === 'kpi' || visual.kind === 'text_box'
      ? (values[0] ?? null)
      : values;
  }
  return resolved;
}

function queryBody(
  visual: VisualSpec,
  predicates: readonly AllowlistedQueryPredicate[],
): string {
  if (!visual.query) {throw new Error(`visual ${visual.name} has no executable query`);}
  const query = structuredClone(visual.query);
  const filters = Array.isArray(query.filters) ? [...query.filters] : [];
  for (const predicate of predicates) {
    const op = QUERY_OPERATOR[predicate.operator];
    if (!op) {throw new Error(`filter operator is not executable: ${predicate.operator}`);}
    filters.push({
      expression: { kind: 'field_ref', name: predicate.field },
      op,
      values: predicate.values,
    });
  }
  query.filters = filters;
  return JSON.stringify(query);
}

interface QueryGroup {
  readonly body: string;
  readonly visuals: VisualSpec[];
}

function groupQueries(
  visuals: readonly VisualSpec[],
  predicatesFor: (visualName: string) => readonly AllowlistedQueryPredicate[],
): Effect.Effect<QueryGroup[], QueryRefreshError> {
  return Effect.try({
    catch: (cause) => new QueryRefreshError({
      message: cause instanceof Error ? cause.message : 'invalid query plan',
      cause,
    }),
    try: () => {
      const groups = new Map<string, VisualSpec[]>();
      for (const visual of visuals) {
        const body = queryBody(visual, predicatesFor(visual.name));
        const grouped = groups.get(body) ?? [];
        grouped.push(visual);
        groups.set(body, grouped);
      }
      return [...groups].map(([body, groupedVisuals]) => ({ body, visuals: groupedVisuals }));
    },
  });
}

function queryError(error: HttpRequestError): QueryRefreshError {
  const suffix = error.status ? ` (${error.status})` : '';
  return new QueryRefreshError({
    cause: error,
    message: error.kind === 'aborted' ? 'query aborted' : `query failed${suffix}`,
  });
}

export interface RefreshVisualsOptions {
  readonly runtimeEndpoint: string;
  readonly visuals: readonly VisualSpec[];
  readonly predicatesFor: (visualName: string) => readonly AllowlistedQueryPredicate[];
  readonly authToken?: string;
  readonly signal?: AbortSignal;
  readonly fetchImpl?: RuntimeFetch;
}

/**
 * Refresh a visual set using one request per unique QuerySpec body.
 *
 * Effect provides bounded structured concurrency: if one grouped request fails,
 * sibling fibers are interrupted rather than being left detached from the UI run.
 */
export const refreshVisualsEffect = Effect.fn('DataViz.refreshVisuals')((options: RefreshVisualsOptions) =>
  Effect.gen(function* runRefreshVisuals() {
    const groups = yield* groupQueries(options.visuals, options.predicatesFor);
    if (groups.length === 0) {
      const empty: (readonly [string, QueryResults])[] = [];
      return empty;
    }

    const endpoint = queryEndpointForRuntime(options.runtimeEndpoint);
    const headers = {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...(options.authToken && { Authorization: `Bearer ${options.authToken}` }),
    };

    const groupedUpdates = yield* Effect.forEach(
      groups,
      (group) => requestJsonEffect({
        body: group.body,
        endpoint,
        fetchImpl: options.fetchImpl,
        headers,
        method: 'POST',
        signal: options.signal,
      }).pipe(
        Effect.mapError(queryError),
        Effect.flatMap((body) => decodeQueryDataset(body).pipe(
          Effect.mapError((cause) => new QueryRefreshError({
            cause,
            message: 'query returned an invalid dataset',
          })),
        )),
        Effect.map((dataset) => {
          const rows = dataset.rows ?? [];
          return group.visuals.map((visual) => [
            visual.name,
            resultsFromDataset(visual, rows),
          ] as const);
        }),
      ),
      { concurrency: MAX_CONCURRENT_QUERY_GROUPS },
    );

    return groupedUpdates.flat();
  }),
);

export function refreshVisuals(options: RefreshVisualsOptions): Promise<(readonly [string, QueryResults])[]> {
  return Effect.runPromise(refreshVisualsEffect(options));
}
