import { useMemo, useState } from 'react';
import { RENDER_PLAN_SCHEMA_VERSION } from '../runtime/types';
import type { RuntimePayload } from '../runtime/client';
import App from '../app';
import { createClinicDemo } from '../authoring/clinicDemo';
import { previewResultsFor, renderPlanForPresentation } from '../authoring/preview';
import { BRANCHES, MONTHS, DEMO_VERSION } from './clinicData';
import { DemoNav } from './DemoNav';
import './demos.css';

/** Vista terminada del snapshot ficticio; nunca carga ni modifica los borradores del editor. */
export default function ClinicDashboard() {
  const [branch, setBranch] = useState('');
  const [month, setMonth] = useState('');
  const demo = useMemo(createClinicDemo, []);
  const payload = useMemo<RuntimePayload>(() => {
    const dataset = { ...demo.dataset, rows: demo.dataset.rows.filter((row) => (!branch || row.Sede === branch)
      && (!month || String(row.Fecha).startsWith(month))) };
    const version = demo.document.versions[0];
    return { plan: { ...renderPlanForPresentation(version.presentation), schema_version: RENDER_PLAN_SCHEMA_VERSION },
      results: previewResultsFor(version.presentation.visuals, dataset, version.model) };
  }, [demo, branch, month]);
  return <div className="demo-dashboard">
    <DemoNav current="dashboard" />
    <header className="demo-dashboard-heading">
      <div><h1>Clínica X</h1><p>Operación ambulatoria · enero a marzo de 2026</p></div>
      <div className="demo-filters">
        <label>Sede<select value={branch} onChange={(event) => setBranch(event.target.value)}><option value="">Todas las sedes</option>{BRANCHES.map((name) => <option key={name}>{name}</option>)}</select></label>
        <label>Período<select value={month} onChange={(event) => setMonth(event.target.value)}><option value="">Todo el trimestre</option>{MONTHS.map((name, index) => <option key={name} value={name}>{['Enero', 'Febrero', 'Marzo'][index]}</option>)}</select></label>
        <button type="button" disabled={!branch && !month} onClick={() => {setBranch(''); setMonth('');}}>Restablecer</button>
      </div>
    </header>
    <p className="demo-source-note">Datos ficticios generados desde cero. Vista de solo lectura · {DEMO_VERSION}</p>
    <App initialPayload={payload} />
    <footer className="demo-dashboard-footer">Explora los filtros o <a href="/agents">consulta a un especialista</a>. Para diseñar tu propia versión, <a href="/studio">abre el editor</a>.</footer>
  </div>;
}
