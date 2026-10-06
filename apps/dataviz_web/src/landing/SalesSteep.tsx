import { useEffect } from 'react';

import './sales-steep.css';

/**
 * Landing de ventas variante Steep (`?landing=steep`).
 *
 * Dirección de arte "Steep — serif analytics on warm paper": lienzo blanco
 * acromático, titulares serif en peso 400 (el serif susurra autoridad),
 * botones píldora y un único acento cromático (peach) reservado para UNA
 * tarjeta editorial. Los únicos elementos con sombra son los artefactos de
 * producto blancos que flotan alrededor del headline.
 *
 * Anti-slop: las capacidades futuras se declaran "En diseño" o "Roadmap";
 * todo dato mostrado es público y sintético.
 */

/** Revela al hacer scroll los bloques con `data-reveal`. Sin JS el contenido
 * es visible: sólo se marca como pendiente lo que está bajo el fold al montar
 * y un barrido por scroll lo revela (a prueba de saltos de ancla). */
function useRevealOnScroll() {
  useEffect(() => {
    const nodes = [...document.querySelectorAll<HTMLElement>('[data-reveal]')];
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {return;}
    const pending = nodes.filter(
      (node) => !node.classList.contains('is-visible') && node.getBoundingClientRect().top > window.innerHeight,
    );
    if (pending.length === 0) {return;}
    pending.forEach((node) => node.classList.add('js-reveal-pending'));
    const sweep = () => {
      const limit = window.innerHeight * 0.95;
      for (const node of pending) {
        if (!node.classList.contains('is-visible') && node.getBoundingClientRect().top < limit) {
          node.classList.add('is-visible');
          node.classList.remove('js-reveal-pending');
        }
      }
    };
    sweep();
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(sweep);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);
}

const EVIDENCE = [
  ['Oráculo numérico', '9.994 filas con paridad Hyper ↔ Power BI.'],
  ['Traza DAX', '13/13 visuales medibles, sin BLANK.'],
  ['Suite', '1.152 pruebas automatizadas.'],
] as const;

const CAPABILITIES = [
  {
    body: 'Llevamos tus cuadernos de Tableau y Power BI a Power BI con evidencia en cada paso: oráculo numérico sobre 9.994 filas, traza DAX que cubre 13/13 visuales y auditoría estructural. La fidelidad se demuestra, no se declara.',
    id: '01',
    status: 'Disponible hoy',
    title: 'Migración con fidelidad verificable',
  },
  {
    body: 'Desde datasets CSV abres un estudio propio: canvas, inspector, métricas y filtros bajo tu control. El agente propone los mismos cambios como operaciones tipadas; nada se aplica sin que aceptes, edites o rechaces.',
    id: '02',
    status: 'Hoy · Planner determinista',
    title: 'Estudio de autoría con aprobación humana',
  },
  {
    body: 'Cada métrica, cálculo y filtro vive en un modelo intermedio versionado, con procedencia, granularidad y timestamp. Historial completo, publicación y rollback desde la propia interfaz.',
    id: '03',
    status: 'Disponible hoy',
    title: 'IR versionado con procedencia',
  },
  {
    body: 'Agentes como Claude Code, OpenAI Codex u OpenCode podrán descubrir métricas, consultar valores exactos y proponer acciones acotadas sobre tu base de conocimiento analítico.',
    id: '04',
    status: 'En diseño',
    title: 'API semántica + SDK + MCP',
  },
] as const;

const STEPS = [
  {
    body: 'Importa un cuaderno de Tableau o Power BI para migrarlo, o un dataset CSV para empezar un análisis nuevo dentro del producto.',
    id: '01',
    title: 'Conecta',
  },
  {
    body: 'La plataforma compara estructura, números y render contra el origen, guarda la evidencia y marca las brechas honestamente.',
    id: '02',
    title: 'Verifica',
  },
  {
    body: 'Editas con control total — posición, colores, títulos, tema — o delegas al agente; cada propuesta pasa por tu aprobación.',
    id: '03',
    title: 'Autoría',
  },
] as const;

const STATUS_TODAY = [
  'Runtime local, corre en tu máquina',
  'Migración Tableau → Power BI con oráculo numérico',
  'Estudio de autoría sobre datasets CSV',
  'Historial, publicación y rollback locales',
];

const STATUS_DESIGN = [
  'API semántica + SDK para integraciones',
  'Servidor MCP para agentes externos',
];

const STATUS_ROADMAP = [
  'Despliegue self-hosted',
  'Copiloto LLM conectado al estudio',
  'Base de conocimiento analítica completa',
  'Persistencia SaaS y colaboración',
];

function SteepNav() {
  return (
    <header className="dv-steep__nav">
      <div className="dv-steep__wrap dv-steep__nav-row">
        <a className="dv-steep__brand" href="?landing=steep" aria-label="DataVIZ">
          DataVIZ
        </a>
        <nav className="dv-steep__nav-links" aria-label="Secciones">
          <a href="#steep-producto">Producto</a>
          <a href="#steep-agentes">Agentes</a>
          <a href="#steep-hoja-de-ruta">Hoja de ruta</a>
        </nav>
        <a className="dv-steep__btn dv-steep__btn--primary dv-steep__btn--small" href="?mode=author">
          Abrir la demo
        </a>
      </div>
    </header>
  );
}

interface ArtifactProps { className: string }

/** Artefacto flotante: KPI con sparkline que se dibuja. */
function ArtifactKpi({ className }: ArtifactProps) {
  return (
    <figure className={`dv-steep__artifact ${className}`} role="img" aria-label="Tarjeta de producto: métrica de ingresos con sparkline, datos sintéticos">
      <figcaption className="dv-steep__artifact-label">Ingresos · Q3</figcaption>
      <div className="dv-steep__artifact-value">
        <strong>$4,2M</strong>
        <span>+12,4%</span>
      </div>
      <svg className="dv-steep__spark" viewBox="0 0 220 56" preserveAspectRatio="none" aria-hidden="true">
        <path className="dv-steep__spark-line" pathLength={1} d="M4 46 C36 42 54 28 84 33 S138 12 166 20 S204 8 216 12" />
      </svg>
      <span className="dv-steep__artifact-foot">Datos sintéticos</span>
    </figure>
  );
}

/** Artefacto flotante: propuesta tipada esperando aprobación. */
function ArtifactProposal({ className }: ArtifactProps) {
  return (
    <figure className={`dv-steep__artifact ${className}`} role="img" aria-label="Tarjeta de producto: propuesta tipada del agente esperando aprobación humana">
      <figcaption className="dv-steep__artifact-label">Propuesta tipada · #128</figcaption>
      <div className="dv-steep__diff" aria-hidden="true">
        <span className="dv-steep__diff-out">− Top N clientes: 10</span>
        <span className="dv-steep__diff-in">+ Top N clientes: 12</span>
      </div>
      <span className="dv-steep__chip">Esperando tu aprobación</span>
    </figure>
  );
}

/** Artefacto flotante: consulta de agente contra el IR. */
function ArtifactAgent({ className }: ArtifactProps) {
  return (
    <figure className={`dv-steep__artifact ${className}`} role="img" aria-label="Tarjeta de producto: consulta de agente resuelta contra el modelo versionado">
      <span className="dv-steep__artifact-query">&gt; margen bruto por región</span>
      <span className="dv-steep__artifact-answer">IR v41 · 3 fuentes · procedencia verificada</span>
    </figure>
  );
}

function SteepHero() {
  return (
    <section className="dv-steep__hero">
      <div className="dv-steep__aura" aria-hidden="true">
        <div className="dv-steep__aura-img" />
      </div>
      <div className="dv-steep__wrap dv-steep__hero-inner">
        <p className="dv-steep__eyebrow" data-enter="1">Analítica agent-native</p>
        <h1 className="dv-steep__display" data-enter="2">
          Conocimiento de negocio,<br />
          <em>no gráficos mudos.</em>
        </h1>
        <p className="dv-steep__hero-sub" data-enter="3">
          Que el conocimiento de tu negocio no quede atrapado en dashboards.
          DataVIZ lo disponibiliza: métricas, fórmulas y reglas con procedencia,
          versionadas y consultables por personas y agentes.
        </p>
        <div className="dv-steep__hero-actions" data-enter="4">
          <a className="dv-steep__btn dv-steep__btn--primary" href="?mode=author">Abrir la demo</a>
          <a className="dv-steep__btn dv-steep__btn--ghost" href="#steep-producto">Ver el producto</a>
        </div>
        <ul className="dv-steep__evidence" data-enter="5" aria-label="Evidencia verificable">
          {EVIDENCE.map(([title, body]) => (
            <li key={title}>
              <strong>{title}</strong>
              <span>{body}</span>
            </li>
          ))}
        </ul>
        <ArtifactKpi className="dv-steep__artifact--kpi" />
        <ArtifactProposal className="dv-steep__artifact--proposal" />
        <ArtifactAgent className="dv-steep__artifact--agent" />
      </div>
    </section>
  );
}

function SteepStatement() {
  return (
    <section className="dv-steep__section dv-steep__section--alt">
      <div className="dv-steep__wrap dv-steep__statement" data-reveal>
        <p className="dv-steep__eyebrow">La tesis</p>
        <h2 className="dv-steep__headline">
          El conocimiento de negocio no se pierde en dashboards.{' '}
          <em>DataVIZ lo disponibiliza.</em>
        </h2>
        <p className="dv-steep__lede">
          Un gráfico muestra un número; no guarda qué significa, de dónde viene ni
          quién puede cambiarlo. DataVIZ convierte cada visual en conocimiento
          estructurado — con versión, filtros, granularidad y procedencia — que
          permanece cuando la pestaña se cierra.
        </p>
      </div>
    </section>
  );
}

function SteepCapabilities() {
  return (
    <section className="dv-steep__section" id="steep-producto">
      <div className="dv-steep__wrap">
        <div className="dv-steep__section-head" data-reveal>
          <p className="dv-steep__eyebrow">Qué hace DataVIZ</p>
          <h2 className="dv-steep__headline dv-steep__headline--md">Cuatro piezas, un mismo modelo.</h2>
        </div>
        <div className="dv-steep__grid" data-reveal data-reveal-stagger>
          {CAPABILITIES.map((item) => (
            <article className="dv-steep__card" key={item.id}>
              <span className="dv-steep__card-index">{item.id}</span>
              <h3>{item.title}</h3>
              <p>{item.body}</p>
              <span className="dv-steep__status">{item.status}</span>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

/** Única tarjeta peach de la página: el mensaje Agent Native. */
function SteepAgentNative() {
  return (
    <section className="dv-steep__section" id="steep-agentes">
      <div className="dv-steep__wrap">
        <article className="dv-steep__peach" data-reveal>
          <p className="dv-steep__eyebrow dv-steep__eyebrow--sienna">Agent native</p>
          <h2 className="dv-steep__headline dv-steep__headline--md">
            Tu analítica, legible para agentes.
          </h2>
          <p className="dv-steep__peach-body">
            Las fórmulas, reglas y resultados dejan de vivir dentro de cada gráfico:
            alimentan una base de conocimiento analítico con versión, filtros,
            granularidad y timestamp. Una API semántica, un SDK y MCP permiten que un
            agente descubra, razone, compare y proponga acciones — siempre con
            aprobación humana.
          </p>
          <ul className="dv-steep__peach-chips" aria-label="Superficies de acceso para agentes">
            <li>dataviz.query("margen", periodo="Q3")</li>
            <li>GET /api/v1/metrics</li>
            <li>MCP · dataviz-discover</li>
          </ul>
          <p className="dv-steep__peach-note">
            API semántica, SDK y MCP están en diseño; el IR versionado y el estudio de
            autoría ya existen hoy.
          </p>
        </article>
      </div>
    </section>
  );
}

function SteepSteps() {
  return (
    <section className="dv-steep__section dv-steep__section--alt">
      <div className="dv-steep__wrap">
        <div className="dv-steep__section-head" data-reveal>
          <p className="dv-steep__eyebrow">Cómo se trabaja</p>
          <h2 className="dv-steep__headline dv-steep__headline--md">Tres pasos, cero caja negra.</h2>
        </div>
        <ol className="dv-steep__steps" data-reveal data-reveal-stagger>
          {STEPS.map((step) => (
            <li className="dv-steep__step" key={step.id}>
              <span className="dv-steep__step-index" aria-hidden="true">{step.id}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function SteepRoadmap() {
  const columns: [string, readonly string[], string][] = [
    ['Hoy', STATUS_TODAY, 'Funcionando en tu máquina'],
    ['En diseño', STATUS_DESIGN, 'Definición e implementación en curso'],
    ['Roadmap', STATUS_ROADMAP, 'Comprometido, aún no disponible'],
  ];
  return (
    <section className="dv-steep__section" id="steep-hoja-de-ruta">
      <div className="dv-steep__wrap">
        <div className="dv-steep__section-head" data-reveal>
          <p className="dv-steep__eyebrow">Hoja de ruta honesta</p>
          <h2 className="dv-steep__headline dv-steep__headline--md">Lo que hay, lo que viene.</h2>
        </div>
        <div className="dv-steep__roadmap" data-reveal data-reveal-stagger>
          {columns.map(([label, items, note]) => (
            <div className="dv-steep__roadmap-col" key={label}>
              <h3>
                <span className="dv-steep__status dv-steep__status--dot">{label}</span>
              </h3>
              <ul>
                {items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
              <p>{note}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function SteepFinalCta() {
  return (
    <section className="dv-steep__section dv-steep__section--cta">
      <div className="dv-steep__wrap dv-steep__cta" data-reveal>
        <h2 className="dv-steep__display dv-steep__display--sm">
          No dejes otro dashboard.<br />
          <em>Deja conocimiento.</em>
        </h2>
        <p className="dv-steep__hero-sub">
          Abre el estudio con datos sintéticos y prueba el flujo completo:
          autoría, propuestas tipadas, historial y rollback.
        </p>
        <div className="dv-steep__hero-actions dv-steep__hero-actions--center">
          <a className="dv-steep__btn dv-steep__btn--primary" href="?mode=author">Abrir la demo</a>
        </div>
      </div>
    </section>
  );
}

function SteepFooter() {
  return (
    <footer className="dv-steep__footer">
      <div className="dv-steep__wrap dv-steep__footer-row">
        <span className="dv-steep__brand dv-steep__brand--footer">DataVIZ</span>
        <p className="dv-steep__footer-note">
          Contenido público y sintético únicamente. Nunca datos de clientes.
        </p>
        <span className="dv-steep__footer-copy">© 2026 DataVIZ</span>
      </div>
    </footer>
  );
}

/** Landing de ventas variante Steep (`?landing=steep`). */
export function SteepLanding() {
  useRevealOnScroll();
  return (
    <div className="dv-steep">
      <SteepNav />
      <main>
        <SteepHero />
        <SteepStatement />
        <SteepCapabilities />
        <SteepAgentNative />
        <SteepSteps />
        <SteepRoadmap />
        <SteepFinalCta />
      </main>
      <SteepFooter />
    </div>
  );
}

export default SteepLanding;
