import { datasetFingerprint, parseLocalCsv, semanticModelForDataset } from './csv';
import { createVisual } from './seed';
import { initDocument } from './versioning';

export const CLINIC_STORAGE_KEY = 'dataviz.clinic-x.v1';

/** Datos inventados desde cero; ninguna fila, identidad o regla procede de un cliente. */
export function createClinicDemo() {
  const rows = ['Fecha,Sede,Especialidad,Capacidad,Agendadas,Atendidas,Canceladas,Ingresos'];
  const specialties = ['Medicina general', 'Cardiología', 'Dermatología', 'Oftalmología'];
  for (let day = 0; day < 90; day += 1) {
    const date = new Date(Date.UTC(2026, 0, day + 1)).toISOString().slice(0, 10);
    for (let branch = 0; branch < 3; branch += 1) {
      for (let specialty = 0; specialty < specialties.length; specialty += 1) {
        const capacity = 24;
        const scheduled = 15 + ((day * 7 + branch * 3 + specialty * 5) % 10);
        const cancelled = 1 + ((day + branch + specialty) % 4);
        const attended = scheduled - cancelled - ((day + specialty) % 3);
        const revenue = attended * (18000 + specialty * 7000);
        rows.push([date, ['Norte', 'Centro', 'Sur'][branch], specialties[specialty], capacity,
          scheduled, attended, cancelled, revenue].join(','));
      }
    }
  }
  const dataset = parseLocalCsv(rows.join('\n'), 'clinica_x.csv');
  dataset.imported_at = '2026-01-01T00:00:00.000Z';
  const model = semanticModelForDataset(dataset);
  model.description = 'Clínica X: operaciones ficticias generadas desde cero, sin datos personales ni de clientes.';
  const visuals = [
    createVisual('card', 0, { name: 'Atenciones', title: 'Atenciones',
      data_roles: { value: 'measure:Sum_Atendidas' },
      geometry: { x: 24, y: 24, width: 368, height: 130 } }),
    createVisual('card', 1, { name: 'Cancelaciones', title: 'Cancelaciones',
      data_roles: { value: 'measure:Sum_Canceladas' },
      geometry: { x: 416, y: 24, width: 368, height: 130 } }),
    createVisual('card', 2, { name: 'Ingresos', title: 'Ingresos ficticios (CLP)',
      data_roles: { value: 'measure:Sum_Ingresos' },
      geometry: { x: 808, y: 24, width: 368, height: 130 } }),
    createVisual('bar', 3, { name: 'Especialidades', title: 'Atenciones por especialidad',
      data_roles: { category: 'field:Especialidad', value: 'measure:Sum_Atendidas' },
      geometry: { x: 24, y: 178, width: 564, height: 260 } }),
    createVisual('column', 4, { name: 'Sedes', title: 'Atenciones por sede',
      data_roles: { category: 'field:Sede', value: 'measure:Sum_Atendidas' },
      geometry: { x: 612, y: 178, width: 564, height: 260 } }),
    createVisual('line', 5, { name: 'Evolucion', title: 'Atenciones diarias',
      data_roles: { category: 'field:Fecha', value: 'measure:Sum_Atendidas' },
      geometry: { x: 24, y: 462, width: 740, height: 274 } }),
    createVisual('donut', 6, { name: 'Distribucion', title: 'Atenciones por sede',
      data_roles: { category: 'field:Sede', value: 'measure:Sum_Atendidas' },
      geometry: { x: 788, y: 462, width: 388, height: 274 } }),
  ];
  const document = initDocument(model, {
    doc_id: 'clinica-x', message: 'Demo ficticia de Clínica X', visuals,
  });
  document.versions[0].dataset_fingerprint = datasetFingerprint(dataset);
  document.versions[0].presentation_ir.title = 'Clínica X';
  return { dataset, document };
}
