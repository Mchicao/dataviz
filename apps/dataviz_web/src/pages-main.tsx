import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';

import LandingEntry from './landing/LandingEntry';

// En el sitio público (Pages) no corre el studio: los CTAs de demo llevan al
// quickstart del README en el repositorio.
const REPO_QUICKSTART = 'https://github.com/Mchicao/dataviz#inicio-rápido';

function RewireDemoLinks(): null {
  useEffect(() => {
    for (const anchor of document.querySelectorAll<HTMLAnchorElement>('a[href="?mode=author"]')) {
      anchor.setAttribute('href', REPO_QUICKSTART);
      anchor.setAttribute('target', '_blank');
      anchor.setAttribute('rel', 'noopener');
    }
  }, []);
  return null;
}

createRoot(document.querySelector('#root')!).render(
  <StrictMode>
    <RewireDemoLinks />
    <LandingEntry value="sales" />
  </StrictMode>,
);
