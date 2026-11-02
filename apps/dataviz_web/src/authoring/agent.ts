import { addFilter, addMetric } from './operations';
import { createVisual } from './seed';
import type {
  AgentProposal,
  Document,
  Metric,
  Operation,
  PresentationSnapshot,
  SemanticModel,
  VisualLayoutSpec,
  VisualOperation,
} from './types';
import { customVisualOpsCostWarnings } from '../visuals/custom/spec';
import type { CustomVisualSpec } from '../visuals/custom/spec';
import { addVisual, previewVisualOperations, updateVisual } from './visualOperations';
import { jsonEqual } from './jsonEqual';
import { getHeadVersion } from './versioning';

/**
 * Spec combinada de referencia: barras de ventas + línea de utilidad por región.
 * Roles canónicos (x_axis, y_axis, comparison_metric). max_data_points mantiene
 * el costo estructural dentro de ambos presupuestos (3000 celdas / 3000
 * unidades con los presupuestos 20000/5000).
 */
const COMBINED_CUSTOM_SPEC: CustomVisualSpec = {
  layers: [
    { mark: 'bar', x_role: 'x_axis', y_role: 'y_axis' },
    { mark: 'line', x_role: 'x_axis', y_role: 'comparison_metric', show_points: true },
  ],
  max_data_points: 1000,
  schema_version: '1.0.0',
};

/**
 * Convert a bounded natural-language intent into typed operations.
 *
 * This is deliberately deterministic and provider-neutral. A future LLM may
 * select the same allowlisted recipes, but it never receives an apply/publish
 * capability and it cannot emit arbitrary SQL or executable code.
 */
