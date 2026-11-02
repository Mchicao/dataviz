import React, { useEffect, useState } from 'react';
import type { AgentProposal } from './types';
import { CHATBOT_MODELS, CHATBOT_THINKING, loadAgentConfig, parseSelection } from './agentSettings';
import type { AgentSelection } from './agentSettings';
import { customVisualOpsRequireConfirmation } from '../visuals/custom/spec';

interface AgentPanelProps {
  title?: string;
  proposal: AgentProposal | null;
  response?: string;
  previewError?: string;
  endpoint?: string;
  busy?: boolean;
  onCancel?: () => void;
  onCollapse: () => void;
  onPlan: (prompt: string, selection?: AgentSelection) => boolean | Promise<boolean>;
  onApply: () => void;
  onReject: () => void;
  referencedVisual?: { id: string; name: string; title?: string; intent?: string } | null;
  onClearReferencedVisual?: () => void;
  prefillPrompt?: string;
  onClearPrefill?: () => void;
}

const PROMPTS = [
  'Muestra una tendencia temporal',
  'Compara categorías',
  'Mejora el contraste',
  'Ordena y simplifica el layout',
];

const PREFERENCE_KEY = 'dataviz.agent.selection.v1';
const LEVEL_LABELS = { high: 'Alto · equilibrado', low: 'Bajo · más rápido', max: 'Máximo · más pausado' };

