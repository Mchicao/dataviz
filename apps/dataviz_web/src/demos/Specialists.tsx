import { useEffect, useMemo, useRef, useState } from 'react';
import { allowedSpecialists, type DemoProfile, type SpecialistAnswer, type SpecialistId } from './catalog';
import { BRANCHES, MONTHS } from './clinicData';
import { DemoNav } from './DemoNav';
import './demos.css';

interface Message { id: string; role: 'user' | 'assistant'; text: string; answer?: SpecialistAnswer }
const formatValue = (value: number, unit: string) => `${new Intl.NumberFormat('es-CL', {
  maximumFractionDigits: unit === 'CLP' || unit === 'count' ? 0 : 1,
}).format(value)}${unit === 'count' ? '' : ` ${unit}`}`;

function validAnswer(value: unknown): value is SpecialistAnswer {
  if (!value || typeof value !== 'object') {return false;}
  const answer = value as Partial<SpecialistAnswer>;
  return typeof answer.message === 'string' && typeof answer.access_scope === 'string'
    && ['finance', 'revenue', 'emergency'].includes(String(answer.agent_id))
    && ['direction', 'operations'].includes(String(answer.profile))
    && ['answered', 'out_of_scope'].includes(String(answer.status))
    && ['local', 'glm', 'local_fallback'].includes(String(answer.mode))
    && Array.isArray(answer.evidence) && answer.evidence.every((metric) => metric && typeof metric.id === 'string'
      && typeof metric.label === 'string' && typeof metric.formula === 'string' && typeof metric.value === 'number'
      && Number.isFinite(metric.value) && ['count', 'CLP', '%', 'min'].includes(metric.unit))
    && Array.isArray(answer.series) && answer.series.every((point) => point && typeof point.label === 'string' && Number.isFinite(point.value))
    && typeof answer.series_label === 'string' && !!answer.source && typeof answer.source.name === 'string'
    && typeof answer.source.period === 'string' && typeof answer.source.version === 'string'
    && typeof answer.source.branch === 'string' && typeof answer.source.month === 'string'
    && typeof answer.source.grain === 'string' && Number.isSafeInteger(answer.source.rows);
}

function Evidence({ answer }: { answer: SpecialistAnswer }) {
  const max = Math.max(1, ...answer.series.map((point) => point.value));
  return <>
    <dl className="specialist-metrics">{answer.evidence.map((metric) => <div key={metric.id}>
      <dt>{metric.label}</dt><dd>{formatValue(metric.value, metric.unit)}</dd>
      <details><summary>Cómo se calculó</summary><code>{metric.formula}</code></details>
    </div>)}</dl>
    {answer.series.length > 0 && <figure className="specialist-chart"><figcaption>{answer.series_label}</figcaption>
      <svg viewBox={`0 0 280 ${answer.series.length * 38 + 8}`} role="img" aria-label={answer.series_label}>
        {answer.series.map((point, index) => <g key={point.label}>
          <title>{`${point.label}: ${formatValue(point.value, answer.evidence[0]?.unit ?? 'count')}`}</title>
          <text x="0" y={index * 38 + 12}>{point.label}</text>
          <rect x="0" y={index * 38 + 18} width={point.value / max * 280} height="10" rx="2" />
        </g>)}
      </svg>
      <table><caption className="demo-sr-only">Valores de {answer.series_label}</caption><tbody>{answer.series.map((point) =>
        <tr key={point.label}><th scope="row">{point.label}</th><td>{formatValue(point.value, answer.evidence[0]?.unit ?? 'count')}</td></tr>)}</tbody></table>
    </figure>}
    <section className="specialist-provenance"><h3>Fuente y alcance</h3>
      <p>{answer.source.name}</p><dl>
        <dt>Versión</dt><dd>{answer.source.version}</dd><dt>Período</dt><dd>{answer.source.period}</dd>
        <dt>Filtros</dt><dd>{answer.source.branch} · {answer.source.month}</dd>
        <dt>Registros consultados</dt><dd>{answer.source.rows.toLocaleString('es-CL')}</dd>
        <dt>Granularidad</dt><dd>{answer.source.grain}</dd>
      </dl><p>{answer.access_scope}</p>
    </section>
  </>;
}

