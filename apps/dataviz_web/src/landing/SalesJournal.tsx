import { useEffect, useRef } from 'react';
import type { CSSProperties } from 'react';

import './sales-journal.css';

/** Retardo escalonado (80 ms) para reveals dentro de una misma sección. */
const revealDelay = (step: number): CSSProperties =>
  ({ '--reveal-delay': `${step * 80}ms` }) as CSSProperties;

type DotTone = 'teal' | 'navy' | 'rose';

/** Eyebrow monoespaciado UPPERCASE con dot de color pulsante, firma de la variante journal. */
function Eyebrow({ tone, children }: { tone: DotTone; children: string }) {
  return (
    <p className="dvj-eyebrow" data-reveal>
      <span className={`dvj-dot dvj-dot--${tone}`} aria-hidden="true" />
      {children}
    </p>
  );
}

/**
 * Ilustración "field notes" dibujada a mano: ventana de navegador con ejes,
 * sparkline que se traza al entrar en viewport, barras esquematicas y doodles
 * (estrella, flecha, círculo) en tinta con fills teal y rose.
 */
function FieldNotesSketch() {
  return (
    <svg
      className="dvj-sketch"
      viewBox="0 0 560 480"
      role="img"
      aria-label="Ilustración a mano de un reporte analítico anotado en una libreta de campo"
      focusable="false"
      data-reveal
      style={revealDelay(4)}
    >
      {/* ventana de navegador */}
      <g className="dvj-sketch-window">
        <rect x="60" y="56" width="400" height="300" rx="14" />
        <line x1="60" y1="102" x2="460" y2="102" />
        <circle cx="88" cy="79" r="5" className="dvj-fill-teal" />
        <circle cx="108" cy="79" r="5" />
        <circle cx="128" cy="79" r="5" className="dvj-fill-rose" />
        {/* barra lateral esquemática */}
        <line x1="92" y1="138" x2="168" y2="138" />
        <line x1="92" y1="164" x2="196" y2="164" />
        <line x1="92" y1="190" x2="150" y2="190" />
        {/* ejes */}
        <line x1="212" y1="136" x2="212" y2="322" />
        <line x1="212" y1="322" x2="430" y2="322" />
        {/* barras */}
        <rect x="232" y="252" width="26" height="70" rx="4" className="dvj-fill-teal" />
        <rect x="270" y="216" width="26" height="106" rx="4" className="dvj-fill-rose" />
        <rect x="308" y="238" width="26" height="84" rx="4" className="dvj-fill-teal" />
        <rect x="346" y="188" width="26" height="134" rx="4" />
        {/* sparkline que se dibuja */}
        <path
          className="dvj-sparkline"
          d="M220 148 C 252 118, 280 172, 312 146 S 372 96, 424 122"
          pathLength={1}
        />
        <circle cx="424" cy="122" r="6" className="dvj-fill-rose dvj-sketch-endpoint" />
      </g>

      {/* nota manuscrita junto al pico del sparkline */}
      <g className="dvj-sketch-note">
        <path d="M452 96 l38 -18" />
        <path d="M496 62 q 26 -12 34 8 q 6 18 -14 24 q -24 6 -28 -10 q -3 -14 8 -22" />
      </g>

      {/* estrella doodle */}
      <g className="dvj-sketch-doodle">
        <path d="M84 396 l7 16 18 2 -13 12 4 17 -16 -9 -16 9 4 -17 -13 -12 18 -2 z" />
      </g>

      {/* flecha curva hacia la figura */}
      <g className="dvj-sketch-doodle">
        <path d="M150 440 q 90 26 176 6" />
        <path d="M326 446 l14 -6 -10 14" />
      </g>

      {/* círculo garabateado alrededor de una barra */}
      <g className="dvj-sketch-doodle dvj-sketch-circle">
        <ellipse cx="359" cy="252" rx="46" ry="72" pathLength={1} />
      </g>
    </svg>
  );
}

