import { useEffect } from 'react';

import './sales.css';

/**
 * Landing de ventas de DataVIZ (`?landing=sales`).
 *
 * Mensaje central (directiva del dueño 2026-08-25): "Que el conocimiento de
 * negocio no se pierda en dashboards. DataVIZ lo disponibiliza." Vende las
 * capacidades actuales y futuras de DataVIZ sin comparativas competitivas.
 * Copy base en `Docs/marketing/LANDING_FEEDBACK_LOG.md`; lo inexistente se
 * declara "En diseño" o "Roadmap".
 */

const EVIDENCE = [
  ['Oráculo numérico', '9.994 filas con paridad Hyper↔Power BI.'],
  ['Traza DAX', '13/13 visuales medibles, sin BLANK.'],
  ['Suite', '1.152 pruebas automatizadas.'],
] as const;

const PILLARS = [
  {
    body: 'Tú controlas cálculos, filtros y tema; el agente propone los mismos cambios como operaciones tipadas. Nada se aplica sin tu aprobación.',
    id: '01',
    status: 'Hoy: planner determinista · Roadmap: copiloto LLM',
    title: 'AI desde el día uno',
  },
  {
    body: 'Un IR versionado con procedencia y granularidad. Las fórmulas dejan de ser gráficos mudos: son conocimiento consultable por APIs, SDK y agentes.',
    id: '02',
    status: 'Hoy: IR + oráculos · Roadmap: catálogo y linaje',
    title: 'Semántica unificada',
  },
  {
    body: 'Corre local y self-hosted. Nadie pelea presupuesto por cada usuario que quiera mirar un número.',
    id: '03',
    status: 'Hoy: local · Roadmap: self-hosted',
    title: 'Costos sin licencias',
  },
] as const;

const CAPABILITIES = [
  {
    feature: 'Migración Tableau y Power BI verificable',
    status: 'Hoy',
    us: 'Oráculo numérico, traza DAX y capturas por visual.',
  },
  {
    feature: 'Estudio de autoría con aprobación humana',
    status: 'Hoy',
    us: 'Cálculos, filtros y tema; propuestas tipadas que tú aceptas o editas.',
  },
  {
    feature: 'Modelo semántico versionado',
    status: 'Hoy',
    us: 'Cada fórmula con procedencia, dependencias y granularidad.',
  },
  {
    feature: 'Agentes conectados por MCP',
    status: 'En diseño',
    us: 'API semántica + SDK para Claude Code, Codex y OpenCode.',
  },
  {
    feature: 'Consultas en lenguaje natural',
    status: 'Roadmap',
    us: 'Preguntas de negocio con respuestas trazadas al modelo.',
  },
  {
    feature: 'Runtime local y self-hosted',
    status: 'Hoy local · Roadmap: self-hosted productivo',
    us: 'Tus datos en tu infraestructura, sin licencia por asiento.',
  },
] as const;

const COSTS = [
  ['Microsoft 365 E3', '$14', 'por usuario / mes'],
  ['Power BI en Fabric F64', '≈ $5.000', 'por mes de capacidad'],
  ['Tableau Cloud', '$15–$75', 'por usuario / mes'],
] as const;

const PIPELINE = ['Tableau', 'Source AST', 'Neutral IR', 'Render plan', 'Evidence'] as const;

const MCP_STEPS = [
  {
    body: 'Métricas, filtros y dependencias con procedencia y versión. Nada de adivinar qué significa «margen» en tu empresa.',
    id: '01',
    title: 'Descubre',
  },
  {
    body: 'El modelo semántico responde con valores del oráculo, no con texto plausible.',
    id: '02',
    title: 'Consulta exacto',
  },
  {
    body: 'Diffs acotados; aceptás, editás o rechazás. Sin SQL libre ni secretos.',
    id: '03',
    title: 'Propone, tú decides',
  },
] as const;

/** Revela al hacer scroll los bloques marcados con `data-reveal`. Sin JS el
 * contenido es visible: el hook sólo marca como pendientes las secciones bajo
 * el fold al montar y las revela con un barrido por scroll (a prueba de
 * saltos de ancla y teclado que el IntersectionObserver no ve pasar). */
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

function Brand() {
  return (
    <a className="dv-sales__brand" href="?landing=sales" aria-label="DataVIZ">
      <span className="dv-sales__brand-mark" aria-hidden="true"><span /><span /><span /></span>
      <strong>DataVIZ</strong>
    </a>
  );
}