export function planAgentProposal(document: Document, prompt: string): AgentProposal {
  const head = getHeadVersion(document);
  const normalized = normalize(prompt);
  if (!head.model.metrics.some((metric) => metric.name === 'Sales')) {
    return planDatasetProposal(document, prompt, normalized);
  }
  const semanticOps: Operation[] = [];
  const visualOps: VisualOperation[] = [];
  const rationale: string[] = [];
  const warnings = [
    'Las instrucciones disponibles usan el modelo de ventas de muestra; revisa que sus campos existan en tu proyecto.',
    'Nada se publica al aplicar la propuesta: se guarda una versión del borrador. Publicar requiere una solicitud y aprobación aparte.',
  ];

  if (contains(normalized, ['ejecutiv', 'executive', 'resumen', 'overview', 'direccion'])) {
    const existingSalesCard = findBoundVisual(head.presentation, ['card', 'kpi'], {
      value: 'measure:Sales',
    });
    if (existingSalesCard) {
      const aligned = {
        ...existingSalesCard,
        geometry: { height: 164, width: 360, x: 24, y: 24 },
      };
      if (!jsonEqual(aligned, existingSalesCard)) {
        visualOps.push(updateVisual(aligned));
      }
      rationale.push(`Reutiliza el KPI existente «${existingSalesCard.title}» en lugar de duplicarlo.`);
    } else {
      addIfMissing(head.presentation, visualOps, createVisual('card', 0, {
        data_roles: { value: 'measure:Sales' }, geometry: { height: 164, width: 360, x: 24, y: 24 }, id: 'agent-sales-kpi', name: 'KPI Ventas', title: 'Ventas totales',
      }));
    }
    addIfMissing(head.presentation, visualOps, createVisual('card', 1, {
      data_roles: { value: 'measure:Profit' }, geometry: { height: 164, width: 360, x: 404, y: 24 }, id: 'agent-profit-kpi', name: 'KPI Utilidad', title: 'Utilidad',
    }));
    addIfMissing(head.presentation, visualOps, createVisual('line', 2, {
      data_roles: { category: 'field:OrderDate', value: 'measure:Sales' }, geometry: { height: 280, width: 740, x: 24, y: 212 }, id: 'agent-sales-trend', name: 'Tendencia de ventas', title: 'Ventas por mes',
    }));
    addIfMissing(head.presentation, visualOps, createVisual('bar', 3, {
      data_roles: { category: 'field:Region', value: 'measure:Sales' }, geometry: { height: 468, width: 392, x: 784, y: 24 }, id: 'agent-region-bars', name: 'Ventas por región', title: 'Ventas por región',
    }));
    rationale.push('Prioriza dos KPI, una tendencia temporal y una comparación regional.');
  } else if (contains(normalized, ['custom', 'personaliza', 'combina', 'superp'])) {
    addIfMissing(head.presentation, visualOps, createVisual('custom_visual', head.presentation.visuals.length, {
      custom_spec: COMBINED_CUSTOM_SPEC,
      data_roles: { comparison_metric: 'measure:Profit', x_axis: 'field:Region', y_axis: 'measure:Sales' },
      geometry: { height: 320, width: 752, x: 24, y: 24 },
      id: nextId(head.presentation, 'custom'),
      name: 'Combinado ventas y utilidad',
      title: 'Ventas y utilidad por región',
    }));
    rationale.push('Combina barras de ventas y línea de utilidad por región en un único visual declarativo con capas.');
  } else if (contains(normalized, ['tendencia', 'trend', 'tiempo', 'mensual', 'linea'])) {
    visualOps.push(addVisual(createVisual('line', head.presentation.visuals.length, {
      data_roles: { category: 'field:OrderDate', value: 'measure:Sales' },
      id: nextId(head.presentation, 'trend'),
      name: 'Tendencia de ventas',
      title: 'Evolución mensual de ventas',
    })));
    rationale.push('Una línea mantiene el orden temporal y hace visible la dirección del cambio.');
  } else if (contains(normalized, ['region', 'compar', 'barra', 'bar'])) {
    visualOps.push(addVisual(createVisual('bar', head.presentation.visuals.length, {
      data_roles: { category: 'field:Region', value: 'measure:Sales' },
      id: nextId(head.presentation, 'regions'),
      name: 'Comparación regional',
      title: 'Ventas por región',
    })));
    rationale.push('Las barras facilitan comparar magnitudes entre categorías discretas.');
  } else if (contains(normalized, ['margen', 'margin'])) {
    if (!head.model.metrics.some((metric) => metric.name === 'ProfitMargin')) {
      const metric: Metric = {
        data_type: 'decimal',
        description: 'Utilidad dividida por ventas.',
        expression: {
          children: [
            { kind: 'measure_ref', name: 'Profit' },
            { kind: 'measure_ref', name: 'Sales' },
          ], kind: 'binary', op: '/',
        },
        format_string: '0.0%',
        name: 'ProfitMargin',
      };
      semanticOps.push(addMetric(metric));
    }
    visualOps.push(addVisual(createVisual('card', head.presentation.visuals.length, {
      data_roles: { value: 'measure:ProfitMargin' },
      id: nextId(head.presentation, 'margin'),
      name: 'KPI Margen',
      title: 'Margen de utilidad',
    })));
    rationale.push('Crea una métrica explícita y la presenta como KPI revisable.');
  } else if (contains(normalized, ['filtro', 'filter'])) {
    if (!head.model.filters.some((filter) => filter.name === 'RegionFilter')) {
      semanticOps.push(addFilter({
        description: 'Filtro regional propuesto por el asistente.',
        name: 'RegionFilter',
        operator: 'in',
        target: { entity: 'sales', kind: 'field_ref', name: 'Region' },
        values: ['Norte', 'Centro', 'Sur', 'Oriente'],
      }));
    }
    rationale.push('Añade un filtro semántico allowlisted sin generar SQL libre.');
  } else if (contains(normalized, ['orden', 'layout', 'aline', 'limpi', 'simplif'])) {
    head.presentation.visuals.forEach((visual, index) => {
      visualOps.push(updateVisual({
        ...visual,
        geometry: {
          height: visual.kind === 'card' || visual.kind === 'kpi' ? 180 : 244,
          width: 552,
          x: 24 + (index % 2) * 576,
          y: 24 + Math.floor(index / 2) * 268,
        },
      }));
    });
    rationale.push('Alinea el contenido en una retícula de dos columnas sin borrar visuales.');
  } else if (contains(normalized, ['color', 'accesib', 'contraste', 'marca'])) {
    head.presentation.visuals.forEach((visual) => {
      visualOps.push(updateVisual({
        ...visual,
        format_settings: {
          ...visual.format_settings,
          accent_color: '#0b6bcb',
          background_color: '#ffffff',
          text_color: '#102a43',
        },
      }));
    });
    rationale.push('Propone una paleta contenida con contraste alto para texto y selección.');
  } else {
    rationale.push(/\b(hola|hello|hi|buenos dias|buenas tardes|buenas noches)\b/.test(normalized)
      ? '¡Hola! Puedo proponer un resumen ejecutivo, una tendencia de ventas o una comparación por región. Elige un ejemplo o escribe una de esas instrucciones. No he cambiado tu dashboard.'
      : 'No reconozco una instrucción de edición en ese mensaje. Prueba con uno de los ejemplos disponibles. No he cambiado tu dashboard.');
  }

  warnings.push(...referenceWarnings(head.model, semanticOps, visualOps), ...customVisualOpsCostWarnings(visualOps));
  const visualChanges = previewVisualOperations(head.presentation, visualOps);
  return {
    base_version: head.number,
    prompt,
    proposal_id: `proposal-v${head.number}-${hash(normalized || 'default')}`,
    rationale,
    semantic_ops: semanticOps,
    summary: semanticOps.length || visualOps.length
      ? proposalSummary(semanticOps.length, visualChanges)
      : rationale.join(' ') || 'No hay cambios pendientes para esa instrucción.',
    visual_changes: visualChanges,
    visual_ops: visualOps,
    warnings,
  };
}

