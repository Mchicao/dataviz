import { useEffect, useMemo, useRef, useState } from "react";
import { allowedSpecialists, type DemoProfile, type SpecialistAnswer, type SpecialistId } from "./catalog";
import { BRANCHES, MONTHS } from "./clinicData";
import { DemoNav } from "./DemoNav";
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
  const sources = getSourcesForAgent(answer.agent_id);
  return <>
    <div className="specialist-dashboard-source">
      <span className="specialist-source-label">Dashboard de origen:</span>
      <a className="specialist-dashboard-link" href="/dashboard" target="_blank" rel="noreferrer">
        📊 Clínica X — Operación y Finanzas ↗
      </a>
    </div>

    <div className="specialist-source-groups">
      <h3>Fuentes agrupadas por base de datos</h3>
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
            <span className="specialist-card-sublabel">Tablas consultadas:</span>
            <ul>
              {src.tables.map((t) => <li key={t}><code>{t}</code></li>)}
            </ul>
          </div>
          <div className="specialist-source-card-metrics">
            <span className="specialist-card-sublabel">Métricas:</span> {src.metrics.join(" · ")}
          </div>
        </div>
      ))}
    </div>

    <div className="specialist-evidence-metrics-box">
      <h3>Métricas calculadas</h3>
      <dl className="specialist-metrics">{answer.evidence.map((metric) => <div key={metric.id}>
        <dt>{metric.label}</dt><dd>{formatValue(metric.value, metric.unit)}</dd>
        <details><summary>Cómo se calculó</summary><code>{metric.formula}</code></details>
      </div>)}</dl>
    </div>

    <section className="specialist-provenance">
      <h3>Auditoría y alcance</h3>
      <dl>
        <dt>Versión</dt><dd>{answer.source.version}</dd>
        <dt>Período</dt><dd>{answer.source.period}</dd>
        <dt>Filtros</dt><dd>{answer.source.branch} · {answer.source.month}</dd>
        <dt>Registros consultados</dt><dd>{answer.source.rows.toLocaleString("es-CL")}</dd>
        <dt>Granularidad</dt><dd>{answer.source.grain}</dd>
      </dl>
      <p>{answer.access_scope}</p>
    </section>
  </>;
}

