import { INTERACTION_IR_SCHEMA_VERSION } from '../interactions';
import type { InteractionIR } from '../interactions';
import type {
  CanonicalVisualPresentation,
  Document,
  Operation,
  PresentationIR,
  PresentationSnapshot,
  RemoteAuthoringState,
  RemoteDraftRequest,
  RemoteDurableVersion,
  RemoteInteractionOperation,
  RemotePresentationOperation,
  RemoteRollbackRequest,
  SemanticModel,
  Version,
  VisualLayoutSpec,
  VisualOperation,
} from './types';
import { jsonEqual } from './jsonEqual';
import { materialize } from './operations';
import {
  presentationFromIR,
  presentationToIR,
  updatePresentationIRFromSnapshot,
} from './presentationBridge';

function getNowISO(): string {
  return new Date().toISOString();
}

function checkBaseVersion(doc: Document, baseVersion?: number): void {
  if (baseVersion === undefined) {return;}
  if (!Number.isInteger(baseVersion)) {
    throw new TypeError('base_version must be an integer');
  }
  if (baseVersion < 1) {
    throw new Error('base_version must be >= 1');
  }
  const actual = getHeadVersion(doc).number;
  if (baseVersion !== actual) {
    throw new Error(`stale base version: expected ${baseVersion}, actual ${actual}`);
  }
}

function createInteractionIR(docId: string): InteractionIR {
  return {
    doc_id: docId,
    global_filters: [],
    metadata: {},
    pages: [],
    schema_version: INTERACTION_IR_SCHEMA_VERSION,
  };
}

/**
 * Modelo semántico vacío y honesto para filas de historial cuyo snapshot el
 * servicio no devolvió. Nunca se copia el modelo del head actual como si fuera
 * el contenido de una versión anterior (LUNA1 P1).
 */
const EMPTY_MODEL: SemanticModel = {
  description: 'Versión sin snapshot durable (el servicio no devolvió payload).',
  entities: [],
  filters: [],
  metrics: [],
  name: 'nuevo_dashboard',
  parameters: [],
  relationships: [],
  rls_intents: [],
  schema_version: '2.0.0',
};

function validateInteractionIR(ir: InteractionIR, docId: string): void {
  if (ir.schema_version !== INTERACTION_IR_SCHEMA_VERSION) {
    throw new Error(`Incompatible InteractionIR schema_version: ${ir.schema_version}`);
  }
  if ((ir.doc_id ?? '') !== docId) {
    throw new Error(`InteractionIR doc_id must equal document doc_id "${docId}"`);
  }
  const pageIds = (ir.pages ?? []).map((page) => page.page_id);
  if (new Set(pageIds).size !== pageIds.length) {throw new Error('duplicate InteractionIR page_id');}
  const filterIds = (ir.global_filters ?? []).map((filter) => filter.filter_id);
  if (new Set(filterIds).size !== filterIds.length) {throw new Error('duplicate InteractionIR global filter_id');}
}

/** Copy JSON-safe IR payloads so a new version never aliases mutable history. */
function snapshot<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

/** Validate document invariants on construction / mutation. */
// ponytail: re-valida materialize sobre TODAS las versiones en cada commit — O(historial × modelo)
// por acción, sin poda. Correcto a escala MVP (decenas de versiones); si el historial crece a
// cientos, cachear la validación por número de versión es el camino de upgrade.
export function validateDocument(doc: Document): void {
  if (!doc.doc_id || typeof doc.doc_id !== 'string') {
    throw new Error('doc_id is required');
  }
  if (!doc.versions || doc.versions.length === 0) {
    throw new Error('Document must have at least one version');
  }
  const numbers = doc.versions.map((v) => v.number);
  const sorted = [...numbers].sort((a, b) => a - b);
  if (
    JSON.stringify(numbers) !== JSON.stringify(sorted) ||
    new Set(numbers).size !== numbers.length
  ) {
    throw new Error('versions must be unique and strictly increasing by number');
  }
  const publishedCount = doc.versions.filter((v) => v.status === 'published').length;
  if (publishedCount > 1) {
    throw new Error('at most one published version is allowed');
  }
  for (const version of doc.versions) {
    if (version.dataset_fingerprint !== undefined
      && typeof version.dataset_fingerprint !== 'string') {
      throw new Error(`version ${version.number} has an invalid dataset_fingerprint`);
    }
    assertUnique(version.presentation.visuals.map((visual) => visual.id), 'visual id', version.number);
    assertUnique(version.presentation.visuals.map((visual) => visual.name), 'visual name', version.number);
    if (!version.presentation_ir) {throw new Error(`version ${version.number} is missing PresentationIR`);}
    if (version.presentation_ir.doc_id !== doc.doc_id) {
      throw new Error(`version ${version.number} PresentationIR doc_id does not match document`);
    }
    const pageId = version.active_page_id ?? version.presentation_ir.pages[0]?.page_id;
    const projected = presentationFromIR(version.presentation_ir, pageId);
    if (!jsonEqual(projected, version.presentation)) {
      throw new Error(`version ${version.number} presentation cache diverges from canonical PresentationIR`);
    }
    if (!version.interaction_ir) {throw new Error(`version ${version.number} is missing InteractionIR`);}
    validateInteractionIR(version.interaction_ir, doc.doc_id);
    const semanticSections = [
      ['entity', version.model.entities],
      ['relationship', version.model.relationships],
      ['metric', version.model.metrics],
      ['parameter', version.model.parameters],
      ['filter', version.model.filters],
      ['rls_intent', version.model.rls_intents],
    ] as const;
    for (const [label, items] of semanticSections) {
      assertUnique(items.map((item) => item.name), `${label} name`, version.number);
    }
    materialize(version.model, []);
  }
}

