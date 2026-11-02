import { useMemo, useState } from 'react';
import { RENDER_PLAN_SCHEMA_VERSION } from '../runtime/types';
import type { RuntimePayload } from '../runtime/client';
import App from '../app';
import { createClinicDemo } from '../authoring/clinicDemo';
import { previewResultsFor, renderPlanForPresentation } from '../authoring/preview';
import { BRANCHES, MONTHS, DEMO_VERSION } from './clinicData';
import { DemoNav } from './DemoNav';
import { useDemoLang, DEMO_TEXTS } from './demoLang';
import './demos.css';

/** Vista terminada del snapshot ficticio; nunca carga ni modifica los borradores del editor. */
export default function ClinicDashboard() {
  const [lang] = useDemoLang();
  const t = DEMO_TEXTS[lang];
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

  return (
    <div className="demo-dashboard">
      <DemoNav current="dashboard" />
      <header className="demo-dashboard-heading">
        <div>
          <h1>{t.clinic_title}</h1>
          <p>{t.dashboard_subtitle}</p>
        </div>
        <div className="demo-filters">
          <label>
            {t.branch_label}
            <select value={branch} onChange={(event) => setBranch(event.target.value)}>
              <option value="">{t.all_branches}</option>
              {BRANCHES.map((name) => <option key={name}>{name}</option>)}
            </select>
          </label>
          <label>
            {t.period_label}
            <select value={month} onChange={(event) => setMonth(event.target.value)}>
              <option value="">{t.all_period}</option>
              {MONTHS.map((name, index) => (
                <option key={name} value={name}>{t.months[index]}</option>
              ))}
            </select>
          </label>
        </div>
      </header>
      <main className="demo-dashboard-body">
        <App initialPayload={payload} />
      </main>
      <footer className="demo-dashboard-footer">
        <p>{t.snapshot_tag} v{DEMO_VERSION} · {t.snapshot_note}</p>
      </footer>
    </div>
  );
}
