import React from 'react';
import {
  AUTHORING_STORAGE_KEY,
  VisualAuthoringEditor,
  createAuthoringDocument,
  loadDraft,
  loadLocalDataset,
} from './index';
import type { Document } from './types';
import type { LocalDataset } from './csv';
import type { RemoteAuthoringConfig } from './VisualAuthoringEditor';
import './authoring.css';

export interface AuthoringEntryProps {
  remote?: RemoteAuthoringConfig;
  projectId?: string;
  endpoint?: string;
  authToken?: string;
  storageKey?: string;
  initialDocument?: Document;
  initialDataset?: LocalDataset | null;
}

/** Carga el borrador local o inicializa el modo Studio remoto según los parámetros de consulta o configuración. */
export default function AuthoringEntry(props: AuthoringEntryProps = {}): React.JSX.Element {
  const remoteConfig = React.useMemo<RemoteAuthoringConfig | undefined>(() => {
    if (props.remote) {return props.remote;}
    if (typeof window === 'undefined') {return;}

    const globalConfig = window as unknown as {
      __DATAVIZ_CONFIG__?: {
        authoring?: { projectId?: string; endpoint?: string; authToken?: string };
        remote?: { projectId?: string; endpoint?: string; authToken?: string };
      };
      __DATAVIZ_REMOTE__?: { projectId?: string; endpoint?: string; authToken?: string };
    };
    const configRemote = globalConfig.__DATAVIZ_CONFIG__?.authoring
      || globalConfig.__DATAVIZ_CONFIG__?.remote
      || globalConfig.__DATAVIZ_REMOTE__;

    const params = new URLSearchParams(window.location.search);
    const projectId = props.projectId
      || configRemote?.projectId
      || params.get('project')
      || params.get('projectId');
    if (!projectId) {return;}

    const endpoint = props.endpoint
      || configRemote?.endpoint
      || params.get('endpoint')
      || window.location.origin;

    // LUNA1: el token nunca viene de la URL ni de localStorage persistente;
    // solo props del bootstrapper o configuración global del host (sesión).
    const authToken = props.authToken || configRemote?.authToken || undefined;

    return {
      authToken,
      endpoint,
      projectId,
    };
  }, [props.remote, props.projectId, props.endpoint, props.authToken]);

  const effectiveStorageKey = props.storageKey ?? (remoteConfig ? undefined : AUTHORING_STORAGE_KEY);
  const [initialDocument] = React.useState<Document>(() => {
    if (props.initialDocument) {return props.initialDocument;}
    if (remoteConfig) {
      return createAuthoringDocument();
    }
    try {
      return (effectiveStorageKey ? loadDraft(window.localStorage, effectiveStorageKey) : null)
        ?? createAuthoringDocument();
    } catch {
      return createAuthoringDocument();
    }
  });

  const [initialDataset] = React.useState<LocalDataset | null>(() => {
    if (props.initialDataset !== undefined) {return props.initialDataset;}
    if (remoteConfig) {
      return null;
    }
    try {
      return effectiveStorageKey
        ? loadLocalDataset(window.localStorage, `${effectiveStorageKey}.dataset`)
        : null;
    } catch {
      return null;
    }
  });

  return (
    <VisualAuthoringEditor
      agentEndpoint={remoteConfig ? undefined : '/api/assistant'}
      initialDocument={initialDocument}
      initialDataset={initialDataset}
      storageKey={effectiveStorageKey}
      remote={remoteConfig}
    />
  );
}
