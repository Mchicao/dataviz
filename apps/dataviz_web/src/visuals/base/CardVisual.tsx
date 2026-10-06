import React from 'react';
import type { VisualProps } from './types';
import { formatScalar, getRole, scalarForRef } from './data';
import { displayTitle } from './title';

/**
 * Card visual: renders a single scalar headline value.
 *
 * Resolves role `value` (falls back to the first declared role). Accessibility:
 * exposed as `role="figure"` with an `aria-label` carrying title + value.
 */
export const CardVisual: React.FC<VisualProps> = ({ visual }) => {
  const role = getRole(visual, ['value', 'y_axis', 'label', 'x_axis', 'size']);
  const value = role ? scalarForRef(role.ref, role.data) : null;
  const display = formatScalar(value);
  const title = displayTitle(visual.title, visual.name);

  return (
    <section
      className="dv-card"
      role="figure"
      aria-label={`${title}: ${display}`}
      style={{
        backgroundColor: 'var(--dv-card-bg, #ffffff)',
        border: '1px solid var(--dv-card-border, #e2e8f0)',
        borderRadius: '6px',
        boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: 'column',
        gap: 'clamp(0.15rem, 0.5vw, 0.3rem)',
        height: 'inherit',
        justifyContent: 'center',
        minHeight: 0,
        minWidth: 0,
        overflow: 'hidden',
        padding: 'clamp(0.5rem, 1.2vw, 0.9rem) clamp(0.6rem, 1.6vw, 1rem)',
      }}
    >
      <div
        className="dv-card-title"
        title={title}
        style={{
          color: 'var(--dv-card-title, #64748b)',
          fontSize: 'clamp(0.68rem, 0.64rem + 0.16vw, 0.8rem)',
          fontWeight: 500,
          letterSpacing: '0.025em',
          lineHeight: 1.25,
          minWidth: 0,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          textTransform: 'uppercase',
          whiteSpace: 'nowrap',
        }}
      >
        {title}
      </div>
      <div
        className="dv-card-value"
        title={display}
        style={{
          color: 'var(--dv-accent, #0f172a)',
          fontSize: 'clamp(1.1rem, 0.88rem + 1.15vw, 1.9rem)',
          fontVariantNumeric: 'tabular-nums',
          fontWeight: 700,
          lineHeight: 1.05,
          minWidth: 0,
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
        }}
      >
        {display}
      </div>
    </section>
  );
};