/** Mini diagrama de flujo del IR: orígenes → IR versionado → superficies. */
function IrDiagram() {
  return (
    <svg
      className="dvj-ir-diagram"
      viewBox="0 0 720 240"
      role="img"
      aria-label="Diagrama: Tableau, Power BI y CSV entran al IR versionado; de ahí salen la UI, la API semántica y los agentes"
      focusable="false"
      data-reveal
    >
      <g className="dvj-ir-node">
        <rect x="20" y="30" width="130" height="44" rx="12" />
        <text x="85" y="57">Tableau</text>
        <rect x="20" y="98" width="130" height="44" rx="12" />
        <text x="85" y="125">Power BI</text>
        <rect x="20" y="166" width="130" height="44" rx="12" />
        <text x="85" y="193">CSV</text>
      </g>
      <g className="dvj-ir-links">
        <path d="M158 52 C 210 52, 220 104, 268 110" />
        <path d="M158 120 L 264 120" />
        <path d="M158 188 C 210 188, 220 136, 268 130" />
        <path d="M262 104 l 12 6 -12 6" />
      </g>
      <g className="dvj-ir-core">
        <rect x="276" y="86" width="180" height="68" rx="12" />
        <text x="366" y="115">IR versionado</text>
        <text x="366" y="137">procedencia · diffs</text>
      </g>
      <g className="dvj-ir-links">
        <path d="M464 100 C 512 96, 520 74, 566 70" />
        <path d="M464 120 L 562 120" />
        <path d="M464 140 C 512 144, 520 166, 566 170" />
        <path d="M558 64 l 12 5 -11 8" />
        <path d="M558 114 l 12 6 -12 6" />
        <path d="M558 176 l 12 -5 -11 -8" />
      </g>
      <g className="dvj-ir-node">
        <rect x="572" y="48" width="128" height="44" rx="12" />
        <text x="636" y="75">UI propia</text>
        <rect x="572" y="98" width="128" height="44" rx="12" />
        <text x="636" y="125">API semántica</text>
        <rect x="572" y="148" width="128" height="44" rx="12" />
        <text x="636" y="175">Agentes · MCP</text>
      </g>
    </svg>
  );
}

const MIGRATION_CHECKS = [
  'Oráculo numérico: paridad fila a fila sobre 9.994 registros',
  'Traza DAX equivalente: 13 de 13 cálculos verificados contra el origen',
  '1.152 pruebas automatizadas custodian cada conversión',
  'Visuales, filtros y layout reconocibles en Power BI',
];

const AUTHORING_CHECKS = [
  'Canvas propio para construir desde cero, partiendo de un CSV',
  'El agente propone cambios tipados: tú aceptas, editas o rechazas',
  'Inspector de métricas, filtros, colores, posición e interacciones',
  'Historial completo con preview y rollback',
];

const AGENT_SURFACES = [
  {
    desc: 'Descubrir, consultar y comparar métricas con semántica explícita: definición, granularidad y procedencia.',
    name: 'API semántica',
    status: 'En diseño',
    tone: 'rose' as const,
  },
  {
    desc: 'Leer y operar el IR desde código propio, con tipos derivados del mismo modelo que usa la UI.',
    name: 'SDK tipado',
    status: 'Roadmap',
    tone: 'sand' as const,
  },
  {
    desc: 'Claude Code, OpenAI Codex y OpenCode proponiendo operaciones acotadas sobre el IR, siempre con aprobación humana.',
    name: 'MCP para agentes',
    status: 'Roadmap',
    tone: 'navy' as const,
  },
];

const IR_CHIPS = ['Procedencia', 'Dependencias', 'Versión', 'Granularidad', 'Timestamp'];

const STATS = [
  '9.994 filas en paridad',
  '13/13 traza DAX',
  '1.152 pruebas',
  'runtime local, hoy',
];

