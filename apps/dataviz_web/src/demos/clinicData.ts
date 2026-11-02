import { parseLocalCsv } from '../authoring/csv';

export const DEMO_PERIOD = '2026-01-01 / 2026-03-31';
export const DEMO_VERSION = 'clinic-x-2026-q1-v1';
export const BRANCHES = ['Norte', 'Centro', 'Sur'] as const;
export const MONTHS = ['2026-01', '2026-02', '2026-03'] as const;

/** Generación independiente y reproducible; no utiliza entradas ni identidades de clientes. */
export function createClinicDataset() {
  const rows = ['Fecha,Sede,Especialidad,Capacidad,Agendadas,Atendidas,Canceladas,Ingresos'];
  const specialties = ['Medicina general', 'Cardiología', 'Dermatología', 'Oftalmología'];
  for (let day = 0; day < 90; day += 1) {
    const date = new Date(Date.UTC(2026, 0, day + 1)).toISOString().slice(0, 10);
    for (let branch = 0; branch < 3; branch += 1) {
      for (let specialty = 0; specialty < specialties.length; specialty += 1) {
        const scheduled = 15 + ((day * 7 + branch * 3 + specialty * 5) % 10);
        const cancelled = 1 + ((day + branch + specialty) % 4);
        const attended = scheduled - cancelled - ((day + specialty) % 3);
        rows.push([date, BRANCHES[branch], specialties[specialty], 24, scheduled,
          attended, cancelled, attended * (18000 + specialty * 7000)].join(','));
      }
    }
  }
  const dataset = parseLocalCsv(rows.join('\n'), 'clinica_x.csv');
  dataset.imported_at = '2026-01-01T00:00:00.000Z';
  return dataset;
}

/** Operación de urgencias ficticia, agregada por día, sede y prioridad; sin fichas personales. */
export function createEmergencyRows() {
  return Array.from({ length: 90 * 3 * 3 }, (_, index) => {
    const day = Math.floor(index / 9);
    const branch = Math.floor(index % 9 / 3);
    const priority = index % 3;
    const visits = 12 + (day * 7 + branch * 5 + priority * 3) % 24;
    const wait = 8 + priority * 18 + (day + branch) % 12;
    return { date: new Date(Date.UTC(2026, 0, day + 1)).toISOString().slice(0, 10),
      branch: BRANCHES[branch], priority: ['Alta', 'Media', 'Baja'][priority],
      visits, wait_minutes_total: visits * wait, admitted: Math.floor(visits / (8 + priority * 4)) };
  });
}
