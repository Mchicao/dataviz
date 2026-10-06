# Effect V4 boundary

DataVIZ uses Effect at asynchronous and validation boundaries, not inside presentational React code.

- Pinned runtime: `effect@4.0.0-rc.111`. The exact RC is intentional while Effect V4 is pre-GA; upgrades must be reviewed rather than floated through an npm range.
- TypeScript baseline: `7.1.0-dev.20260823.1`. The web toolchain requires Node
  `>=26.7.0` and npm `12.0.2`; this is a product policy above Vite's wider
  compatibility range, not a claim that Vite itself requires Node 26.
- DataVIZ does not use the TypeScript Compiler API, so it does not require a TypeScript 6 compatibility alias alongside the native TypeScript 7 compiler.
- `src/runtime/http.ts` owns Promise-to-Effect conversion, interruption-aware fetch, JSON transport failures, and injectable transports.
- `src/runtime/client.ts` owns the runtime payload schema and runtime-specific error classification.
- `src/runtime/query.ts` owns QuerySpec compilation, request deduplication, bounded concurrency, response decoding, and visual result projection.
- `src/app.tsx` remains a React adapter. It should not accumulate retry policies, HTTP status handling, response parsing, query deduplication, or Effect services.
- Runtime results use the exact `2.0.0` contract emitted by the Python
  materializer; plan validation remains exact at `2.1.0`.

The Python Source AST / semantic IR remains the cross-language source of truth. Effect Schema validates TypeScript runtime boundaries; it does not redefine the canonical DataVIZ IR.

## Runtime policy

- Query POSTs are not retried implicitly. A BI query can be expensive and retry safety depends on the datasource and operation; retries must be introduced at a use-case boundary with an explicit idempotency/cost policy.
- Effect `Layer`/service graphs are intentionally deferred while an injectable transport is sufficient. Introduce them only when multiple runtime dependencies need lifecycle or environment composition.
- Concurrency is bounded internally instead of becoming public configuration before there is workload evidence that tuning is needed.
- Do not adopt `effect/unstable/*` modules without a concrete product requirement.

## Upgrade rule

Before changing the pinned Effect RC or TypeScript version, run `npm ci`,
`npm run typecheck`, `npm test`, `npm run build`, `npm run check:bundle`, and
`npm audit --audit-level=low`. Review V4 migration notes for changes to
`Effect`, `Schema`, interruption, and tagged errors.

React, React DOM and `react-dom/client` stay external to the library bundles.
The classic JSX transform prevents React 19's automatic runtime from being
embedded. This guards against accidental duplication in a host that already
provides React DOM; it does not prove a lower total page payload. Do not remove
either setting without checking both ES and UMD budgets and the consuming host.

Repository npm policy rejects unsupported Node engines, ignores dependency
lifecycle scripts, saves future dependencies exactly, audits known advisories,
pins npm 12.0.2 on Node 26.7.0 Current, and verifies registry signatures in CI.
React canary is pinned exactly; an npm override maps Testing Library's stable
React peer declaration to the root canary pair so clean installs remain
reproducible without `--force` or globally disabling peer checks. A
dependency that truly requires an
install script must be reviewed and allowlisted explicitly rather than enabling
all scripts globally.
