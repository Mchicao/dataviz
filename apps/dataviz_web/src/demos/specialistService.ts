import { allowedSpecialists, type DemoProfile, type EvidenceMetric, type SpecialistAnswer, type SpecialistId } from './catalog';
import { BRANCHES, MONTHS, DEMO_PERIOD, DEMO_VERSION, createClinicDataset, createEmergencyRows } from './clinicData';

export class SpecialistError extends Error {
  constructor(message: string, public status = 400) { super(message); }
}
interface Query { agent_id: SpecialistId; profile: DemoProfile; prompt: string; branch: string; month: string }
interface ScopedRow { date: string; branch: string; values: Record<string, number | undefined> }
const normalize = (value: string) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
let outpatient: ReturnType<typeof createClinicDataset>['rows'] | undefined;
let emergency: ReturnType<typeof createEmergencyRows> | undefined;

/** Resuelve permisos y filtros a partir de vocabularios cerrados; el cliente no envía datos ni capacidades. */
export function parseSpecialistQuery(value: unknown): Query {
  if (!record(value) || Object.keys(value).some((key) => !['agent_id', 'profile', 'prompt', 'branch', 'month'].includes(key))
    || !['direction', 'operations'].includes(String(value.profile))
    || !['finance', 'revenue', 'emergency'].includes(String(value.agent_id))
    || typeof value.prompt !== 'string' || !value.prompt.trim() || value.prompt.length > 2000
    || typeof value.branch !== 'string' || !['', ...BRANCHES].includes(value.branch)
    || typeof value.month !== 'string' || !['', ...MONTHS].includes(value.month)) {
    throw new SpecialistError('Solicitud inválida. Selecciona un agente, perfil y período válidos.');
  }
  const query = value as unknown as Query;
  if (!allowedSpecialists(query.profile).some((agent) => agent.id === query.agent_id)) {
    throw new SpecialistError('Este perfil de demostración no tiene acceso a ese agente.', 403);
  }
  const prompt = normalize(query.prompt);
  const years = prompt.match(/\b(?:19|20)\d{2}\b/g) ?? [];
  if (years.some((year) => year !== '2026')
    || /\b(abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre|hoy|ayer|actual|actuales)\b|\b(este mes|ultimo mes|ultima semana|esta semana)\b/.test(prompt)
    || /\b\d{1,2}\s+de\s+(enero|febrero|marzo)\b|\b\d{4}-\d{2}-\d{2}\b/.test(prompt)
    || (prompt.match(/\b\d{4}-\d{2}\b/g) ?? []).some((month) => !MONTHS.includes(month as typeof MONTHS[number]))) {
    throw new SpecialistError('Este snapshot solo permite consultas mensuales de enero a marzo de 2026. Selecciona uno de esos meses o todo el trimestre.');
  }
  const branches = BRANCHES.filter((branch) => new RegExp(`\\b${normalize(branch)}\\b`).test(prompt));
  const months = ['enero', 'febrero', 'marzo'].flatMap((month, index) => prompt.includes(month) || prompt.includes(MONTHS[index]) ? [MONTHS[index]] : []);
  if (branches.length > 1 || months.length === 2 || query.month && months.length > 1) {
    throw new SpecialistError('Selecciona una sede o todas, y un mes o el trimestre completo. No se aplicó una selección parcial ambigua.');
  }
  if (query.branch && branches.length === 1 && query.branch !== branches[0]
    || query.month && months.length === 1 && query.month !== months[0]) {
    throw new SpecialistError('La pregunta y los filtros no coinciden. Cambia los filtros o reformula la pregunta.');
  }
  return { ...query, branch: query.branch || (branches.length === 1 ? branches[0] : ''),
    month: query.month || (months.length === 1 ? months[0] : '') };
}

function scopedRows(agent: SpecialistId): ScopedRow[] {
  if (agent === 'emergency') {
    emergency ??= createEmergencyRows();
    return emergency.map((row) => ({ date: row.date, branch: row.branch,
      values: { visits: row.visits, wait: row.wait_minutes_total, admitted: row.admitted } }));
  }
  outpatient ??= createClinicDataset().rows;
  return outpatient.map<ScopedRow>((row, index) => ({ date: String(row.Fecha), branch: String(row.Sede), values: agent === 'finance'
    ? { collected: Number(row.Ingresos), billed: Number(row.Ingresos) + (index % 5) * 1000, pending: (index % 5) * 1000 }
    : { capacity: Number(row.Capacidad), scheduled: Number(row.Agendadas), attended: Number(row.Atendidas), cancelled: Number(row.Canceladas) } }));
}

