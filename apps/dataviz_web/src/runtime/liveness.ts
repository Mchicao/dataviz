import type {
  LivenessPolicy,
  QueryResult,
  RenderPlan,
  RuntimeResults,
  VisualLivenessRecord,
} from './types';

/** Text is decorative; every data visual must expose at least one rendered mark. */
function livenessPolicyFor(kind: string): LivenessPolicy {
  return kind === 'text_box' ? 'decorative' : 'requires_marks';
}

function resultSize(value: QueryResult | undefined): number {
  if (value === null || value === undefined) {return 0;}
  if (Array.isArray(value)) {return value.length;}
  return 1;
}

function renderedMarkCount(kind: string, rowCount: number, policy: LivenessPolicy): number {
  if (policy === 'decorative') {return 1;}
  if (kind === 'table') {return Math.min(rowCount, 100);}
  return rowCount;
}

/** Build a cheap, deterministic manifest for E2E evidence collection. */
export function buildLivenessManifest(
  plan: RenderPlan,
  results: RuntimeResults,
): VisualLivenessRecord[] {
  return plan.visuals.map((visual) => {
    const compactTable = visual.kind === 'table' && Number(visual.geometry?.height ?? 48) < 48;
    const policy = visual.liveness_policy
      ?? (compactTable ? 'decorative' : livenessPolicyFor(visual.kind));
    const roleResults = results.visuals[visual.name] ?? {};
    const count = Math.max(0, ...Object.values(roleResults).map(resultSize));
    return {
      error: '',
      mark_count: renderedMarkCount(visual.kind, count, policy),
      policy,
      row_count: count,
      visual_id: visual.name,
    };
  });
}
