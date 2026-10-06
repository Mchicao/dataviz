import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import LandingEntry from './landing/LandingEntry';

createRoot(document.querySelector('#root')!).render(
  <StrictMode>
    <LandingEntry value="sales" />
  </StrictMode>,
);
