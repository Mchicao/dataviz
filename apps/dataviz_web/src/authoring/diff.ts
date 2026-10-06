import type { Change, Operation, SemanticDiff, SemanticModel, TargetKind } from './types';
import { jsonEqual } from './jsonEqual';
import { materialize } from './operations';

const TARGET_SECTIONS: Record<TargetKind, keyof SemanticModel> = {
  entity: 'entities',
  filter: 'filters',
  metric: 'metrics',
  parameter: 'parameters',
  relationship: 'relationships',
  rls_intent: 'rls_intents',
};

function indexSection(
  model: SemanticModel,
  target: TargetKind,
): Map<string, unknown> {
  const sectionKey = TARGET_SECTIONS[target];
  const list = (model[sectionKey] as { name: string }[]) || [];
  const indexed = new Map<string, unknown>();
  for (const item of list) {
    if (indexed.has(item.name)) {throw new Error(`duplicate ${target} name "${item.name}"`);}
    indexed.set(item.name, item);
  }
  return indexed;
}

/** Compute the structured semantic diff between two model snapshots by stable IR id. */
export function diff(before: SemanticModel, after: SemanticModel): SemanticDiff {
  const changes: Change[] = [];
  const targets: TargetKind[] = [
    'entity',
    'metric',
    'parameter',
    'filter',
    'relationship',
    'rls_intent',
  ];

  for (const target of targets) {
    const a = indexSection(before, target);
    const b = indexSection(after, target);

    // Added
    for (const [name, afterVal] of b.entries()) {
      if (!a.has(name)) {
        changes.push({ after: afterVal, kind: 'added', name, target });
      }
    }

    // Removed
    for (const [name, beforeVal] of a.entries()) {
      if (!b.has(name)) {
        changes.push({ before: beforeVal, kind: 'removed', name, target });
      }
    }

    // Changed
    for (const [name, beforeVal] of a.entries()) {
      if (b.has(name)) {
        const afterVal = b.get(name);
        if (!jsonEqual(beforeVal, afterVal)) {
          changes.push({ after: afterVal, before: beforeVal, kind: 'changed', name, target });
        }
      }
    }
  }

  const added = changes.filter((c) => c.kind === 'added');
  const removed = changes.filter((c) => c.kind === 'removed');
  const changed = changes.filter((c) => c.kind === 'changed');

  return {
    added,
    changed,
    changes,
    is_empty: changes.length === 0,
    removed,
  };
}

/** Preview the diff resulting from applying ops to model without mutating model. */
export function preview(model: SemanticModel, ops: Operation[]): SemanticDiff {
  const nextModel = materialize(model, ops);
  return diff(model, nextModel);
}
