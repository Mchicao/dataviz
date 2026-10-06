import { presentationFromIR } from './presentationBridge';
import { validateCanonicalPresentationIR } from './types';
import type { AgentProposal, Document, VisualOperation } from './types';
import { getHeadVersion } from './versioning';
import { addVisual, previewVisualOperations, removeVisual, updateVisual } from './visualOperations';
import { jsonEqual } from './jsonEqual';
import type { AgentSelection } from './agentSettings';
import { compareRemoteCostReport, customVisualOpsCostWarnings } from '../visuals/custom/spec';

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' && value !== null && !Array.isArray(value)
);

export async function requestAgentProposal(
  endpoint: string,
  document: Document,
  prompt: string,
  selection: AgentSelection,
  pageId: string,
  selectedId: string | null,
  signal: AbortSignal,
): Promise<AgentProposal> {
  const head = getHeadVersion(document);
  const before = presentationFromIR(head.presentation_ir, pageId);
  const response = await fetch(`${endpoint}/proposals`, {
    body: JSON.stringify({
      prompt,
      model: selection.model,
      thinking: selection.thinking,
      base_version: head.number,
      page_id: pageId,
      selected_visual_id: selectedId,
      semantic_model: head.model,
      presentation: head.presentation_ir,
    }),
    headers: { 'Content-Type': 'application/json' },
    method: 'POST',
    signal,
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(isRecord(body) && typeof body.error === 'string'
      ? body.error : `El servicio de IA no completó la solicitud (HTTP ${response.status}).`);
  }
  if (!isRecord(body) || typeof body.reply !== 'string' || typeof body.proposal_id !== 'string'
    || body.base_version !== head.number || body.model !== selection.model || body.thinking !== selection.thinking) {
    throw new Error('La respuesta de IA no corresponde a la solicitud. No se aplicó ningún cambio.');
  }
  const ir = validateCanonicalPresentationIR(body.presentation);
  if (ir.doc_id !== head.presentation_ir.doc_id || !ir.pages.some((page) => page.page_id === pageId)) {
    throw new Error('La propuesta no corresponde al dashboard actual.');
  }
  const after = presentationFromIR(ir, pageId);
  const visualOps: VisualOperation[] = [];
  for (const visual of before.visuals) {
    const updated = after.visuals.find((candidate) => candidate.id === visual.id);
    if (!updated) {visualOps.push(removeVisual(visual.id));}
    else if (!jsonEqual(visual, updated)) {visualOps.push(updateVisual(updated));}
  }
  for (const visual of after.visuals) {
    if (!before.visuals.some((candidate) => candidate.id === visual.id)) {visualOps.push(addVisual(visual));}
  }
  // El costo de los custom visuals se calcula SIEMPRE localmente desde las
  // visual_ops; el cost_report del modelo sólo se acepta si coincide con ese
  // cálculo (nunca se confía en la entrada del modelo).
  const warnings = ['La IA recibe la estructura del dashboard, sin filas de datos.', 'Aplicar guarda un borrador; no publica.'];
  const customWarnings = customVisualOpsCostWarnings(visualOps);
  if (customWarnings.length > 0) {
    warnings.push(...customWarnings);
    const mismatch = compareRemoteCostReport(body.cost_report, visualOps);
    if (mismatch) {warnings.push(mismatch);}
  }
  return {
    base_version: head.number,
    prompt,
    proposal_id: body.proposal_id,
    rationale: [`Propuesta de ${selection.model} · razonamiento ${selection.thinking}.`],
    semantic_ops: [],
    summary: body.reply,
    visual_changes: previewVisualOperations(before, visualOps),
    visual_ops: visualOps,
    warnings,
  };
}
