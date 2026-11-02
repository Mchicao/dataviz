export const PROFILES = ['direction', 'operations'] as const;
export type DemoProfile = typeof PROFILES[number];
export type SpecialistId = 'finance' | 'revenue' | 'emergency';
export const SPECIALISTS = [
  { id: 'finance' as const, name: 'Finanzas', initials: 'F', color: '#236746',
    description: 'Facturación, cobros y saldo pendiente.', source: 'Facturación ficticia',
    scope: 'Importes agregados por fecha y sede. Sin datos de urgencias ni identidades.',
    prompts: ['¿Cuánto se ha cobrado?', 'Compara los cobros por sede', '¿Cómo evolucionó el cobro mensual?'] },
  { id: 'revenue' as const, name: 'Ciclo de ingresos', initials: 'CI', color: '#225f95',
    description: 'Agenda, atenciones y cancelaciones.', source: 'Operación ambulatoria ficticia',
    scope: 'Capacidad y agenda agregadas por fecha y sede. Sin importes financieros.',
    prompts: ['Resume la actividad ambulatoria', '¿Cuál es la tasa de cancelación?', 'Compara atenciones por sede'] },
  { id: 'emergency' as const, name: 'Urgencias', initials: 'U', color: '#9a4d25',
    description: 'Demanda, espera e ingresos hospitalarios.', source: 'Operación de urgencias ficticia',
    scope: 'Consultas y espera agregadas por día, sede y prioridad. Sin facturación ni fichas clínicas.',
    prompts: ['Resume la actividad de urgencias', '¿Cuál es la espera media?', 'Compara consultas por sede'] },
];

export function allowedSpecialists(profile: DemoProfile) {
  return SPECIALISTS.filter((agent) => profile === 'direction' || agent.id !== 'finance');
}

export interface EvidenceMetric { id: string; label: string; value: number; unit: 'CLP' | '%' | 'min' | 'count'; formula: string }
export interface SpecialistAnswer {
  agent_id: SpecialistId;
  profile: DemoProfile;
  status: 'answered' | 'out_of_scope';
  message: string;
  mode: 'local' | 'glm' | 'local_fallback';
  evidence: EvidenceMetric[];
  series: { label: string; value: number }[];
  series_label: string;
  source: { name: string; version: string; period: string; branch: string; month: string; rows: number; grain: string };
  access_scope: string;
}
