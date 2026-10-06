import type {
  PresentationSnapshot,
  VisualChange,
  VisualLayoutSpec,
  VisualOperation,
} from './types';
import { jsonEqual } from './jsonEqual';

/** Build a typed visual add operation. */
export function addVisual(visual: VisualLayoutSpec): VisualOperation {
  return { kind: 'add', payload: visual, visual_id: visual.id };
}

/** Build a typed visual update operation. */
export function updateVisual(visual: VisualLayoutSpec): VisualOperation {
  return { kind: 'update', payload: visual, visual_id: visual.id };
}

/** Build a typed visual removal operation. */
export function removeVisual(visualId: string): VisualOperation {
  return { kind: 'remove', visual_id: visualId };
}

/** Apply a batch atomically and preserve the canvas contract. */
export function materializeVisuals(
  presentation: PresentationSnapshot,
  operations: VisualOperation[],
): PresentationSnapshot {
  const visuals = new Map(presentation.visuals.map((visual) => [visual.id, visual]));
  for (const operation of operations) {
    const exists = visuals.has(operation.visual_id);
    if (operation.kind === 'remove') {
      if (!exists) {throw new Error(`cannot remove visual "${operation.visual_id}": not found`);}
      if (operation.payload !== undefined) {throw new Error('remove visual ops must not carry payload');}
      visuals.delete(operation.visual_id);
      continue;
    }
    if (!operation.payload || operation.payload.id !== operation.visual_id) {
      throw new Error(`${operation.kind} visual op requires a matching payload`);
    }
    if (operation.kind === 'add' && exists) {
      throw new Error(`cannot add visual "${operation.visual_id}": already exists`);
    }
    if (operation.kind === 'update' && !exists) {
      throw new Error(`cannot update visual "${operation.visual_id}": not found`);
    }
    visuals.set(operation.visual_id, operation.payload);
  }
  const names = new Set<string>();
  for (const visual of visuals.values()) {
    if (names.has(visual.name)) {
      throw new Error(`visual name "${visual.name}" must be unique`);
    }
    names.add(visual.name);
  }
  return { ...presentation, visuals: [...visuals.values()] };
}

/** Return the human-reviewable presentation diff without mutating state. */
export function previewVisualOperations(
  presentation: PresentationSnapshot,
  operations: VisualOperation[],
): VisualChange[] {
  const after = materializeVisuals(presentation, operations);
  const beforeById = new Map(presentation.visuals.map((visual) => [visual.id, visual]));
  const afterById = new Map(after.visuals.map((visual) => [visual.id, visual]));
  const changes: VisualChange[] = [];
  for (const [id, visual] of afterById) {
    const before = beforeById.get(id);
    if (!before) {changes.push({ kind: 'added', visual_id: id, after: visual });}
    else if (!jsonEqual(before, visual)) {
      changes.push({ after: visual, before, kind: 'changed', visual_id: id });
    }
  }
  for (const [id, visual] of beforeById) {
    if (!afterById.has(id)) {changes.push({ kind: 'removed', visual_id: id, before: visual });}
  }
  return changes;
}
