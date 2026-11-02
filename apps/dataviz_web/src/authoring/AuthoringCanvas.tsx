import React, { useEffect, useMemo, useRef, useState } from 'react';
import { renderPlan } from '../visuals/base';
import { previewResultsFor, renderPlanForPresentation } from './preview';
import type { LocalDataset } from './csv';
import type { PresentationSnapshot, SemanticModel, VisualLayoutSpec } from './types';

interface AuthoringCanvasProps {
  presentation: PresentationSnapshot;
  selectedId: string | null;
  proposalPreview: boolean;
  canEdit: boolean;
  zoom: number;
  onZoomChange: (zoom: number) => void;
  viewportRef: React.RefObject<HTMLDivElement | null>;
  onSelect: (id: string) => void;
  onAdd: (kind: string) => void;
  onResize: (visual: VisualLayoutSpec, message: string) => void;
  onMove: (visual: VisualLayoutSpec, message: string) => void;
  onDeselect: () => void;
  onVisualContextMenu: (x: number, y: number, visualId: string) => void;
  dataset?: LocalDataset | null;
  model: SemanticModel;
}

export interface SnapResult {
  x: number;
  y: number;
  guidesX: number[];
  guidesY: number[];
}

export function computeSnap(
  target: { x: number; y: number; width: number; height: number },
  others: Array<{ geometry: { x: number; y: number; width: number; height: number } }>,
  canvas: { width: number; height: number },
  options: { disabled?: boolean; threshold?: number; gridSize?: number } = {},
): SnapResult {
  const { disabled = false, threshold = 6, gridSize = 10 } = options;
  if (disabled) {
    return { x: target.x, y: target.y, guidesX: [], guidesY: [] };
  }

  let snappedX = target.x;
  let snappedY = target.y;
  let minDiffX = threshold + 1;
  let minDiffY = threshold + 1;
  let guideX: number | null = null;
  let guideY: number | null = null;

  const xTargets: number[] = [0, Math.round(canvas.width / 2), canvas.width];
  const yTargets: number[] = [0, Math.round(canvas.height / 2), canvas.height];

  for (const other of others) {
    const og = other.geometry;
    xTargets.push(og.x, Math.round(og.x + og.width / 2), og.x + og.width);
    yTargets.push(og.y, Math.round(og.y + og.height / 2), og.y + og.height);
  }

  const testPointsX = [
    { offset: 0 },
    { offset: Math.round(target.width / 2) },
    { offset: target.width },
  ];

  for (const t of xTargets) {
    for (const p of testPointsX) {
      const cur = target.x + p.offset;
      const diff = Math.abs(cur - t);
      if (diff <= threshold && diff < minDiffX) {
        minDiffX = diff;
        snappedX = t - p.offset;
        guideX = t;
      }
    }
  }

  if (guideX === null && gridSize > 0) {
    const gridX = Math.round(target.x / gridSize) * gridSize;
    if (Math.abs(gridX - target.x) <= threshold) {
      snappedX = gridX;
    }
  }

  const testPointsY = [
    { offset: 0 },
    { offset: Math.round(target.height / 2) },
    { offset: target.height },
  ];

  for (const t of yTargets) {
    for (const p of testPointsY) {
      const cur = target.y + p.offset;
      const diff = Math.abs(cur - t);
      if (diff <= threshold && diff < minDiffY) {
        minDiffY = diff;
        snappedY = t - p.offset;
        guideY = t;
      }
    }
  }

  if (guideY === null && gridSize > 0) {
    const gridY = Math.round(target.y / gridSize) * gridSize;
    if (Math.abs(gridY - target.y) <= threshold) {
      snappedY = gridY;
    }
  }

  return {
    x: Math.round(snappedX),
    y: Math.round(snappedY),
    guidesX: guideX !== null ? [guideX] : [],
    guidesY: guideY !== null ? [guideY] : [],
  };
}

