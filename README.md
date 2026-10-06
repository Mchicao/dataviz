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

## Project status

DataVIZ is under active development as a SaaS product. This repository contains the local authoring and consumption vertical. No license granted: all rights reserved.
