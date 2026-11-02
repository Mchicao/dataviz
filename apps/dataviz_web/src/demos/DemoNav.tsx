import type { DemoRoute } from './routes';
import { useDemoLang, DEMO_TEXTS } from './demoLang';

export function DemoNav({ current }: { current: DemoRoute }) {
  const [lang, setLang] = useDemoLang();
  const t = DEMO_TEXTS[lang];

  return (
    <nav className="demo-nav" aria-label="Demos de DataVIZ">
      <a className="demo-brand" href="https://dataviz.matiaschicao.cl/">DataVIZ</a>
      <div className="demo-nav-right">
        <div className="demo-nav-links">
          {([
            ['studio', t.create_dashboards],
            ['dashboard', t.view_dashboard],
            ['agents', t.agents],
          ] as const).map(([route, label]) => (
            <a key={route} href={`/${route}`} aria-current={current === route ? 'page' : undefined}>
              {label}
            </a>
          ))}
        </div>
        <div className="demo-lang-toggle" role="group" aria-label={t.switch_lang_aria}>
          <button
            type="button"
            className={lang === 'en' ? 'is-active' : ''}
            onClick={() => setLang('en')}
            title="English"
          >
            EN
          </button>
          <button
            type="button"
            className={lang === 'es' ? 'is-active' : ''}
            onClick={() => setLang('es')}
            title="Español"
          >
            ES
          </button>
        </div>
      </div>
    </nav>
  );
}