function assertUnique(values: string[], label: string, version: number): void {
  if (new Set(values).size !== values.length) {
    throw new Error(`duplicate ${label} in version ${version}`);
  }
}

/** Initialize a document with version 1 draft. */
export function initDocument(
  model: SemanticModel,
  options: {
    doc_id: string;
    message?: string;
    visuals?: VisualLayoutSpec[];
    presentationIR?: PresentationIR;
    interactionIR?: InteractionIR;
  },
): Document {
  const presentationIR = snapshot(options.presentationIR ?? presentationToIR(
    createPresentation(options.visuals ?? []), options.doc_id,
  ));
  const presentation = presentationFromIR(presentationIR);
  const interactionIR = snapshot(options.interactionIR ?? createInteractionIR(options.doc_id));
  const v1: Version = {
    created_at: getNowISO(),
    interaction_ir: interactionIR,
    message: options.message || 'initial',
    model: snapshot(model),
    number: 1,
    parent: null,
    presentation,
    presentation_ir: presentationIR,
    status: 'draft',
  };
  const doc: Document = {
    doc_id: options.doc_id,
    versions: [v1],
  };
  validateDocument(doc);
  return doc;
}

/** Get the latest head version of a document. */
export function getHeadVersion(doc: Document): Version {
  return doc.versions[doc.versions.length - 1];
}

/** Read a published version supplied by a governed/legacy document; this module cannot publish. */
export function getPublishedVersion(doc: Document): Version | null {
  for (let i = doc.versions.length - 1; i >= 0; i--) {
    if (doc.versions[i].status === 'published') {
      return doc.versions[i];
    }
  }
  return null;
}

/** Look up a version by number or throw. */
function getVersion(doc: Document, number: number): Version {
  const found = doc.versions.find((v) => v.number === number);
  if (!found) {
    throw new Error(`version ${number} not found in document "${doc.doc_id}"`);
  }
  return found;
}

/** Validate ops against doc head and append a new draft version. */
export function applyOps(
  doc: Document,
  ops: Operation[],
  options: {
    message?: string;
    presentation?: PresentationSnapshot;
    presentation_ir?: PresentationIR;
    interaction_ir?: InteractionIR;
    modelMetadata?: Pick<SemanticModel, 'schema_version' | 'name' | 'description'>;
    datasetFingerprint?: string;
    base_version?: number;
    activePageId?: string | null;
  } = {},
): Document {
  checkBaseVersion(doc, options.base_version);
  const head = getHeadVersion(doc);
  const materialized = materialize(head.model, ops);
  const newModel = options.modelMetadata
    ? { ...materialized, ...options.modelMetadata }
    : materialized;
  const canonicalPresentation = snapshot(
    options.presentation_ir
      ?? (options.presentation
        ? updatePresentationIRFromSnapshot(head.presentation_ir, options.presentation, doc.doc_id, options.activePageId)
        : head.presentation_ir),
  );
  const projectedPresentation = presentationFromIR(canonicalPresentation, options.activePageId);
  const canonicalInteraction = snapshot(options.interaction_ir ?? head.interaction_ir);
  const newVersion: Version = {
    active_page_id: options.activePageId ?? undefined,
    created_at: getNowISO(),
    dataset_fingerprint: options.datasetFingerprint ?? head.dataset_fingerprint,
    interaction_ir: canonicalInteraction,
    message: options.message || '',
    model: snapshot(newModel),
    number: head.number + 1,
    parent: head.number,
    presentation: projectedPresentation,
    presentation_ir: canonicalPresentation,
    status: 'draft',
  };
  const nextDoc: Document = {
    doc_id: doc.doc_id,
    versions: [...doc.versions, newVersion],
  };
  validateDocument(nextDoc);
  return nextDoc;
}

