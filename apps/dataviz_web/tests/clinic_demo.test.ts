import { describe, expect, it } from 'vitest';
import { createClinicDemo } from '../src/authoring/clinicDemo';
import { compileLocalRuntimePayload } from '../src/authoring/localRuntime';
import { addMetric } from '../src/authoring/operations';
import { applyOps } from '../src/authoring/versioning';
import { planAgentProposal } from '../src/authoring/agent';

describe('Clinic X public demo', () => {
  it('proposes edits against actual dataset fields and keeps layout within the canvas', () => {
    const { document } = createClinicDemo();
    const proposal = planAgentProposal(document, 'Compara categorías');
    expect(proposal.visual_ops[0].payload?.data_roles).toEqual({ category: 'field:Sede', value: 'measure:Sum_Atendidas' });
    expect(proposal.warnings.join(' ')).not.toContain('No se pudo resolver');
    for (const op of planAgentProposal(document, 'Ordena el layout').visual_ops) {
      const geometry = op.payload!.geometry;
      expect(geometry.x + geometry.width).toBeLessThanOrEqual(1200);
      expect(geometry.y + geometry.height).toBeLessThanOrEqual(760);
    }
    expect(document.versions).toHaveLength(1);
  });
  it('renders the complete fictional dataset with independently checked totals', () => {
    const { dataset, document } = createClinicDemo();
    expect(dataset.rows).toHaveLength(1080);
    expect(dataset.rows.every((row) => Number(row.Atendidas) + Number(row.Canceladas) <= Number(row.Agendadas)
      && Number(row.Agendadas) <= Number(row.Capacidad))).toBe(true);
    const runtime = compileLocalRuntimePayload(document, undefined, dataset);
    const total = dataset.rows.reduce((sum, row) => sum + Number(row.Atendidas), 0);
    expect(runtime.results.visuals.Atenciones['measure:Sum_Atendidas']).toBe(total);
    const grouped = runtime.results.visuals.Especialidades['measure:Sum_Atendidas'] as number[];
    expect(grouped.reduce((sum, value) => sum + value, 0)).toBe(total);
    expect(runtime.plan.visuals).toHaveLength(7);
  });

  it('creates an editable metric without replacing the clinic dataset', () => {
    const { dataset, document } = createClinicDemo();
    const model = document.versions[0].model;
    const metric = { ...model.metrics.find((metric) => metric.name === 'Sum_Atendidas')!, name: 'Mis_atenciones' };
    const changed = applyOps(document, [addMetric(metric)], { message: 'Crear métrica' });
    expect(changed.versions.at(-1)?.model.metrics.some((metric) => metric.name === 'Mis_atenciones')).toBe(true);
    expect(compileLocalRuntimePayload(changed, undefined, dataset).results.visuals.Atenciones['measure:Sum_Atendidas'])
      .toBe(compileLocalRuntimePayload(document, undefined, dataset).results.visuals.Atenciones['measure:Sum_Atendidas']);
  });
});