function Nav() {
  return (
    <header className="dv-sales__nav">
      <Brand />
      <nav className="dv-sales__nav-links" aria-label="Secciones">
        <a href="#sales-product">Producto</a>
        <a href="#sales-agent-native">Agent Native</a>
        <a href="#sales-parity">Paridad</a>
        <a href="#sales-costs">Costos</a>
      </nav>
      <a className="dv-sales__nav-cta" href="?mode=author">Abrir demo</a>
    </header>
  );
}

/** Ventana de producto (spec Ox): title bar + KPI + sparkline + linaje. Sin captions. */
function ProductWindow() {
  return (
    <div className="dv-sales__halo" aria-hidden="true">
      <figure className="dv-sales__window" role="img" aria-label="DataVIZ Studio: métrica con linaje consultable por un agente MCP">
        <div className="dv-sales__window-bar">
          <span className="dv-sales__window-dots" aria-hidden="true"><i /><i /><i /></span>
          <span className="dv-sales__window-title">DataVIZ — Studio</span>
          <span className="dv-sales__window-badge"><i aria-hidden="true" />MCP</span>
        </div>
        <div className="dv-sales__window-body">
          <div className="dv-sales__kpi">
            <small>Ingreso · Q3</small>
            <strong>$4,2M</strong>
            <span>+12,4% <i aria-hidden="true" /></span>
          </div>
          <svg className="dv-sales__spark" aria-hidden="true" viewBox="0 0 280 64" preserveAspectRatio="none">
            <path className="dv-sales__spark-area" d="M4 56 C40 50 64 36 96 41 S156 16 190 26 S248 10 276 15 L276 62 L4 62Z" />
            <path className="dv-sales__spark-line" d="M4 56 C40 50 64 36 96 41 S156 16 190 26 S248 10 276 15" />
          </svg>
          <ol className="dv-sales__lineage" aria-label="Linaje del dato">
            <li>Fuente</li>
            <li>Modelo</li>
            <li className="dv-sales__lineage-agent">Agente MCP</li>
          </ol>
        </div>
      </figure>
    </div>
  );
}

function Hero() {
  return (
    <section className="dv-sales__hero">
      <div className="dv-sales__hero-copy">
        <h1>Analítica de clase mundial.<br /><em>Agent Native.</em></h1>
        <p className="dv-sales__hero-thesis">
          Que el conocimiento de negocio no se pierda en dashboards. DataVIZ
          lo disponibiliza: fórmulas y métricas consultables por tu equipo y
          por agentes de IA.
        </p>
        <div className="dv-sales__actions">
          <a className="dv-sales__button dv-sales__button--primary" href="?mode=author">Abrir el estudio de autoría</a>
          <a className="dv-sales__button" href="#sales-parity">Ver capacidades</a>
        </div>
        <ul className="dv-sales__evidence" id="sales-proof" aria-label="Evidencia verificable">
          {EVIDENCE.map(([title, body]) => (
            <li key={title}><strong>{title}</strong><span>{body}</span></li>
          ))}
        </ul>
      </div>
      <ProductWindow />
    </section>
  );
}

function Problem() {
  return (
    <section className="dv-sales__problem">
      <blockquote>
        <p>El conocimiento de tu negocio merece vivir fuera del dashboard.</p>
      </blockquote>
      <div className="dv-sales__problem-pair" data-reveal>
        <article>
          <h3>El dashboard mudo</h3>
          <p>Tu lógica vive sepultada en DAX y LODs que sólo saben dibujarse. Cada pregunta nueva es una licencia más.</p>
        </article>
        <article>
          <h3>El conocimiento vivo</h3>
          <p>Las mismas fórmulas, expuestas como base de conocimiento: versionadas, con dependencias, listas para humanos y agentes.</p>
        </article>
      </div>
    </section>
  );
}

function Pillars() {
  return (
    <section className="dv-sales__pillars" id="sales-product">
      <h2>Un producto, tres promesas</h2>
      <div className="dv-sales__pillar-grid">
        {PILLARS.map(({ id, title, body, status }) => (
          <article className="dv-sales__pillar" key={id} data-reveal>
            <span className="dv-sales__pillar-num">{id}</span>
            <h3>{title}</h3>
            <p>{body}</p>
            <small>{status}</small>
          </article>
        ))}
      </div>
    </section>
  );
}