/** Append a new draft version restoring the model of target version. Preserves history. */
export function rollback(
  doc: Document,
  to_number: number,
  options: { message?: string; base_version?: number } = {},
): Document {
  checkBaseVersion(doc, options.base_version);
  const target = getVersion(doc, to_number);
  const head = getHeadVersion(doc);
  const restoredVersion: Version = {
    created_at: getNowISO(),
    dataset_fingerprint: target.dataset_fingerprint,
    interaction_ir: snapshot(target.interaction_ir),
    message: options.message || `rollback to v${to_number}`,
    model: snapshot(target.model),
    number: head.number + 1,
    parent: to_number,
    presentation: presentationFromIR(target.presentation_ir),
    presentation_ir: snapshot(target.presentation_ir),
    status: 'draft',
  };
  const nextDoc: Document = {
    doc_id: doc.doc_id,
    versions: [...doc.versions, restoredVersion],
  };
  validateDocument(nextDoc);
  return nextDoc;
}

/** Upgrade a legacy locally persisted document to canonical authoring contracts. */
export function upgradeDocumentContracts(document: Document): Document {
  const versions = document.versions.map((version) => {
    if (!version.presentation || !Array.isArray(version.presentation.visuals)) {
      throw new Error(`version ${version.number} has no authoring presentation snapshot`);
    }
    const presentationIR = version.presentation_ir
      ?? presentationToIR(version.presentation, document.doc_id);
    const interactionIR = version.interaction_ir ?? createInteractionIR(document.doc_id);
    return {
      ...version,
      interaction_ir: snapshot(interactionIR),
      presentation: presentationFromIR(presentationIR),
      presentation_ir: snapshot(presentationIR),
    };
  });
  const upgraded = { ...document, versions };
  validateDocument(upgraded);
  return upgraded;
}

/** Create the neutral presentation snapshot shared by manual and agentic edits. */
export function createPresentation(visuals: VisualLayoutSpec[]): PresentationSnapshot {
  return {
    canvas: {
      background_color: '#f4f7fa',
      height: 760,
      width: 1200,
    },
    visuals: snapshot(visuals),
  };
}

// ---------------------------------------------------------------------------
// Modo Studio remoto (CMVP-04B)
// Puentes puros hacia el contrato wire del servicio. El fetch y el state machine
// viven en VisualAuthoringEditor; aquí SOLO construcción y validación
// determinista (sin fetch, sin DOM, sin localStorage).
// ---------------------------------------------------------------------------

export const REMOTE_CHECKSUM_PATTERN = /^[0-9a-f]{64}$/;

export function isRemoteChecksum(value: string): boolean {
  return REMOTE_CHECKSUM_PATTERN.test(value);
}

/**
 * Construye el Document de trabajo a partir del head durable del servicio.
 * El head es la ÚNICA autoridad: una sola versión inmutable cuya
 * `status` refleja si ya fue publicada por el servicio gobernado.
 */
export function remoteHeadDocument(state: RemoteAuthoringState): Document {
  const {head} = state;
  const docId = head.presentation.doc_id || state.project_id;
  const historyEntries = (state.history ?? []).filter(
    (entry) => entry.version_number < head.version_number,
  );
  historyEntries.sort((a, b) => a.version_number - b.version_number);

  const versions: Version[] = [];
  for (const entry of historyEntries) {
    if (entry.presentation && entry.semantic && entry.interaction) {
      const presIR = snapshot(entry.presentation);
      presIR.doc_id = docId;
      if (!presIR.title && head.presentation.title) {
        presIR.title = head.presentation.title;
      }
      const interIR = snapshot(entry.interaction);
      interIR.doc_id = docId;
      versions.push({
        created_at: entry.created_at,
        interaction_ir: interIR,
        message: entry.message ?? '',
        model: snapshot(entry.semantic),
        number: entry.version_number,
        parent: null,
        presentation: presentationFromIR(presIR),
        presentation_ir: presIR,
        status:
          state.published_version !== null && state.published_version === entry.version_number
            ? 'published'
            : 'draft',
      });
    } else {
      // LUNA1 P1: el servicio no devolvió snapshot de esta versión. No se fábrica
      // historia falsa copiando modelo/interacción del head actual: se materializa
      // una fila honesta con modelo vacío y lienzo en blanco.
      const fallbackPres = presentationToIR(
        { canvas: { background_color: '#FFFFFF', height: 760, width: 1200 }, visuals: [] },
        docId,
        head.presentation.title ?? '',
      );
      versions.push({
        created_at: entry.created_at,
        interaction_ir: createInteractionIR(docId),
        message: entry.message ?? '',
        model: snapshot(EMPTY_MODEL),
        number: entry.version_number,
        parent: null,
        presentation: presentationFromIR(fallbackPres),
        presentation_ir: fallbackPres,
        status:
          state.published_version !== null && state.published_version === entry.version_number
            ? 'published'
            : 'draft',
      });
    }
  }

  const headPresIR = snapshot(head.presentation);
  headPresIR.doc_id = docId;
  const headInterIR = snapshot(head.interaction);
  headInterIR.doc_id = docId;
  const headVersion: Version = {
    created_at: head.created_at,
    interaction_ir: headInterIR,
    message: head.message ?? '',
    model: snapshot(head.semantic),
    number: head.version_number,
    parent: null,
    presentation: presentationFromIR(headPresIR),
    presentation_ir: headPresIR,
    status:
      state.published_version !== null && state.published_version === head.version_number
        ? 'published'
        : 'draft',
  };
  versions.push(headVersion);

  const doc: Document = { doc_id: docId, versions };
  validateDocument(doc);
  return doc;
}

