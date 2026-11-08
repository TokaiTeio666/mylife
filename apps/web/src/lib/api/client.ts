import { copy } from "@/lib/copy";
import type { ErrorEnvelope } from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: unknown;
  readonly retryable: boolean;
  readonly requestId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: unknown,
    retryable: boolean,
    requestId: string | null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.retryable = retryable;
    this.requestId = requestId;
  }
}

function readCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const prefix = `${name}=`;
  const hit = document.cookie
    .split("; ")
    .find((row) => row.startsWith(prefix));
  return hit ? decodeURIComponent(hit.slice(prefix.length)) : null;
}

/** 写请求前确保 CSRF Cookie 存在（§1.3）。 */
export async function ensureCsrf(): Promise<string> {
  const existing = readCookie("mylife_csrf");
  if (existing) return existing;
  const response = await fetch(`${API_BASE}/auth/csrf`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) throw new Error(copy.common.csrfFailed);
  const body = (await response.json()) as { csrf_token: string };
  return body.csrf_token;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  const isFormData =
    typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body && !isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!SAFE_METHODS.has(method)) {
    headers.set("X-CSRF-Token", await ensureCsrf());
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    credentials: "include",
    cache: "no-store",
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const data = text ? JSON.parse(text) : null;

  if (!response.ok) {
    const envelope = data as ErrorEnvelope | null;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "INTERNAL_ERROR",
      envelope?.error?.message ?? copy.common.requestFailed,
      envelope?.error?.details ?? null,
      envelope?.error?.retryable ?? false,
      envelope?.request_id ?? response.headers.get("X-Request-Id"),
    );
  }
  return data as T;
}

