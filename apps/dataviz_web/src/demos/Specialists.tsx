import { useEffect, useMemo, useRef, useState } from "react";
import { allowedSpecialists, type DemoProfile, type SpecialistAnswer, type SpecialistId } from "./catalog";
import { BRANCHES, MONTHS } from "./clinicData";
import { DemoNav } from "./DemoNav";
import { useDemoLang, DEMO_TEXTS } from "./demoLang";
import "./demos.css";
import "./specialists.css";

function Icon({ name }: { name: "evidence" | "filters" | "send" | "chevron" | "close" }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    {name === "evidence" ? <><path d="M5 3h10l4 4v14H5zM15 3v5h4M8 12h8M8 16h6" /></>
      : name === "filters" ? <><path d="M4 6h16M4 12h16M4 18h16" /><path d="M8 4v4M16 10v4M10 16v4" /></>
      : name === "send" ? <path d="M12 19V5m-5 5 5-5 5 5" />
      : name === "close" ? <path d="m6 6 12 12M18 6 6 18" /> : <path d="m8 10 4 4 4-4" />}
  </svg>;
}

interface Message { id: string; role: "user" | "assistant"; text: string; answer?: SpecialistAnswer }
const formatValue = (value: number, unit: string) => `${new Intl.NumberFormat("es-CL", {
  maximumFractionDigits: unit === "CLP" || unit === "count" ? 0 : 1,
}).format(value)}${unit === "count" ? "" : ` ${unit}`}`;

function validAnswer(value: unknown): value is SpecialistAnswer {
  if (!value || typeof value !== "object") {return false;}
  const answer = value as Partial<SpecialistAnswer>;
  return typeof answer.message === "string" && typeof answer.access_scope === "string"
    && ["finance", "revenue", "emergency"].includes(String(answer.agent_id))
    && ["direction", "operations"].includes(String(answer.profile))
    && ["answered", "out_of_scope"].includes(String(answer.status))
    && ["local", "glm", "local_fallback"].includes(String(answer.mode))
    && Array.isArray(answer.evidence) && answer.evidence.every((metric) => metric && typeof metric.id === "string"
      && typeof metric.label === "string" && typeof metric.formula === "string" && typeof metric.value === "number"
      && Number.isFinite(metric.value) && ["count", "CLP", "%", "min"].includes(metric.unit))
    && Array.isArray(answer.series) && answer.series.every((point) => point && typeof point.label === "string" && Number.isFinite(point.value))
    && typeof answer.series_label === "string" && !!answer.source && typeof answer.source.name === "string"
    && typeof answer.source.period === "string" && typeof answer.source.version === "string"
    && typeof answer.source.branch === "string" && typeof answer.source.month === "string"
    && typeof answer.source.grain === "string" && Number.isSafeInteger(answer.source.rows);
}

interface SourceGroup {
  engine: "Redshift" | "PostgreSQL";
  icon: string;
  name: string;
  host: string;
  database: string;
  tables: string[];
  metrics: string[];
}

