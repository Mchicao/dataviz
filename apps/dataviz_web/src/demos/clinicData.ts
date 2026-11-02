import { parseLocalCsv } from "../authoring/csv";

export const DEMO_PERIOD = "2026-01-01 / 2026-03-31";
export const DEMO_VERSION = "clinic-x-2026-q1-v1";
export const BRANCHES = ["Norte", "Centro", "Sur"] as const;
export const MONTHS = ["2026-01", "2026-02", "2026-03"] as const;

/** Generación independiente y reproducible; no utiliza entradas ni identidades de clientes. */
export function createClinicDataset() {
  const rows = ["Fecha,Sede,Especialidad,Capacidad,Agendadas,Atendidas,Canceladas,Ingresos"];
  const specialties = ["Medicina general", "Cardiología", "Dermatología", "Oftalmología"];
  const branchWeights = [0.92, 1.55, 0.53];
  const specialtyWeights = [1.60, 1.05, 0.75, 0.60];
  const tariffs = [22000, 48000, 38000, 35000];
  const dayOfWeekMultipliers = [0.04, 1.32, 1.20, 1.05, 0.98, 0.82, 0.28];

  for (let day = 0; day < 90; day += 1) {
    const date = new Date(Date.UTC(2026, 0, day + 1)).toISOString().slice(0, 10);
    const dayOfWeek = (day + 4) % 7;
    const isJan1 = (day === 0);
    const seasonalMultiplier = day < 31 ? (isJan1 ? 0.05 : 0.96) : (day < 59 ? 0.78 : 1.22);
    const dayMultiplier = isJan1 ? 0.05 : dayOfWeekMultipliers[dayOfWeek];

    for (let branch = 0; branch < 3; branch += 1) {
      for (let specialty = 0; specialty < specialties.length; specialty += 1) {
        const baseSlots = 32;
        const capacity = Math.max(8, Math.round(baseSlots * branchWeights[branch] * specialtyWeights[specialty]));
        const variation = 0.94 + (((day * 13 + branch * 7 + specialty * 11) % 15) / 100);
        const demand = capacity * dayMultiplier * seasonalMultiplier * variation;
        const scheduled = Math.min(capacity, Math.max(0, Math.round(demand)));
        const cancelRate = 0.06 + (((day + branch + specialty) % 5) * 0.012);
        const cancelled = Math.min(scheduled, Math.round(scheduled * cancelRate));
        const attended = scheduled - cancelled;
        const income = attended * tariffs[specialty];

        rows.push([date, BRANCHES[branch], specialties[specialty], capacity, scheduled,
          attended, cancelled, income].join(","));
      }
    }
  }
  const dataset = parseLocalCsv(rows.join("\n"), "clinica_x.csv");
  dataset.imported_at = "2026-01-01T00:00:00.000Z";
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
      branch: BRANCHES[branch], priority: ["Alta", "Media", "Baja"][priority],
      visits, wait_minutes_total: visits * wait, admitted: Math.floor(visits / (8 + priority * 4)) };
  });
}