export const api = {
  getMe: () => apiFetch<import("./types").MeResponse>("/auth/me"),
  login: (body: import("./types").LoginRequest) =>
    apiFetch<import("./types").LoginResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  register: (body: import("./types").RegisterRequest) =>
    apiFetch<import("./types").RegisterResponse>("/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  logout: () => apiFetch<void>("/auth/logout", { method: "POST" }),
  logoutAll: () => apiFetch<void>("/auth/logout-all", { method: "POST" }),
  listWorkspaces: () =>
    apiFetch<{ items: import("./types").Workspace[] }>("/workspaces"),
  createWorkspace: (name: string) =>
    apiFetch<import("./types").Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name }),
    }),
  switchWorkspace: (id: string) =>
    apiFetch<{ active_workspace_id: string; csrf_token: string }>(
      `/workspaces/${id}/switch`,
      { method: "POST" },
    ),
  workspaceDetail: (id: string) =>
    apiFetch<import("./types").WorkspaceDetail>(`/workspaces/${id}`),
  documentTree: () =>
    apiFetch<import("./types").DocumentTree>("/documents/tree"),
  documentByPath: (path: string) =>
    apiFetch<import("./types").DocumentDetail>(
      `/documents/by-path?path=${encodeURIComponent(path)}`,
    ),
  search: (params: Record<string, string | number | boolean | undefined>) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") search.set(key, String(value));
    }
    return apiFetch<import("./types").SearchResponse>(`/search?${search}`);
  },
  uploadZip: (file: File) => {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("mode", "managed");
    form.append("reviews_root", "wiki/复盘"); // i18n-ignore: 数据路径约定
    return apiFetch<{ job: import("./types").IngestJob }>("/ingest/zip", {
      method: "POST",
      body: form,
    });
  },
  jobDetail: (jobId: string) =>
    apiFetch<import("./types").IngestJobDetail>(`/ingest/jobs/${jobId}`),
  retryJob: (jobId: string) =>
    apiFetch<import("./types").IngestJob>(`/ingest/jobs/${jobId}/retry`, {
      method: "POST",
      body: JSON.stringify({ only_failed: true }),
    }),
  listConversations: () =>
    apiFetch<{ items: import("./types").ConversationSummary[] }>("/conversations"),
  createConversation: (title?: string) =>
    apiFetch<import("./types").ConversationSummary>("/conversations", {
      method: "POST",
      body: JSON.stringify(title ? { title } : {}),
    }),
  getConversation: (id: string) =>
    apiFetch<import("./types").ConversationSummary>(`/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    apiFetch<import("./types").ConversationSummary>(`/conversations/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    }),
  deleteConversation: (id: string) =>
    apiFetch<void>(`/conversations/${id}`, { method: "DELETE" }),
  listMessages: (id: string) =>
    apiFetch<{ items: import("./types").ChatMessage[] }>(
      `/conversations/${id}/messages`,
    ),
  sendTurn: (id: string, body: { client_turn_id: string; message: string }) =>
    apiFetch<import("./types").ChatTurnResponse>(`/conversations/${id}/turns`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getRun: (runId: string) =>
    apiFetch<import("./types").RunDetail>(`/runs/${runId}`),
  cancelRun: (runId: string) =>
    apiFetch<{ run_id: string; state: string }>(`/runs/${runId}/cancel`, {
      method: "POST",
    }),
  // ---------------------------------------------------------------- Phase 4
  listReviews: (params: Record<string, string | number | undefined> = {}) => {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") search.set(key, String(value));
    }
    return apiFetch<{ items: import("./types").ReviewSummary[] }>(
      `/reviews?${search}`,
    );
  },
  getReview: (reviewId: string, version?: number) =>
    apiFetch<import("./types").ReviewDetail>(
      `/reviews/${reviewId}${version ? `?version=${version}` : ""}`,
    ),
  createReview: (body: { kind: string; period_key: string; allow_empty?: boolean }) =>
    apiFetch<{ run_id: string; review_id: string; version: number }>("/reviews", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  approveRun: (runId: string, body: { decision_note?: string } = {}) =>
    apiFetch<{ run_id: string; state: string; delivery_id: string | null }>(
      `/runs/${runId}/approve`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  rejectRun: (runId: string, body: { reason_code: string; note?: string }) =>
    apiFetch<{ run_id: string; state: string }>(`/runs/${runId}/reject`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rollbackReview: (reviewId: string, version: number) =>
    apiFetch<{ run_id: string; target_version: number }>(
      `/reviews/${reviewId}/rollback`,
      { method: "POST", body: JSON.stringify({ version, confirm: true }) },
    ),
  listTopics: () =>
    apiFetch<{ items: import("./types").TopicSummary[] }>("/topics"),
  listTopicItems: (topic: string, status?: string) =>
    apiFetch<{ items: import("./types").TopicItem[] }>(
      `/topics/${encodeURIComponent(topic)}/items${status ? `?status=${status}` : ""}`,
    ),
  confirmTopicItem: (topic: string, itemId: string, decision: "accept" | "drop") =>
    apiFetch<import("./types").TopicItem>(
      `/topics/${encodeURIComponent(topic)}/items/${itemId}/confirm`,
      { method: "POST", body: JSON.stringify({ decision }) },
    ),
  resolveTopicItem: (topic: string, itemId: string) =>
    apiFetch<import("./types").TopicItem>(
      `/topics/${encodeURIComponent(topic)}/items/${itemId}/resolve`,
      { method: "POST", body: JSON.stringify({}) },
    ),
  listDeliveries: (reviewId: string) =>
    apiFetch<{ items: import("./types").DeliveryRecord[] }>(
      `/deliveries?review_id=${reviewId}`,
    ),
  getDelivery: (deliveryId: string) =>
    apiFetch<import("./types").DeliveryDetail>(`/deliveries/${deliveryId}`),
  retryDelivery: (deliveryId: string) =>
    apiFetch<{ delivery_id: string; status: string }>(
      `/deliveries/${deliveryId}/retry`,
      { method: "POST" },
    ),
  dashboardLatest: () =>
    apiFetch<import("./types").DashboardLatest>("/dashboard/latest"),
  dashboardTasks: (status?: string) =>
    apiFetch<import("./types").DashboardTaskList>(
      `/dashboard/tasks${status ? `?status=${status}` : ""}`,
    ),
  dashboardCalendar: (year: number, month?: number) =>
    apiFetch<import("./types").DashboardCalendar>(
      `/dashboard/calendar?year=${year}${month ? `&month=${month}` : ""}`,
    ),
  dashboardStats: (range = "30d") =>
    apiFetch<import("./types").DashboardStats>(`/dashboard/stats?range=${range}`),
  getSchedule: () =>
    apiFetch<import("./types").ScheduleSettings>("/settings/schedule"),
  putSchedule: (body: Record<string, unknown>) =>
    apiFetch<import("./types").ScheduleSettings>("/settings/schedule", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  listGitConnections: () =>
    apiFetch<{ items: import("./types").GitConnectionSummary[] }>("/settings/git"),
  putGitConnection: (body: Record<string, unknown>) =>
    apiFetch<import("./types").GitConnectionSummary>("/settings/git", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteGitConnections: () =>
    apiFetch<void>("/settings/git", { method: "DELETE" }),
  currentDeletion: () =>
    apiFetch<import("./types").DeletionRequest | null>("/deletions/current"),
  requestDeletion: (body: { scope: string; confirm_text: string }) =>
    apiFetch<import("./types").DeletionRequest>("/deletions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  cancelDeletion: (deletionId: string) =>
    apiFetch<{ state: string }>(`/deletions/${deletionId}/cancel`, {
      method: "POST",
    }),
  // ---------------------------------------------------------------- Phase 5
  getLlmSettings: () => apiFetch<import("./types").LlmSettings>("/settings/llm"),
  putLlmSettings: (body: Record<string, unknown>) =>
    apiFetch<import("./types").LlmSettings>("/settings/llm", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  testLlm: (body: Record<string, unknown>) =>
    apiFetch<import("./types").LlmTestResult>("/settings/llm/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getEmbeddingSettings: () =>
    apiFetch<import("./types").EmbeddingSettings>("/settings/embedding"),
  createEmbeddingProfile: (body: Record<string, unknown>) =>
    apiFetch<{ profile: import("./types").EmbeddingProfile; job_id: string }>(
      "/settings/embedding/profiles",
      { method: "POST", body: JSON.stringify(body) },
    ),
  testEmbedding: (body: Record<string, unknown>) =>
    apiFetch<import("./types").EmbeddingTestResult>("/settings/embedding/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  rollbackEmbedding: () =>
    apiFetch<import("./types").EmbeddingSettings>("/settings/embedding/rollback", {
      method: "POST",
      body: JSON.stringify({}),
    }),
  createExport: (body: Record<string, unknown>, idempotencyKey: string) =>
    apiFetch<import("./types").ExportCreated>("/exports", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  getExport: (exportId: string) =>
    apiFetch<import("./types").ExportJob>(`/exports/${exportId}`),
  exportDownloadUrl: (exportId: string) => `${API_BASE}/exports/${exportId}/download`,
  getUsage: (from: string, to: string = new Date().toISOString()) =>
    apiFetch<import("./types").UsageResponse>(
      `/settings/usage?${toQuery({ from, to, group_by: "day" })}`,
    ),
  // ---------------------------------------------------------------- admin
  adminUsers: (params: Record<string, string | number | undefined> = {}) =>
    apiFetch<{ items: import("./types").AdminUser[]; next_cursor: string | null }>(
      `/admin/users?${toQuery(params)}`,
    ),
  adminUser: (id: string) =>
    apiFetch<import("./types").AdminUser>(`/admin/users/${id}`),
  adminDisableUser: (id: string, reason: string) =>
    apiFetch<import("./types").AdminUser>(`/admin/users/${id}/disable`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  adminEnableUser: (id: string) =>
    apiFetch<import("./types").AdminUser>(`/admin/users/${id}/enable`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  adminWorkspaces: (params: Record<string, string | number | undefined> = {}) =>
    apiFetch<{
      items: import("./types").AdminWorkspace[];
      next_cursor: string | null;
    }>(`/admin/workspaces?${toQuery(params)}`),
  adminAuditLogs: (params: Record<string, string | number | undefined> = {}) =>
    apiFetch<{
      items: import("./types").AdminAuditLog[];
      next_cursor: string | null;
    }>(`/admin/audit-logs?${toQuery(params)}`),
  adminListInviteCodes: (status?: string) =>
    apiFetch<{ items: import("./types").InviteCode[] }>(
      `/admin/invite-codes${status ? `?status=${status}` : ""}`,
    ),
  adminCreateInviteCodes: (body: { count: number; expires_in_days: number }) =>
    apiFetch<{ items: { code: string; expires_at: string }[] }>(
      "/admin/invite-codes",
      { method: "POST", body: JSON.stringify(body) },
    ),
  adminPutSystemConfig: (key: string, value: unknown) =>
    apiFetch<import("./types").AdminSystemConfigItem>("/admin/system-config", {
      method: "PUT",
      body: JSON.stringify({ key, value }),
    }),
  adminUsage: (from: string, to: string = new Date().toISOString()) =>
    apiFetch<import("./types").AdminUsageResponse>(
      `/admin/usage?${toQuery({ from, to })}`,
    ),
  adminSystemConfig: () =>
    apiFetch<{ items: import("./types").AdminSystemConfigItem[] }>(
      "/admin/system-config",
    ),
};

function toQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") search.set(key, String(value));
  }
  return search.toString();
}
