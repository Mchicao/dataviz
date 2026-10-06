import React from 'react';
import ReactDOM from 'react-dom/client';
import { optionalQueryValue, resolveEntryMode } from './entryMode';
import { RouteErrorBoundary } from './RouteErrorBoundary';

const AuthoringEntry = React.lazy(() => import('./authoring/AuthoringEntry'));
const LandingEntry = React.lazy(() => import('./landing/LandingEntry'));
const ViewerEntry = React.lazy(() => import('./ViewerEntry'));

const rootElement = document.querySelector('#root');
if (!rootElement) {throw new Error('DataVIZ Studio requires a #root element');}
{
  const query = new URL(window.location.href).searchParams;
  const landing = optionalQueryValue(query, 'landing');
  const versionId = optionalQueryValue(query, 'version');
  const endpoint = optionalQueryValue(query, 'endpoint');
  const localVersion = optionalQueryValue(query, 'localVersion');
  const projectId = optionalQueryValue(query, 'project') ?? optionalQueryValue(query, 'projectId');
  // LUNA1: el token nunca viaja por la URL (filtraría a historial, referrer y
  // logs). Solo se acepta desde la configuración bootstrap del host o props del entry.
  const sessionConfig = window as unknown as {
    __DATAVIZ_CONFIG__?: {
      authoring?: { authToken?: string };
      viewer?: { authToken?: string };
      remote?: { authToken?: string };
    };
    __DATAVIZ_REMOTE__?: { authToken?: string };
  };
  const authToken = sessionConfig.__DATAVIZ_CONFIG__?.authoring?.authToken
    || sessionConfig.__DATAVIZ_CONFIG__?.viewer?.authToken
    || sessionConfig.__DATAVIZ_CONFIG__?.remote?.authToken
    || sessionConfig.__DATAVIZ_REMOTE__?.authToken
    || undefined;
  const authoring = resolveEntryMode(query) === 'authoring';
  document.documentElement.dataset.dvMode = authoring ? 'authoring' : 'viewer';
  ReactDOM.createRoot(rootElement).render(
    <React.StrictMode>
      <RouteErrorBoundary>
        <React.Suspense fallback={<p className="dv-runtime-loading">Cargando DataVIZ…</p>}>
          {landing === undefined
            ? authoring
              ? <AuthoringEntry authToken={authToken} endpoint={endpoint} projectId={projectId} />
              : <ViewerEntry endpoint={endpoint} localVersion={localVersion} versionId={versionId} authToken={authToken} />
            : <LandingEntry value={landing} />}
        </React.Suspense>
      </RouteErrorBoundary>
    </React.StrictMode>
  );
}