function getSourcesForAgent(agentId: SpecialistId): SourceGroup[] {
  if (agentId === "finance") {
    return [
      {
        engine: "PostgreSQL",
        icon: "🐘",
        name: "ERP Transaccional y Recaudación (PSQL)",
        host: "prod-erp-psql.internal:5432",
        database: "clinica_erp",
        tables: ["finanzas.recaudacion_caja_diaria", "finanzas.facturacion_prestaciones", "finanzas.cuentas_por_cobrar"],
        metrics: ["Cobrado", "Facturado", "Pendiente de cobro"],
      },
      {
        engine: "Redshift",
        icon: "🔴",
        name: "Data Warehouse Clínico (AWS Redshift)",
        host: "redshift-dw-cluster.internal:5439",
        database: "clinica_dw",
        tables: ["dw_clinica.fact_atenciones_diarias", "dw_clinica.dim_sedes"],
        metrics: ["Conciliación de bonos y liquidación por sede"],
      },
    ];
  }
  if (agentId === "emergency") {
    return [
      {
        engine: "PostgreSQL",
        icon: "🚑",
        name: "Sistema de Triage y Admisiones Urgencias (PSQL)",
        host: "prod-urgencias-psql.internal:5432",
        database: "urgencias_db",
        tables: ["triage.admisiones_urgencia", "triage.tiempos_atencion_manchester"],
        metrics: ["Consultas de urgencias", "Espera media", "Ingresos hospitalarios"],
      },
      {
        engine: "Redshift",
        icon: "🔴",
        name: "Data Warehouse de Ocupación Hospitalaria (AWS Redshift)",
        host: "redshift-dw-cluster.internal:5439",
        database: "clinica_dw",
        tables: ["dw_clinica.dim_sedes", "dw_clinica.fact_ocupacion_camas"],
        metrics: ["Disponibilidad de camas y traslados por prioridad"],
      },
    ];
  }
  // revenue / operaciones
  return [
    {
      engine: "Redshift",
      icon: "🔴",
      name: "Data Warehouse Clínico (AWS Redshift)",
      host: "redshift-dw-cluster.internal:5439",
      database: "clinica_dw",
      tables: ["dw_clinica.fact_atenciones_ambulatorias", "dw_clinica.dim_especialidades", "dw_clinica.dim_sedes"],
      metrics: ["Atenciones", "Citas agendadas", "Cancelaciones", "Tasa de cancelación", "Utilización de capacidad"],
    },
    {
      engine: "PostgreSQL",
      icon: "🐘",
      name: "Agenda Médica y Turnos (PSQL)",
      host: "prod-agenda-psql.internal:5432",
      database: "agenda_medica",
      tables: ["agenda.turnos_medicos", "agenda.bloqueos_horarios"],
      metrics: ["Capacidad horaria disponible por sede"],
    },
  ];
}

function Evidence({ answer }: { answer: SpecialistAnswer }) {
  const [lang] = useDemoLang();
  const t = DEMO_TEXTS[lang];
  const sources = getSourcesForAgent(answer.agent_id);
  return <>
    <div className="specialist-dashboard-source">
      <span className="specialist-source-label">{t.dashboard_source_label}</span>
      <a className="specialist-dashboard-link" href="/dashboard" target="_blank" rel="noreferrer">
        {t.dashboard_link_text}
      </a>
    </div>

    <div className="specialist-source-groups">
      <h3>{t.db_sources_grouped}</h3>
      {sources.map((src) => (
        <div key={src.name} className="specialist-source-card">
          <div className="specialist-source-card-header">
            <span className="specialist-source-card-icon">{src.icon}</span>
            <div>
              <strong>{src.name}</strong>
              <small>{src.host} · db: <code>{src.database}</code></small>
            </div>
          </div>
          <div className="specialist-source-card-tables">
            <span className="specialist-card-sublabel">{t.tables_consulted}</span>
            <ul>
              {src.tables.map((tbl) => <li key={tbl}><code>{tbl}</code></li>)}
            </ul>
          </div>
          <div className="specialist-source-card-metrics">
            <span className="specialist-card-sublabel">{t.metrics_label}</span> {src.metrics.join(" · ")}
          </div>
        </div>
      ))}
    </div>

    <div className="specialist-evidence-metrics-box">
      <h3>{t.calculated_metrics}</h3>
      <dl className="specialist-metrics">{answer.evidence.map((metric) => <div key={metric.id}>
        <dt>{metric.label}</dt><dd>{formatValue(metric.value, metric.unit)}</dd>
        <details><summary>{t.how_calculated}</summary><code>{metric.formula}</code></details>
      </div>)}</dl>
    </div>

    <section className="specialist-provenance">
      <h3>{t.audit_heading}</h3>
      <dl>
        <dt>{t.version}</dt><dd>{answer.source.version}</dd>
        <dt>{t.period}</dt><dd>{answer.source.period}</dd>
        <dt>{t.filters_btn}</dt><dd>{answer.source.branch} · {answer.source.month}</dd>
        <dt>{lang === 'en' ? 'Records queried' : 'Registros consultados'}</dt><dd>{answer.source.rows.toLocaleString(lang === 'en' ? 'en-US' : 'es-CL')}</dd>
        <dt>{lang === 'en' ? 'Granularity' : 'Granularidad'}</dt><dd>{answer.source.grain}</dd>
      </dl>
      <p>{answer.access_scope}</p>
    </section>
  </>;
}

