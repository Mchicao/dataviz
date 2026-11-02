export const PROFILES = ['direction', 'operations'] as const;
export type DemoProfile = typeof PROFILES[number];
export type SpecialistId = 'finance' | 'revenue' | 'emergency';

export interface SpecialistDatabaseSource {
  engine: 'PostgreSQL' | 'Redshift';
  icon: string;
  name: string;
  host: string;
  database: string;
  tables: string[];
  metrics: string[];
}

export interface SpecialistItem {
  id: SpecialistId;
  name: string;
  names: { en: string; es: string };
  initials: string;
  color: string;
  description: string;
  descriptions: { en: string; es: string };
  knowledge_summary: { en: string; es: string };
  sources_summary: SpecialistDatabaseSource[];
  source: string;
  scope: string;
  prompts: string[];
  localized_prompts: { en: string[]; es: string[] };
}

export const SPECIALISTS: SpecialistItem[] = [
  {
    id: 'finance',
    name: 'Finanzas',
    names: { en: 'Finance', es: 'Finanzas' },
    initials: 'F',
    color: '#236746',
    description: 'Acceso a: PostgreSQL Clínico (facturación y copagos) y Redshift ERP (cobros, honorarios y tesorería).',
    descriptions: {
      en: 'Access to: Clinical PostgreSQL (billing & copays) and Redshift ERP (collections, fees, treasury).',
      es: 'Acceso a: PostgreSQL Clínico (facturación y copagos) y Redshift ERP (cobros, honorarios y tesorería).',
    },
    knowledge_summary: {
      en: 'Connects directly to Clinical PostgreSQL (patient billing & copays) and AWS Redshift ERP (collections reconciliation, medical fees, and treasury balance).',
      es: 'Conecta directamente a PostgreSQL Clínico (facturación de pacientes y copagos) y AWS Redshift ERP (conciliación de cobros, honorarios médicos y tesorería).',
    },
    sources_summary: [
      {
        engine: 'PostgreSQL',
        icon: '🐘',
        name: 'ERP Transaccional y Recaudación (PSQL)',
        host: 'prod-erp-psql.internal:5432',
        database: 'clinica_erp',
        tables: ['finanzas.recaudacion_caja_diaria', 'finanzas.facturacion_prestaciones', 'finanzas.cuentas_por_cobrar'],
        metrics: ['Cobrado', 'Facturado', 'Pendiente de cobro'],
      },
      {
        engine: 'Redshift',
        icon: '🔴',
        name: 'Data Warehouse Clínico (AWS Redshift)',
        host: 'redshift-dw-cluster.internal:5439',
        database: 'clinica_dw',
        tables: ['dw_clinica.fact_atenciones_diarias', 'dw_clinica.dim_sedes'],
        metrics: ['Conciliación de bonos', 'Liquidación por sede'],
      },
    ],
    source: 'ERP Transaccional (PSQL) y Redshift DW',
    scope: 'Importes agregados por fecha y sede. Sin datos de urgencias ni identidades.',
    prompts: ['Cobros del trimestre', 'Cobros por sede', 'Evolución mensual'],
    localized_prompts: {
      en: ['Quarterly collections', 'Collections by branch', 'Monthly trend'],
      es: ['Cobros del trimestre', 'Cobros por sede', 'Evolución mensual'],
    },
  },
  {
    id: 'revenue',
    name: 'Ciclo de ingresos',
    names: { en: 'Revenue Cycle', es: 'Ciclo de ingresos' },
    initials: 'CI',
    color: '#225f95',
    description: 'Acceso a: PostgreSQL HIS (agenda y atenciones) y Redshift DW (capacidad médica y cancelaciones).',
    descriptions: {
      en: 'Access to: Operations PostgreSQL (scheduling & visits) and Redshift DW (physician capacity & cancellations).',
      es: 'Acceso a: PostgreSQL HIS (agenda y atenciones) y Redshift DW (capacidad médica y cancelaciones).',
    },
    knowledge_summary: {
      en: 'Connects directly to HIS Outpatient PostgreSQL (scheduling, appointment volume) and AWS Redshift DW (physician capacity, cancellation logs, and slot utilization).',
      es: 'Conecta directamente a PostgreSQL HIS (agenda, citas y atenciones ambulatorias) y AWS Redshift DW (capacidad médica, registro de cancelaciones y ocupación de box).',
    },
    sources_summary: [
      {
        engine: 'PostgreSQL',
        icon: '🩺',
        name: 'HIS Ambulatorio y Agenda Médica (PSQL)',
        host: 'prod-ambulatorio-psql.internal:5432',
        database: 'ambulatorio_his',
        tables: ['agenda.citas_programadas', 'agenda.atenciones_realizadas'],
        metrics: ['Atenciones totales', 'Citas agendadas'],
      },
      {
        engine: 'Redshift',
        icon: '🔴',
        name: 'Data Warehouse de Operaciones (AWS Redshift)',
        host: 'redshift-dw-cluster.internal:5439',
        database: 'clinica_dw',
        tables: ['dw_clinica.fact_ocupacion_medica', 'dw_clinica.dim_bloques_horarios'],
        metrics: ['Tasa de cancelación', 'Utilización de capacidad'],
      },
    ],
    source: 'HIS Ambulatorio (PSQL) y Redshift DW',
    scope: 'Capacidad y agenda agregadas por fecha y sede. Sin importes financieros.',
    prompts: ['Actividad ambulatoria', 'Tasa de cancelación', 'Atenciones por sede'],
    localized_prompts: {
      en: ['Outpatient volume', 'Cancellation rate', 'Visits by branch'],
      es: ['Actividad ambulatoria', 'Tasa de cancelación', 'Atenciones por sede'],
    },
  },
  {
    id: 'emergency',
    name: 'Urgencias',
    names: { en: 'Emergency Care', es: 'Urgencias' },
    initials: 'U',
    color: '#9a4d25',
    description: 'Acceso a: PostgreSQL Triage (admisiones y tiempos Manchester) y Redshift DW (tiempos de espera y camas).',
    descriptions: {
      en: 'Access to: Triage PostgreSQL (ER intake & Manchester triage) and Redshift DW (wait times & bed occupancy).',
      es: 'Acceso a: PostgreSQL Triage (admisiones y tiempos Manchester) y Redshift DW (tiempos de espera y camas).',
    },
    knowledge_summary: {
      en: 'Connects directly to ER Triage PostgreSQL (emergency admissions, Manchester triage C1-C5) and AWS Redshift DW (wait time analysis and critical bed occupancy).',
      es: 'Conecta directamente a PostgreSQL Triage (admisiones de urgencias, categorización Manchester C1-C5) y AWS Redshift DW (tiempos de espera y ocupación hospitalaria).',
    },
    sources_summary: [
      {
        engine: 'PostgreSQL',
        icon: '🚑',
        name: 'Sistema de Triage y Admisiones Urgencias (PSQL)',
        host: 'prod-urgencias-psql.internal:5432',
        database: 'urgencias_db',
        tables: ['triage.admisiones_urgencia', 'triage.tiempos_atencion_manchester'],
        metrics: ['Consultas de urgencia', 'Tiempo de espera en box'],
      },
      {
        engine: 'Redshift',
        icon: '🔴',
        name: 'Data Warehouse Hospitalario (AWS Redshift)',
        host: 'redshift-dw-cluster.internal:5439',
        database: 'clinica_dw',
        tables: ['dw_clinica.fact_urgencias_categorizadas', 'dw_clinica.dim_prioridades_triage'],
        metrics: ['Tiempo de espera medio', 'Pacientes categorizados'],
      },
    ],
    source: 'Sistema de Triage (PSQL) y Redshift DW',
    scope: 'Consultas y espera agregadas por día, sede y prioridad. Sin facturación ni fichas clínicas.',
    prompts: ['Actividad de urgencias', 'Espera media', 'Consultas por sede'],
    localized_prompts: {
      en: ['Emergency activity', 'Average wait time', 'Visits by branch'],
      es: ['Actividad de urgencias', 'Espera media', 'Consultas por sede'],
    },
  },
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
