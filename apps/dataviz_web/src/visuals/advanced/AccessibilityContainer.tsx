import React from 'react';
import type { InterpretedVisual } from '../../runtime/types';
import type { AdvancedAccessibilityConfig } from './types';
import { displayTitle } from '../base/title';

/**
 * Automatically generate a screen reader summary for a visual.
 *
 * @param visual Interpreted visual
 */
export function generateScreenReaderSummary(visual: InterpretedVisual): string {
  const title = displayTitle(visual.title, visual.name);
  const roleKeys = Object.keys(visual.roles);
  if (roleKeys.length === 0) {
    return `${title} (${visual.kind} visual) has no bound data.`;
  }

  const summaries: string[] = [];
  for (const rKey of roleKeys) {
    const r = visual.roles[rKey];
    if (r.data !== undefined && r.data !== null) {
      if (Array.isArray(r.data)) {
        summaries.push(`${rKey} contains ${r.data.length} items`);
      } else {
        summaries.push(`${rKey} is ${String(r.data)}`);
      }
    }
  }

  const detailStr = summaries.length > 0 ? summaries.join(', ') : 'no data values';
  return `${title} (${visual.kind} visual): ${detailStr}.`;
}

export interface AccessibilityContainerProps {
  visual: InterpretedVisual;
  config?: Partial<AdvancedAccessibilityConfig>;
  children: React.ReactNode;
  onActivate?: () => void;
  highContrast?: boolean;
}

/**
 * Accessibility Wrapper Container for DataVIZ Visuals.
 *
 * Fulfills Corpus Gap 6: ARIA landmarks, screen reader summaries, keyboard focus,
 * and high contrast awareness.
 */
export const AccessibilityContainer: React.FC<AccessibilityContainerProps> = ({
  visual,
  config,
  children,
  onActivate,
  highContrast = false,
}) => {
  const title = displayTitle(visual.title, visual.name);
  const ariaLabel = config?.ariaLabel || title || `${visual.kind} visualization`;
  const altText = config?.altText || `Visualization showing ${title}`;
  const tabIndex = config?.tabIndex ?? 0;
  const screenReaderSummary = config?.screenReaderSummary || generateScreenReaderSummary(visual);
  const summaryId = `a11y_summary_${visual.name.replaceAll(/\s+/g, '_')}`;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
    if ((e.key === 'Enter' || e.key === ' ') && onActivate) {
      e.preventDefault();
      onActivate();
    }
  };

  return (
    <figure
      className={`dataviz-a11y-container ${highContrast ? 'high-contrast-mode' : ''}`}
      role="region"
      aria-label={ariaLabel}
      aria-describedby={summaryId}
      tabIndex={tabIndex}
      onKeyDown={handleKeyDown}
      style={{
        backgroundColor: highContrast ? '#FFFFFF' : 'transparent',
        border: highContrast ? '2px solid #000000' : '1px solid transparent',
        color: highContrast ? '#000000' : 'inherit',
        margin: 0,
        outline: 'none',
        padding: '8px',
      }}
    >
      <div className="dataviz-visual-wrapper">{children}</div>

      {/* Screen Reader Only Summary */}
      <figcaption
        id={summaryId}
        style={{
          border: 0,
          clip: 'rect(0, 0, 0, 0)',
          height: '1px',
          margin: '-1px',
          overflow: 'hidden',
          padding: 0,
          position: 'absolute',
          whiteSpace: 'nowrap',
          width: '1px',
        }}
      >
        {altText}. {screenReaderSummary}
      </figcaption>
    </figure>
  );
};
