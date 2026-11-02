# DataVIZ

**Business intelligence designed for agents.**

DataVIZ is an authoring and consumption studio for dashboards where humans and AI agents edit the same document: the assistant proposes bounded, versioned changes and you approve them. Business knowledge — metrics, rules, hierarchies — lives in the model, not trapped inside a chart.

🔗 **[Visit the landing page](https://dataviz.matiaschicao.cl/)** · [Versión en español](https://dataviz.matiaschicao.cl/es/)

## Features

**Full visual authoring**
- 9 visual types: KPI, bars, columns, line, area, pie, donut, scatter and table, with position, size, color and title editing.
- Power BI-style canvas with grid snapping, zoom HUD, and reordering/resizing via gestures and keyboard.

**Real metrics and filters**
- Calculated metrics with an expression editor and filters at 3 scopes (page, all pages, visual) with per-filter visibility and pinning.
- Local CSV import (up to 2 MiB) with fingerprinting: your data never leaves the browser.

**Human–agent collaboration**
- Assistant connected to an LLM (Z.ai / GLM) or deterministic local rules: describe what you want and receive a structured proposal with summary, changes and warnings.
- Every proposal is previewed and requires human approval; rejecting restores the previous state without creating a version.

**Versioning and governed publication**
- Full history with per-change messages, restore to any version, and a browser-persisted draft.
- Governed publication against the service: publication request with a pending candidate and durable rollback backed by CAS concurrency control and idempotency keys.

**Consumption viewer**
- Read-only mode for any version (`?mode=viewer`) with hierarchy drill-down, cross-filtering, tooltips and accessible loading/empty states (WCAG).

## Quick start

Requirements: Node 20.19+ (24/26 recommended) and Python 3.11–3.14 with [uv](https://docs.astral.sh/uv/).

```bash
# 1. Frontend (port 3000)
cd apps/dataviz_web
npm install
npm run dev

# 2. Assistant backend (port 8766, proxied at /api/assistant)
#    optional: copy .env.example to .env and set ZAI_API_KEY
uv sync
uv run python -m apps.dataviz_service.local_assistant --port 8766
```

Open `http://localhost:3000`. Without `ZAI_API_KEY` the assistant falls back to deterministic local rules.

## Architecture

| Layer | Technology |
| --- | --- |
| Frontend | React 19 · Effect 4 · TypeScript 7 (tsgo) · Vite 8 |
| Backend | Python 3.11–3.14 · Starlette · MCP |
| Engine | `core/`: typed IR, render-plan/PBIP compilers, connectors and fidelity validation |

- `apps/dataviz_web`: authoring editor, viewer and landing pages.
- `apps/dataviz_service`: local assistant, migration jobs, governed publication and API.
- `core/`: the engine — canonical contracts (interaction, semantic and presentation IR), render-plan and PBIP compilers, connectors (Postgres, Excel, JSON, Hyper) and numeric/visual fidelity gates.

## Clinic X public demo

Three views share the same independently generated Clinic X snapshot:

- [Studio](https://demo.matiaschicao.cl/studio): editable browser-local drafts.
- [Dashboard](https://demo.matiaschicao.cl/dashboard): the finished read-only
  snapshot, with branch/month filters; it never loads visitors' Studio drafts.
- [Agents](https://demo.matiaschicao.cl/agents): Finance, Revenue Cycle, and
  Emergency Operations conversations with calculated metrics, formulas, source
  versions, record counts, filters, and explicit access scopes.

The agent profiles are **public permission simulations**, not authentication or
tenant security. Direction can select all three specialists; Operations cannot
select Finance. The server validates the selected profile, agent and bounded
filters before querying its fixed synthetic projection. Anyone may select the
Direction profile; no confidential sources are connected. No agent executes
actions. The outpatient generator is shared with the Studio and dashboard;
billing and emergency extensions are independently fictional, never client-derived.

Without GLM, specialist answers use deterministic server-side calculations.
After `ZAI_API_KEY` is configured, GLM receives only the selected specialist's
aggregate evidence for a qualitative explanation. Numeric evidence always comes
from the calculations; unvalidated model output falls back to the local answer.
The demo supports these fixed metrics and filters, not arbitrary analytical
queries, autonomous workflows, or a production knowledge base.

Open the Studio with `?demo=clinic-x` to load an independently generated fictional
clinic dashboard: 1,080 daily branch/specialty records, seven editable visuals,
and browser-local draft history. There are no patient identities or client-derived
inputs. Edits are private to each browser; this demo does not offer shared SaaS
persistence or remote publication. The metric control creates sums of selected
numeric fields; it is not a general DAX formula editor.

The Cloudflare Worker serves the Studio and `/api/assistant` on the same origin:

```bash
cd apps/dataviz_web
npm ci
npm run deploy:demo
```

Authenticate Wrangler with the intended Cloudflare account before deployment.
For local verification after a build, use `npm run dev:demo` and open
`http://127.0.0.1:8787/?demo=clinic-x`.

Without a key, the assistant offers deterministic local rules. To enable GLM,
add a **standard Z.ai API** key as the Worker's `ZAI_API_KEY` secret through the
Cloudflare dashboard or `npx wrangler secret put ZAI_API_KEY`. Never put this key
in frontend variables or source files. The public Worker uses the standard
`https://api.z.ai/api/paas/v4` endpoint, not Coding Plan subscription quota.
The existing Python assistant is a separate localhost development integration.

The public assistant proposes current-page visual changes for human approval;
it cannot create formulas, query source rows, publish, or run arbitrary code.
Requests contain dashboard structure, labels, and metric names, but no dataset
rows. The Worker enforces JSON/body limits, same-origin browser requests, a
45-second provider timeout, and native Cloudflare rate limits (3 requests per IP
and 10 for the shared demo key per minute **per Cloudflare location**). These
limits are not a global spending cap; configure an API-provider budget before
enabling the key. Hosting allowances and model usage are separate.

## Project status

DataVIZ is under active development as a SaaS product. This repository contains the local authoring and consumption vertical. No license granted: all rights reserved.
