import React from 'react';
import type { VisualProps } from './types';
import { SUPPORTED_VISUAL_KINDS } from '../../runtime/types';
import { displayTitle } from './title';

/**
 * Explicit fallback for visual kinds the runtime does not render.
 *
 * Shown for any kind outside `SUPPORTED_VISUAL_KINDS`. Accessibility: `role="alert"` so
 * assistive tech announces the gap rather than silently skipping the visual.
 */
export const UnsupportedVisual: React.FC<VisualProps> = ({ visual }) => (
  <section
    className="dv-unsupported"
    role="alert"
    aria-live="polite"
    style={{
      background: '#fff5f5',
      border: '1px dashed #e53e3e',
      borderRadius: 4,
      color: '#9b2c2c',
      padding: '0.75rem',
    }}
  >
    <div className="dv-unsupported-title" style={{ fontWeight: 600 }}>
      {displayTitle(visual.title, visual.name)}
    </div>
    <div className="dv-unsupported-kind">
      Tipo de visual no soportado: <code>{visual.kind}</code>
    </div>
    <div className="dv-unsupported-hint" style={{ fontSize: '0.8rem' }}>
      {`Tipos soportados: ${SUPPORTED_VISUAL_KINDS.join(', ')}.`}
    </div>
  </section>
);
