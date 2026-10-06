import type { ReactNode } from 'react';

import './landing.css';

export const LANDING_CONCEPTS = [
  { id: 1, name: 'Precision', note: 'Dark, technical, evidence-first', slug: 'precision' },
  { id: 2, name: 'Editorial', note: 'Story-led, typographic, calm', slug: 'editorial' },
  { id: 3, name: 'Command', note: 'Developer console, compiler language', slug: 'command' },
  { id: 4, name: 'Prism', note: 'Luminous, modern product launch', slug: 'prism' },
  { id: 5, name: 'Blueprint', note: 'Architectural, systematic, precise', slug: 'blueprint' },
  { id: 6, name: 'Signal', note: 'Dashboard-first, visual proof', slug: 'signal' },
  { id: 7, name: 'Swiss', note: 'Brutalist, high-contrast, memorable', slug: 'swiss' },
  { id: 8, name: 'Canvas', note: 'Warm, human, design-conscious', slug: 'canvas' },
  { id: 9, name: 'Enterprise', note: 'Trustworthy B2B, governance-ready', slug: 'enterprise' },
  { id: 10, name: 'Orbit', note: 'Spatial, platform vision, bold', slug: 'orbit' },
] as const;

export type LandingId = (typeof LANDING_CONCEPTS)[number]['id'];
/** Valores que acepta `?landing=`: conceptos, galería y landings de ventas. */
export type LandingSelection = LandingId | 'gallery' | 'sales' | 'steep' | 'journal';
/** Selecciones que renderiza el laboratorio de conceptos (excluye ventas). */
export type ConceptSelection = LandingId | 'gallery';
export type SalesSurface = Exclude<LandingSelection, ConceptSelection | null>;

const PROOF = [
  ['Structure', 'Trace source objects into versioned contracts.'],
  ['Numbers', 'Validate totals, aggregations, filters and calculations.'],
  ['Visuals', 'Keep recognizable intent and disclose every approximation.'],
] as const;

const PIPELINE = ['Tableau', 'Source AST', 'Neutral IR', 'Render plan', 'Evidence'] as const;

export function resolveLandingSelection(value: string | null): LandingSelection | null {
  if (value === null) {return null;}
  if (value === 'gallery') {return 'gallery';}
  if (value === 'sales' || value === 'steep' || value === 'journal') {return value;}
  const parsed = Number(value);
  const concept = LANDING_CONCEPTS.find(({ id }) => id === parsed);
  return concept?.id ?? 'gallery';
}

function Brand({ inverse = false }: { inverse?: boolean }) {
  return (
    <a className="dv-landing__brand" data-inverse={inverse || undefined} href="?landing=gallery" aria-label="DataVIZ landing concepts">
      <span className="dv-landing__brand-mark" aria-hidden="true">
        <span />
        <span />
        <span />
      </span>
      <strong>DataVIZ</strong>
    </a>
  );
}

function Switcher({ active }: { active: LandingSelection }) {
  return (
    <nav className="dv-landing__switcher" aria-label="Landing page concepts">
      <a className="dv-landing__switcher-gallery" aria-current={active === 'gallery' ? 'page' : undefined} href="?landing=gallery">
        All
      </a>
      {LANDING_CONCEPTS.map(({ id }) => (
        <a aria-current={active === id ? 'page' : undefined} href={`?landing=${id}`} key={id}>
          {id}
        </a>
      ))}
    </nav>
  );
}

function Actions({ compact = false }: { compact?: boolean }) {
  return (
    <div className={`dv-landing__actions${compact ? ' dv-landing__actions--compact' : ''}`}>
      <a className="dv-landing__button dv-landing__button--primary" href="#proof">See how proof travels</a>
      <a className="dv-landing__button dv-landing__button--ghost" href="?landing=gallery">Compare concepts</a>
    </div>
  );
}