/** Conversaciones separadas por agente, perfil y filtros; el servidor aplica el alcance antes de consultar. */
export default function Specialists() {
  const [lang] = useDemoLang();
  const t = DEMO_TEXTS[lang];
  const [profile, setProfile] = useState<DemoProfile>("direction");
  const [selected, setSelected] = useState<SpecialistId>("finance");
  const [search, setSearch] = useState("");
  const [branch, setBranch] = useState("");
  const [month, setMonth] = useState("");
  const [prompt, setPrompt] = useState("");
  const [threads, setThreads] = useState<Record<string, Message[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showEvidence, setShowEvidence] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.innerWidth <= 950);
  const [evidenceMessage, setEvidenceMessage] = useState<string | null>(null);
  const requestRef = useRef<AbortController | null>(null);
  const feed = useRef<HTMLDivElement | null>(null);
  const evidenceDialog = useRef<HTMLDialogElement | null>(null);
  const evidenceReturnFocus = useRef<HTMLElement | null>(null);
  const agents = allowedSpecialists(profile);
  const agent = agents.find((item) => item.id === selected) ?? agents[0];
  const threadKey = `${profile}/${agent.id}/${branch}/${month}`;
  const messages = threads[threadKey] ?? [];
  const evidence = messages.find((message) => message.id === evidenceMessage)?.answer
    ?? [...messages].reverse().find((message) => message.answer)?.answer;

  const visibleAgents = useMemo(() => agents.filter((item) => {
    const nameStr = (item.names?.[lang] ?? item.name).toLowerCase();
    const q = search.toLowerCase();
    return nameStr.includes(q);
  }), [agents, lang, search]);

  useEffect(() => {
    const handleResize = () => setNarrow(window.innerWidth <= 950);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    feed.current?.scrollTo({ top: feed.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const openEvidence = (messageId?: string) => {
    evidenceReturnFocus.current = document.activeElement as HTMLElement | null;
    if (messageId) {setEvidenceMessage(messageId);}
    setShowEvidence(true);
    if (narrow) {evidenceDialog.current?.showModal();}
  };

  const closeEvidence = () => {
    setShowEvidence(false);
    setEvidenceMessage(null);
    if (narrow) {evidenceDialog.current?.close();}
    evidenceReturnFocus.current?.focus();
  };

  async function send(text?: string) {
    const message = (text ?? prompt).trim();
    if (!message || message.length > 2000 || busy) {return;}
    const controller = new AbortController();
    requestRef.current = controller;
    let timedOut = false;
    const timer = setTimeout(() => {timedOut = true; controller.abort();}, 60000);
    setError(""); setBusy(true); setPrompt("");
    setThreads((current) => ({ ...current, [threadKey]: [...(current[threadKey] ?? []), { id: crypto.randomUUID(), role: "user", text: message }].slice(-20) as Message[] }));
    try {
      const response = await fetch("/api/agents/chat", { method: "POST", signal: controller.signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile, agent_id: agent.id, prompt: message, branch, month }) });
      const body: unknown = await response.json();
      if (!response.ok) {throw new Error(body && typeof body === "object" && "error" in body && typeof body.error === "string" ? body.error : "No se pudo completar la consulta.");}
      if (!validAnswer(body) || body.agent_id !== agent.id || body.profile !== profile) {throw new Error("La respuesta no corresponde al agente seleccionado.");}
      if (controller.signal.aborted) {return;}
      const id = crypto.randomUUID();
      setThreads((current) => ({ ...current, [threadKey]: [...(current[threadKey] ?? []), { id, role: "assistant", text: body.message, answer: body }].slice(-20) as Message[] }));
      setEvidenceMessage(id);
    } catch (failure) {
      if (timedOut) {setError(lang === 'en' ? 'The query timed out.' : 'La consulta superó el tiempo disponible.');}
      else if (!controller.signal.aborted) {setError(failure instanceof Error ? failure.message : (lang === 'en' ? 'Could not reach the agent.' : 'No se pudo consultar el agente.'));}
    } finally {
      clearTimeout(timer);
      if (requestRef.current === controller) {requestRef.current = null; setBusy(false);}
    }
  }

  const evidencePanel = <>
    <header><h2>{t.evidence_title}</h2><button type="button" aria-label={t.close_evidence} onClick={closeEvidence}><Icon name="close" /></button></header>
    {evidence && <Evidence answer={evidence} />}
  </>;

  const agentDisplayName = agent.names?.[lang] ?? agent.name;
  const agentDescription = agent.descriptions?.[lang] ?? agent.description;
  const agentPrompts = agent.localized_prompts?.[lang] ?? agent.prompts;

  return <div className="specialists-app">
    <DemoNav current="agents" />
    <div className="specialists-workspace">
      <aside className="specialists-directory" aria-label={t.specialists_title}>
        <h1 className="demo-sr-only">{t.specialists_title}</h1>
        <label className="demo-sr-only" htmlFor="agent-search">{t.search_placeholder}</label>
        <input id="agent-search" type="search" placeholder={t.search_placeholder} value={search} onChange={(event) => setSearch(event.target.value)} />
        <div className="specialists-list">{visibleAgents.map((item) => {
          const itemDisplayName = item.names?.[lang] ?? item.name;
          const itemDesc = item.descriptions?.[lang] ?? item.description;
          return (
            <button type="button" key={item.id} aria-pressed={item.id === agent.id} onClick={() => setSelected(item.id)}>
              <span className="specialist-avatar" style={{ backgroundColor: item.color }}>{item.initials}</span>
              <span><strong>{itemDisplayName}</strong><small>{[...(threads[`${profile}/${item.id}/${branch}/${month}`] ?? [])].reverse().find((message) => message.role === "assistant")?.text ?? itemDesc}</small></span>
            </button>
          );
        })}</div>
        {!visibleAgents.length && <p className="specialist-empty-search">{t.empty_search}</p>}
        <details className="specialist-account"><summary><span className="specialist-avatar">CX</span><span>{t.clinic_title}<small>{t.simulated_account}</small></span><Icon name="chevron" /></summary>
          <div><label className="specialist-profile">{t.demo_profile}<select value={profile} onChange={(event) => setProfile(event.target.value as DemoProfile)}>
            <option value="direction">{t.profile_direction}</option><option value="operations">{t.profile_operations}</option>
          </select></label><p>{t.profile_note}</p></div>
        </details>
      </aside>

      <main className="specialist-conversation">
        <header className="specialist-thread-heading"><span className="specialist-avatar" style={{ backgroundColor: agent.color }}>{agent.initials}</span>
          <div><h2>{agentDisplayName}</h2></div>
          <div className="specialist-thread-tools">
            {(branch || month) && <span className="specialist-active-filters">{[branch, month].filter(Boolean).join(" · ")}</span>}
            <button type="button" aria-label={t.filters_btn} title={t.filters_btn} aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}><Icon name="filters" /></button>
            <button type="button" aria-label={t.sources_btn} title={t.sources_btn} aria-expanded={showEvidence} disabled={!evidence} onClick={() => showEvidence ? closeEvidence() : openEvidence()}><Icon name="evidence" /></button>
          </div>
        </header>
        {showFilters && <div className="specialist-scope-filters">
          <label>{t.branch_label}<select value={branch} onChange={(event) => setBranch(event.target.value)}><option value="">{t.all_branches}</option>{BRANCHES.map((name) => <option key={name}>{name}</option>)}</select></label>
          <label>{t.period_label}<select value={month} onChange={(event) => setMonth(event.target.value)}><option value="">{t.all_period}</option>{MONTHS.map((name, index) => <option key={name} value={name}>{t.months[index]}</option>)}</select></label>
        </div>}
        <div className="specialist-messages" ref={feed} role="log" aria-label={`Conversación con ${agentDisplayName}`} aria-live="polite">
          {!messages.length && (
            <div className="specialist-welcome">
              <div className="specialist-welcome-badge">
                <span className="specialist-welcome-dot" style={{ backgroundColor: agent.color }} />
                <span>{agentDisplayName}</span>
              </div>
              <h3>{t.knowledge_heading}</h3>
              <p className="specialist-welcome-desc">{agent.knowledge_summary?.[lang] ?? agentDescription}</p>

              <div className="specialist-knowledge-box">
                <div className="specialist-knowledge-grid">
                  {(agent.sources_summary ?? getSourcesForAgent(agent.id)).map((src) => (
                    <div key={src.name} className="specialist-source-chip">
                      <div className="specialist-source-chip-head">
                        <span>{src.icon}</span>
                        <strong>{src.name}</strong>
                        <code>{src.database}</code>
                      </div>
                      <div className="specialist-source-chip-row">
                        <span>{t.tables_consulted}</span>
                        <code>{src.tables.join(', ')}</code>
                      </div>
                      <div className="specialist-source-chip-row specialist-source-chip-metrics">
                        <span>{t.metrics_label}</span>
                        <em>{src.metrics.join(' · ')}</em>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="specialist-prompts">{agentPrompts.map((item) => (
                <button type="button" key={item} onClick={() => void send(item)} disabled={busy}>{item}</button>
              ))}</div>
            </div>
          )}
          {messages.map((message) => <article key={message.id} className={`specialist-message specialist-message-${message.role}`}>
            <span className="demo-sr-only">{message.role === "user" ? t.you : agentDisplayName}</span>
            <p className="specialist-message-text">{message.text}</p>

            {message.answer && message.answer.evidence.length > 0 && (
              <div className="specialist-chat-visualizer">
                <div className="specialist-kpi-chips">
                  {message.answer.evidence.slice(0, 3).map((metric) => (
                    <div className="specialist-kpi-chip" key={metric.id}>
                      <span className="specialist-kpi-label">{metric.label}</span>
                      <strong className="specialist-kpi-value">{formatValue(metric.value, metric.unit)}</strong>
                    </div>
                  ))}
                </div>

                {message.answer.series.length > 0 && (
                  <div className="specialist-chat-chart">
                    <div className="specialist-chat-chart-title">
                      <span>📊 {message.answer.series_label}</span>
                      <small>{t.chat_visualizer}</small>
                    </div>
                    <div className="specialist-chat-bars">
                      {message.answer.series.map((point) => {
                        const maxVal = Math.max(1, ...message.answer!.series.map((p) => p.value));
                        const pct = Math.max(6, Math.round((point.value / maxVal) * 100));
                        return (
                          <div className="specialist-chat-bar-item" key={point.label}>
                            <span className="specialist-chat-bar-name" title={point.label}>{point.label}</span>
                            <div className="specialist-chat-bar-track">
                              <div
                                className="specialist-chat-bar-fill"
                                style={{ width: `${pct}%`, backgroundColor: agent.color }}
                              />
                            </div>
                            <span className="specialist-chat-bar-amount">
                              {formatValue(point.value, message.answer!.evidence[0]?.unit ?? "count")}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            )}

            {message.answer && <div className="specialist-answer-meta">
              <span>{message.answer.mode === "glm" ? "GLM" : message.answer.mode === "local_fallback" ? "GLM (fallback)" : (lang === 'en' ? 'Verified computation' : 'Cálculo verificado')}</span>
              <button type="button" onClick={() => openEvidence(message.id)}>{lang === 'en' ? 'View sources (Redshift / PSQL)' : 'Ver fuentes (Redshift / PSQL)'}</button>
            </div>}
          </article>)}
          {busy && <p className="specialist-query-status" role="status">{lang === 'en' ? 'Querying authorized evidence...' : 'Consultando la evidencia autorizada.'}</p>}
        </div>
        {error && <p className="specialist-error" role="alert">{error} {lang === 'en' ? 'You can retry the question.' : 'Puedes volver a enviar la pregunta.'}</p>}
        <form className="specialist-composer" onSubmit={(event) => {event.preventDefault(); void send();}}>
          <label className="demo-sr-only" htmlFor="specialist-prompt">{lang === 'en' ? `Message for ${agentDisplayName}` : `Mensaje para ${agentDisplayName}`}</label>
          <textarea id="specialist-prompt" placeholder={t.input_placeholder} maxLength={2000} rows={1} value={prompt}
            onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {event.preventDefault(); void send();}
            }} />
          {busy ? <button type="button" onClick={() => {requestRef.current?.abort(); requestRef.current = null; setBusy(false);}}>{lang === 'en' ? 'Cancel' : 'Cancelar'}</button>
            : <button type="submit" aria-label={t.send} disabled={!prompt.trim()}><Icon name="send" /></button>}
        </form>
      </main>

      {showEvidence && (narrow
        ? <dialog className="specialist-evidence" ref={evidenceDialog} aria-label={t.evidence_title}
          onCancel={(event) => {event.preventDefault(); closeEvidence();}}>{evidencePanel}</dialog>
        : <aside className="specialist-evidence" aria-label={t.evidence_title}>{evidencePanel}</aside>)}
    </div>
  </div>;
}
