import React, { useEffect, useMemo, useRef, useState } from 'react';
import { name } from '@gdp-ts/core';
import type { Named } from '@gdp-ts/core';
import { AgentPanel } from './AgentPanel';
import { AuthoringCanvas } from './AuthoringCanvas';
import { FiltersPanel } from './FiltersPanel';
import { InspectorPanel } from './InspectorPanel';
import { authorizeAgentProposal, authorizePublication, authorizeRollback, requestRemotePublication } from '../proofs/authorization';
import type { ProposalApproved, RollbackAuthorized } from '../proofs/authorization';
import { planAgentProposal } from './agent';
import type { AgentSelection } from './agentSettings';
import { ALL_AUTHORING_PERMISSIONS, hasAuthoringPermission } from './permissions';
import type { AuthoringPermission } from './permissions';
import { DEFAULT_CSV_LIMITS, datasetFingerprint, parseLocalCsv, semanticModelForDataset, semanticReplacementOps } from './csv';
import type { LocalDataset } from './csv';
import { saveLocalDataset } from './datasetStorage';
import { diff } from './diff';
import { jsonEqual } from './jsonEqual';
import { addFilter, addMetric, removeFilter, removeMetric, updateFilter } from './operations';
import { createVisual } from './seed';
import { saveDraft } from './storage';
import { presentationFromIR } from './presentationBridge';
import type {
  AgentProposal,
  Document,
  Filter,
  Metric,
  Operation,
  PresentationIR,
  RemoteAuthoringState,
  RemoteDraftRequest,
  RemoteDurableVersion,
  RemotePublicationCandidate,
  RemoteRollbackRequest,
  RemoteSaveState,
  SemanticDiff,
  VisualLayoutSpec,
  VisualOperation,
} from './types';
import {
  addVisual,
  materializeVisuals,
  previewVisualOperations,
  removeVisual,
  updateVisual,
} from './visualOperations';
import {
  applyOps,
  buildRemoteDraftRequest,
  buildRemoteRollbackRequest,
  createPresentation,
  getHeadVersion,
  getPublishedVersion,
  newRemoteIdempotencyKey,
  presentationOpsFromVisualOps,
  remoteBaseFromVersion,
  remoteHeadDocument,
  rollback,
} from './versioning';

export interface RemoteAuthoringConfig {
  /** Origen del servicio (sin barra final), p.ej. "https://dataviz.example.com". */
  endpoint: string;
  projectId: string;
  /** Token opcional; sin él se omite el header Authorization. */
  authToken?: string;
  /** Fetch inyectable para pruebas; por defecto usa globalThis.fetch. */
  fetchImpl?: typeof fetch;
}

export interface VisualAuthoringEditorProps {
  agentEndpoint?: string;
  initialDocument: Document;
  initialVisuals?: VisualLayoutSpec[];
  initialDataset?: LocalDataset | null;
  onDocumentChange?: (doc: Document) => void;
  storageKey?: string;
  permissions?: readonly AuthoringPermission[];
  onRequestPublication?: (version: number) => void;
  onManageConnections?: () => void;
  /** Activa el modo Studio remoto: el servicio es la autoridad durable. */
  remote?: RemoteAuthoringConfig;
}

type WorkspaceView = 'canvas' | 'changes' | 'history';