function Pipeline({ vertical = false }: { vertical?: boolean }) {
  return (
    <div className={`dv-landing__pipeline${vertical ? ' dv-landing__pipeline--vertical' : ''}`} aria-label="Migration pipeline">
      {PIPELINE.map((item, index) => (
        <div className="dv-landing__pipeline-step" key={item}>
          <span>{String(index + 1).padStart(2, '0')}</span>
          <strong>{item}</strong>
        </div>
      ))}
    </div>
  );
}

function Evidence({ compact = false, anchor = false }: { compact?: boolean; anchor?: boolean }) {
  return (
    <div className={`dv-landing__evidence${compact ? ' dv-landing__evidence--compact' : ''}`} id={anchor ? 'proof' : undefined}>
      {PROOF.map(([title, body], index) => (
        <article key={title}>
          <span>{String(index + 1).padStart(2, '0')}</span>
          <h3>{title}</h3>
          <p>{body}</p>
        </article>
      ))}
    </div>
  );
}

function DashboardMock({ dense = false }: { dense?: boolean }) {
  return (
    <div className={`dv-landing__dashboard${dense ? ' dv-landing__dashboard--dense' : ''}`} aria-label="Synthetic dashboard migration preview">
      <div className="dv-landing__dashboard-topbar">
        <span><i /> Sales Overview</span>
        <small>synthetic demo</small>
      </div>
      <div className="dv-landing__dashboard-grid">
        <div className="dv-landing__metric">
          <small>Revenue</small>
          <strong>$2.41M</strong>
          <span>validated</span>
        </div>
        <div className="dv-landing__metric">
          <small>Margin</small>
          <strong>12.5%</strong>
          <span>validated</span>
        </div>
        <div className="dv-landing__chart">
          <svg aria-hidden="true" viewBox="0 0 440 170">
            <path className="dv-chart-grid" d="M10 35H430M10 75H430M10 115H430M10 155H430" />
            <path className="dv-chart-area" d="M12 139 C45 130 66 116 94 121 S149 72 184 85 S241 42 277 61 S335 34 368 47 S408 22 429 30 L429 158 L12 158Z" />
            <path className="dv-chart-line" d="M12 139 C45 130 66 116 94 121 S149 72 184 85 S241 42 277 61 S335 34 368 47 S408 22 429 30" />
          </svg>
          <div><span>Jan</span><span>Mar</span><span>May</span><span>Jul</span><span>Sep</span></div>
        </div>
        <div className="dv-landing__fidelity-card">
          <div><span>Native</span><strong>preserved</strong></div>
          <div><span>Extended</span><strong>explained</strong></div>
          <div><span>Approximate</span><strong>flagged</strong></div>
        </div>
      </div>
    </div>
  );
}

function ProofStamp() {
  return (
    <div className="dv-landing__proof-stamp" aria-label="Evidence-driven migration">
      <span>VERIFIABLE</span>
      <strong>BI PORTABILITY</strong>
      <small>Source → meaning → evidence</small>
    </div>
  );
}

function RoadmapBadge() {
  return <span className="dv-landing__roadmap">Roadmap · managed SaaS + self-hosted</span>;
}

function Footer({ inverse = false }: { inverse?: boolean }) {
  return (
    <footer className="dv-landing__footer" data-inverse={inverse || undefined}>
      <Brand inverse={inverse} />
      <p>Portable analytics without pretending every translation is perfect.</p>
      <span>Concept exploration · public/synthetic content only</span>
    </footer>
  );
}

function Shell({ id, children }: { id: LandingId; children: ReactNode }) {
  const concept = LANDING_CONCEPTS.find((item) => item.id === id)!;
  return (
    <div className={`dv-landing dv-landing--${concept.slug}`} data-landing-id={id} data-landing-name={concept.name}>
      <Switcher active={id} />
      {children}
    </div>
  );
}

