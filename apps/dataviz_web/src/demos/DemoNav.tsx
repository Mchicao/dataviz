import type { DemoRoute } from './routes';

export function DemoNav({ current }: { current: DemoRoute }) {
  return <nav className="demo-nav" aria-label="Demos de DataVIZ">
    <a className="demo-brand" href="https://dataviz.matiaschicao.cl/">DataVIZ</a>
    <div>{([['studio', 'Crear dashboards'], ['dashboard', 'Ver dashboard'], ['agents', 'Agentes']] as const).map(([route, label]) =>
      <a key={route} href={`/${route}`} aria-current={current === route ? 'page' : undefined}>{label}</a>)}</div>
  </nav>;
}