export const VisualAuthoringEditor: React.FC<VisualAuthoringEditorProps> = ({
  agentEndpoint,
  initialDocument,
  initialVisuals = [],
  initialDataset = null,
  onDocumentChange,
  storageKey,
  permissions = ALL_AUTHORING_PERMISSIONS,
  onRequestPublication,
  onManageConnections,
  remote,
}) => {
  // El modo Studio remoto es la ÚNICA ruta hacia la autoridad durable del
  // servicio; el modo local/demo mantiene todo su comportamiento actual.
  const remoteMode = remote !== undefined;
  const [doc, setDoc] = useState<Document>(() => (
    remoteMode ? initialDocument : withInitialVisuals(initialDocument, initialVisuals)
  ));
  const [dataset, setDataset] = useState<LocalDataset | null>(initialDataset);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [proposal, setProposal] = useState<AgentProposal | null>(null);
  const [agentResponse, setAgentResponse] = useState('');
  const [agentBusy, setAgentBusy] = useState(false);
  const agentRequest = useRef<AbortController | null>(null);
  useEffect(() => () => agentRequest.current?.abort(), []);
  const [view, setView] = useState<WorkspaceView>('canvas');
  const [validationError, setValidationError] = useState('');
  const [storageError, setStorageError] = useState('');
  const [datasetStorageError, setDatasetStorageError] = useState('');
  const [importingCsv, setImportingCsv] = useState(false);
  const [dataPaneCollapsed, setDataPaneCollapsed] = useState(false);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [filtersCollapsed, setFiltersCollapsed] = useState(false);
  const [agentCollapsed, setAgentCollapsed] = useState(false);
  const [pageMenu, setPageMenu] = useState<{ pageId: string; x: number; y: number } | null>(null);
  const pageMenuRef = useRef<HTMLDivElement | null>(null);
  const visualMenuRef = useRef<HTMLDivElement | null>(null);
  const [visualMenu, setVisualMenu] = useState<{ visualId: string; x: number; y: number } | null>(null);
  const [zoom, setZoom] = useState(80);
  const [paneWidths, setPaneWidths] = useState({ agent: 240, data: 240, filters: 240, visual: 252 });
  const canvasViewportRef = useRef<HTMLDivElement | null>(null);
  const [renamingPage, setRenamingPage] = useState<{ id: string; value: string } | null>(null);
  const canConsume = hasAuthoringPermission(permissions, 'consume');
  const canAuthor = hasAuthoringPermission(permissions, 'author');
  const canRequestPublish = hasAuthoringPermission(permissions, 'request_publish');
  const canRevert = hasAuthoringPermission(permissions, 'revert');
  const canManageConnections = hasAuthoringPermission(permissions, 'manage_connections');
  // Guard síncrono contra la carrera del await de handleCsvImport: bloquea toda mutación
  // del documento mientras el archivo se lee, para que el import no sobrescriba versiones.
  const importBusyRef = React.useRef(false);

  // ---------------------------------------------------------------------------
  // Estado del modo Studio remoto — el servicio es la autoridad CAS. El "ready"
  // es la ÚNICA puerta de entrada para mutaciones (commit / rollback).
  // ---------------------------------------------------------------------------
  const [remoteHead, setRemoteHead] = useState<{ version_number: number; checksum: string } | null>(null);
  const [remoteVersionId, setRemoteVersionId] = useState<string | null>(null);
  const [remoteState, setRemoteState] = useState<RemoteSaveState>(remoteMode ? 'loading' : 'ready');
  const [remoteError, setRemoteError] = useState('');
  const [remoteNotice, setRemoteNotice] = useState('');
  const [remotePublished, setRemotePublished] = useState<number | null>(null);
  const [requestingPublication, setRequestingPublication] = useState(false);
  const pendingRemote = React.useRef<{
    kind: 'draft' | 'rollback';
    request: RemoteDraftRequest | RemoteRollbackRequest;
    next: Document | null;
    idempotencyKey: string;
  } | null>(null);

  const remoteSaveLabel: Record<RemoteSaveState, string> = {
    loading: 'Cargando proyecto remoto…',
    ready: 'Guardado en el servicio',
    saving: 'Guardando en el servicio…',
    'server-error': 'Error del servicio',
    'stale-conflict': 'Conflicto de versión',
  };

  /** Transporte HTTP al servicio; inyectable vía fetchImpl para pruebas. */
  const remoteFetch = async (path: string, init: RequestInit = {}): Promise<Response> => {
    if (!remote) {throw new Error('modo remoto no configurado');}
    const impl = remote.fetchImpl ?? globalThis.fetch;
    if (typeof impl !== 'function') {
      throw new TypeError('fetch no disponible en este entorno');
    }
    const headers = new Headers(init.headers);
    if (!headers.has('Content-Type')) {headers.set('Content-Type', 'application/json');}
    if (remote.authToken) {headers.set('Authorization', `Bearer ${remote.authToken}`);}
    const base = remote.endpoint.replace(/\/+$/, '');
    return impl(`${base}${path}`, { ...init, headers }) as Promise<Response>;
  };

  /** Carga el head durable desde el servicio. Sin flags: muestra loading. conflict=true: deja estado stale-conflict. quiet=true: no cambia el estado y devuelve null si falla. */
  const loadRemoteProject = async (conflict: boolean, quiet = false): Promise<Document | null> => {
    if (!remoteMode || !remote) {return null;}
    if (!conflict && !quiet) {
      setRemoteState('loading');
      setRemoteError('');
    }
    try {
      const response = await remoteFetch(`/api/projects/${encodeURIComponent(remote.projectId)}/authoring`);
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const errorCode = body && typeof body === 'object' && 'error' in body
          ? String((body as { error: unknown }).error)
          : '';
        throw new Error(errorCode
          ? `El servicio rechazó la carga del proyecto (${errorCode}).`
          : `El servicio rechazó la carga del proyecto (HTTP ${response.status}).`);
      }
      const state = body as RemoteAuthoringState;
      const loaded = remoteHeadDocument(state);
      setDoc(loaded);
      setRemoteHead({ checksum: state.head.checksum, version_number: state.head.version_number });
      setRemoteVersionId(state.head.id);
      setRemotePublished(state.published_version);
      if (!quiet) {
        setSelectedId(null);
        setProposal(null);
        pendingRemote.current = null;
      }
      setRemoteState(conflict ? 'stale-conflict' : 'ready');
      setRemoteNotice(conflict
        ? 'El proyecto cambió en el servicio; se cargó la versión vigente y tus cambios pendientes se descartaron.'
        : '');
      setValidationError('');
      return loaded;
    } catch (error: unknown) {
      if (!quiet) {
        setRemoteError(error instanceof Error ? error.message : 'No se pudo cargar el proyecto remoto.');
        setRemoteState('server-error');
      }
      return null;
    }
  };

  /** Persiste un batch local (draft o rollback) en el servicio. En 409 recarga y descarta. */
  const persistRemoteMutation = async (batch: NonNullable<typeof pendingRemote.current>): Promise<void> => {
    if (!remoteMode || !remote) {return;}
    try {
      const path = batch.kind === 'draft'
        ? `/api/projects/${encodeURIComponent(remote.projectId)}/authoring/drafts`
        : `/api/projects/${encodeURIComponent(remote.projectId)}/authoring/rollback`;
      const response = await remoteFetch(path, {
        body: JSON.stringify(batch.request),
        headers: { 'Idempotency-Key': batch.idempotencyKey },
        method: 'POST',
      });
      const body: unknown = await response.json().catch(() => null);
      if (response.ok) {
        const version = body as RemoteDurableVersion;
        setRemoteHead(remoteBaseFromVersion(version));
        setRemoteVersionId(version.id);
        pendingRemote.current = null;
        setValidationError('');
        setRemoteError('');
        setRemoteNotice('');
        setRemoteState('saving');
        // LUNA1 P1: el 201 solo confirma la fila durable. Tras cada POST remoto se
        // recarga el head canónico y se adopta el documento del servicio; nunca se
        // promueve el batch local como verdad durable. Queda en 'saving' hasta que
        // aterriza la recarga: ningún commit puede entrar en la ventana de carrera.
        const canonical = await loadRemoteProject(false, true);
        if (!canonical) {setRemoteState('ready');}
        onDocumentChange?.(canonical ?? batch.next ?? doc);
        return;
      }
      const errorCode = body && typeof body === 'object' && 'error' in body
        ? String((body as { error: unknown }).error)
        : '';
      if (response.status === 409) {
        pendingRemote.current = null;
        await loadRemoteProject(true);
        return;
      }
      setRemoteError(errorCode
        ? `El servicio rechazó el cambio (${errorCode}).`
        : `El servicio rechazó el cambio (HTTP ${response.status}).`);
      setRemoteState('server-error');
    } catch (error: unknown) {
      setRemoteError(error instanceof Error ? error.message : 'No se pudo contactar el servicio remoto.');
      setRemoteState('server-error');
    }
  };

  /** Reintenta el batch pendiente con la MISMA Idempotency-Key (replay seguro ante respuesta perdida). */
  const retryPendingMutation = () => {
    const batch = pendingRemote.current;
    if (!batch) {return;}
    setRemoteError('');
    setRemoteState('saving');
    void persistRemoteMutation(batch);
  };

  useEffect(() => {
    if (!remoteMode) {return;}
    void loadRemoteProject(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [remoteMode, remote?.endpoint, remote?.projectId, remote?.authToken]);

  const head = getHeadVersion(doc);
  const currentPages = head.presentation_ir.pages;
  const [activePageId, setActivePageId] = useState<string | null>(null);
  const effectivePageId = (activePageId && currentPages.some((p) => p.page_id === activePageId))
    ? activePageId
    : (currentPages[0]?.page_id ?? 'canvas');
  const activePresentation = useMemo(
    () => presentationFromIR(head.presentation_ir, effectivePageId),
    [head.presentation_ir, effectivePageId],
  );

  const headFingerprint = head.dataset_fingerprint ?? null;
  const activeDataset = dataset && headFingerprint !== null && datasetFingerprint(dataset) === headFingerprint
    ? dataset
    : null;
  const datasetMissing = headFingerprint !== null && !activeDataset;
  const published = getPublishedVersion(doc);
  const selected = activePresentation.visuals.find((visual) => visual.id === selectedId) ?? null;
  useEffect(() => {
    if (!agentRequest.current) {return;}
    agentRequest.current.abort();
    agentRequest.current = null;
    setAgentBusy(false);
    setAgentResponse('El dashboard cambió durante la consulta. Vuelve a enviar tu solicitud con la versión actual.');
  }, [head.number, effectivePageId]);
  const preview = useMemo(() => {
    if (!proposal) {return { presentation: activePresentation, error: '' };}
    try {
      return { error: '', presentation: materializeVisuals(activePresentation, proposal.visual_ops) };
    } catch (error: unknown) {
      return {
        error: error instanceof Error ? error.message : 'Error desconocido',
        presentation: activePresentation,
      };
    }
  }, [activePresentation, proposal]);
  const currentDiff: SemanticDiff = published
    ? diff(published.model, head.model)
    : head.number > 1 && doc.versions.length > 1
      ? diff(doc.versions[doc.versions.length - 2].model, head.model)
      : diff(head.model, head.model);
  const presentationDiff = published
    ? diffPresentations(
        presentationFromIR(published.presentation_ir, effectivePageId).visuals,
        activePresentation.visuals,
      )
    : [];

  useEffect(() => {
    const title = head.presentation_ir.title || head.model.name;
    document.title = `${title} · DataVIZ Studio`;
  }, [head.presentation_ir.title, head.model.name]);

  useEffect(() => {
    if (remoteMode || !storageKey || typeof window === 'undefined') {return;}
    try {
      saveDraft(window.localStorage, storageKey, doc);
      setStorageError('');
    } catch (error: unknown) {
      const detail = error instanceof Error ? ` ${error.message}` : '';
      setStorageError(`El cambio sigue abierto, pero no se pudo guardar localmente.${detail}`);
    }
  }, [doc, storageKey, remoteMode]);

  useEffect(() => {
    if (remoteMode || !storageKey || !dataset || typeof window === 'undefined') {return;}
    try {
      saveLocalDataset(window.localStorage, `${storageKey}.dataset`, dataset);
      setDatasetStorageError('');
    } catch (error: unknown) {
      const detail = error instanceof Error ? ` ${error.message}` : '';
      setDatasetStorageError(`Los datos siguen abiertos, pero no se pudieron guardar localmente.${detail}`);
    }
  }, [dataset, storageKey, remoteMode]);

  const commit = (
    semanticOps: Operation[],
    visualOps: VisualOperation[],
    message: string,
    applyingProposal = false,
  ): boolean => {
    if (!canAuthor) {
      setValidationError('No tienes permiso para crear o modificar este proyecto.');
      return false;
    }
    if (proposal && !applyingProposal) {
      setValidationError('Aplica o rechaza la propuesta del agente antes de editar manualmente.');
      return false;
    }
    if (importBusyRef.current) {
      setValidationError('Espera a que termine la importación de datos antes de editar.');
      return false;
    }

    // --- Modo remoto: la autoridad CAS vive en el servicio ---
    if (remoteMode) {
      if (remoteState !== 'ready') {
        setValidationError(
          remoteState === 'saving'
            ? 'Espera a que el servicio confirme el guardado actual.'
            : remoteState === 'loading'
              ? 'El proyecto remoto todavía se está cargando.'
              : remoteState === 'stale-conflict'
                ? 'El proyecto cambió en el servicio; usa la versión vigente antes de editar.'
                : 'Resuelve el error del servicio antes de seguir editando.',
        );
        return false;
      }
      if (!remoteHead) {
        setValidationError('El proyecto remoto no tiene un head durable cargado.');
        return false;
      }
      try {
        const presentation = materializeVisuals(activePresentation, visualOps);
        const next = applyOps(doc, semanticOps, {
          activePageId: effectivePageId,
          message,
          presentation,
        });
        const nextHead = getHeadVersion(next);
        const presentationOps = presentationOpsFromVisualOps(
          nextHead.presentation_ir,
          visualOps,
          effectivePageId,
        );
        const request = buildRemoteDraftRequest({
          checksum: remoteHead.checksum,
          interaction_ops: [],
          message,
          presentation_ops: presentationOps,
          semantic_ops: semanticOps,
          version_number: remoteHead.version_number,
        });
        pendingRemote.current = {
          idempotencyKey: newRemoteIdempotencyKey(),
          kind: 'draft',
          next,
          request,
        };
        setDoc(next);
        setProposal(null);
        setValidationError('');
        setRemoteError('');
        setRemoteState('saving');
        void persistRemoteMutation(pendingRemote.current);
        return true;
      } catch (error: unknown) {
        setValidationError(error instanceof Error ? error.message : String(error));
        return false;
      }
    }

    // --- Modo local/demo: comportamiento original (aprobación local instantánea) ---
    try {
      const presentation = materializeVisuals(activePresentation, visualOps);
      const next = applyOps(doc, semanticOps, {
        activePageId: effectivePageId,
        message,
        presentation,
      });
      setDoc(next);
      setProposal(null);
      setValidationError('');
      onDocumentChange?.(next);
      return true;
    } catch (error: unknown) {
      setValidationError(error instanceof Error ? error.message : String(error));
      return false;
    }
  };

  const handleAddVisual = (kind: string) => {
    const binding = defaultBindingForModel(kind, head.model);
    const visual = createVisual(kind, activePresentation.visuals.length, {
      data_roles: binding.dataRoles,
      id: uniqueVisualId(activePresentation.visuals),
      title: activeDataset ? binding.title : undefined,
    });
    if (commit([], [addVisual(visual)], `Agregar ${visual.title}`)) {
      setSelectedId(visual.id);
      setView('canvas');
      focusVisual(visual.id);
    }
  };

  const handleCsvImport = async (file: File | undefined) => {
    if (!file) {return;}
    if (remoteMode) {
      setValidationError('El import CSV local está desactivado en modo Studio remoto; el modelo del servicio es la autoridad.');
      return;
    }
    if (proposal) {
      setValidationError('Aplica o rechaza la propuesta del agente antes de importar datos.');
      return;
    }
    // Rechazar por tamaño ANTES de materializar el archivo en memoria: file.text() cargaría
    // el archivo completo aunque el parser lo rechace después.
    if (file.size > DEFAULT_CSV_LIMITS.maxBytes) {
      setValidationError(
        `El CSV pesa ${Math.round(file.size / (1024 * 1024))} MiB y supera el límite de 2 MiB.`,
      );
      return;
    }
    if (importBusyRef.current) {return;}
    importBusyRef.current = true;
    setImportingCsv(true);
    try {
      const parsed = parseLocalCsv(await file.text(), file.name);
      const model = semanticModelForDataset(parsed);
      const semanticOps = semanticReplacementOps(head.model, model);
      const visualOps = head.presentation.visuals.map((visual) => removeVisual(visual.id));
      const presentation = materializeVisuals(head.presentation, visualOps);
      const next = applyOps(doc, semanticOps, {
        datasetFingerprint: datasetFingerprint(parsed),
        message: `Importar CSV local ${parsed.source_filename}`,
        modelMetadata: {
          description: model.description,
          name: model.name,
          schema_version: model.schema_version,
        },
        presentation,
      });
      setDoc(next);
      setDataset(parsed);
      setProposal(null);
      setSelectedId(null);
      setValidationError('');
      setView('canvas');
      onDocumentChange?.(next);
    } catch (error: unknown) {
      setValidationError(error instanceof Error ? error.message : 'No se pudo importar el CSV.');
    } finally {
      importBusyRef.current = false;
      setImportingCsv(false);
    }
  };

  const handleUpdateVisual = (visual: VisualLayoutSpec, message: string) => {
    commit([], [updateVisual(visual)], message);
  };

  /** Cambios de estructura de páginas: versiona el PresentationIR completo. */
  const commitPresentationIR = (presentationIR: PresentationIR, message: string): boolean => {
    if (!canAuthor) {
      setValidationError('No tienes permiso para crear o modificar este proyecto.');
      return false;
    }
    if (remoteMode) {
      setValidationError('La gestión de páginas aún no se sincroniza con el servicio remoto.');
      return false;
    }
    if (proposal) {
      setValidationError('Aplica o rechaza la propuesta del agente antes de editar manualmente.');
      return false;
    }
    try {
      const next = applyOps(doc, [], { activePageId: effectivePageId, message, presentation_ir: presentationIR });
      setDoc(next);
      setProposal(null);
      setValidationError('');
      onDocumentChange?.(next);
      return true;
    } catch (error: unknown) {
      setValidationError(error instanceof Error ? error.message : String(error));
      return false;
    }
  };

  const beginPaneResize = (event: React.PointerEvent<HTMLDivElement>, key: keyof typeof paneWidths) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = paneWidths[key];
    const onMove = (moveEvent: PointerEvent) => {
      const width = Math.min(480, Math.max(190, Math.round(startWidth + (moveEvent.clientX - startX))));
      setPaneWidths((current) => ({ ...current, [key]: width }));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  };

  /** Ajusta el zoom para que el lienzo quepa en el viewport disponible. */
  const fitToCanvas = () => {
    const node = canvasViewportRef.current;
    if (!node) {return;}
    const { clientWidth, clientHeight } = node;
    if (clientWidth <= 0 || clientHeight <= 0) {return;}
    const { width, height } = activePresentation.canvas;
    const fit = Math.min((clientWidth - 30) / width, (clientHeight - 30) / height) * 100;
    setZoom(Math.round(Math.min(200, Math.max(30, fit))));
  };

  useEffect(() => {
    const menu = pageMenu ?? visualMenu;
    if (!menu) {return;}
    // Patrón ARIA menu: el foco entra al primer ítem hábil y vuelve al disparador al cerrar.
    const opener = document.activeElement as HTMLElement | null;
    (pageMenu ? pageMenuRef.current : visualMenuRef.current)
      ?.querySelector<HTMLElement>('button[role="menuitem"]:not([disabled])')?.focus();
    const close = () => setPageMenu(null);
    const closeVisual = () => setVisualMenu(null);
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {return;}
      setPageMenu(null);
      setVisualMenu(null);
    };
    window.addEventListener('click', close);
    window.addEventListener('click', closeVisual);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('click', close);
      window.removeEventListener('click', closeVisual);
      window.removeEventListener('keydown', onKey);
      opener?.focus?.();
    };
  }, [pageMenu, visualMenu]);

  const handleAddPage = () => {
    const ir = head.presentation_ir;
    const base = ir.pages[ir.pages.length - 1];
    if (!base) {return;}
    const pageId = `page-${Date.now().toString(36)}`;
    const displayName = `Página ${ir.pages.length + 1}`;
    const nextIR: PresentationIR = {
      ...ir,
      pages: [...ir.pages, { ...base, display_name: displayName, name: pageId, page_id: pageId, properties: {}, visuals: [] }],
    };
    if (commitPresentationIR(nextIR, `Agregar página ${displayName}`)) {
      setActivePageId(pageId);
      setSelectedId(null);
    }
  };

  const handleDuplicatePage = (pageId: string) => {
    const ir = head.presentation_ir;
    const source = ir.pages.find((page) => page.page_id === pageId);
    if (!source) {return;}
    const newId = `page-${Date.now().toString(36)}`;
    const nextIR: PresentationIR = {
      ...ir,
      pages: [...ir.pages, { ...source, display_name: `${source.display_name} (copia)`, name: newId, page_id: newId, properties: { ...source.properties } }],
    };
    if (commitPresentationIR(nextIR, `Duplicar página ${source.display_name}`)) {
      setActivePageId(newId);
      setSelectedId(null);
    }
  };

  const handleDeletePage = (pageId: string) => {
    const ir = head.presentation_ir;
    if (ir.pages.length <= 1) {
      setValidationError('Debe quedar al menos una página en el dashboard.');
      return;
    }
    const page = ir.pages.find((item) => item.page_id === pageId);
    const nextIR: PresentationIR = { ...ir, pages: ir.pages.filter((item) => item.page_id !== pageId) };
    if (commitPresentationIR(nextIR, `Eliminar página ${page?.display_name ?? pageId}`)) {
      if (activePageId === pageId) {setActivePageId(null);}
      setSelectedId(null);
    }
  };

  const handleRenamePage = (pageId: string, displayName: string) => {
    const ir = head.presentation_ir;
    const nextIR: PresentationIR = {
      ...ir,
      pages: ir.pages.map((page) => (page.page_id === pageId ? { ...page, display_name: displayName } : page)),
    };
    commitPresentationIR(nextIR, `Renombrar página a ${displayName}`);
  };

  const handleDuplicateVisual = (visual: VisualLayoutSpec) => {
    const copy: VisualLayoutSpec = {
      ...visual,
      geometry: {
        ...visual.geometry,
        x: Math.max(0, Math.min(visual.geometry.x + 28, activePresentation.canvas.width - visual.geometry.width)),
        y: Math.max(0, Math.min(visual.geometry.y + 28, activePresentation.canvas.height - visual.geometry.height)),
      },
      id: uniqueVisualId(activePresentation.visuals),
      name: `${visual.name} copia`,
      title: `${visual.title} copia`,
    };
    if (commit([], [addVisual(copy)], `Duplicar ${visual.title}`)) {setSelectedId(copy.id);}
  };

  const handleRemove = () => {
    if (!selected) {return;}
    if (commit([], [removeVisual(selected.id)], `Eliminar ${selected.title}`)) {setSelectedId(null);}
  };

  const handleRemoveForId = (visualId: string) => {
    const visual = activePresentation.visuals.find((item) => item.id === visualId);
    if (!visual) {return;}
    if (commit([], [removeVisual(visual.id)], `Eliminar ${visual.title}`)) {setSelectedId(null);}
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    const isDelete = event.key === 'Delete';
    const isArrow = event.key.startsWith('Arrow');
    if (
      (!isDelete && !isArrow) || event.defaultPrevented || event.nativeEvent.isComposing
      || event.ctrlKey || event.metaKey || event.altKey
      || (isDelete && (event.shiftKey || event.repeat))
      || (isArrow && event.repeat)
      || view !== 'canvas' || !selected || !canAuthor || proposal
    ) {return;}
    if (event.target instanceof Element && event.target.closest(
      'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="spinbutton"], [role="slider"], [role="dialog"], dialog',
    )) {return;}
    if (isDelete) {
      event.preventDefault();
      handleRemove();
      return;
    }
    // Nudges estilo Power BI: flechas mueven el visual, Shift+flechas en fino.
    const step = event.shiftKey ? 1 : 8;
    const { width: canvasWidth, height: canvasHeight } = activePresentation.canvas;
    const { x, y, width, height } = selected.geometry;
    const dx = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
    const dy = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
    event.preventDefault();
    handleUpdateVisual({
      ...selected,
      geometry: {
        height,
        width,
        x: Math.min(Math.max(x + dx, 0), canvasWidth - width),
        y: Math.min(Math.max(y + dy, 0), canvasHeight - height),
      },
    }, `Mover ${selected.title}`);
  };

  const handlePlan = (prompt: string, selection?: AgentSelection): boolean | Promise<boolean> => {
    if (agentRequest.current) {return false;}
    if (importBusyRef.current) {
      setValidationError('Espera a que termine la importación de datos antes de pedir propuestas.');
      return false;
    }
    const accept = (planned: AgentProposal) => {
      const hasChanges = planned.visual_ops.length > 0 || planned.semantic_ops.length > 0;
      setProposal(hasChanges ? planned : null);
      setAgentResponse(hasChanges ? '' : planned.summary);
      setValidationError('');
      if (hasChanges) {setView('canvas');}
      return true;
    };
    if (selection && selection.model !== 'local') {
      if (!agentEndpoint || remoteMode || !effectivePageId) {
        setValidationError('La conexión de IA requiere el editor local y su servicio de asistente.');
        return false;
      }
      const controller = new AbortController();
      agentRequest.current = controller;
      setAgentBusy(true);
      setAgentResponse('');
      setValidationError('');
      return import('./agentClient')
        .then(({ requestAgentProposal }) => {
          if (controller.signal.aborted) {throw new DOMException('Solicitud cancelada', 'AbortError');}
          return requestAgentProposal(agentEndpoint, doc, prompt, selection, effectivePageId, selectedId, controller.signal);
        })
        .then((planned) => controller.signal.aborted ? false : accept(planned))
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {setValidationError(error instanceof Error ? error.message : 'No se pudo consultar a la IA.');}
          return false;
        })
        .finally(() => {
          if (agentRequest.current === controller) {
            agentRequest.current = null;
            setAgentBusy(false);
          }
        });
    }
    try {
      return accept(planAgentProposal(doc, prompt));
    } catch (error: unknown) {
      setValidationError(error instanceof Error ? error.message : 'No se pudo construir la propuesta.');
      return false;
    }
  };

  const handleApplyProposal = () => {
    if (remoteMode) {
      setValidationError('En modo Studio remoto las propuestas del agente se planifican y previsualizan, pero no se aplican directamente.');
      return;
    }
    if (!proposal) {return;}
    name(proposal, head.number, (namedProposal, headVersion) => {
      const approved = authorizeAgentProposal(namedProposal, headVersion, {
        previewError: preview.error,
        remoteMode,
      });
      if (!approved.proof) {
        setValidationError(approved.error);
        return;
      }
      commitApprovedProposal(namedProposal, headVersion, approved.proof);
    });
  };

  /** Único camino al commit con applyingProposal=true: exige proof GDP. */
  function commitApprovedProposal<P, V>(
    proposal: Named<P, AgentProposal>,
    _head: Named<V, number>,
    _proof: ProposalApproved<P, V>,
  ): void {
    commit(proposal.value.semantic_ops, proposal.value.visual_ops, agentCommitMessage(proposal.value.prompt), true);
  }

  const handleRequestPublication = async () => {
    if (remoteMode) {
      if (remoteState !== 'ready') {
        setValidationError('Espera a que el modo remoto esté listo para solicitar publicación.');
        return;
      }
      if (!remoteHead || !remote) {return;}
      if (requestingPublication) {return;}
      setRequestingPublication(true);
      setRemoteError('');
      try {
        await name(remoteHead.version_number, async (targetVersion) => {
          const authorized = authorizePublication(targetVersion, {
            canRequestPublish,
            datasetMissing,
            hasPendingProposal: Boolean(proposal),
            hasPublicationHook: Boolean(onRequestPublication),
            importBusy: importBusyRef.current,
            mode: 'remote',
            remoteReady: remoteState === 'ready',
          });
          if (!authorized.proof) {
            setValidationError(authorized.error);
            return;
          }
          const outcome = await requestRemotePublication(
            { fetchImpl: remoteFetch, projectId: remote.projectId },
            targetVersion,
            authorized.proof,
          );
          const body = outcome.body;
          if (!outcome.ok) {
            const errorCode = body && typeof body === 'object' && 'error' in body
              ? String((body as { error: unknown }).error)
              : '';
            setRemoteError(errorCode
              ? `No se pudo solicitar la publicación (${errorCode}).`
              : `No se pudo solicitar la publicación (HTTP ${outcome.status}).`);
            return;
          }
          const candidate = body as RemotePublicationCandidate;
          setValidationError('');
          setRemoteNotice(
            `Publicación solicitada para v${candidate.target_version} en el servicio. Candidato ${candidate.id.slice(0, 8)} pendiente de aprobación.`,
          );
        });
      } catch (error: unknown) {
        setRemoteError(error instanceof Error ? error.message : 'No se pudo contactar el servicio remoto.');
      } finally {
        setRequestingPublication(false);
      }
      return;
    }

    // --- Modo local/demo: comportamiento original ---
    name(head.number, (targetVersion) => {
      const authorized = authorizePublication(targetVersion, {
        canRequestPublish,
        datasetMissing,
        hasPendingProposal: Boolean(proposal),
        hasPublicationHook: Boolean(onRequestPublication),
        importBusy: importBusyRef.current,
        mode: 'local',
        remoteReady: false,
      });
      if (!authorized.proof) {
        setValidationError(authorized.error);
        return;
      }
      onRequestPublication?.(targetVersion.value);
      setValidationError('');
    });
  };

  const handleRollback = (version: number) => {
    name(version, (target) => {
      const authorized = authorizeRollback(target, {
        canRevert,
        hasRemoteHead: Boolean(remoteHead),
        importBusy: importBusyRef.current,
        remoteMode,
        remoteReady: remoteState === 'ready',
      });
      if (!authorized.proof) {
        setValidationError(authorized.error);
        return;
      }
      executeRollback(target, authorized.proof);
    });
  };

  /** Único camino al rollback del documento: exige proof GDP sobre la versión. */
  function executeRollback<V>(target: Named<V, number>, _proof: RollbackAuthorized<V>): void {
    const version = target.value;
    // --- Modo remoto: rollback durable ---
    if (remoteMode) {
      if (!remoteHead) {return;}
      try {
        let next: Document | null = null;
        try {
          next = rollback(doc, version, { message: `Restaurar v${version}` });
        } catch {
          next = null;
        }
        const request = buildRemoteRollbackRequest({
          checksum: remoteHead.checksum,
          message: `Restaurar v${version}`,
          target_version: version,
          version_number: remoteHead.version_number,
        });
        pendingRemote.current = {
          idempotencyKey: newRemoteIdempotencyKey(),
          kind: 'rollback',
          next,
          request,
        };
        if (next) {setDoc(next);}
        setProposal(null);
        setSelectedId(null);
        setValidationError('');
        setRemoteError('');
        setRemoteState('saving');
        void persistRemoteMutation(pendingRemote.current);
      } catch (error: unknown) {
        setValidationError(error instanceof Error ? error.message : String(error));
      }
      return;
    }

    // --- Modo local/demo: comportamiento original (aprobación local instantánea) ---
    const next = rollback(doc, version, { message: `Restaurar v${version}` });
    setDoc(next);
    setProposal(null);
    setSelectedId(null);
    onDocumentChange?.(next);
  }

  return (
    <main className="dv-authoring-shell" data-testid="authoring-editor-root" id="authoring-editor-root" onKeyDown={handleKeyDown}>
      <h1 className="dv-sr-only">{head.model.name} · DataVIZ Studio</h1>
      <header className="dv-studio-header">
        <div className="dv-studio-brand"><span aria-hidden="true">DV</span></div>
        <nav className="dv-header-nav" aria-label="Vistas del proyecto">
          <button aria-current={view === 'canvas' ? 'page' : undefined} onClick={() => setView('canvas')} type="button">
            <svg aria-hidden="true" className="dv-rail-glyph" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.7} viewBox="0 0 24 24">
              <rect height="8" rx="1.5" width="7" x="3" y="3" /><rect height="5" rx="1.5" width="7" x="14" y="3" /><rect height="9" rx="1.5" width="7" x="14" y="12" /><rect height="6" rx="1.5" width="7" x="3" y="15" />
            </svg>
            <span>Canvas</span>
          </button>
          <button aria-current={view === 'changes' ? 'page' : undefined} aria-label={`Cambios ${currentDiff.changes.length + presentationDiff.length}`} onClick={() => setView('changes')} type="button">
            <svg aria-hidden="true" className="dv-rail-glyph" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.7} viewBox="0 0 24 24">
              <path d="M12 5v6M9 8h6" /><path d="M9 17h6" />
            </svg>
            <span>Cambios</span>
            <b>{currentDiff.changes.length + presentationDiff.length}</b>
          </button>
          <button aria-current={view === 'history' ? 'page' : undefined} aria-label={`Versiones ${doc.versions.length}`} onClick={() => setView('history')} type="button">
            <svg aria-hidden="true" className="dv-rail-glyph" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.7} viewBox="0 0 24 24">
              <path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1L3.5 8.4" /><path d="M3.5 3.5v4.9h4.9" /><path d="M12 7.5V12l3 3" />
            </svg>
            <span>Versiones</span>
            <b>{doc.versions.length}</b>
          </button>
        </nav>
        <div className="dv-document-title">
          <span className="dv-breadcrumb">Proyectos /</span>
          <strong>{(head.presentation_ir.title || head.model.name).replaceAll('_', ' ')}</strong>
          <span className="dv-save-state" data-testid={remoteMode ? 'remote-save-state' : undefined}><i /> {remoteMode ? remoteSaveLabel[remoteState] : 'Guardado local'}</span>
        </div>
        <div className="dv-header-actions">
          {(onRequestPublication || remoteMode)
            ? <button className="dv-button-primary" disabled={!canRequestPublish || Boolean(proposal) || datasetMissing || (remoteMode && (remoteState !== 'ready' || requestingPublication))} onClick={handleRequestPublication} type="button">Solicitar publicación v{head.number}</button>
            : <details className="dv-publication-help">
                <summary>Publicación no disponible</summary>
                <p>Este borrador se guarda en tu navegador. Para compartirlo con otras personas, abre un proyecto conectado al servicio y solicita su publicación. «Vista» permite explorarlo aquí.</p>
              </details>}
          {onManageConnections && <button disabled={!canManageConnections} onClick={onManageConnections} type="button">Conexiones</button>}
          {canConsume ? <a className="dv-view-link" href={viewerHref(head.number, remote, { publishedVersion: remotePublished, versionId: remoteVersionId, versionNumber: remoteHead?.version_number })} title="Abrir runtime de consumo">Vista</a> : <span aria-disabled="true" className="dv-view-link">Vista</span>}
        </div>
      </header>

      {validationError && <div className="dv-validation-error" role="alert"><strong>No se aplicó el cambio.</strong> {validationError}<button aria-label="Cerrar error" onClick={() => setValidationError('')} type="button">×</button></div>}
      {storageError && <div className="dv-validation-error" role="alert"><strong>Borrador sin guardar.</strong> {storageError}<button aria-label="Cerrar error de guardado" onClick={() => setStorageError('')} type="button">×</button></div>}
      {datasetStorageError && <div className="dv-validation-error" role="alert"><strong>Datos sin guardar.</strong> {datasetStorageError}<button aria-label="Cerrar error de datos" onClick={() => setDatasetStorageError('')} type="button">×</button></div>}
      {datasetMissing && <div className="dv-validation-error" role="alert" data-testid="dataset-missing-banner"><strong>Datos importados no disponibles.</strong> Esta versión se creó desde un CSV que no coincide con los datos cargados; las vistas usan datos de muestra. Reimporta el CSV antes de solicitar publicación.</div>}
      {remoteMode && remoteError && (
        <div className="dv-validation-error" role="alert" data-testid="remote-error-banner">
          <strong>Studio remoto.</strong> {remoteError}
          {remoteState === 'server-error' && pendingRemote.current && (
            <>
              {' '}<button onClick={retryPendingMutation} type="button">Reintentar sincronización</button>
              {' '}<button onClick={() => { pendingRemote.current = null; void loadRemoteProject(false); }} type="button">Recargar desde el servicio</button>
            </>
          )}
          <button aria-label="Cerrar error remoto" onClick={() => setRemoteError('')} type="button">×</button>
        </div>
      )}
      {remoteMode && remoteState === 'stale-conflict' && (
        <div className="dv-validation-error" role="alert" data-testid="remote-stale-banner">
          <strong>Conflicto de versión.</strong> {remoteNotice}
          <button onClick={() => { setRemoteState('ready'); setRemoteNotice(''); }} type="button">Usar versión vigente</button>
        </div>
      )}
      {remoteMode && remoteState === 'ready' && remoteNotice && (
        <div className="dv-validation-error" role="alert" data-testid="remote-notice-banner">
          <strong>Studio remoto.</strong> {remoteNotice}
          <button aria-label="Cerrar aviso remoto" onClick={() => setRemoteNotice('')} type="button">×</button>
        </div>
      )}

      <div className="dv-studio-shell">
        <div className="dv-studio-main">
          {view === 'canvas' && (
            <AuthoringCanvas
              canEdit={canAuthor && !proposal}
              dataset={activeDataset}
              model={head.model}
              onAdd={canAuthor ? handleAddVisual : () => setValidationError('No tienes permiso para crear o modificar este proyecto.')}
              onResize={handleUpdateVisual}
              onMove={handleUpdateVisual}
              onDeselect={() => setSelectedId(null)}
              onVisualContextMenu={(x, y, visualId) => setVisualMenu({ visualId, x, y })}
              onZoomChange={setZoom}
              viewportRef={canvasViewportRef}
              zoom={zoom}
              onSelect={setSelectedId}
              presentation={preview.presentation}
              proposalPreview={Boolean(proposal)}
              selectedId={selectedId}
            />
          )}
          {view === 'changes' && <ChangesView currentDiff={currentDiff} presentationChanges={presentationDiff} />}
          {view === 'history' && <HistoryView canRevert={canRevert} document={doc} headNumber={head.number} onRollback={handleRollback} />}
        </div>

        <div
          className="dv-right-panes"
          style={{
            '--w-agent': `${paneWidths.agent}px`,
            '--w-data': `${paneWidths.data}px`,
            '--w-filters': `${paneWidths.filters}px`,
            '--w-visual': `${paneWidths.visual}px`,
          } as React.CSSProperties}
        >
          {!inspectorCollapsed && <div aria-hidden="true" className="dv-pane-resizer" onPointerDown={(event) => beginPaneResize(event, 'visual')} title="Arrastra para ajustar el ancho" />}
          {inspectorCollapsed ? (
            <div
              aria-label="Expandir visualizaciones"
              className="dv-side-strip"
              onClick={() => setInspectorCollapsed(false)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') {setInspectorCollapsed(false);} }}
              role="button"
              tabIndex={0}
            >
              <span aria-hidden="true" className="dv-side-strip-label">Visualizaciones</span>
            </div>
          ) : (
            <InspectorPanel
              canvas={activePresentation.canvas}
              model={head.model}
              onAddFilter={(filter: Filter) => commit([addFilter(filter)], [], `Crear filtro ${filter.name}`)}
              onAddMetric={(metric: Metric) => commit([addMetric(metric)], [], `Crear métrica ${metric.name}`)}
              onAddVisual={canAuthor ? handleAddVisual : () => setValidationError('No tienes permiso para crear o modificar este proyecto.')}
              onCollapse={() => setInspectorCollapsed(true)}
              onRemoveFilter={(name) => commit([removeFilter(name)], [], `Eliminar filtro ${name}`)}
              onRemoveMetric={(name) => commit([removeMetric(name)], [], `Eliminar métrica ${name}`)}
              onUpdateVisual={handleUpdateVisual}
              selected={selected}
            />
          )}

          {!filtersCollapsed && <div aria-hidden="true" className="dv-pane-resizer" onPointerDown={(event) => beginPaneResize(event, 'filters')} title="Arrastra para ajustar el ancho" />}
          {filtersCollapsed ? (
            <div
              aria-label="Expandir filtros"
              className="dv-side-strip"
              onClick={() => setFiltersCollapsed(false)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') {setFiltersCollapsed(false);} }}
              role="button"
              tabIndex={0}
            >
              <span aria-hidden="true" className="dv-side-strip-label">Filtros</span>
            </div>
          ) : (
            <section aria-label="Filtros" className="dv-right-pane dv-pane-filters">
              <div className="dv-pane-header">
                <span className="dv-pane-title"><span aria-hidden="true" className="dv-pane-icon">▽</span><strong>Filtros</strong></span>
                <button aria-label="Minimizar filtros" className="dv-pane-collapse" onClick={() => setFiltersCollapsed(true)} title="Minimizar panel" type="button">»</button>
              </div>
              <FiltersPanel
                activePageId={effectivePageId}
                canAuthor={canAuthor && !proposal}
                model={head.model}
                onAddFilter={(filter: Filter) => commit([addFilter(filter)], [], `Crear filtro ${filter.name}`)}
                onRemoveFilter={(name) => commit([removeFilter(name)], [], `Eliminar filtro ${name}`)}
                onUpdateFilter={(filter) => commit([updateFilter(filter)], [], `Actualizar filtro ${filter.name}`)}
                selectedVisualId={selected?.id ?? null}
              />
            </section>
          )}

          {!dataPaneCollapsed && <div aria-hidden="true" className="dv-pane-resizer" onPointerDown={(event) => beginPaneResize(event, 'data')} title="Arrastra para ajustar el ancho" />}
          {dataPaneCollapsed ? (
            <div
              aria-label="Expandir datos"
              className="dv-side-strip"
              onClick={() => setDataPaneCollapsed(false)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') {setDataPaneCollapsed(false);} }}
              role="button"
              tabIndex={0}
            >
              <span aria-hidden="true" className="dv-side-strip-label">Datos</span>
            </div>
          ) : (
            <section aria-label="Datos y campos" className="dv-right-pane dv-pane-data">
              <div className="dv-pane-header">
                <span className="dv-pane-title"><span aria-hidden="true" className="dv-pane-icon">⛁</span><strong>Datos</strong></span>
                <button aria-label="Minimizar datos" className="dv-pane-collapse" onClick={() => setDataPaneCollapsed(true)} title="Minimizar panel" type="button">»</button>
              </div>
              <div className="dv-right-pane-body">
                <div className="dv-sidebar-section">
                  <label className="dv-csv-import">
                    <span>{importingCsv ? 'Importando…' : 'Importar CSV local'}</span>
                    <input
                      accept=".csv,text/csv"
                      aria-label="Importar archivo CSV local"
                      disabled={importingCsv || remoteMode}
                      onChange={(event) => {
                        void handleCsvImport(event.target.files?.[0]);
                        event.target.value = '';
                      }}
                      type="file"
                    />
                    <small>{remoteMode
                      ? 'Desactivado en modo Studio remoto: el modelo del servicio es la autoridad.'
                      : activeDataset
                        ? `${activeDataset.source_filename} · ${activeDataset.rows.length} filas`
                        : 'Máximo 2 MiB, 5.000 filas y 100 columnas. No se sube a un servidor.'}</small>
                  </label>
                  {head.model.entities.map((entity) => (
                    <details key={entity.name} open>
                      <summary>{entity.name}</summary>
                      <ul>{entity.fields.filter((field) => !field.hidden).map((field) => <li key={field.name}><span className={`dv-field-type dv-field-type--${field.data_type}`}>{field.data_type === 'decimal' ? '#' : field.data_type === 'date' ? '▦' : 'A'}</span>{field.name}</li>)}</ul>
                    </details>
                  ))}
                </div>
                <div className="dv-sidebar-section">
                  <div className="dv-sidebar-heading"><h2>Métricas</h2><span>{head.model.metrics.length}</span></div>
                  <ul className="dv-field-list">{head.model.metrics.map((metric) => <li key={metric.name}><span className="dv-field-type dv-field-type--metric">ƒ</span>{metric.name}</li>)}</ul>
                </div>
              </div>
            </section>
          )}

          {!agentCollapsed && <div aria-hidden="true" className="dv-pane-resizer" onPointerDown={(event) => beginPaneResize(event, 'agent')} title="Arrastra para ajustar el ancho" />}
          {agentCollapsed && (
            <div
              aria-label="Expandir asistente"
              className="dv-side-strip"
              onClick={() => setAgentCollapsed(false)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') {setAgentCollapsed(false);} }}
              role="button"
              tabIndex={0}
            >
              <span aria-hidden="true" className="dv-side-strip-label">Asistente</span>
            </div>
          )}
          <div className={agentCollapsed ? 'dv-hidden-pane' : 'dv-agent-wrap'}>
            <AgentPanel
              busy={agentBusy}
              endpoint={agentEndpoint}
              onApply={handleApplyProposal}
              onCancel={() => {
                agentRequest.current?.abort();
                agentRequest.current = null;
                setAgentBusy(false);
                setAgentResponse('Solicitud cancelada. No se aplicó ningún cambio.');
              }}
              onCollapse={() => setAgentCollapsed(true)}
              onPlan={handlePlan}
              onReject={() => setProposal(null)}
              previewError={preview.error}
              proposal={proposal}
              response={agentResponse}
            />
          </div>
        </div>
      </div>

      <div aria-label="Páginas del dashboard" className="dv-pages-bar" role="tablist">
          {currentPages.map((page) => (
            renamingPage?.id === page.page_id ? (
              <input
                aria-label="Nombre de la página"
                autoFocus
                defaultValue={renamingPage.value}
                key={page.page_id}
                onBlur={(event) => {
                  const value = event.currentTarget.value.trim();
                  if (value && value !== page.display_name) {handleRenamePage(page.page_id, value);}
                  setRenamingPage(null);
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {event.currentTarget.blur();}
                  if (event.key === 'Escape') {setRenamingPage(null);}
                }}
                type="text"
              />
            ) : (
              <button
                aria-selected={page.page_id === effectivePageId}
                className="dv-page-tab"
                id={`dv-page-tab-${page.page_id}`}
                key={page.page_id}
                onClick={() => { setActivePageId(page.page_id); setSelectedId(null); }}
                onContextMenu={(event) => { event.preventDefault(); setPageMenu({ pageId: page.page_id, x: event.clientX, y: event.clientY }); }}
                onDoubleClick={() => setRenamingPage({ id: page.page_id, value: page.display_name || page.name })}
                onKeyDown={(event) => {
                  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') {return;}
                  event.preventDefault();
                  const index = currentPages.findIndex((item) => item.page_id === page.page_id);
                  const next = currentPages[index + (event.key === 'ArrowRight' ? 1 : -1)];
                  if (!next) {return;}
                  setActivePageId(next.page_id);
                  setSelectedId(null);
                  document.getElementById(`dv-page-tab-${next.page_id}`)?.focus();
                }}
                role="tab"
                tabIndex={page.page_id === effectivePageId ? 0 : -1}
                title="Doble clic para renombrar"
                type="button"
              >{page.display_name || page.name || page.page_id}</button>
            )
          ))}
          <button aria-label="Agregar página" className="dv-page-add" disabled={remoteMode || !canAuthor || Boolean(proposal)} onClick={handleAddPage} title="Agregar página" type="button">＋</button>
          <span aria-hidden="true" className="dv-pages-bar-spacer" />
          <div className="dv-zoom-hud">
            <button aria-label="Ajustar al espacio disponible" className="dv-zoom-fit" onClick={fitToCanvas} title="Ajustar al espacio disponible" type="button">⤢</button>
            <button aria-label="Reducir zoom" className="dv-zoom-step" onClick={() => setZoom((z) => Math.max(30, z - 10))} type="button">−</button>
            <input aria-label="Zoom del canvas" max="200" min="30" onChange={(event) => setZoom(Number(event.target.value))} type="range" value={zoom} />
            <button aria-label="Aumentar zoom" className="dv-zoom-step" onClick={() => setZoom((z) => Math.min(200, z + 10))} type="button">+</button>
            <span className="dv-zoom-value">{zoom}%</span>
          </div>
        </div>

        {pageMenu && (
          <div className="dv-context-menu" role="menu" ref={pageMenuRef} style={{ left: pageMenu.x, top: pageMenu.y }}>
            <button role="menuitem" onClick={() => { handleDuplicatePage(pageMenu.pageId); setPageMenu(null); }} type="button">Duplicar página</button>
            <button role="menuitem" disabled={head.presentation_ir.pages.length <= 1} onClick={() => { handleDeletePage(pageMenu.pageId); setPageMenu(null); }} type="button">Eliminar página</button>
          </div>
        )}

        {visualMenu && (
          <div className="dv-context-menu" role="menu" ref={visualMenuRef} style={{ left: visualMenu.x, top: visualMenu.y }}>
            <button role="menuitem" disabled={!canAuthor || Boolean(proposal)} onClick={() => { const visual = activePresentation.visuals.find((item) => item.id === visualMenu.visualId); if (visual) {handleDuplicateVisual(visual);} setVisualMenu(null); }} type="button">Duplicar visual</button>
            <button role="menuitem" disabled={!canAuthor || Boolean(proposal)} onClick={() => { const visual = activePresentation.visuals.find((item) => item.id === visualMenu.visualId); if (visual) {handleRemoveForId(visual.id);} setVisualMenu(null); }} type="button">Eliminar visual</button>
          </div>
        )}

      <footer className="dv-studio-footer dv-sr-only">
        <span>{doc.doc_id}</span>
        <span>Head v{head.number} · {head.status}</span>
        <span>Versión publicada: {remoteMode && remotePublished !== null ? `v${remotePublished}` : published ? `v${published.number}` : 'ninguna'}</span>
        <span>{activePresentation.visuals.length} visuales · {head.model.metrics.length} métricas · {head.model.filters.length} filtros</span>
      </footer>
    </main>
  );
};

const ChangesView: React.FC<{
  currentDiff: SemanticDiff;
  presentationChanges: ReturnType<typeof diffPresentations>;
}> = ({ currentDiff, presentationChanges }) => (
  <section className="dv-workspace-panel" aria-labelledby="changes-heading">
    <div className="dv-workspace-heading"><div><span>Revisión</span><h1 id="changes-heading">Cambios sin publicar</h1></div><p>Compara el head actual con la última versión publicada.</p></div>
    {currentDiff.is_empty && presentationChanges.length === 0 ? <div className="dv-audit-empty"><strong>No hay cambios pendientes</strong><p>Publica o continúa editando el dashboard.</p></div> : (
      <div className="dv-audit-list">
        {presentationChanges.map((change) => <article key={`${change.kind}-${change.visual_id}`}><span className={`dv-audit-kind dv-audit-kind--${change.kind}`}>{change.kind}</span><div><strong>{change.after?.title ?? change.before?.title ?? change.visual_id}</strong><small>presentation · {change.visual_id}</small></div></article>)}
        {currentDiff.changes.map((change) => <article key={`${change.kind}-${change.target}-${change.name}`}><span className={`dv-audit-kind dv-audit-kind--${change.kind}`}>{change.kind}</span><div><strong>{change.name}</strong><small>{change.target} · Semantic IR</small></div></article>)}
      </div>
    )}
  </section>
);

const HistoryView: React.FC<{ canRevert: boolean; document: Document; headNumber: number; onRollback: (version: number) => void }> = ({ canRevert, document, headNumber, onRollback }) => (
  <section className="dv-workspace-panel" aria-labelledby="history-heading">
    <div className="dv-workspace-heading"><div><span>Linaje</span><h1 id="history-heading">Historial del proyecto</h1></div><p>Cada cambio manual o agéntico crea una versión inmutable.</p></div>
    <ol className="dv-history-list">
      {[...document.versions].reverse().map((version) => <li key={version.number}><span className={`dv-version-dot dv-version-dot--${version.status}`} /><div><strong>v{version.number} · {version.message || 'Sin nota'}</strong><small>{new Date(version.created_at).toLocaleString()} · {version.presentation.visuals.length} visuales · {version.model.metrics.length} métricas</small></div><span className={`dv-version-status dv-version-status--${version.status}`}>{version.status}</span>{version.number !== headNumber && <button aria-label={`Restaurar versi\u00F3n ${version.number}`} disabled={!canRevert} onClick={() => onRollback(version.number)} type="button">Restaurar</button>}</li>)}
    </ol>
  </section>
);

function withInitialVisuals(document: Document, visuals: VisualLayoutSpec[]): Document {
  const head = getHeadVersion(document);
  if (visuals.length === 0 || head.presentation.visuals.length > 0) {return document;}
  return applyOps(document, [], {
    message: 'Cargar visuales iniciales',
    presentation: createPresentation(visuals),
  });
}

function uniqueVisualId(visuals: VisualLayoutSpec[]): string {
  let index = visuals.length + 1;
  while (visuals.some((visual) => visual.id === `visual-${index}`)) {index += 1;}
  return `visual-${index}`;
}

function defaultBindingForModel(kind: string, model: Document['versions'][number]['model']): {
  dataRoles: Record<string, string>;
  title: string;
} {
  const entity = model.entities.find((candidate) => !candidate.hidden) ?? model.entities[0];
  const fields = entity?.fields.filter((field) => !field.hidden) ?? [];
  const dimensions = fields.filter((field) => (
    field.data_type === 'string' || field.data_type === 'date'
    || field.data_type === 'datetime' || field.data_type === 'boolean'
  ));
  const dimension = dimensions[0] ?? fields[0];
  const secondDimension = dimensions.find((candidate) => candidate !== dimension) ?? dimension;
  const numericField = fields.find((field) => field.data_type === 'integer' || field.data_type === 'decimal');
  const metrics = model.metrics.filter((candidate) => !candidate.hidden);
  const metric = metrics[0];
  const secondMetric = metrics.find((candidate) => candidate !== metric) ?? metric;
  const valueRef = metric
    ? `measure:${metric.name}`
    : dimension
      ? `field:${dimension.name}`
      : 'measure:Sales';
  const dimensionRef = dimension ? `field:${dimension.name}` : 'field:Region';
  const secondDimensionRef = secondDimension ? `field:${secondDimension.name}` : dimensionRef;
  const secondValueRef = secondMetric
    ? `measure:${secondMetric.name}`
    : numericField
      ? `field:${numericField.name}`
      : valueRef;
  const rawValueRef = numericField ? `field:${numericField.name}` : valueRef;
  const dataRoles: Record<string, string> = (() => {
    switch (kind) {
      case 'card': case 'kpi': case 'gauge': {
        const roles: Record<string, string> = { value: valueRef };
        if (kind === 'gauge') {roles.target_metric = secondValueRef;}
        return roles;
      }
      case 'bullet': {
        return { category: dimensionRef, comparison_metric: secondValueRef, value: valueRef };
      }
      case 'combo': {
        return { category: dimensionRef, comparison_metric: secondValueRef, value: valueRef };
      }
      case 'heatmap': {
        return { column: secondDimensionRef, row: dimensionRef, value: valueRef };
      }
      case 'matrix': {
        return { column: secondDimensionRef, row: dimensionRef, value: valueRef };
      }
      case 'histogram': {
        return { value: rawValueRef };
      }
      case 'box_plot': {
        return { category: dimensionRef, value: rawValueRef };
      }
      case 'stacked_bar': {
        return { category: dimensionRef, series: secondDimensionRef, value: valueRef };
      }
      case 'stacked_column': {
        return { category: dimensionRef, series: secondDimensionRef, value: valueRef };
      }
      case 'percent_stacked_bar': {
        return { category: dimensionRef, series: secondDimensionRef, value: valueRef };
      }
      case 'percent_stacked_column': {
        return { category: dimensionRef, series: secondDimensionRef, value: valueRef };
      }
      case 'stacked_area': {
        return { category: dimensionRef, series: secondDimensionRef, value: valueRef };
      }
      case 'ribbon': {
        return { category: dimensionRef, series: secondDimensionRef, value: valueRef };
      }
      case 'packed_bubbles': {
        return { category: dimensionRef, value: valueRef };
      }
      case 'gantt': {
        const dateDimension = dimensions.find((candidate) => candidate.data_type === 'date' || candidate.data_type === 'datetime') ?? dimension;
        return {
          category: dimensionRef,
          value: rawValueRef,
          x_axis: dateDimension ? `field:${dateDimension.name}` : dimensionRef,
        };
      }
      case 'slicer': {
        return { category: dimensionRef };
      }
      default: {
        return { category: dimensionRef, value: valueRef };
      }
    }
  })();
  return {
    dataRoles,
    title: metric?.name.replaceAll('_', ' ') ?? dimension?.name.replaceAll('_', ' ') ?? 'Nuevo visual',
  };
}

function focusVisual(id: string): void {
  window.setTimeout(() => document.getElementById(`dv-visual-${id}`)?.focus(), 0);
}

function viewerHref(
  headNumber: number,
  remote?: RemoteAuthoringConfig,
  remoteDurable?: {
    versionId?: string | null;
    versionNumber?: number | null;
    publishedVersion?: number | null;
  } | null,
): string {
  const query = new URLSearchParams(typeof window === 'undefined' ? '' : window.location.search);
  query.set('mode', 'viewer');
  if (remote) {
    query.set('project', remote.projectId);
    const durableVersion = remoteDurable?.versionId
      || (remoteDurable?.versionNumber !== undefined && remoteDurable?.versionNumber !== null
        ? String(remoteDurable.versionNumber)
        : String(headNumber));
    query.set('version', durableVersion);
    if (remoteDurable?.publishedVersion !== null && remoteDurable?.publishedVersion !== undefined) {
      query.set('publication', String(remoteDurable.publishedVersion));
      query.set('publishedVersion', String(remoteDurable.publishedVersion));
    }
    if (remote.endpoint) {
      query.set('endpoint', remote.endpoint);
    }
    query.delete('localVersion');
  } else if (!query.get('version')?.trim() && !query.get('endpoint')?.trim()) {
    query.set('localVersion', String(headNumber));
  } else {
    query.delete('localVersion');
  }
  return `?${query.toString()}`;
}

function agentCommitMessage(prompt: string): string {
  const safe = prompt
    .replaceAll(/[\u0000-\u001F\u007F]/g, ' ')
    .replaceAll(/\s+/g, ' ')
    .trim()
    .slice(0, 160);
  return `Agente: ${safe || 'propuesta aplicada'}`;
}

function diffPresentations(before: VisualLayoutSpec[], after: VisualLayoutSpec[]) {
  const beforePresentation = createPresentation(before);
  const afterById = new Map(after.map((visual) => [visual.id, visual]));
  const operations: VisualOperation[] = [];
  for (const visual of before) {
    const current = afterById.get(visual.id);
    if (!current) {operations.push(removeVisual(visual.id));}
    else if (!jsonEqual(current, visual)) {operations.push(updateVisual(current));}
  }
  for (const visual of after) {
    if (!before.some((candidate) => candidate.id === visual.id)) {operations.push(addVisual(visual));}
  }
  return previewVisualOperations(beforePresentation, operations);
}

export default VisualAuthoringEditor;
