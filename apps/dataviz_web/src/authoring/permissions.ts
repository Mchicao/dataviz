export type AuthoringPermission =
  | 'consume'
  | 'author'
  | 'request_publish'
  | 'publish'
  | 'revert'
  | 'manage_connections';

export const ALL_AUTHORING_PERMISSIONS: readonly AuthoringPermission[] = [
  'consume', 'author', 'request_publish', 'publish', 'revert', 'manage_connections',
] as const;

export function hasAuthoringPermission(
  permissions: readonly AuthoringPermission[],
  permission: AuthoringPermission,
): boolean {
  return permissions.includes(permission);
}
