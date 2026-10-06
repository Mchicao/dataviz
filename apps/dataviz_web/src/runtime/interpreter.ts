/**
 * Render Plan interpreter.
 *
 * Pure data layer: validates the canonical RenderPlan, resolves each visual's
 * `data_roles` against external results isolated per visual, and emits `InterpretedVisual`s
 * ready for rendering. Performs no DOM work and reads no source files.
 *
 * Validation mirrors the Python contract: exact schema_version, required +
 * unique visual names. Unknown / unimplemented kinds are not rejected -- they
 * are flagged `supported: false` so the renderer can show an explicit
 * Unsupported fallback (graceful degradation, never a crash).
 */

import type {
  InterpretedRole,
  InterpretedVisual,
  QueryResults,
  RenderPlan,
  RuntimeResults,
  VisualSpec,
} from './types';
import {
  RENDER_PLAN_SCHEMA_VERSION,
  RUNTIME_RESULTS_SCHEMA_VERSION,
  SUPPORTED_VISUAL_KINDS,
} from './types';
import { parseCustomVisualSpec } from '../visuals/custom/spec';
import type { CustomVisualSpec } from '../visuals/custom/spec';

const SUPPORTED: ReadonlySet<string> = new Set(SUPPORTED_VISUAL_KINDS);

const EMPTY_RESULTS: RuntimeResults = {
  schema_version: RUNTIME_RESULTS_SCHEMA_VERSION,
  visuals: {},
};

/**
 * Valida la spec declarativa fail-closed. Una spec inválida se descarta
 * (degrada a estado explícito en el componente) en lugar de tumbar el plan:
 * nunca se renderiza contenido no validado.
 */
// `unknown` es deliberado: este es el parser fail-closed de la frontera.
// oxlint-disable-next-line anti-slop/no-unknown-parameters
function safeParseCustomSpec(payload: unknown): CustomVisualSpec | undefined {
  if (payload === undefined || payload === null) {return undefined;}
  try {
    return parseCustomVisualSpec(payload);
  } catch {
    return undefined;
  }
}

export class RenderPlanInterpreter {
  private readonly results: RuntimeResults;

  /**
   * @param results query results keyed by neutral reference. Defaults to empty
   *   (all roles resolve to `undefined` -> renderers show an empty state).
   */
  constructor(results: RuntimeResults = EMPTY_RESULTS) {
    this.results = results;
  }

  /** Interpret every visual in `plan` into a renderable form. */
  interpret(plan: RenderPlan): InterpretedVisual[] {
    if (plan.schema_version !== RENDER_PLAN_SCHEMA_VERSION) {
      throw new Error(
        `schema_version incompatible: expected "${RENDER_PLAN_SCHEMA_VERSION}", received "${String(
          plan.schema_version ?? '',
        )}"`,
      );
    }
    const seen = new Set<string>();
    const out: InterpretedVisual[] = [];
    for (const spec of plan.visuals) {
      out.push(this.interpretVisual(spec, seen));
    }
    return out;
  }

  private interpretVisual(spec: VisualSpec, seen: Set<string>): InterpretedVisual {
    const name = (spec.name ?? '').trim();
    if (!name) {throw new Error('visual.name is required');}
    if (seen.has(name)) {throw new Error(`duplicate visual name: "${name}"`);}
    seen.add(name);

    const rawTitle = spec.title;
    return {
      custom_spec: safeParseCustomSpec(spec.custom_spec),
      geometry: { ...spec.geometry },
      kind: spec.kind,
      name,
      roles: RenderPlanInterpreter.resolveRoles(spec.data_roles ?? {}, this.results.visuals[name] ?? {}),
      supported: SUPPORTED.has(spec.kind),
      title: rawTitle || titleFromName(name),
    };
  }

  private static resolveRoles(
    dataRoles: Record<string, string>,
    results: QueryResults,
  ): Record<string, InterpretedRole> {
    const roles: Record<string, InterpretedRole> = {};
    for (const [roleName, ref] of Object.entries(dataRoles)) {
      // R-01: each role binds a neutral reference to its query result (if any).
      roles[roleName] = { data: results[ref], ref };
    }
    // Diccionario dinámico por diseño: las claves son los roles del plan.
    // oxlint-disable-next-line anti-slop/no-known-value-widening
    return roles;
  }
}

/** Derive a human-readable title from a visual name (snake/kebab -> words). */
export function titleFromName(name: string): string {
  return name.replaceAll(/[_-]+/g, ' ').replaceAll(/\s+/g, ' ').trim();
}