export const AgentPanel: React.FC<AgentPanelProps> = ({ proposal, response, previewError, endpoint, busy = false, onCancel, onCollapse, onPlan, onApply, onReject, referencedVisual, onClearReferencedVisual, prefillPrompt, onClearPrefill, title }) => {
  const [prompt, setPrompt] = useState('');
  useEffect(() => {
    if (prefillPrompt) {
      setPrompt(prefillPrompt);
      onClearPrefill?.();
    }
  }, [prefillPrompt, onClearPrefill]);

  const [selection, setSelection] = useState<AgentSelection>({ model: 'local', thinking: 'high' });
  const [configured, setConfigured] = useState(false);
  const [configError, setConfigError] = useState('');
  const [loadingConfig, setLoadingConfig] = useState(Boolean(endpoint));
  const [confirmedHighCostProposal, setConfirmedHighCostProposal] = useState<AgentProposal | null>(null);
  const requiresHighCostConfirmation = proposal
    ? customVisualOpsRequireConfirmation(proposal.visual_ops)
    : false;
  const highCostConfirmed = proposal !== null && proposal === confirmedHighCostProposal;

  useEffect(() => {
    if (!endpoint) {return;}
    const controller = new AbortController();
    setLoadingConfig(true);
    void loadAgentConfig(endpoint, controller.signal).then((config) => {
      if (controller.signal.aborted) {return;}
      setConfigured(config.configured);
      let saved: AgentSelection | null = null;
      try { saved = parseSelection(JSON.parse(localStorage.getItem(PREFERENCE_KEY) ?? 'null')); } catch { /* El almacenamiento puede estar desactivado. */ }
      setSelection(saved ?? { model: config.configured ? config.default_model : 'local', thinking: config.default_thinking });
      setConfigError(config.configured ? '' : 'Falta configurar la clave en el servidor.');
    }).catch(() => {
      if (!controller.signal.aborted) {setConfigError('Servicio de IA desconectado. Sólo están disponibles las reglas locales.');}
    }).finally(() => {
      if (!controller.signal.aborted) {setLoadingConfig(false);}
    });
    return () => controller.abort();
  }, [endpoint]);

  const choose = (next: AgentSelection) => {
    setSelection(next);
    try { localStorage.setItem(PREFERENCE_KEY, JSON.stringify(next)); } catch { /* La selección sigue disponible durante esta sesión. */ }
  };

  const submit = () => {
    const value = prompt.trim();
    if (!value || busy || loadingConfig || (selection.model !== 'local' && !configured)) {return;}
    const result = onPlan(value, selection);
    if (typeof result === 'boolean') { if (result) {setPrompt('');} }
    else {void result.then((success) => { if (success) {setPrompt('');} });}
  };

  return (
    <section aria-label="Asistente de diseño" className="dv-right-pane dv-agent-pane">
      <div
        className="dv-pane-header dv-clickable-pane-header"
        onClick={onCollapse}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onCollapse?.();
          }
        }}
        role="button"
        tabIndex={0}
        title="Minimizar panel de asistente"
      >
        <span className="dv-pane-title"><span aria-hidden="true" className="dv-pane-icon">✦</span><strong>{title ?? "Asistente"}</strong></span>
        <span className="dv-agent-status">{busy
          ? <>Consultando<span aria-hidden="true" className="dv-agent-dots"><i /><i /><i /></span></>
          : loadingConfig ? 'Conectando…' : 'Listo'}</span>
        <span aria-hidden="true" className="dv-pane-collapse">»</span>
      </div>
      <div aria-live="polite" className="dv-sr-only">
        {proposal ? `Propuesta lista. ${proposal.summary}` : ''}
      </div>
      <div className="dv-right-pane-body dv-agent-body">
        {proposal ? (
          <div className="dv-proposal">
            <div className="dv-proposal-summary">
              <span className="dv-proposal-label">Vista previa, sin aplicar</span>
              <strong>{proposal.summary}</strong>
              <p>{proposal.rationale.join(' ')}</p>
            </div>
            <div className="dv-proposal-changes">
              {proposal.visual_changes.map((change) => (
                <span className={`dv-change dv-change--${change.kind}`} key={`${change.kind}-${change.visual_id}`}>
                  {change.kind === 'added' ? '+' : change.kind === 'removed' ? '−' : '~'} {change.after?.title ?? change.before?.title ?? change.visual_id}
                </span>
              ))}
              {proposal.semantic_ops.map((operation) => (
                <span className="dv-change dv-change--semantic" key={`${operation.kind}-${operation.target}-${operation.name}`}>
                  ◇ {operation.kind} {operation.target}: {operation.name}
                </span>
              ))}
            </div>
            {requiresHighCostConfirmation && (
              <div className="dv-proposal-cost" role="alert">
                <strong>Visual de alto costo</strong>
                <p>La estimación supera el presupuesto de consulta o render. Revisa el detalle antes de aplicar.</p>
                <label>
                  <input
                    checked={highCostConfirmed}
                    onChange={(event) => setConfirmedHighCostProposal(
                      event.target.checked ? proposal : null,
                    )}
                    type="checkbox"
                  />
                  Permitir este visual de alto costo
                </label>
              </div>
            )}
            <div className="dv-proposal-actions">
              <button className="dv-button-secondary" onClick={onReject} type="button">Rechazar</button>
              <button className="dv-button-primary" disabled={Boolean(previewError) || (requiresHighCostConfirmation && !highCostConfirmed)} onClick={onApply} type="button">Aplicar propuesta</button>
            </div>
            {previewError && <p className="dv-proposal-error" role="alert">No se puede materializar el preview: {previewError}</p>}
            <details>
              <summary>Seguridad y supuestos</summary>
              <ul>{proposal.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
            </details>
          </div>
        ) : (
          <div className="dv-agent-compose">
            {configError && <p className="dv-agent-help">{configError}</p>}
            {response && <p className="dv-agent-response" role="status">{response}</p>}
            <div className="dv-prompt-row">
              {referencedVisual && (
                <div className="dv-agent-reference-chip">
                  <span className="dv-agent-reference-label">
                    🎯 <strong>Visual referenciada:</strong> {referencedVisual.title || referencedVisual.name}
                    {referencedVisual.intent ? ` (${referencedVisual.intent})` : ''}
                  </span>
                  {onClearReferencedVisual && (
                    <button
                      aria-label="Quitar referencia de visual"
                      className="dv-agent-reference-clear"
                      onClick={onClearReferencedVisual}
                      title="Quitar referencia"
                      type="button"
                    >
                      ✕
                    </button>
                  )}
                </div>
              )}
              <label className="dv-sr-only" htmlFor="agent-prompt">Describe el dashboard</label>
              <textarea
                id="agent-prompt"
                maxLength={2000}
                disabled={busy}
                onChange={(event) => setPrompt(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing && !event.repeat) {
                    event.preventDefault();
                    submit();
                  }
                }}
                placeholder="Ej.: compara categorías o cambia el título de un gráfico"
                rows={3}
                value={prompt}
              />
              <div className="dv-agent-input-row">
                <select aria-label="Modelo del agente" className="dv-agent-chip" disabled={busy || Boolean(proposal) || loadingConfig} value={selection.model} onChange={(event) => {
                  const next = parseSelection({ ...selection, model: event.target.value });
                  if (next) {choose(next);}
                }}>
                  <option value="local">Reglas locales</option>
                  {CHATBOT_MODELS.map((model) => <option key={model} value={model} disabled={!configured}>{model}</option>)}
                </select>
                <select aria-label="Nivel de razonamiento" className="dv-agent-chip" disabled={busy || Boolean(proposal) || selection.model === 'local'} value={selection.thinking} onChange={(event) => {
                  const next = parseSelection({ ...selection, thinking: event.target.value });
                  if (next) {choose(next);}
                }}>
                  {CHATBOT_THINKING.map((level) => <option key={level} value={level}>{LEVEL_LABELS[level].split(' ')[0]}</option>)}
                </select>
                {busy ? <button aria-label="Cancelar" className="dv-agent-send" onClick={onCancel} title="Cancelar solicitud" type="button">×</button>
                  : <button aria-label="Enviar" className="dv-agent-send" disabled={!prompt.trim() || loadingConfig || (selection.model !== 'local' && !configured)} onClick={submit} type="button">↑</button>}
              </div>
              <div className="dv-prompt-suggestions" aria-label="Sugerencias de instrucciones">
                {PROMPTS.map((item) => <button disabled={busy || loadingConfig || (selection.model !== 'local' && !configured)} key={item} onClick={() => { setPrompt(item); void onPlan(item, selection); }} type="button">{item}</button>)}
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
};