/** Landing de ventas variante Journal (`?landing=journal`) — notas de campo sobre papel cálido. */
export function JournalLanding() {
  const rootRef = useRef<HTMLDivElement>(null);

  // Reveal al scroll: sin JS el contenido es visible; sólo se marca como
  // pendiente lo bajo el fold al montar y un barrido por scroll lo revela
  // (a prueba de saltos de ancla que el IntersectionObserver no ve pasar).
  useEffect(() => {
    const root = rootRef.current;
    if (!root) {return;}
    const targets = [...root.querySelectorAll<HTMLElement>('[data-reveal]')];
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      return;
    }
    const pending = targets.filter(
      (el) => !el.classList.contains('is-visible') && el.getBoundingClientRect().top > window.innerHeight,
    );
    if (pending.length === 0) {return;}
    pending.forEach((el) => el.classList.add('js-reveal-pending'));
    const sweep = () => {
      const limit = window.innerHeight * 0.95;
      for (const el of pending) {
        if (!el.classList.contains('is-visible') && el.getBoundingClientRect().top < limit) {
          el.classList.add('is-visible');
          el.classList.remove('js-reveal-pending');
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

  return (
    <div className="dvj-journal" ref={rootRef}>
      {/* ─── Barra superior ─────────────────────────────────────────────── */}
      <header className="dvj-topbar">
        <div className="dvj-shell dvj-topbar-row">
          <a className="dvj-brand" href="#top" aria-label="DataVIZ, volver arriba">
            <span className="dvj-dot dvj-dot--teal" aria-hidden="true" />
            DataVIZ
          </a>
          <nav className="dvj-nav" aria-label="Secciones">
            <a href="#migracion">Migración</a>
            <a href="#autoria">Autoría</a>
            <a href="#ir">El IR</a>
            <a href="#agentes">Agentes</a>
          </nav>
          <a className="dvj-btn dvj-btn--ghost" href="?mode=author">
            Abrir la demo
          </a>
        </div>
      </header>

      <main id="top">
        {/* ─── Hero ───────────────────────────────────────────────────────── */}
        <section className="dvj-hero">
          <img
            className="dvj-hero-aura"
            src="/landing-aura.jpg"
            alt=""
            aria-hidden="true"
            loading="eager"
          />
          <div className="dvj-shell dvj-hero-grid">
            <div className="dvj-hero-copy">
              <p className="dvj-eyebrow" data-reveal>
                <span className="dvj-dot dvj-dot--teal" aria-hidden="true" />
                Notas de campo · Nº 001
              </p>
              <h1 className="dvj-h1" data-reveal style={revealDelay(1)}>
                El conocimiento de tu negocio,{' '}
                <em className="dvj-em-underline">
                  consultable
                  <svg viewBox="0 0 260 18" aria-hidden="true" focusable="false">
                    <path
                      className="dvj-squiggle"
                      d="M6 11 C 40 4, 78 15, 116 9 S 192 4, 254 10"
                      pathLength={1}
                    />
                  </svg>
                </em>
                .
              </h1>
              <p className="dvj-lede" data-reveal style={revealDelay(2)}>
                Las fórmulas, métricas y reglas que hoy quedan atrapadas en dashboards,
                disponibilizadas como una base de conocimiento: con procedencia, versión
                y evidencia numérica.
              </p>
              <div className="dvj-hero-actions" data-reveal style={revealDelay(3)}>
                <a className="dvj-btn dvj-btn--primary" href="?mode=author">
                  Abrir la demo
                </a>
                <a className="dvj-btn dvj-btn--navy" href="#migracion">
                  Ver la migración
                </a>
              </div>
            </div>
            <div className="dvj-hero-art">
              <FieldNotesSketch />
            </div>
          </div>
        </section>

        {/* ─── Franja de estadísticas ─────────────────────────────────────── */}
        <section className="dvj-statstrip" aria-label="Evidencia de fidelidad verificada">
          <div className="dvj-shell dvj-statstrip-row" data-reveal>
            {STATS.map((stat, i) => (
              <span className="dvj-stat" key={stat}>
                {i > 0 && (
                  <span className="dvj-stat-pipe" aria-hidden="true">
                    |
                  </span>
                )}
                <span className="dvj-stat-text">{stat}</span>
              </span>
            ))}
          </div>
        </section>

        {/* ─── Nota 01 · La tesis ─────────────────────────────────────────── */}
        <section className="dvj-section dvj-section--blush" id="tesis">
          <div className="dvj-shell dvj-section-inner">
            <Eyebrow tone="rose">Nota 01 · La tesis</Eyebrow>
            <blockquote className="dvj-pullquote">
              <p data-reveal>
                Que el conocimiento de negocio no se pierda en dashboards.{' '}
                <em>DataVIZ lo disponibiliza.</em>
              </p>
            </blockquote>
            <p className="dvj-body dvj-body--narrow" data-reveal>
              Un dashboard responde la pregunta de ayer; el conocimiento que lo construyó
              suele quedar encerrado en un archivo que nadie puede consultar. DataVIZ
              extrae las definiciones exactas —métricas, reglas, cálculos afinados a
              mano— y las deja disponibles para personas y agentes, sin perder
              trazabilidad.
            </p>
          </div>
        </section>

        {/* ─── Nota 02 · Disponible hoy ───────────────────────────────────── */}
        <section className="dvj-section" id="migracion">
          <div className="dvj-shell dvj-section-inner">
            <Eyebrow tone="teal">Nota 02 · Disponible hoy</Eyebrow>
            <h2 className="dvj-h2" data-reveal>
              Fidelidad que se verifica, autoría que se aprueba.
            </h2>
            <p className="dvj-body" data-reveal>
              Dos frentes funcionan hoy, en runtime local y con datos públicos o
              sintéticos: traer tus reportes existentes sin perder un número, y crear
              unos nuevos con un agente que propone pero no decide.
            </p>
            <div className="dvj-cards">
              <article className="dvj-card" data-reveal style={revealDelay(0)}>
                <h3 className="dvj-card-title">Migración Tableau / Power BI</h3>
                <p className="dvj-card-sub">Con fidelidad verificable, no aproximada.</p>
                <ul className="dvj-checklist">
                  {MIGRATION_CHECKS.map((item) => (
                    <li key={item}>
                      <span className="dvj-check" aria-hidden="true">
                        ✓
                      </span>
                      {item}
                    </li>
                  ))}
                </ul>
              </article>
              <article className="dvj-card" id="autoria" data-reveal style={revealDelay(1)}>
                <h3 className="dvj-card-title">Estudio de autoría</h3>
                <p className="dvj-card-sub">Manual y agéntica sobre el mismo lienzo.</p>
                <ul className="dvj-checklist">
                  {AUTHORING_CHECKS.map((item) => (
                    <li key={item}>
                      <span className="dvj-check" aria-hidden="true">
                        ✓
                      </span>
                      {item}
                    </li>
                  ))}
                </ul>
              </article>
            </div>
          </div>
        </section>

        {/* ─── Nota 03 · El IR ────────────────────────────────────────────── */}
        <section className="dvj-section dvj-section--foam" id="ir">
          <div className="dvj-shell dvj-section-inner">
            <Eyebrow tone="navy">Nota 03 · La base</Eyebrow>
            <h2 className="dvj-h2" data-reveal>
              Un IR versionado, no capturas.
            </h2>
            <p className="dvj-body" data-reveal>
              Toda edición —manual o propuesta por un agente— produce el mismo diff
              versionado sobre una representación intermedia del reporte. Cada nodo
              conserva qué es, de dónde viene, de qué depende y bajo qué filtros existe.
            </p>
            <ul className="dvj-chips" data-reveal>
              {IR_CHIPS.map((chip) => (
                <li className="dvj-chip" key={chip}>
                  {chip}
                </li>
              ))}
            </ul>
            <IrDiagram />
          </div>
        </section>

        {/* ─── Nota 04 · Agentes ──────────────────────────────────────────── */}
        <section className="dvj-section" id="agentes">
          <div className="dvj-shell dvj-section-inner">
            <Eyebrow tone="teal">Nota 04 · Agent native</Eyebrow>
            <h2 className="dvj-h2" data-reveal>
              Una superficie para agentes, con permisos claros.
            </h2>
            <p className="dvj-body" data-reveal>
              El agente no recibe SQL libre ni autoridad implícita: propone operaciones
              tipadas sobre el IR y el sistema aplica políticas. Las superficies que lo
              conectan están en camino.
            </p>
            <div className="dvj-cards dvj-cards--three">
              {AGENT_SURFACES.map((surface, i) => (
                <article className="dvj-mini" key={surface.name} data-reveal style={revealDelay(i)}>
                  <div className="dvj-mini-head">
                    <h3 className="dvj-mini-title">{surface.name}</h3>
                    <span className={`dvj-tag dvj-tag--${surface.tone}`}>{surface.status}</span>
                  </div>
                  <p className="dvj-mini-desc">{surface.desc}</p>
                </article>
              ))}
            </div>
            <p className="dvj-runtime-note" data-reveal>
              <span className="dvj-tag dvj-tag--teal">Disponible hoy</span>
              Runtime local para explorar la demo con datos sintéticos ·{' '}
              <span className="dvj-tag dvj-tag--sand">Roadmap</span> despliegue
              self-hosted para equipos.
            </p>
          </div>
        </section>

        {/* ─── CTA final ──────────────────────────────────────────────────── */}
        <section className="dvj-section dvj-section--blush">
          <div className="dvj-shell dvj-cta">
            <h2 className="dvj-h2 dvj-h2--center" data-reveal>
              Tu próxima métrica puede vivir <em>fuera</em> del dashboard.
            </h2>
            <p className="dvj-body dvj-body--center" data-reveal style={revealDelay(1)}>
              Abre el estudio, mueve un filtro, propón un cambio con el agente y mira el
              diff. Todo corre local, con datos públicos y sintéticos.
            </p>
            <div data-reveal style={revealDelay(2)}>
              <a className="dvj-btn dvj-btn--primary" href="?mode=author">
                Abrir la demo
              </a>
            </div>
          </div>
        </section>
      </main>

      {/* ─── Pie ──────────────────────────────────────────────────────────── */}
      <footer className="dvj-footer">
        <div className="dvj-shell dvj-footer-row">
          <p className="dvj-footer-disclaimer">
            Contenido público y sintético únicamente. Nunca datos de clientes.
          </p>
          <p className="dvj-footer-meta">DataVIZ · edición journal · 2026</p>
        </div>
      </footer>
    </div>
  );
}

export default JournalLanding;