function Precision() {
  return (
    <Shell id={1}>
      <header className="dv-landing__nav"><Brand inverse /><span>Tableau → Power BI · public MVP</span><RoadmapBadge /></header>
      <main>
        <section className="dv-landing__hero dv-landing__hero--split">
          <div className="dv-landing__hero-copy">
            <p className="dv-landing__eyebrow">Analytics migration, with receipts.</p>
            <h1>Move dashboards.<br /><em>Keep the meaning.</em></h1>
            <p className="dv-landing__lede">DataVIZ translates BI artifacts through neutral contracts, checks the result, and makes every gap inspectable instead of hiding it behind a successful export.</p>
            <Actions />
            <div className="dv-landing__micro-proof"><span>Python migration engine</span><span>Versioned IR</span><span>React runtime</span></div>
          </div>
          <div className="dv-landing__hero-art"><DashboardMock /><ProofStamp /></div>
        </section>
        <section className="dv-landing__section dv-landing__section--dark"><Pipeline /><Evidence anchor /></section>
      </main>
      <Footer inverse />
    </Shell>
  );
}

function Editorial() {
  return (
    <Shell id={2}>
      <header className="dv-landing__nav"><Brand /><span className="dv-landing__issue">No. 02 · portability</span><a href="#proof">Read the method ↓</a></header>
      <main>
        <section className="dv-editorial__masthead">
          <div className="dv-editorial__kicker">A different way to think about BI migration</div>
          <h1>Dashboards are not screenshots.<br />They are <em>meaning in motion.</em></h1>
          <div className="dv-editorial__intro">
            <p>Most migrations are judged by whether a file opens. DataVIZ asks a harder question: did the numbers, calculations, filters and visual intent survive the trip?</p>
            <Actions compact />
          </div>
        </section>
        <section className="dv-editorial__feature">
          <div><span className="dv-editorial__index">01</span><h2>Translate the intent, not just the format.</h2><p>A Source AST preserves provenance. Neutral semantic and presentation contracts describe what the dashboard means before a destination decides how to render it.</p></div>
          <DashboardMock dense />
        </section>
        <section className="dv-editorial__proof" id="proof">
          <p className="dv-editorial__pullquote">“A successful export is not the same thing as a faithful migration.”</p>
          <Pipeline />
          <Evidence compact />
        </section>
      </main>
      <Footer />
    </Shell>
  );
}

function Command() {
  return (
    <Shell id={3}>
      <header className="dv-landing__nav"><Brand inverse /><span className="dv-command__status"><i /> engine.ready</span><span>docs / architecture / evidence</span></header>
      <main className="dv-command__main">
        <section className="dv-command__hero">
          <div className="dv-command__prompt">$ dataviz migrate dashboard.twbx --target power-bi --explain</div>
          <h1>BI migration should behave more like a <span>compiler</span> than a copy machine.</h1>
          <p>Parse source intent. Lower it into neutral contracts. Render the supported subset. Emit diagnostics for everything else.</p>
          <Actions />
        </section>
        <section className="dv-command__terminal" aria-label="DataVIZ compilation trace">
          <div className="dv-command__terminal-bar"><span /><span /><span /><strong>migration.trace</strong></div>
          <pre><code><b>01</b> parse       tableau/source.twbx{`\n`}<b>02</b> normalize   source_ast@v1{`\n`}<b>03</b> lower       semantic_ir + presentation_ir{`\n`}<b>04</b> render      power_bi/pbip{`\n`}<b>05</b> verify      structure · numbers · visuals{`\n`}<b>06</b> report      <em>native | extended | approximate</em></code></pre>
          <div className="dv-command__terminal-result"><span>✓ translation completed</span><span>≠ gaps remain visible</span></div>
        </section>
        <section className="dv-command__proof" id="proof"><Pipeline vertical /><Evidence /></section>
      </main>
      <Footer inverse />
    </Shell>
  );
}