/** Conversaciones separadas por agente, perfil y filtros; el servidor aplica el alcance antes de consultar. */
export default function Specialists() {
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
  const visibleAgents = useMemo(() => agents.filter((item) => item.name.toLocaleLowerCase("es").includes(search.toLocaleLowerCase("es"))), [profile, search]);

  useEffect(() => {
    requestRef.current?.abort(); requestRef.current = null;
    setBusy(false); setPrompt(""); setError(""); setEvidenceMessage(null); setShowEvidence(false);
  }, [threadKey]);
  useEffect(() => () => requestRef.current?.abort(), []);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth <= 950);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => {
    if (narrow && showEvidence && evidenceDialog.current && !evidenceDialog.current.open) {
      evidenceDialog.current.showModal();
    }
  }, [narrow, showEvidence]);
  useEffect(() => { if (feed.current) {feed.current.scrollTop = feed.current.scrollHeight;} }, [messages.length, busy, threadKey]);

  function openEvidence(messageId?: string) {
    evidenceReturnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (messageId) {setEvidenceMessage(messageId);}
    setShowEvidence(true);
  }
  function closeEvidence() {
    evidenceDialog.current?.close();
    setShowEvidence(false);
    evidenceReturnFocus.current?.focus();
  }

  async function send(text = prompt) {
    const message = text.trim();
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
      if (timedOut) {setError("La consulta superó el tiempo disponible.");}
      else if (!controller.signal.aborted) {setError(failure instanceof Error ? failure.message : "No se pudo consultar el agente.");}
    } finally {
      clearTimeout(timer);
      if (requestRef.current === controller) {requestRef.current = null; setBusy(false);}
    }
  }

  const evidencePanel = <>
    <header><h2>Fuentes y Linaje</h2><button type="button" aria-label="Cerrar evidencia" onClick={closeEvidence}><Icon name="close" /></button></header>
    {evidence && <Evidence answer={evidence} />}
  </>;

  return <div className="specialists-app">
    <DemoNav current="agents" />
    <div className="specialists-workspace">
      <aside className="specialists-directory" aria-label="Especialistas">
        <h1 className="demo-sr-only">Agentes de Clínica X</h1>
        <label className="demo-sr-only" htmlFor="agent-search">Buscar agente</label><input id="agent-search" type="search" placeholder="Buscar agente" value={search} onChange={(event) => setSearch(event.target.value)} />
        <div className="specialists-list">{visibleAgents.map((item) => <button type="button" key={item.id} aria-pressed={item.id === agent.id} onClick={() => setSelected(item.id)}>
          <span className="specialist-avatar" style={{ backgroundColor: item.color }}>{item.initials}</span>
          <span><strong>{item.name}</strong><small>{[...(threads[`${profile}/${item.id}/${branch}/${month}`] ?? [])].reverse().find((message) => message.role === "assistant")?.text ?? item.description}</small></span>
        </button>)}</div>
        {!visibleAgents.length && <p className="specialist-empty-search">No hay agentes con ese nombre.</p>}
        <details className="specialist-account"><summary><span className="specialist-avatar">CX</span><span>Clínica X<small>Datos ficticios</small></span><Icon name="chevron" /></summary>
          <div><label className="specialist-profile">Perfil de demostración<select value={profile} onChange={(event) => setProfile(event.target.value as DemoProfile)}>
            <option value="direction">Dirección</option><option value="operations">Operaciones</option>
          </select></label><p>Permisos simulados. Sin autenticación ni fuentes reales.</p></div>
        </details>
      </aside>

      <main className="specialist-conversation">
        <header className="specialist-thread-heading"><span className="specialist-avatar" style={{ backgroundColor: agent.color }}>{agent.initials}</span>
          <div><h2>{agent.name}</h2></div>
          <div className="specialist-thread-tools">
            {(branch || month) && <span className="specialist-active-filters">{[branch, month].filter(Boolean).join(" · ")}</span>}
            <button type="button" aria-label="Filtros" title="Filtros" aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}><Icon name="filters" /></button>
            <button type="button" aria-label="Evidencia" title="Ver fuentes" aria-expanded={showEvidence} disabled={!evidence} onClick={() => showEvidence ? closeEvidence() : openEvidence()}><Icon name="evidence" /></button>
          </div>
        </header>
        {showFilters && <div className="specialist-scope-filters">
          <label>Sede<select value={branch} onChange={(event) => setBranch(event.target.value)}><option value="">Todas</option>{BRANCHES.map((name) => <option key={name}>{name}</option>)}</select></label>
          <label>Período<select value={month} onChange={(event) => setMonth(event.target.value)}><option value="">Enero-marzo 2026</option>{MONTHS.map((name, index) => <option key={name} value={name}>{["Enero", "Febrero", "Marzo"][index]}</option>)}</select></label>
        </div>}
        <div className="specialist-messages" ref={feed} role="log" aria-label={`Conversación con ${agent.name}`} aria-live="polite">
          {!messages.length && <div className="specialist-welcome"><h3>Pregunta a {agent.name.toLowerCase()}</h3>
            <div className="specialist-prompts">{agent.prompts.map((item) => <button type="button" key={item} onClick={() => void send(item)} disabled={busy}>{item}</button>)}</div>
          </div>}
          {messages.map((message) => <article key={message.id} className={`specialist-message specialist-message-${message.role}`}>
            <span className="demo-sr-only">{message.role === "user" ? "Tú" : agent.name}</span>
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
                      <small>Visualizador interactivo</small>
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
              <span>{message.answer.mode === "glm" ? "GLM" : message.answer.mode === "local_fallback" ? "GLM (fallback)" : "Cálculo verificado"}</span>
              <button type="button" onClick={() => openEvidence(message.id)}>Ver fuentes (Redshift / PSQL)</button>
            </div>}
          </article>)}
          {busy && <p className="specialist-query-status" role="status">Consultando la evidencia autorizada.</p>}
        </div>
        {error && <p className="specialist-error" role="alert">{error} Puedes volver a enviar la pregunta.</p>}
        <form className="specialist-composer" onSubmit={(event) => {event.preventDefault(); void send();}}>
          <label className="demo-sr-only" htmlFor="specialist-prompt">Mensaje para {agent.name}</label>
          <textarea id="specialist-prompt" placeholder={`Mensaje a ${agent.name.toLowerCase()}.`} maxLength={2000} rows={1} value={prompt}
            onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {event.preventDefault(); void send();}
            }} />
          {busy ? <button type="button" onClick={() => {requestRef.current?.abort(); requestRef.current = null; setBusy(false);}}>Cancelar</button>
            : <button type="submit" aria-label="Enviar pregunta" disabled={!prompt.trim()}><Icon name="send" /></button>}
        </form>
      </main>

      {showEvidence && (narrow
        ? <dialog className="specialist-evidence" ref={evidenceDialog} aria-label="Evidencia y permisos"
          onCancel={(event) => {event.preventDefault(); closeEvidence();}}>{evidencePanel}</dialog>
        : <aside className="specialist-evidence" aria-label="Evidencia y permisos">{evidencePanel}</aside>)}
    </div>
  </div>;
}
