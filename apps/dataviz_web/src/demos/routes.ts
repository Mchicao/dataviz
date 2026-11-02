export type DemoRoute = 'studio' | 'dashboard' | 'agents';

export function demoRoute(pathname: string): DemoRoute | undefined {
  const path = pathname.replace(/\/+$/, '');
  return path === '/studio' ? 'studio' : path === '/dashboard' ? 'dashboard' : path === '/agents' ? 'agents' : undefined;
}

export function isClinicDemoLocation(url: URL): boolean {
  return demoRoute(url.pathname) === 'studio' || url.searchParams.get('demo') === 'clinic-x';
}
