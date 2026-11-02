import React from 'react';

import App from '../app';
import { compileLocalRuntimePayload } from './localRuntime';
import { AUTHORING_STORAGE_KEY } from './seed';
import { loadLocalDataset } from './datasetStorage';
import { loadDraft } from './storage';
import { CLINIC_STORAGE_KEY, createClinicDemo } from './clinicDemo';

export interface LocalViewerEntryProps {
  localVersion?: string;
}

function parseLocalVersion(value?: string): number | undefined {
  if (value === undefined) {return undefined;}
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) {throw new Error(`versión de autoría local inválida: ${value}`);}
  return parsed;
}

/** Consume an immutable locally-authored version through the normal Viewer App. */
export default function LocalViewerEntry({ localVersion }: LocalViewerEntryProps): React.JSX.Element {
  const resolved = React.useMemo(() => {
    try {
      const clinic = new URLSearchParams(window.location.search).get('demo') === 'clinic-x'
        ? createClinicDemo() : null;
      const storageKey = clinic ? CLINIC_STORAGE_KEY : AUTHORING_STORAGE_KEY;
      const document = loadDraft(window.localStorage, storageKey) ?? clinic?.document;
      if (!document) {return { payload: null, error: 'No hay un documento de autoría local disponible.' };}
      const dataset = loadLocalDataset(window.localStorage, `${storageKey}.dataset`) ?? clinic?.dataset ?? null;
      const payload = compileLocalRuntimePayload(document, parseLocalVersion(localVersion), dataset);
      return { error: '', payload };
    } catch (error) {
      return {
        payload: null,
        error: error instanceof Error ? error.message : 'La versión de autoría local no está disponible.',
      };
    }
  }, [localVersion]);

  if (resolved.error) {return <main className="dv-runtime-error" role="alert">{resolved.error}</main>;}
  return <App initialPayload={resolved.payload} />;
}
