import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';

import LandingEntry from './landing/LandingEntry';

// En el sitio público (Pages) no corre el studio: los CTAs de demo llevan al
// quickstart del README en el repositorio. Delegación en fase de captura
// porque los CTAs se renderizan dentro de un chunk lazy (aparecen tarde).
const REPO_QUICKSTART = 'https://github.com/Mchicao/dataviz#inicio-rápido';

function RewireDemoLinks(): null {
  useEffect(() => {
    const onClick = (event: MouseEvent): void => {
      const anchor = (event.target as Element | null)?.closest?.('a[href="?mode=author"]');
      if (anchor) {
        event.preventDefault();
        window.open(REPO_QUICKSTART, '_blank', 'noopener');
      }
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);
  return null;
}

createRoot(document.querySelector('#root')!).render(
  <StrictMode>
    <RewireDemoLinks />
    <LandingEntry value="sales" />
  </StrictMode>,
);
