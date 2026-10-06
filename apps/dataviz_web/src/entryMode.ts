export type EntryMode = 'authoring' | 'viewer';

/** Resolve the product surface without importing either route bundle. */
export function resolveEntryMode(query: URLSearchParams): EntryMode {
  const requested = query.get('mode');
  if (requested === 'author' || requested === 'authoring') {return 'authoring';}
  if (requested === 'viewer') {return 'viewer';}
  return query.get('version')?.trim() || query.get('endpoint')?.trim()
    ? 'viewer'
    : 'authoring';
}

/** Treat empty query values as absent before passing them to the runtime. */
export function optionalQueryValue(query: URLSearchParams, name: string): string | undefined {
  return query.get(name)?.trim() || undefined;
}