/** Propone ediciones locales con referencias del documento, sin asumir un modelo de ventas. */
function planDatasetProposal(document: Document, prompt: string, normalized: string): AgentProposal {
  const head = getHeadVersion(document);
  const fields = head.model.entities.flatMap((entity) => entity.fields);
  const metricRef = head.presentation.visuals.find((visual) => visual.data_roles.value?.startsWith('measure:'))?.data_roles.value
    ?? (head.model.metrics[0] ? `measure:${head.model.metrics[0].name}` : undefined);
  const category = fields.find((field) => field.data_type === 'string')?.name;
  const date = fields.find((field) => field.data_type === 'date' || field.data_type === 'datetime')?.name;
  const operations: VisualOperation[] = [];
  let summary = 'Puedo proponer una tendencia, comparar categorías, mejorar el contraste u ordenar los visuales. Los cambios requieren tu aprobación.';
  if (contains(normalized, ['orden', 'layout', 'aline', 'simplif'])) {
    const rows = Math.max(1, Math.ceil(head.presentation.visuals.length / 2));
    const width = (head.presentation.canvas.width - 72) / 2;
    const height = (head.presentation.canvas.height - 24 * (rows + 1)) / rows;
    if (height >= 100) {
      head.presentation.visuals.forEach((visual, index) => operations.push(updateVisual({
        ...visual, geometry: { x: 24 + index % 2 * (width + 24),
          y: 24 + Math.floor(index / 2) * (height + 24), width, height },
      })));
      summary = 'Propongo ordenar los visuales dentro del canvas.';
    } else {summary = 'Hay demasiados visuales para ordenarlos con un tamaño legible. Elimina algunos o usa otra página.';}
  } else if (contains(normalized, ['color', 'contraste', 'accesib'])) {
    head.presentation.visuals.forEach((visual) => operations.push(updateVisual({ ...visual,
      format_settings: { ...visual.format_settings, accent_color: '#0b6bcb', background_color: '#ffffff', text_color: '#102a43' },
    })));
    summary = 'Propongo mejorar el contraste de los visuales.';
  } else if (metricRef && contains(normalized, ['tendencia', 'trend', 'compar', 'barra', 'bar'])) {
    const trend = contains(normalized, ['tendencia', 'trend']);
    const field = trend ? date : category;
    const existing = head.presentation.visuals.find((visual) => visual.kind === (trend ? 'line' : 'bar'));
    if (field && existing) {
      operations.push(updateVisual({ ...existing, data_roles: { category: `field:${field}`, value: metricRef },
        title: `${metricRef.slice('measure:'.length).replace(/^Sum_/, '')} por ${field}` }));
      summary = 'Propongo actualizar el gráfico con campos y métricas del documento.';
    } else {summary = 'Agrega un gráfico y los campos necesarios desde el inspector para poder proponer esta edición.';}
  }
  const visualOps = operations.filter((operation) => !jsonEqual(
    head.presentation.visuals.find((visual) => visual.id === operation.visual_id), operation.payload,
  ));
  return { base_version: head.number, prompt, proposal_id: `proposal-v${head.number}-${hash(normalized)}`,
    rationale: [summary], semantic_ops: [], summary, visual_ops: visualOps,
    visual_changes: previewVisualOperations(head.presentation, visualOps),
    warnings: ['Reglas locales: no se consultó un modelo de IA.', 'Aplicar guarda un borrador; no publica.'] };
}