function Prism() {
  return (
    <Shell id={4}>
      <div className="dv-prism__aurora" aria-hidden="true"><span /><span /><span /></div>
      <header className="dv-landing__nav dv-prism__nav"><Brand inverse /><div><a href="#proof">Method</a><a href="?landing=gallery">Concepts</a></div><RoadmapBadge /></header>
      <main>
        <section className="dv-prism__hero">
          <span className="dv-prism__pill">Verifiable BI portability</span>
          <h1>Your analytics deserve a<br /><span>portable future.</span></h1>
          <p>DataVIZ separates business meaning from vendor formats, then validates what survives each translation.</p>
          <Actions />
          <div className="dv-prism__stage"><DashboardMock /><div className="dv-prism__float dv-prism__float--left"><small>Contract</small><strong>Neutral IR</strong><span>versioned + reviewable</span></div><div className="dv-prism__float dv-prism__float--right"><small>Evidence</small><strong>Gaps disclosed</strong><span>no silent equivalence</span></div></div>
        </section>
        <section className="dv-prism__proof" id="proof"><h2>One migration. Multiple layers of confidence.</h2><Evidence /><Pipeline /></section>
      </main>
      <Footer inverse />
    </Shell>
  );
}

function Blueprint() {
  return (
    <Shell id={5}>
      <header className="dv-landing__nav"><Brand /><span>DATAVIZ / SYSTEM DRAWING 05</span><span>REV · 2026</span></header>
      <main className="dv-blueprint__sheet">
        <section className="dv-blueprint__titleblock">
          <div><span>PROJECT</span><strong>Verifiable BI portability</strong></div>
          <div><span>PUBLIC MVP</span><strong>Tableau → Power BI</strong></div>
          <div><span>METHOD</span><strong>Parse · translate · validate</strong></div>
        </section>
        <section className="dv-blueprint__hero">
          <div><p className="dv-landing__eyebrow">Architecture, not magic</p><h1>A migration you can<br />draw, inspect and trust.</h1><p>Every transformation passes through explicit contracts, so fidelity can be measured at the boundaries instead of inferred from appearance.</p><Actions /></div>
          <div className="dv-blueprint__diagram"><div className="dv-blueprint__node dv-blueprint__node--source"><span>A</span><strong>Source</strong><small>TWB / TWBX</small></div><div className="dv-blueprint__connector">→</div><div className="dv-blueprint__node"><span>B</span><strong>Neutral IR</strong><small>semantic + presentation</small></div><div className="dv-blueprint__connector">→</div><div className="dv-blueprint__node dv-blueprint__node--target"><span>C</span><strong>Target</strong><small>PBIP / web runtime</small></div></div>
        </section>
        <section className="dv-blueprint__proof" id="proof"><Pipeline /><Evidence /></section>
      </main>
      <Footer />
    </Shell>
  );
}

function Signal() {
  return (
    <Shell id={6}>
      <header className="dv-landing__nav"><Brand inverse /><span>Signal integrity for analytics</span><a href="#proof">Evidence layer</a></header>
      <main>
        <section className="dv-signal__hero">
          <div className="dv-signal__copy"><p className="dv-landing__eyebrow">The dashboard is the proof surface</p><h1>See what survived<br />the migration.</h1><p>DataVIZ keeps the rendered result close to its evidence: what was preserved, what was extended, and what still needs review.</p><Actions /></div>
          <div className="dv-signal__score"><span>FIDELITY</span><strong>measured</strong><small>not assumed</small></div>
        </section>
        <section className="dv-signal__dashboard"><DashboardMock /><div className="dv-signal__rail"><article><span>01</span><strong>Inspect</strong><p>Trace each output back to source intent.</p></article><article><span>02</span><strong>Compare</strong><p>Run structural and numeric checks separately.</p></article><article><span>03</span><strong>Decide</strong><p>Review explicit gaps before accepting the result.</p></article></div></section>
        <section className="dv-signal__proof" id="proof"><Evidence /><Pipeline /></section>
      </main>
      <Footer inverse />
    </Shell>
  );
}

