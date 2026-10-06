import React, { useEffect } from 'react';
import type { PageTooltipState } from './types';
import { RUNTIME_RESULTS_SCHEMA_VERSION } from '../../runtime/types';
import type { RenderPlan, RuntimeResults } from '../../runtime/types';
import { renderPlan } from '../base/registry';

export interface PageTooltipOverlayProps {
  state: PageTooltipState;
  targetPlan?: RenderPlan;
  targetResults?: RuntimeResults;
  onClose?: () => void;
  className?: string;
}

export const PageTooltipOverlay: React.FC<PageTooltipOverlayProps> = ({
  state,
  targetPlan,
  targetResults = { schema_version: RUNTIME_RESULTS_SCHEMA_VERSION, visuals: {} },
  onClose,
  className = '',
}) => {
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && state.isVisible && onClose) {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [state.isVisible, onClose]);

  if (!state.isVisible) {
    return null;
  }

  const { position, targetPageId, sourceVisualId, dataContext } = state;

  return (
    <div
      className={`dataviz-page-tooltip-overlay ${className}`}
      role="tooltip"
      id={`tooltip_${sourceVisualId}`}
      aria-hidden={!state.isVisible}
      style={{
        backgroundColor: '#FFFFFF',
        border: '1px solid #CBD5E1',
        borderRadius: '8px',
        boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05)',
        left: `${position.x + 12}px`,
        maxWidth: '400px',
        minWidth: '240px',
        padding: '12px',
        pointerEvents: 'none',
        position: 'fixed',
        top: `${position.y + 12}px`,
        zIndex: 9999,
      }}
    >
      <div
        className="dataviz-page-tooltip-header"
        style={{
          alignItems: 'center',
          borderBottom: '1px solid #F1F5F9',
          display: 'flex',
          justifyContent: 'space-between',
          marginBottom: '8px',
          paddingBottom: '6px',
        }}
      >
        <span style={{ color: '#475569', fontSize: '12px', fontWeight: 600 }}>
          {`Page Tooltip: ${targetPageId}`}
        </span>
      </div>

      {targetPlan ? (
        <div className="dataviz-page-tooltip-content">
          {renderPlan(targetPlan, targetResults)}
        </div>
      ) : dataContext ? (
        <div className="dataviz-page-tooltip-context">
          <ul style={{ fontSize: '13px', listStyle: 'none', margin: 0, padding: 0 }}>
            {Object.entries(dataContext).map(([k, v]) => (
              <li key={k} style={{ display: 'flex', justifyContent: 'space-between', padding: '2px 0' }}>
                <span style={{ color: '#64748B', fontWeight: 500 }}>{`${k}:`}</span>
                <span style={{ color: '#0F172A', fontWeight: 600 }}>{String(v)}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p style={{ color: '#64748B', fontSize: '13px', margin: 0 }}>
          {`Tooltip preview for ${sourceVisualId}`}
        </p>
      )}
    </div>
  );
};