export const AuthoringCanvas: React.FC<AuthoringCanvasProps> = ({
  presentation,
  selectedId,
  proposalPreview,
  canEdit,
  zoom,
  onZoomChange,
  viewportRef,
  onSelect,
  onAdd,
  onResize,
  onMove,
  onDeselect,
  onVisualContextMenu,
  dataset,
  model,
}) => {
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const [guides, setGuides] = useState<{ x?: number[]; y?: number[] }>({});
  const rendered = useMemo(() => renderPlan(
    renderPlanForPresentation(presentation),
    previewResultsFor(presentation.visuals, dataset, model),
  ), [dataset, model, presentation]);

  // Ctrl+rueda sobre el lienzo cambia el zoom (preventDefault exige listener no pasivo).
  useEffect(() => {
    const node = viewportRef.current;
    if (!node) {return;}
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) {return;}
      event.preventDefault();
      onZoomChange(Math.min(200, Math.max(30, zoom + (event.deltaY < 0 ? 8 : -8))));
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, [zoom, onZoomChange]);

  return (
    <section className="dv-authoring-workspace" aria-label="Canvas de autoría">
      {proposalPreview && (
        <div className="dv-canvas-toolbar">
          <div className="dv-canvas-meta">
            <span className="dv-preview-pill">Vista previa del asistente</span>
          </div>
        </div>
      )}

      <div className="dv-authoring-canvas-viewport" ref={viewportRef}>
        <div
          className="dv-authoring-canvas-scale"
          style={{
            height: presentation.canvas.height * zoom / 100,
            width: presentation.canvas.width * zoom / 100,
          }}
        >
          <div
            className="dv-authoring-canvas"
            data-proposal-preview={proposalPreview || undefined}
            ref={canvasRef}
            onClick={onDeselect}
            style={{
              backgroundColor: presentation.canvas.background_color,
              height: presentation.canvas.height,
              transform: `scale(${zoom / 100})`,
              transformOrigin: 'top left',
              width: presentation.canvas.width,
            }}
          >
            {presentation.visuals.length === 0 && (
              <div className="dv-canvas-empty">
                <div className="dv-empty-illustration" aria-hidden="true"><span /><span /><span /></div>
                <h2>Construye tu primera vista</h2>
                <p>Agrega un visual manualmente o prueba una instrucción del asistente local.</p>
                <div><button onClick={() => onAdd('card')} type="button">Agregar un KPI</button><button onClick={() => onAdd('bar')} type="button">Agregar barras</button></div>
              </div>
            )}
            {guides.x?.map((gx) => (
              <div aria-hidden="true" className="dv-canvas-guide dv-canvas-guide--x" key={`guide-x-${gx}`} style={{ left: `${gx}px` }} />
            ))}
            {guides.y?.map((gy) => (
              <div aria-hidden="true" className="dv-canvas-guide dv-canvas-guide--y" key={`guide-y-${gy}`} style={{ top: `${gy}px` }} />
            ))}
            {presentation.visuals.map((visual, index) => (
              <VisualFrame
                canEdit={canEdit}
                canvasRef={canvasRef}
                element={rendered[index]}
                key={visual.id}
                onMove={onMove}
                onResize={onResize}
                onVisualContextMenu={onVisualContextMenu}
                onGuidesChange={setGuides}
                onSelect={() => onSelect(visual.id)}
                presentation={presentation}
                selected={visual.id === selectedId}
                visual={visual}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
};

type ResizeHandleId = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';

const RESIZE_HANDLES: readonly { id: ResizeHandleId; label: string }[] = [
  { id: 'nw', label: 'Redimensionar: esquina superior izquierda' },
  { id: 'n', label: 'Redimensionar: borde superior' },
  { id: 'ne', label: 'Redimensionar: esquina superior derecha' },
  { id: 'e', label: 'Redimensionar: borde derecho' },
  { id: 'se', label: 'Redimensionar: esquina inferior derecha' },
  { id: 's', label: 'Redimensionar: borde inferior' },
  { id: 'sw', label: 'Redimensionar: esquina inferior izquierda' },
  { id: 'w', label: 'Redimensionar: borde izquierdo' },
];

const MIN_VISUAL_WIDTH = 80;
const MIN_VISUAL_HEIGHT = 52;

const VisualFrame: React.FC<{
  canEdit: boolean;
  canvasRef: React.RefObject<HTMLDivElement | null>;
  element: React.ReactElement;
  onMove: (visual: VisualLayoutSpec, message: string) => void;
  onResize: (visual: VisualLayoutSpec, message: string) => void;
  onVisualContextMenu: (x: number, y: number, visualId: string) => void;
  onGuidesChange: (guides: { x?: number[]; y?: number[] }) => void;
  onSelect: () => void;
  presentation: PresentationSnapshot;
  selected: boolean;
  visual: VisualLayoutSpec;
}> = ({ canEdit, canvasRef, element, onMove, onResize, onVisualContextMenu, onGuidesChange, onSelect, presentation, selected, visual }) => {
  const { width: canvasWidth, height: canvasHeight } = presentation.canvas;
  const [draftGeometry, setDraftGeometry] = useState<VisualLayoutSpec['geometry'] | null>(null);
  const movedRecently = useRef(false);
  const geometry = draftGeometry ?? visual.geometry;
  const style = {
    '--dv-accent': visual.format_settings.accent_color,
    '--dv-ink': visual.format_settings.text_color,
    backgroundColor: visual.format_settings.background_color,
    color: visual.format_settings.text_color,
    height: `${(geometry.height / canvasHeight) * 100}%`,
    left: `${(geometry.x / canvasWidth) * 100}%`,
    top: `${(geometry.y / canvasHeight) * 100}%`,
    width: `${(geometry.width / canvasWidth) * 100}%`,
  } as React.CSSProperties;

  /**
   * Escala px pantalla → unidades de lienzo según el zoom real del canvas;
   * en contextos sin medición (pruebas) queda 1:1.
   */
  const canvasScale = () => {
    const bounds = canvasRef.current?.getBoundingClientRect();
    return {
      scaleX: bounds && bounds.width > 0 ? canvasWidth / bounds.width : 1,
      scaleY: bounds && bounds.height > 0 ? canvasHeight / bounds.height : 1,
    };
  };

  const trackEscape = (onFinish: () => void) => {
    const onKey = (keyEvent: KeyboardEvent) => {
      if (keyEvent.key === 'Escape') {
        keyEvent.preventDefault();
        onFinish();
      }
    };
    window.addEventListener('keydown', onKey);
    return onKey;
  };

  /**
   * Arrastre de handles con preview local y commit versionado al soltar.
   * Cada handle conserva su borde opuesto como ancla y se detiene en el
   * límite del canvas sin mover el ancla.
   */
  const beginResize = (event: React.PointerEvent<HTMLElement>, handle: ResizeHandleId) => {
    if (!canEdit) {return;}
    event.stopPropagation();
    event.preventDefault();
    const canvasEl = canvasRef.current;
    if (!canvasEl) {return;}
    const { scaleX, scaleY } = canvasScale();
    const start = { geometry: visual.geometry, x: event.clientX, y: event.clientY };
    const west = handle.includes('w');
    const east = handle.includes('e');
    const north = handle.includes('n');
    const south = handle.includes('s');
    let latest: VisualLayoutSpec['geometry'] | null = null;
    const apply = (clientX: number, clientY: number, altKey = false) => {
      const dx = (clientX - start.x) * scaleX;
      const dy = (clientY - start.y) * scaleY;
      const right = start.geometry.x + start.geometry.width;
      const bottom = start.geometry.y + start.geometry.height;
      let { x, y, width, height } = start.geometry;
      if (east) {width = Math.min(Math.max(width + dx, MIN_VISUAL_WIDTH), canvasWidth - x);}
      if (south) {height = Math.min(Math.max(height + dy, MIN_VISUAL_HEIGHT), canvasHeight - y);}
      if (west) { x = Math.min(Math.max(start.geometry.x + dx, 0), right - MIN_VISUAL_WIDTH); width = right - x; }
      if (north) { y = Math.min(Math.max(start.geometry.y + dy, 0), bottom - MIN_VISUAL_HEIGHT); height = bottom - y; }

      const guidesX: number[] = [];
      const guidesY: number[] = [];

      if (!altKey) {
        const threshold = 6;
        const gridSize = 10;
        const others = presentation.visuals.filter((v) => v.id !== visual.id);
        const xTargets = [0, Math.round(canvasWidth / 2), canvasWidth, ...others.flatMap((o) => [o.geometry.x, Math.round(o.geometry.x + o.geometry.width / 2), o.geometry.x + o.geometry.width])];
        const yTargets = [0, Math.round(canvasHeight / 2), canvasHeight, ...others.flatMap((o) => [o.geometry.y, Math.round(o.geometry.y + o.geometry.height / 2), o.geometry.y + o.geometry.height])];

        if (east) {
          const curR = x + width;
          let bestDiff = threshold + 1;
          let bestT: number | null = null;
          for (const t of xTargets) {
            const diff = Math.abs(curR - t);
            if (diff <= threshold && diff < bestDiff && t - x >= MIN_VISUAL_WIDTH && t <= canvasWidth) {
              bestDiff = diff;
              bestT = t;
            }
          }
          if (bestT !== null) {
            width = bestT - x;
            guidesX.push(bestT);
          } else if (gridSize > 0) {
            const gridR = Math.round(curR / gridSize) * gridSize;
            if (Math.abs(gridR - curR) <= threshold && gridR - x >= MIN_VISUAL_WIDTH && gridR <= canvasWidth) {
              width = gridR - x;
            }
          }
        } else if (west) {
          let bestDiff = threshold + 1;
          let bestT: number | null = null;
          for (const t of xTargets) {
            const diff = Math.abs(x - t);
            if (diff <= threshold && diff < bestDiff && right - t >= MIN_VISUAL_WIDTH && t >= 0) {
              bestDiff = diff;
              bestT = t;
            }
          }
          if (bestT !== null) {
            x = bestT;
            width = right - x;
            guidesX.push(bestT);
          } else if (gridSize > 0) {
            const gridX = Math.round(x / gridSize) * gridSize;
            if (Math.abs(gridX - x) <= threshold && right - gridX >= MIN_VISUAL_WIDTH && gridX >= 0) {
              x = gridX;
              width = right - x;
            }
          }
        }

        if (south) {
          const curB = y + height;
          let bestDiff = threshold + 1;
          let bestT: number | null = null;
          for (const t of yTargets) {
            const diff = Math.abs(curB - t);
            if (diff <= threshold && diff < bestDiff && t - y >= MIN_VISUAL_HEIGHT && t <= canvasHeight) {
              bestDiff = diff;
              bestT = t;
            }
          }
          if (bestT !== null) {
            height = bestT - y;
            guidesY.push(bestT);
          } else if (gridSize > 0) {
            const gridB = Math.round(curB / gridSize) * gridSize;
            if (Math.abs(gridB - curB) <= threshold && gridB - y >= MIN_VISUAL_HEIGHT && gridB <= canvasHeight) {
              height = gridB - y;
            }
          }
        } else if (north) {
          let bestDiff = threshold + 1;
          let bestT: number | null = null;
          for (const t of yTargets) {
            const diff = Math.abs(y - t);
            if (diff <= threshold && diff < bestDiff && bottom - t >= MIN_VISUAL_HEIGHT && t >= 0) {
              bestDiff = diff;
              bestT = t;
            }
          }
          if (bestT !== null) {
            y = bestT;
            height = bottom - y;
            guidesY.push(bestT);
          } else if (gridSize > 0) {
            const gridY = Math.round(y / gridSize) * gridSize;
            if (Math.abs(gridY - y) <= threshold && bottom - gridY >= MIN_VISUAL_HEIGHT && gridY >= 0) {
              y = gridY;
              height = bottom - y;
            }
          }
        }
        onGuidesChange({ x: guidesX, y: guidesY });
      } else {
        onGuidesChange({});
      }

      latest = {
        height: Math.round(height),
        width: Math.round(width),
        x: Math.round(x),
        y: Math.round(y),
      };
      setDraftGeometry(latest);
    };
    const onMovePointer = (moveEvent: PointerEvent) => apply(moveEvent.clientX, moveEvent.clientY, moveEvent.altKey);
    const finish = (commitChange: boolean) => {
      window.removeEventListener('pointermove', onMovePointer);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('keydown', onKey);
      setDraftGeometry(null);
      onGuidesChange({});
      const next = latest;
      const changed = next && (
        next.x !== start.geometry.x || next.y !== start.geometry.y
        || next.width !== start.geometry.width || next.height !== start.geometry.height
      );
      if (commitChange && next && changed) {
        onResize({ ...visual, geometry: next }, `Redimensionar ${visual.title}`);
      }
    };
    const onUp = (upEvent: PointerEvent) => {
      apply(upEvent.clientX, upEvent.clientY, upEvent.altKey);
      finish(true);
    };
    const onCancel = () => finish(false);
    const onKey = trackEscape(() => finish(false));
    window.addEventListener('pointermove', onMovePointer);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
  };

  /**
   * Arrastre del cuerpo del visual seleccionado para reposicionarlo: un clic
   * sin desplazamiento (umbral de 4px) sigue comportándose como selección.
   */
  const beginMove = (event: React.PointerEvent<HTMLElement>) => {
    if (!canEdit || !selected || event.button !== 0) {return;}
    const { scaleX, scaleY } = canvasScale();
    const start = { geometry: visual.geometry, x: event.clientX, y: event.clientY };
    let moved = false;
    let latest: VisualLayoutSpec['geometry'] | null = null;
    const apply = (clientX: number, clientY: number, altKey = false) => {
      if (!moved && Math.hypot(clientX - start.x, clientY - start.y) < 4) {return;}
      moved = true;
      let rawX = Math.min(Math.max(start.geometry.x + (clientX - start.x) * scaleX, 0), canvasWidth - start.geometry.width);
      let rawY = Math.min(Math.max(start.geometry.y + (clientY - start.y) * scaleY, 0), canvasHeight - start.geometry.height);

      if (!altKey) {
        const snap = computeSnap(
          { x: rawX, y: rawY, width: start.geometry.width, height: start.geometry.height },
          presentation.visuals.filter((v) => v.id !== visual.id),
          { width: canvasWidth, height: canvasHeight }
        );
        rawX = snap.x;
        rawY = snap.y;
        onGuidesChange({ x: snap.guidesX, y: snap.guidesY });
      } else {
        onGuidesChange({});
      }

      latest = { ...start.geometry, x: Math.round(rawX), y: Math.round(rawY) };
      setDraftGeometry(latest);
    };
    const finish = (commitChange: boolean) => {
      window.removeEventListener('pointermove', onMovePointer);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('keydown', onKey);
      setDraftGeometry(null);
      onGuidesChange({});
      movedRecently.current = moved;
      const next = latest;
      if (commitChange && moved && next
        && (next.x !== start.geometry.x || next.y !== start.geometry.y)) {
        onMove({ ...visual, geometry: next }, `Mover ${visual.title}`);
      }
    };
    const onMovePointer = (moveEvent: PointerEvent) => apply(moveEvent.clientX, moveEvent.clientY, moveEvent.altKey);
    const onUp = (upEvent: PointerEvent) => {
      apply(upEvent.clientX, upEvent.clientY, upEvent.altKey);
      finish(true);
    };
    const onCancel = () => finish(false);
    const onKey = trackEscape(() => finish(false));
    event.preventDefault();
    window.addEventListener('pointermove', onMovePointer);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
  };

  const openContextMenu = (event: React.MouseEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
    onSelect();
    onVisualContextMenu(event.clientX, event.clientY, visual.id);
  };

  return (
    <article
      aria-label={`Editar ${visual.title}`}
      aria-pressed={selected}
      className="dv-editable-visual"
      id={`dv-visual-${visual.id}`}
      data-selected={selected || undefined}
      data-show-title={String(visual.format_settings.show_title)}
      data-resizing={draftGeometry ? 'true' : undefined}
      data-can-move={selected && canEdit ? 'true' : undefined}
      data-moving={draftGeometry ? 'true' : undefined}
      onClickCapture={(event) => {
        if (movedRecently.current) {
          event.stopPropagation();
          event.preventDefault();
          movedRecently.current = false;
        }
      }}
      onClick={(event) => { event.stopPropagation(); onSelect(); }}
      onContextMenu={openContextMenu}
      onPointerDown={beginMove}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          if (event.key === ' ') {event.preventDefault();}
          onSelect();
        }
      }}
      role="button"
      style={style}
      tabIndex={0}
    >
      {!visual.format_settings.show_title && <span aria-hidden="true" className="dv-hidden-title">Título oculto</span>}
      <div className="dv-editable-content">{element}</div>
      {selected && canEdit && RESIZE_HANDLES.map((handle) => (
        <button
          aria-label={handle.label}
          className={`dv-resize-handle dv-resize-handle--${handle.id}`}
          data-resize-handle={handle.id}
          key={handle.id}
          onPointerDown={(event) => beginResize(event, handle.id)}
          tabIndex={-1}
          type="button"
        />
      ))}
    </article>
  );
};
