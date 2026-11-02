import { useEffect, useState } from 'react';

export type DemoLang = 'en' | 'es';

const STORAGE_KEY = 'dataviz-demo-lang';
const EVENT_KEY = 'dataviz-demo-lang-change';

export function getDemoLang(): DemoLang {
  if (typeof window === 'undefined') return 'en';
  try {
    const urlLang = new URLSearchParams(window.location.search).get('lang');
    if (urlLang === 'es' || urlLang === 'en') return urlLang;
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'es' || stored === 'en') return stored;
  } catch {
    /* fallback to en */
  }
  return 'en'; // English is the default
}

export function setDemoLang(lang: DemoLang): void {
  if (typeof window !== 'undefined') {
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* ignore */
    }
    window.dispatchEvent(new CustomEvent(EVENT_KEY, { detail: lang }));
  }
}

export function useDemoLang(): [DemoLang, (lang: DemoLang) => void] {
  const [lang, setLang] = useState<DemoLang>(() => getDemoLang());

  useEffect(() => {
    const handler = (e: Event) => {
      setLang((e as CustomEvent<DemoLang>).detail);
    };
    window.addEventListener(EVENT_KEY, handler);
    return () => window.removeEventListener(EVENT_KEY, handler);
  }, []);

  const update = (next: DemoLang) => {
    setDemoLang(next);
    setLang(next);
  };

  return [lang, update];
}

export const DEMO_TEXTS = {
  en: {
    // Nav
    create_dashboards: 'Create dashboards',
    view_dashboard: 'View dashboard',
    agents: 'Agents',
    switch_lang_aria: 'Change demo language',

    // Studio Header
    canvas: 'Canvas',
    changes: 'Changes',
    versions: 'Versions',
    undo: 'Undo',
    redo: 'Redo',
    undo_title: 'Undo last action (Ctrl + Z)',
    redo_title: 'Redo action (Ctrl + Y)',
    projects: 'Projects /',
    saved_locally: 'Saved locally',
    request_publish: 'Request publication',
    connections: 'Connections',

    // Studio Panels
    visualizations: 'Visualizations',
    filters: 'Filters',
    data: 'Data',
    assistant: 'Assistant',
    minimize_visualizations: 'Minimize visualizations panel',
    minimize_filters: 'Minimize filters panel',
    minimize_data: 'Minimize data panel',
    minimize_assistant: 'Minimize assistant panel',
    expand_visualizations: 'Expand visualizations',
    expand_filters: 'Expand filters',
    expand_data: 'Expand data',
    expand_assistant: 'Expand assistant',

    // Dashboard
    clinic_title: 'Clinic X',
    dashboard_subtitle: 'Outpatient operation · January to March 2026',
    branch_label: 'Branch',
    period_label: 'Period',
    all_branches: 'All branches',
    all_period: 'All Q1 (Jan-Mar 2026)',
    months: ['January', 'February', 'March'],
    snapshot_tag: 'Snapshot',
    snapshot_note: 'Simulated clinical data · Read-only view',

    // Specialists
    specialists_title: 'Clinic X Agents',
    search_placeholder: 'Search agent...',
    empty_search: 'No agents match that name.',
    simulated_account: 'Simulated data',
    demo_profile: 'Demo profile',
    profile_direction: 'Executive / Leadership',
    profile_operations: 'Operations',
    profile_note: 'Simulated permissions. No real credentials or sources.',
    active_filters: 'Active filters:',
    knowledge_heading: 'Connected knowledge sources & databases',
    tables_consulted: 'Queried tables:',
    metrics_label: 'Canonical metrics:',
    evidence_title: 'Sources & Lineage',
    dashboard_source_label: 'Origin dashboard:',
    dashboard_link_text: '📊 Clinic X — Operations & Finance ↗',
    db_sources_grouped: 'Sources grouped by database engine',
    calculated_metrics: 'Calculated metrics',
    how_calculated: 'How it was calculated',
    audit_heading: 'Audit & scope',
    version: 'Version',
    period: 'Period',
    chat_visualizer: 'Interactive visualizer',
    input_placeholder: 'Ask the agent with evidence...',
    send: 'Send',
    sources_btn: 'View sources & lineage',
    filters_btn: 'Filters',
    close_evidence: 'Close evidence drawer',
    you: 'You',
  },
  es: {
    // Nav
    create_dashboards: 'Crear dashboards',
    view_dashboard: 'Ver dashboard',
    agents: 'Agentes',
    switch_lang_aria: 'Cambiar idioma de la demo',

    // Studio Header
    canvas: 'Canvas',
    changes: 'Cambios',
    versions: 'Versiones',
    undo: 'Deshacer',
    redo: 'Rehacer',
    undo_title: 'Deshacer última acción (Ctrl + Z)',
    redo_title: 'Rehacer acción (Ctrl + Y)',
    projects: 'Proyectos /',
    saved_locally: 'Guardado local',
    request_publish: 'Solicitar publicación',
    connections: 'Conexiones',

    // Studio Panels
    visualizations: 'Visualizaciones',
    filters: 'Filtros',
    data: 'Datos',
    assistant: 'Asistente',
    minimize_visualizations: 'Minimizar panel de visualizaciones',
    minimize_filters: 'Minimizar panel de filtros',
    minimize_data: 'Minimizar panel de datos',
    minimize_assistant: 'Minimizar panel de asistente',
    expand_visualizations: 'Expandir visualizaciones',
    expand_filters: 'Expandir filtros',
    expand_data: 'Expandir datos',
    expand_assistant: 'Expandir asistente',

    // Dashboard
    clinic_title: 'Clínica X',
    dashboard_subtitle: 'Operación ambulatoria · enero a marzo de 2026',
    branch_label: 'Sede',
    period_label: 'Período',
    all_branches: 'Todas',
    all_period: 'Enero-marzo 2026',
    months: ['Enero', 'Febrero', 'Marzo'],
    snapshot_tag: 'Snapshot',
    snapshot_note: 'Datos clínicos ficticios · Vista de sólo lectura',

    // Specialists
    specialists_title: 'Agentes de Clínica X',
    search_placeholder: 'Buscar agente...',
    empty_search: 'No hay agentes con ese nombre.',
    simulated_account: 'Datos ficticios',
    demo_profile: 'Perfil de demostración',
    profile_direction: 'Dirección',
    profile_operations: 'Operaciones',
    profile_note: 'Permisos simulados. Sin autenticación ni fuentes reales.',
    active_filters: 'Filtros activos:',
    knowledge_heading: 'Fuentes de conocimiento y bases conectadas',
    tables_consulted: 'Tablas consultadas:',
    metrics_label: 'Métricas canónicas:',
    evidence_title: 'Fuentes y Linaje',
    dashboard_source_label: 'Dashboard de origen:',
    dashboard_link_text: '📊 Clínica X — Operación y Finanzas ↗',
    db_sources_grouped: 'Fuentes agrupadas por base de datos',
    calculated_metrics: 'Métricas calculadas',
    how_calculated: 'Cómo se calculó',
    audit_heading: 'Auditoría y alcance',
    version: 'Versión',
    period: 'Período',
    chat_visualizer: 'Visualizador interactivo',
    input_placeholder: 'Pregunta al agente con evidencia...',
    send: 'Enviar',
    sources_btn: 'Ver fuentes y linaje',
    filters_btn: 'Filtros',
    close_evidence: 'Cerrar evidencia',
    you: 'Tú',
  },
} as const;
