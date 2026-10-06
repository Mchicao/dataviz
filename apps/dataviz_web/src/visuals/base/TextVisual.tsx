import React from 'react';
import type { VisualProps } from './types';
import { displayTitle } from './title';

/** Minimal neutral text-box renderer for Tableau text marks and annotations. */
export const TextVisual: React.FC<VisualProps> = ({ visual }) => (
  <section className="dv-text-box" role="note" aria-label={displayTitle(visual.title, visual.name)}>
    {displayTitle(visual.title, visual.name)}
  </section>
);
