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
  review: (workspaceId: string, reviewId: string) =>
    ["review", workspaceId, reviewId] as const,
  topics: (workspaceId: string, topic?: string) =>
    ["topics", workspaceId, topic ?? ""] as const,
  deliveries: (workspaceId: string, reviewId: string) =>
    ["deliveries", workspaceId, reviewId] as const,
  deletion: (workspaceId: string) => ["deletion", workspaceId] as const,
};

// Phase 5：LLM/Embedding/导出/管理后台（FRONTEND-DESIGN §6.1）
export const phase5Keys = {
  llm: (workspaceId: string) => ["settings", workspaceId, "llm"] as const,
  embedding: (workspaceId: string) =>
    ["settings", workspaceId, "embedding"] as const,
  usage: (workspaceId: string, range: string) =>
    ["settings", workspaceId, "usage", range] as const,
  exportJob: (workspaceId: string, exportId: string) =>
    ["exports", workspaceId, exportId] as const,
  admin: (section: "users" | "workspaces" | "invites" | "audit" | "system") =>
    ["admin", section] as const,
};