/** Base CAS durable que el próximo commit/rollback debe usar tras una confirmación 201. */
export function remoteBaseFromVersion(version: RemoteDurableVersion): {
  version_number: number;
  checksum: string;
} {
  return { checksum: version.checksum, version_number: version.version_number };
}

/**
 * Traduce operaciones de proyección del editor a operaciones canónicas de
 * PresentationIR (wire). `nextPresentationIR` es el IR local ya materializado:
 * el payload canónico que se envía es exactamente el visual resultante, de modo
 * que el servicio deriva el mismo IR aunque aplique las ops en otro orden.
 */
export function presentationOpsFromVisualOps(
  nextPresentationIR: PresentationIR,
  ops: VisualOperation[],
  activePageId?: string | null,
): RemotePresentationOperation[] {
  const page = (activePageId ? nextPresentationIR.pages.find((p) => p.page_id === activePageId) : null)
    ?? nextPresentationIR.pages[0];
  if (!page) {
    throw new Error('No se pudo construir el payload canónico: el PresentationIR no tiene una página de canvas.');
  }
  const visualsById = new Map(page.visuals.map((visual) => [visual.visual_id, visual]));
  return ops.map((op) => {
    const payload = op.kind === 'remove' ? null : visualsById.get(op.visual_id) ?? null;
    if (op.kind !== 'remove' && payload === null) {
      throw new Error(
        `No se pudo construir el payload canónico del visual "${op.visual_id}" sobre la página "${page.page_id}".`,
      );
    }
    return {
      kind: op.kind,
      name: op.visual_id,
      page_id: page.page_id,
      payload: payload as CanonicalVisualPresentation | null,
      target: 'visual',
    };
  });
}

/** Cuerpo del POST drafts con validación CAS de la base. */
export function buildRemoteDraftRequest(options: {
  version_number: number;
  checksum: string;
  semantic_ops: Operation[];
  presentation_ops: RemotePresentationOperation[];
  interaction_ops: RemoteInteractionOperation[];
  message: string;
}): RemoteDraftRequest {
  if (!Number.isInteger(options.version_number) || options.version_number < 1) {
    throw new Error('base_version del head remoto inválido');
  }
  if (!isRemoteChecksum(options.checksum)) {
    throw new Error('checksum del head remoto inválido');
  }
  return {
    base_checksum: options.checksum,
    base_version: options.version_number,
    interaction_ops: options.interaction_ops,
    message: options.message,
    presentation_ops: options.presentation_ops,
    semantic_ops: options.semantic_ops,
  };
}

/** Cuerpo del POST rollback con validación CAS de la base. */
export function buildRemoteRollbackRequest(options: {
  version_number: number;
  checksum: string;
  target_version: number;
  message: string;
}): RemoteRollbackRequest {
  if (!Number.isInteger(options.version_number) || options.version_number < 1) {
    throw new Error('base_version del head remoto inválido');
  }
  if (!Number.isInteger(options.target_version) || options.target_version < 1) {
    throw new Error('target_version inválido');
  }
  if (options.target_version >= options.version_number) {
    throw new Error('El target del rollback debe preceder al head remoto actual');
  }
  if (!isRemoteChecksum(options.checksum)) {
    throw new Error('checksum del head remoto inválido');
  }
  return {
    base_checksum: options.checksum,
    base_version: options.version_number,
    message: options.message,
    target_version: options.target_version,
  };
}

/**
 * Identificador de idempotencia para una mutación remota. El editor reutiliza
 * la MISMA key al reintentar un batch con resultado desconocido (replay seguro).
 */
export function newRemoteIdempotencyKey(): string {
  const cryptoObject = typeof crypto === 'undefined' ? null : crypto;
  if (cryptoObject && typeof cryptoObject.randomUUID === 'function') {
    return cryptoObject.randomUUID();
  }
  return `au-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}