function AgentNative() {
  return (
    <section className="dv-sales__mcp" id="sales-agent-native">
      <div className="dv-sales__mcp-inner">
        <h2>Agent Native, por diseño</h2>
        <p className="dv-sales__mcp-lead">
          Claude Code, OpenAI Codex y OpenCode se conectan vía MCP. Cada
          respuesta llega con su fórmula, su granularidad y su fuente.
        </p>
        <ol className="dv-sales__mcp-steps">
          {MCP_STEPS.map(({ id, title, body }) => (
            <li key={id} data-reveal>
              <span>{id}</span>
              <h3>{title}</h3>
              <p>{body}</p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

function Capabilities() {
  return (
    <section className="dv-sales__parity" id="sales-parity">
      <h2>Lo que DataVIZ hace por tu conocimiento</h2>
      <p className="dv-sales__section-lead">
        Capacidades con estado real: disponible hoy, en diseño o en roadmap.
      </p>
      <div className="dv-sales__parity-table" role="table" aria-label="Capacidades de DataVIZ y su estado" data-reveal>
        <div role="row" className="dv-sales__parity-head">
          <span role="columnheader">Capacidad</span>
          <span role="columnheader">Qué significa</span>
          <span role="columnheader">Estado</span>
        </div>
        {CAPABILITIES.map((row) => (
          <div role="row" key={row.feature} data-status={row.status}>
            <span role="cell"><strong>{row.feature}</strong></span>
            <span role="cell">{row.us}</span>
            <span role="cell"><span className="dv-sales__dot" aria-hidden="true" />{row.status}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function Migration() {
  return (
    <section className="dv-sales__migration">
      <h2>De Tableau o Power BI, sin perder nada</h2>
      <p className="dv-sales__section-lead">
        La migración conserva el significado y devuelve evidencia verificable
        en cada paso del camino.
      </p>
      <ol className="dv-sales__pipeline" aria-label="Pipeline de migración" data-reveal>
        {PIPELINE.map((step, index) => (
          <li key={step}>
            <span>{String(index + 1).padStart(2, '0')}</span>
            <strong>{step}</strong>
          </li>
        ))}
      </ol>
      <div className="dv-sales__fidelity">
        <div><span>Native</span><strong>preservado</strong></div>
        <div><span>Extended</span><strong>explicado</strong></div>
        <div><span>Approximate</span><strong>marcado</strong></div>
      </div>
    </section>
  );
}

function Costs() {
  return (
    <section className="dv-sales__costs" id="sales-costs">
      <h2>Menos licencias, más acceso</h2>
      <p className="dv-sales__section-lead">
        Referencia del mercado donde hoy viven tus dashboards:
      </p>
      <dl className="dv-sales__costs-table" data-reveal>
        {COSTS.map(([item, price, unit]) => (
          <div key={item}>
            <dt>{item}</dt>
            <dd><strong>{price}</strong><span>{unit}</span></dd>
          </div>
        ))}
      </dl>
      <p className="dv-sales__costs-note">
        DataVIZ corre hoy en local, sin licencias por asiento. Precios de lista
        públicos; varían por región y negociación.
      </p>
    </section>
  );
}

function FinalCta() {
  return (
    <section className="dv-sales__final">
      <h2>Conocimiento de negocio, disponible.</h2>
      <div className="dv-sales__actions">
        <a className="dv-sales__button dv-sales__button--primary" href="?mode=author">Abrir la demo</a>
        <a className="dv-sales__button" href="?landing=gallery">Conceptos visuales</a>
      </div>
    </section>
  );
}

export function SalesLanding() {
  useRevealOnScroll();
  return (
    <div className="dv-sales" data-sales-landing="true">
      <Nav />
      <main>
        <Hero />
        <Problem />
        <Pillars />
        <AgentNative />
        <Capabilities />
        <Migration />
        <Costs />
        <FinalCta />
      </main>
      <footer className="dv-sales__footer">
        <Brand />
        <p>Contenido público y sintético únicamente. Nunca datos de clientes.</p>
        <a href="?landing=gallery">Laboratorio de conceptos</a>
      </footer>
    </div>
  );
}

export default SalesLanding;