function addIfMissing(
  presentation: PresentationSnapshot,
  operations: VisualOperation[],
  visual: VisualLayoutSpec,
): void {
  if (
    !presentation.visuals.some((candidate) => candidate.id === visual.id)
    && !findBoundVisual(presentation, equivalentKinds(visual.kind), visual.data_roles)
  ) {
    operations.push(addVisual(visual));
  }
}

function findBoundVisual(
  presentation: PresentationSnapshot,
  kinds: string[],
  roles: Record<string, string>,
): VisualLayoutSpec | undefined {
  return presentation.visuals.find((candidate) => (
    kinds.includes(candidate.kind) && jsonEqual(candidate.data_roles, roles)
  ));
}

function equivalentKinds(kind: string): string[] {
  return kind === 'card' || kind === 'kpi' ? ['card', 'kpi'] : [kind];
}

function nextId(presentation: PresentationSnapshot, stem: string): string {
  let index = presentation.visuals.length + 1;
  while (presentation.visuals.some((visual) => visual.id === `agent-${stem}-${index}`)) {index += 1;}
  return `agent-${stem}-${index}`;
}

function normalize(value: string): string {
  return value.normalize('NFD').replaceAll(/[\u0300-\u036F]/g, '').toLowerCase();
}

function referenceWarnings(
  model: SemanticModel,
  semanticOps: Operation[],
  visualOps: VisualOperation[],
): string[] {
  const fields = new Set(model.entities.flatMap((entity) => entity.fields.map((field) => field.name)));
  const measures = new Set(model.metrics.map((metric) => metric.name));
  for (const operation of semanticOps) {
    if (operation.target === 'metric' && operation.kind !== 'remove') {measures.add(operation.name);}
  }
  const unresolved = new Set<string>();
  for (const operation of visualOps) {
    for (const reference of Object.values(operation.payload?.data_roles ?? {})) {
      const [kind, name] = reference.split(':', 2);
      if (kind === 'field' && !fields.has(name)) {unresolved.add(reference);}
      if (kind === 'measure' && !measures.has(name)) {unresolved.add(reference);}
    }
  }
  return [...unresolved].map((reference) => (
    `No se pudo resolver ${reference} en el modelo actual; el visual puede quedar sin datos.`
  ));
}

function contains(value: string, words: string[]): boolean {
  return words.some((word) => value.includes(word));
}

function proposalSummary(semanticCount: number, changes: AgentProposal['visual_changes']): string {
  const added = changes.filter((change) => change.kind === 'added').length;
  const changed = changes.filter((change) => change.kind === 'changed').length;
  const removed = changes.filter((change) => change.kind === 'removed').length;
  return `${added} visual(es) nuevo(s), ${changed} ajustado(s), ${removed} eliminado(s) y ${semanticCount} cambio(s) semántico(s).`;
}

function hash(value: string): string {
  let result = 2_166_136_261;
  for (let index = 0; index < value.length; index += 1) {
    result ^= value.charCodeAt(index);
    result = Math.imul(result, 16_777_619);
  }
  return (result >>> 0).toString(36);
}
