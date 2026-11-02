import { parseLocalCsv } from "../authoring/csv";

export const DEMO_PERIOD = "2026-01-01 / 2026-03-31";
export const DEMO_VERSION = "clinic-x-2026-q1-v2";
export const BRANCHES = ["Norte", "Centro", "Sur"] as const;
export const MONTHS = ["2026-01", "2026-02", "2026-03"] as const;

/** Generador pseudo-aleatorio determinista y reproducible con semilla fija. */
function createSeededRandom(seed: number) {
  let s = seed;
  return () => {
    s += 0x6D2B79F5;
    let t = Math.imul(s ^ (s >>> 15), s | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Generación independiente y reproducible; refleja la variabilidad orgánica real de una red clínica. */
export function createClinicDataset() {
  const rng = createSeededRandom(20260101);
  const rows = ["Fecha,Sede,Especialidad,Capacidad,Agendadas,Atendidas,Canceladas,Ingresos"];
  const specialties = ["Medicina general", "Cardiología", "Dermatología", "Oftalmología"];
  const branchWeights = [0.95, 1.60, 0.55];
  const specialtyWeights = [1.60, 1.05, 0.75, 0.60];
  const tariffs = [22000, 48000, 38000, 35000];
  const dowBase = [0.05, 1.25, 1.18, 1.08, 1.02, 0.88, 0.30];

  for (let day = 0; day < 90; day += 1) {
    const date = new Date(Date.UTC(2026, 0, day + 1)).toISOString().slice(0, 10);
    const dow = (day + 4) % 7;
    const isJan1 = (day === 0);
    const isJan2 = (day === 1);
    const seasonal = day < 31 ? (isJan1 ? 0.04 : (isJan2 ? 0.60 : 0.95)) : (day < 59 ? 0.82 : 1.18);
    const dayJitter = 0.91 + rng() * 0.18;
    const dayDow = isJan1 ? 0.04 : (isJan2 ? 0.55 : dowBase[dow]);

    for (let branch = 0; branch < 3; branch += 1) {
      for (let specialty = 0; specialty < specialties.length; specialty += 1) {
        const baseSlots = 48;
        const capacity = Math.max(12, Math.round(baseSlots * branchWeights[branch] * specialtyWeights[specialty]));
        const slotNoise = 0.93 + rng() * 0.14;
        const demand = capacity * 0.72 * dayDow * seasonal * dayJitter * slotNoise;
        const scheduled = Math.min(capacity, Math.max(0, Math.round(demand)));
        const cancelRate = 0.06 + rng() * 0.05;
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