function Swiss() {
  return (
    <Shell id={7}>
      <header className="dv-landing__nav"><Brand /><span>07 / DATAVIZ</span><span>BI PORTABILITY</span></header>
      <main className="dv-swiss__main">
        <section className="dv-swiss__hero"><div className="dv-swiss__number">07</div><h1>DON’T<br />MIGRATE<br /><span>BLIND.</span></h1><div className="dv-swiss__side"><p>Tableau → neutral contracts → Power BI.</p><p>Every unsupported equivalence becomes evidence, not a surprise.</p><Actions compact /></div></section>
        <section className="dv-swiss__ticker" aria-label="DataVIZ principles"><span>PARSE</span><b>•</b><span>TRANSLATE</span><b>•</b><span>VALIDATE</span><b>•</b><span>DISCLOSE</span><b>•</b><span>REPEAT</span></section>
        <section className="dv-swiss__grid" id="proof"><div className="dv-swiss__black"><h2>Meaning is the interface.</h2><p>Vendor files change. Business semantics should remain inspectable.</p></div><Evidence compact /><div className="dv-swiss__red"><strong>NO SILENT<br />APPROXIMATIONS.</strong></div><Pipeline vertical /></section>
      </main>
      <Footer />
    </Shell>
  );
}

function Canvas() {
  return (
    <Shell id={8}>
      <header className="dv-landing__nav"><Brand /><span>Built for the people who have to trust the numbers.</span><a href="#proof">Our method</a></header>
      <main>
        <section className="dv-canvas__hero"><div className="dv-canvas__copy"><span className="dv-canvas__scribble">portable by design</span><h1>Take your dashboard.<br />Leave the lock-in.</h1><p>DataVIZ turns BI migration into an inspectable design process: preserve the intent, translate what is safe, and make the compromises easy to see.</p><Actions /></div><div className="dv-canvas__art"><div className="dv-canvas__paper"><DashboardMock dense /></div><div className="dv-canvas__note">Meaning<br />travels first. ↗</div></div></section>
        <section className="dv-canvas__manifesto"><span>01</span><h2>A portable dashboard is more than a portable picture.</h2><p>Calculations, filters, interactions and provenance all belong to the experience. That is why DataVIZ models them explicitly before rendering a destination.</p></section>
        <section className="dv-canvas__proof" id="proof"><Evidence /><Pipeline /></section>
      </main>
      <Footer />
    </Shell>
  );
}

function Enterprise() {
  return (
    <Shell id={9}>
      <header className="dv-enterprise__nav"><Brand /><nav aria-label="Enterprise landing sections"><a href="#method">Method</a><a href="#proof">Validation</a><a href="#roadmap">Roadmap</a></nav><a className="dv-enterprise__cta" href="#proof">Review the evidence</a></header>
      <main>
        <section className="dv-enterprise__hero"><div><span className="dv-enterprise__badge">Tableau → Power BI · public MVP</span><h1>Modernize BI without losing control of the translation.</h1><p>DataVIZ creates a reviewable chain from source artifact to target output, with independent evidence for structure, numerical behavior and visual intent.</p><Actions /></div><DashboardMock /></section>
        <section className="dv-enterprise__logos" aria-label="Architecture capabilities"><span>Source provenance</span><span>Neutral contracts</span><span>Deterministic checks</span><span>Gap reporting</span></section>
        <section className="dv-enterprise__method" id="method"><div><span>HOW IT WORKS</span><h2>A migration process your team can reason about.</h2><p>DataVIZ does not require a leap of faith between import and export. Each boundary is explicit and reviewable.</p></div><Pipeline vertical /></section>
        <section className="dv-enterprise__proof" id="proof"><div className="dv-enterprise__proof-copy"><span>VALIDATION</span><h2>Separate the verdicts.</h2><p>An output that opens can still be numerically or visually wrong. DataVIZ keeps those dimensions independent.</p></div><Evidence compact /></section>
        <section className="dv-enterprise__roadmap" id="roadmap"><div><small>TODAY</small><strong>Verifiable migration MVP</strong><p>Tableau artifacts to Power BI with structured evidence.</p></div><div><small>PRODUCT DIRECTION</small><strong>Portable DataVIZ runtime</strong><p>Neutral contracts powering managed and self-hosted experiences.</p></div></section>
      </main>
      <Footer />
    </Shell>
  );
}

