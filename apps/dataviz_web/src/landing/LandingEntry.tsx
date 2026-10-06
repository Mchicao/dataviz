import { Suspense, lazy } from 'react';

import { LandingPage, resolveLandingSelection } from './LandingPage';

const SalesLanding = lazy(() => import('./SalesLanding'));
const SteepLanding = lazy(() => import('./SalesSteep'));
const JournalLanding = lazy(() => import('./SalesJournal'));

const SALES_TITLES: Record<string, string> = {
  journal: 'DataVIZ · El conocimiento de tu negocio, consultable',
  sales: 'DataVIZ · Analítica de clase mundial. Agent Native.',
  steep: 'DataVIZ · Conocimiento de negocio, disponible',
};

/** Entry adapter for the lazy route: maps a raw `?landing=` value to a landing surface. */
export default function LandingEntry({ value }: { value: string }) {
  const selection = resolveLandingSelection(value) ?? 'gallery';
  document.querySelector('#root')?.classList.add('dv-landing-root');
  document.title = SALES_TITLES[selection] ?? `DataVIZ · Landing ${selection === 'gallery' ? 'concepts' : selection}`;
  if (selection === 'steep') {
    return <Suspense fallback={null}><SteepLanding /></Suspense>;
  }
  if (selection === 'journal') {
    return <Suspense fallback={null}><JournalLanding /></Suspense>;
  }
  if (selection === 'sales') {
    return <Suspense fallback={null}><SalesLanding /></Suspense>;
  }
  return <LandingPage selection={selection as 'gallery' | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10} />;
}
