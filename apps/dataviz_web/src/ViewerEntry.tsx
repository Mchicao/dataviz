import React from 'react';
import App from './app';

const LocalViewerEntry = React.lazy(() => import('./authoring/LocalViewerEntry'));

interface ViewerEntryProps {
  endpoint?: string;
  versionId?: string;
  localVersion?: string;
  authToken?: string;
}

/** Aísla el runtime de consumo para cargarlo únicamente en modo viewer. */
export default function ViewerEntry({ endpoint, versionId, localVersion, authToken }: ViewerEntryProps): React.JSX.Element {
  if (endpoint || versionId) {return <App endpoint={endpoint} versionId={versionId} authToken={authToken} />;}
  return (
    <React.Suspense fallback={<p className="dv-runtime-loading">Loading local authored version...</p>}>
      <LocalViewerEntry localVersion={localVersion} />
    </React.Suspense>
  );
}