function Orbit() {
  return (
    <Shell id={10}>
      <header className="dv-landing__nav"><Brand inverse /><span>Portable analytics system</span><RoadmapBadge /></header>
      <main>
        <section className="dv-orbit__hero"><div className="dv-orbit__copy"><p className="dv-landing__eyebrow">Decouple meaning from the tool that drew it</p><h1>One semantic center.<br />Many possible destinations.</h1><p>DataVIZ treats vendor formats as inputs and outputs around a neutral, inspectable core.</p><Actions /></div><div className="dv-orbit__system" aria-label="DataVIZ neutral contract orbit"><div className="dv-orbit__ring dv-orbit__ring--one" /><div className="dv-orbit__ring dv-orbit__ring--two" /><div className="dv-orbit__core"><small>DATAVIZ</small><strong>Neutral IR</strong><span>meaning</span></div><div className="dv-orbit__planet dv-orbit__planet--a"><span>A</span><strong>Tableau</strong></div><div className="dv-orbit__planet dv-orbit__planet--b"><span>B</span><strong>Power BI</strong></div><div className="dv-orbit__planet dv-orbit__planet--c"><span>C</span><strong>Web runtime</strong></div><div className="dv-orbit__planet dv-orbit__planet--d"><span>✓</span><strong>Evidence</strong></div></div></section>
        <section className="dv-orbit__statement"><p>Portability is not “export everywhere.”</p><h2>It is preserving the intent well enough to know what changed.</h2></section>
        <section className="dv-orbit__proof" id="proof"><Pipeline /><Evidence /></section>
      </main>
      <Footer inverse />
    </Shell>
  );
}

const CONCEPT_COMPONENTS: Record<LandingId, () => ReactNode> = {
  1: Precision,
  10: Orbit,
  2: Editorial,
  3: Command,
  4: Prism,
  5: Blueprint,
  6: Signal,
  7: Swiss,
  8: Canvas,
  9: Enterprise,
};

export function LandingGallery() {
  return (
    <div className="dv-landing dv-landing--gallery" data-landing-name="Gallery">
      <Switcher active="gallery" />
      <header className="dv-gallery__header"><Brand /><span>10 art directions · one product thesis</span></header>
      <main>
        <section className="dv-gallery__intro"><p className="dv-landing__eyebrow">DataVIZ landing exploration</p><h1>Choose the story<br />before choosing the skin.</h1><p>Every concept is built from the same product truth: verifiable BI portability. The hierarchy, visual language and audience emphasis change; the claims do not.</p></section>
        <section className="dv-gallery__grid" aria-label="Landing concept gallery">
          {LANDING_CONCEPTS.map((concept) => (
            <a className={`dv-gallery__card dv-gallery__card--${concept.slug}`} href={`?landing=${concept.id}`} key={concept.id}>
              <div className="dv-gallery__preview" aria-hidden="true"><span className="dv-gallery__preview-brand">DataVIZ</span><span className="dv-gallery__preview-kicker">{String(concept.id).padStart(2, '0')}</span><strong>{concept.name}</strong><div><i /><i /><i /></div></div>
              <div className="dv-gallery__meta"><span>{String(concept.id).padStart(2, '0')}</span><div><h2>{concept.name}</h2><p>{concept.note}</p></div><b>↗</b></div>
            </a>
          ))}
        </section>
      </main>
      <Footer />
    </div>
  );
}

export function LandingPage({ selection }: { selection: ConceptSelection }) {
  if (selection === 'gallery') {return <LandingGallery />;}
  const Concept = CONCEPT_COMPONENTS[selection];
  return <Concept />;
}

export default LandingPage;
