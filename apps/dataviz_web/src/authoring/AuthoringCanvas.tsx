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
            {presentation.visuals.map((visual, index) => (
              <VisualFrame
                canEdit={canEdit}
                canvasRef={canvasRef}
                element={rendered[index]}
                key={visual.id}
                onMove={onMove}
                onResize={onResize}
                onVisualContextMenu={onVisualContextMenu}
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
  onSelect: () => void;
  presentation: PresentationSnapshot;
  selected: boolean;
  visual: VisualLayoutSpec;
}> = ({ canEdit, canvasRef, element, onMove, onResize, onVisualContextMenu, onSelect, presentation, selected, visual }) => {
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
    const apply = (clientX: number, clientY: number) => {
      const dx = (clientX - start.x) * scaleX;
      const dy = (clientY - start.y) * scaleY;
      const right = start.geometry.x + start.geometry.width;
      const bottom = start.geometry.y + start.geometry.height;
      let { x, y, width, height } = start.geometry;
      if (east) {width = Math.min(Math.max(width + dx, MIN_VISUAL_WIDTH), canvasWidth - x);}
      if (south) {height = Math.min(Math.max(height + dy, MIN_VISUAL_HEIGHT), canvasHeight - y);}
      if (west) { x = Math.min(Math.max(start.geometry.x + dx, 0), right - MIN_VISUAL_WIDTH); width = right - x; }
      if (north) { y = Math.min(Math.max(start.geometry.y + dy, 0), bottom - MIN_VISUAL_HEIGHT); height = bottom - y; }
      latest = {
        height: Math.round(height),
        width: Math.round(width),
        x: Math.round(x),
        y: Math.round(y),
      };
      setDraftGeometry(latest);
    };
    const onMovePointer = (moveEvent: PointerEvent) => apply(moveEvent.clientX, moveEvent.clientY);
    const finish = (commitChange: boolean) => {
      window.removeEventListener('pointermove', onMovePointer);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('keydown', onKey);
      setDraftGeometry(null);
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
      apply(upEvent.clientX, upEvent.clientY);
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
    const apply = (clientX: number, clientY: number) => {
      if (!moved && Math.hypot(clientX - start.x, clientY - start.y) < 4) {return;}
      moved = true;
      const x = Math.min(Math.max(start.geometry.x + (clientX - start.x) * scaleX, 0), canvasWidth - start.geometry.width);
      const y = Math.min(Math.max(start.geometry.y + (clientY - start.y) * scaleY, 0), canvasHeight - start.geometry.height);
      latest = { ...start.geometry, x: Math.round(x), y: Math.round(y) };
      setDraftGeometry(latest);
    };
    const finish = (commitChange: boolean) => {
      window.removeEventListener('pointermove', onMovePointer);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('keydown', onKey);
      setDraftGeometry(null);
      movedRecently.current = moved;
      const next = latest;
      if (commitChange && moved && next
        && (next.x !== start.geometry.x || next.y !== start.geometry.y)) {
        onMove({ ...visual, geometry: next }, `Mover ${visual.title}`);
      }
    };
    const onMovePointer = (moveEvent: PointerEvent) => apply(moveEvent.clientX, moveEvent.clientY);
    const onUp = (upEvent: PointerEvent) => {
      apply(upEvent.clientX, upEvent.clientY);
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
