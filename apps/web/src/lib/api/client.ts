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
  if (!response.ok) throw new Error("CSRF 初始化失败");
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
      envelope?.error?.message ?? "请求失败，请稍后重试",
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
    form.append("reviews_root", "wiki/复盘");
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
};