/** Conversaciones separadas por agente, perfil y filtros; el servidor aplica el alcance antes de consultar. */
export default function Specialists() {
  const [profile, setProfile] = useState<DemoProfile>('direction');
  const [selected, setSelected] = useState<SpecialistId>('finance');
  const [search, setSearch] = useState('');
  const [branch, setBranch] = useState('');
  const [month, setMonth] = useState('');
  const [prompt, setPrompt] = useState('');
  const [threads, setThreads] = useState<Record<string, Message[]>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [showEvidence, setShowEvidence] = useState(() => typeof window !== 'undefined' && window.innerWidth > 950);
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.innerWidth <= 950);
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
  const visibleAgents = useMemo(() => agents.filter((item) => item.name.toLocaleLowerCase('es').includes(search.toLocaleLowerCase('es'))), [profile, search]);
  useEffect(() => {
    requestRef.current?.abort(); requestRef.current = null;
    setBusy(false); setPrompt(''); setError(''); setEvidenceMessage(null);
  }, [threadKey]);
  useEffect(() => () => requestRef.current?.abort(), []);
  useEffect(() => {
    const media = window.matchMedia('(max-width: 950px)');
    const update = () => setNarrow(media.matches);
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
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
    setError(''); setBusy(true); setPrompt('');
    setThreads((current) => ({ ...current, [threadKey]: [...(current[threadKey] ?? []), { id: crypto.randomUUID(), role: 'user', text: message }].slice(-20) as Message[] }));
    try {
      const response = await fetch('/api/agents/chat', { method: 'POST', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile, agent_id: agent.id, prompt: message, branch, month }) });
      const body: unknown = await response.json();
      if (!response.ok) {throw new Error(body && typeof body === 'object' && 'error' in body && typeof body.error === 'string' ? body.error : 'No se pudo completar la consulta.');}
      if (!validAnswer(body) || body.agent_id !== agent.id || body.profile !== profile) {throw new Error('La respuesta no corresponde al agente seleccionado.');}
      if (controller.signal.aborted) {return;}
      const id = crypto.randomUUID();
      setThreads((current) => ({ ...current, [threadKey]: [...(current[threadKey] ?? []), { id, role: 'assistant', text: body.message, answer: body }].slice(-20) as Message[] }));
      setEvidenceMessage(id);
    } catch (failure) {
      if (timedOut) {setError('La consulta superó el tiempo disponible.');}
      else if (!controller.signal.aborted) {setError(failure instanceof Error ? failure.message : 'No se pudo consultar el agente.');}
    } finally {
      clearTimeout(timer);
      if (requestRef.current === controller) {requestRef.current = null; setBusy(false);}
    }
  }

  const evidencePanel = <>
    <header><h2>Evidencia</h2><button type="button" aria-label="Cerrar evidencia" onClick={closeEvidence}>×</button></header>
    {evidence ? <Evidence answer={evidence} /> : <div className="specialist-evidence-empty"><h3>De la respuesta a la fuente</h3><p>Haz una pregunta para ver las métricas, sus cálculos y los registros consultados.</p>
      <h3>Acceso de {agent.name.toLowerCase()}</h3><p>{agent.scope}</p><p>Snapshot ficticio: enero–marzo de 2026.</p></div>}
  </>;

  return <div className="specialists-app">
    <DemoNav current="agents" />
    <div className="specialists-workspace">
      <aside className="specialists-directory" aria-label="Especialistas">
        <header><h1>Tu equipo de análisis</h1><p>Especialistas con evidencia de DataVIZ.</p></header>
        <label className="specialist-profile">Perfil de demostración<select value={profile} onChange={(event) => setProfile(event.target.value as DemoProfile)}>
          <option value="direction">Dirección</option><option value="operations">Operaciones</option>
        </select></label>
        <label className="demo-sr-only" htmlFor="agent-search">Buscar agente</label><input id="agent-search" type="search" placeholder="Buscar agente" value={search} onChange={(event) => setSearch(event.target.value)} />
        <div className="specialists-list">{visibleAgents.map((item) => <button type="button" key={item.id} aria-pressed={item.id === agent.id} onClick={() => setSelected(item.id)}>
          <span className="specialist-avatar" style={{ backgroundColor: item.color }}>{item.initials}</span>
          <span><strong>{item.name}</strong><small>{item.description}</small></span>
        </button>)}</div>
        {!visibleAgents.length && <p className="specialist-empty-search">No hay agentes con ese nombre.</p>}
        <p className="specialist-demo-note">Clínica X · datos ficticios.<br />Los perfiles simulan permisos; esta demo no autentica usuarios ni conecta fuentes reales.</p>
      </aside>

      <main className="specialist-conversation">
        <header className="specialist-thread-heading"><span className="specialist-avatar" style={{ backgroundColor: agent.color }}>{agent.initials}</span>
          <div><h2>{agent.name}</h2><p>{agent.description}</p></div>
          <button type="button" className="specialist-evidence-toggle" aria-expanded={showEvidence} onClick={() => showEvidence ? closeEvidence() : openEvidence()}>Evidencia</button>
        </header>
        <div className="specialist-scope-filters">
          <label>Sede<select value={branch} onChange={(event) => setBranch(event.target.value)}><option value="">Todas</option>{BRANCHES.map((name) => <option key={name}>{name}</option>)}</select></label>
          <label>Período<select value={month} onChange={(event) => setMonth(event.target.value)}><option value="">Enero–marzo 2026</option>{MONTHS.map((name, index) => <option key={name} value={name}>{['Enero', 'Febrero', 'Marzo'][index]}</option>)}</select></label>
        </div>
        <div className="specialist-messages" ref={feed} role="log" aria-label={`Conversación con ${agent.name}`} aria-live="polite">
          {!messages.length && <div className="specialist-welcome"><h3>¿Qué necesitas entender?</h3><p>{agent.scope}</p>
            <div className="specialist-prompts">{agent.prompts.map((item) => <button type="button" key={item} onClick={() => void send(item)} disabled={busy}>{item}<span aria-hidden="true">→</span></button>)}</div>
          </div>}
          {messages.map((message) => <article key={message.id} className={`specialist-message specialist-message-${message.role}`}>
            <span className="demo-sr-only">{message.role === 'user' ? 'Tú' : agent.name}</span><p>{message.text}</p>
            {message.answer && <div className="specialist-answer-meta"><span>{message.answer.mode === 'glm' ? 'Explicación GLM · cifras calculadas' : message.answer.mode === 'local_fallback' ? 'Cálculo local · GLM no disponible' : 'Cálculo local · sin LLM'}</span>
              <button type="button" onClick={() => openEvidence(message.id)}>Ver evidencia</button>
            </div>}
          </article>)}
          {busy && <p className="specialist-query-status" role="status">Consultando la evidencia autorizada…</p>}
        </div>
        {error && <p className="specialist-error" role="alert">{error} Puedes volver a enviar la pregunta.</p>}
        <form className="specialist-composer" onSubmit={(event) => {event.preventDefault(); void send();}}>
          <label className="demo-sr-only" htmlFor="specialist-prompt">Mensaje para {agent.name}</label>
          <textarea id="specialist-prompt" placeholder={`Pregunta a ${agent.name.toLowerCase()}…`} maxLength={2000} rows={2} value={prompt}
            onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {event.preventDefault(); void send();}
            }} />
          {busy ? <button type="button" onClick={() => {requestRef.current?.abort(); requestRef.current = null; setBusy(false);}}>Cancelar</button>
            : <button type="submit" aria-label="Enviar pregunta" disabled={!prompt.trim()}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 19V5m-5 5 5-5 5 5" /></svg></button>}
          <p>Las respuestas muestran datos ficticios y su fuente. Ningún agente ejecuta acciones.</p>
        </form>
      </main>

      {showEvidence && (narrow
        ? <dialog className="specialist-evidence" ref={evidenceDialog} aria-label="Evidencia y permisos"
          onCancel={(event) => {event.preventDefault(); closeEvidence();}}>{evidencePanel}</dialog>
        : <aside className="specialist-evidence" aria-label="Evidencia y permisos">{evidencePanel}</aside>)}
    </div>
  </div>;
}
