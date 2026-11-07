/** TanStack Query key 规范（FRONTEND-DESIGN §6.1）。 */
export const queryKeys = {
  workspace: (workspaceId: string) => ["workspace", workspaceId] as const,
  workspaceDetail: (workspaceId: string) =>
    ["workspace", workspaceId, "detail"] as const,
  documents: (workspaceId: string, path?: string) =>
    ["documents", workspaceId, path ?? ""] as const,
  search: (workspaceId: string, query: string, filters: unknown) =>
    ["search", workspaceId, query, filters] as const,
  reviews: (workspaceId: string, filters: unknown) =>
    ["reviews", workspaceId, filters] as const,
  dashboard: (workspaceId: string, section: string) =>
    ["dashboard", workspaceId, section] as const,
  settings: (workspaceId: string) => ["settings", workspaceId] as const,
};