/** Consulta únicamente la proyección autorizada; todas las cifras se calculan fuera del modelo de lenguaje. */
export function answerSpecialist(query: Query): SpecialistAnswer {
  const agent = allowedSpecialists(query.profile).find((item) => item.id === query.agent_id);
  if (!agent) {throw new SpecialistError('Agente fuera del alcance del perfil.', 403);}
  const rows = scopedRows(query.agent_id).filter((row) => (!query.branch || row.branch === query.branch)
    && (!query.month || row.date.startsWith(query.month)));
  const sum = (key: string, selection = rows) => selection.reduce((total, row) => total + (row.values[key] ?? 0), 0);
  const ratio = (numerator: number, denominator: number, scale = 1) => denominator ? numerator / denominator * scale : 0;
  const metric = (id: string, label: string, value: number, unit: EvidenceMetric['unit'], formula: string): EvidenceMetric => ({ id, label, value, unit, formula });
  const evidence = query.agent_id === 'finance' ? [
    metric('collected', 'Cobrado', sum('collected'), 'CLP', 'SUM(Cobrado)'),
    metric('billed', 'Facturado', sum('billed'), 'CLP', 'SUM(Facturado)'),
    metric('pending', 'Pendiente de cobro', sum('pending'), 'CLP', 'SUM(Facturado) − SUM(Cobrado)'),
  ] : query.agent_id === 'revenue' ? [
    metric('attended', 'Atenciones', sum('attended'), 'count', 'SUM(Atendidas)'),
    metric('scheduled', 'Citas agendadas', sum('scheduled'), 'count', 'SUM(Agendadas)'),
    metric('cancelled', 'Cancelaciones', sum('cancelled'), 'count', 'SUM(Canceladas)'),
    metric('cancellation_rate', 'Tasa de cancelación', ratio(sum('cancelled'), sum('scheduled'), 100), '%', 'SUM(Canceladas) / SUM(Agendadas) × 100'),
    metric('utilization', 'Utilización de capacidad', ratio(sum('attended'), sum('capacity'), 100), '%', 'SUM(Atendidas) / SUM(Capacidad) × 100'),
  ] : [
    metric('visits', 'Consultas de urgencias', sum('visits'), 'count', 'SUM(Consultas)'),
    metric('mean_wait', 'Espera media', ratio(sum('wait'), sum('visits')), 'min', 'SUM(MinutosEsperaTotal) / SUM(Consultas)'),
    metric('admitted', 'Ingresos hospitalarios', sum('admitted'), 'count', 'SUM(IngresosHospitalarios)'),
  ];
  const prompt = normalize(query.prompt);
  const denied = /paciente|identidad|correo|contrase|api.?key|credencial/.test(prompt)
    || /\b(utilidad|beneficio|costo|gasto|margen|ebitda|presupuesto|proyeccion|prediccion|causa|mediana|percentil|p95|p99)\b/.test(prompt)
    || query.agent_id !== 'finance' && /finanza|factur|cobrad|cobro|importe|saldo|ingresos monetarios/.test(prompt)
    || query.agent_id !== 'emergency' && /urgencia|triage|espera media|ingresos hospitalarios/.test(prompt)
    || query.agent_id === 'finance' && /cancelacion|citas|capacidad|agend/.test(prompt);
  const byBranch = /compar|sede|ranking/.test(prompt);
  const groups = new Map<string, ScopedRow[]>();
  for (const row of rows) {
    const key = byBranch ? row.branch : row.date.slice(0, 7);
    const group = groups.get(key) ?? []; group.push(row); groups.set(key, group);
  }
  const primary = query.agent_id === 'finance' ? 'collected' : query.agent_id === 'revenue' ? 'attended' : 'visits';
  const series = [...groups].map(([label, group]) => ({ label, value: sum(primary, group) }));
  const fmt = (item: EvidenceMetric) => `${item.label}: ${new Intl.NumberFormat('es-CL', { maximumFractionDigits: item.unit === 'count' || item.unit === 'CLP' ? 0 : 1 }).format(item.value)}${item.unit === 'count' ? '' : ` ${item.unit}`}`;
  return { agent_id: query.agent_id, profile: query.profile, status: denied ? 'out_of_scope' : 'answered',
    message: denied ? `Esa información no está disponible dentro de mi alcance. Puedo consultar: ${agent.description.toLowerCase()}`
      : `${evidence.map(fmt).join('. ')}. ${byBranch ? 'La comparación por sede' : 'La evolución mensual'} aparece en la evidencia adjunta.`,
    mode: 'local', evidence: denied ? [] : evidence, series: denied ? [] : series,
    series_label: `${evidence[0].label} ${byBranch ? 'por sede' : 'por mes'}`,
    source: { name: agent.source, version: DEMO_VERSION, period: DEMO_PERIOD, branch: query.branch || 'Todas las sedes',
      month: query.month || 'Todo el trimestre', rows: denied ? 0 : rows.length,
      grain: query.agent_id === 'emergency' ? 'Día × sede × prioridad' : 'Día × sede × especialidad' },
    access_scope: agent.scope };
}
