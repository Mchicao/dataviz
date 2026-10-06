# DataVIZ

**Analítica de clase mundial. Agent Native.**

DataVIZ es un estudio de autoría y consumo de dashboards donde humanos y agentes de IA editan el mismo documento: el asistente propone cambios acotados y versionados, tú los apruebas. El conocimiento de negocio (métricas, reglas, jerarquías) queda en el modelo, no atrapado en un gráfico.

🔗 **[Ver la landing](https://mchicao.github.io/dataviz/)**

## Características

**Autoría visual completa**
- 9 tipos de visual: KPI, barras, columnas, línea, área, torta, dona, dispersión y tabla, con editor de posición, tamaño, color y títulos.
- Canvas tipo Power BI con snap a grid, zoom HUD, reordenar y redimensionar con gestos y teclado.

**Métricas y filtros reales**
- Métricas calculadas con editor de expresiones y filtros en 3 alcances (página, todas las páginas, visual) con visibilidad y bloqueo por filtro.
- Importación de CSV local (hasta 2 MiB) con fingerprint: los datos nunca salen de tu navegador.

**Colaboración humano-agente**
- Asistente conectado a LLM (Z.ai / GLM) o reglas locales deterministas: describe lo que quieres y recibe una propuesta estructurada con resumen, cambios y advertencias.
- Toda propuesta se previsualiza y requiere aprobación humana: rechazar restaura el estado sin crear versión.

**Versionado y publicación**
- Historial completo con mensajes por cambio, restauración a cualquier versión y borrador persistente en el navegador.
- Publicación gobernada contra el servicio: solicitud de publicación con candidato pendiente de aprobación y rollback durable con control de concurrencia (CAS + idempotencia).

**Viewer de consumo**
- Modo lectura de cualquier versión (`?mode=viewer`), con drill de jerarquías, cross-filter, tooltips y estados de carga/vacío accesibles (WCAG).

## Inicio rápido

Requisitos: Node 20.19+ (recomendado 24/26) y Python 3.11–3.14 con [uv](https://docs.astral.sh/uv/).

```bash
# 1. Frontend (puerto 3000)
cd apps/dataviz_web
npm install
npm run dev

# 2. Backend del asistente (puerto 8766, proxy /api/assistant)
#    opcional: copia .env.example a .env y define ZAI_API_KEY
uv sync
uv run python -m apps.dataviz_service.local_assistant --port 8766
```

Abre `http://localhost:3000`. Sin `ZAI_API_KEY` el asistente funciona con reglas locales.

## Arquitectura

| Pieza | Tecnología |
| --- | --- |
| Frontend | React 19 · Effect 4 · TypeScript 7 (tsgo) · Vite 8 |
| Backend | Python 3.11–3.14 · Starlette · MCP |
| Motor | `core/`: IR tipada, compiladores render-plan/PBIP, conectores y validación de fidelidad |

- `apps/dataviz_web`: editor de authoring, viewer y landings.
- `apps/dataviz_service`: asistente local, trabajos de migración, publicación gobernada y API.
- `core/`: el motor — contratos canónicos (IR de interacción, semántica, presentación), compiladores a render plan y PBIP, conectores (Postgres, Excel, JSON, Hyper) y gates de fidelidad numérica/visual.

## Estado del proyecto

DataVIZ está en desarrollo activo como producto SaaS. Este repositorio contiene el vertical de autoría y consumo que se ejecuta localmente. Sin licencia de uso: todos los derechos reservados.
