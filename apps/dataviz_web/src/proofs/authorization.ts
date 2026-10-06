/**
 * Módulo confiable de autorización (Ghosts of Departed Proofs).
 *
 * Los provers NO se exportan: este módulo es el único que puede acuñar
 * proofs, y cada checker ejecuta todos los chequeos sobre los valores
 * `Named` exactos (propuesta-vs-head, versión objetivo de publicación,
 * versión objetivo de rollback). Las operaciones sensibles exigen el proof
 * como parámetro: sin pasar por el checker no compila, y forjarlo con `as`
 * lo bloquea el preset gdp-ts del linter.
 *
 * Hoy estos proofs codifican la disciplina del lado cliente (el servidor
 * sigue siendo la autoridad). Cuando el control plane SaaS exija proofs
 * server-side, este módulo espeja ese contrato con el mismo patrón.
 */
import { defineProof } from '@gdp-ts/core';
import type { Named, Proof } from '@gdp-ts/core';

import type { AgentProposal } from '../authoring/types';

/** Resultado de un checker: el proof acuñado, o el error de UI a mostrar. */
export type Authorization<ProofT> =
  | { readonly proof: ProofT; readonly error?: undefined }
  | { readonly proof?: undefined; readonly error: string };

const ProposalApproved = defineProof('ProposalApproved');
export interface ProposalApproved<P, V> extends Proof<'ProposalApproved', [P, V]> {}

const PublicationAuthorized = defineProof('PublicationAuthorized');
export interface PublicationAuthorized<V> extends Proof<'PublicationAuthorized', [V]> {}

const RollbackAuthorized = defineProof('RollbackAuthorized');
export interface RollbackAuthorized<V> extends Proof<'RollbackAuthorized', [V]> {}

/**
 * Aprobar la aplicación de una propuesta del agente sobre ESTE head:
 * requiere modo local, propuesta generada sobre la versión vigente y
 * preview sin errores.
 */
export function authorizeAgentProposal<P, V>(
  proposal: Named<P, AgentProposal>,
  head: Named<V, number>,
  state: { readonly remoteMode: boolean; readonly previewError: string },
): Authorization<ProposalApproved<P, V>> {
  if (state.remoteMode) {
    return { error: 'En modo Studio remoto las propuestas del agente se planifican y previsualizan, pero no se aplican directamente.' };
  }
  if (proposal.value.base_version !== head.value) {
    return { error: `La propuesta fue creada sobre v${proposal.value.base_version}; el proyecto ya está en v${head.value}. Vuelve a generarla.` };
  }
  if (state.previewError) {
    return { error: `No se puede aplicar una propuesta cuyo preview falló: ${state.previewError}` };
  }
  return { proof: ProposalApproved.prove(proposal, head) };
}

export interface PublicationState {
  readonly mode: 'remote' | 'local';
  readonly remoteReady: boolean;
  readonly canRequestPublish: boolean;
  readonly hasPublicationHook: boolean;
  readonly hasPendingProposal: boolean;
  readonly importBusy: boolean;
  readonly datasetMissing: boolean;
}

/**
 * Autorizar solicitar la publicación de ESTA versión objetivo. Los chequeos
 * por modo espejan los del flujo original (remoto: solo readiness del
 * servicio; local: permiso, propuesta pendiente, importación y dataset).
 */
export function authorizePublication<V>(
  targetVersion: Named<V, number>,
  state: PublicationState,
): Authorization<PublicationAuthorized<V>> {
  if (state.mode === 'remote') {
    if (!state.remoteReady) {
      return { error: 'Espera a que el modo remoto esté listo para solicitar publicación.' };
    }
    return { proof: PublicationAuthorized.prove(targetVersion) };
  }
  if (!state.hasPublicationHook) {
    return { error: 'La publicación autoritativa se gestiona mediante el servicio gobernado.' };
  }
  if (!state.canRequestPublish) {
    return { error: 'No tienes permiso para solicitar una publicación.' };
  }
  if (state.hasPendingProposal) {
    return { error: 'Aplica o rechaza la propuesta del agente antes de solicitar publicación.' };
  }
  if (state.importBusy) {
    return { error: 'Espera a que termine la importación de datos antes de solicitar publicación.' };
  }
  if (state.datasetMissing) {
    return { error: 'Los datos importados no están disponibles; reimporta el CSV antes de solicitar publicación.' };
  }
  return { proof: PublicationAuthorized.prove(targetVersion) };
}

export interface RemotePublicationOutcome {
  readonly ok: boolean;
  readonly status: number;
  readonly body: unknown;
}

/**
 * POST de solicitud de publicación hacia el servicio: el único camino para
 * cruzar esta frontera exige un proof sobre la versión exacta.
 */
export async function requestRemotePublication<V>(
  deps: {
    readonly projectId: string;
    readonly fetchImpl: (path: string, init: { readonly method: 'POST'; readonly body: string }) => Promise<Response>;
  },
  targetVersion: Named<V, number>,
  _proof: PublicationAuthorized<V>,
): Promise<RemotePublicationOutcome> {
  const response = await deps.fetchImpl(
    `/api/projects/${encodeURIComponent(deps.projectId)}/publication-requests`,
    { method: 'POST', body: JSON.stringify({ target_version: targetVersion.value }) },
  );
  return { ok: response.ok, status: response.status, body: await response.json().catch(() => null) };
}

/**
 * Autorizar restaurar ESTA versión: permiso de revertir, sin importación en
 * curso y, en modo remoto, servicio listo con head durable cargado.
 */
export function authorizeRollback<V>(
  target: Named<V, number>,
  state: {
    readonly canRevert: boolean;
    readonly remoteMode: boolean;
    readonly remoteReady: boolean;
    readonly hasRemoteHead: boolean;
    readonly importBusy: boolean;
  },
): Authorization<RollbackAuthorized<V>> {
  if (!state.canRevert) {
    return { error: 'No tienes permiso para revertir este proyecto.' };
  }
  if (state.importBusy) {
    return { error: 'Espera a que termine la importación de datos antes de restaurar.' };
  }
  if (state.remoteMode) {
    if (!state.remoteReady) {
      return { error: 'Espera a que el modo remoto esté listo para restaurar una versión.' };
    }
    if (!state.hasRemoteHead) {
      return { error: 'El proyecto remoto no tiene un head durable cargado.' };
    }
  }
  return { proof: RollbackAuthorized.prove(target) };
}
