/** Phase 0 手写契约类型；Phase 1 起由 `npm run gen:api` 从 OpenAPI 生成并替换。 */

export type UserStatus = "active" | "disabled" | "deleted";
export type WorkspaceRole = "owner" | "admin" | "member";
export type Plan = "free" | "pro";

export interface User {
  id: string;
  email: string;
  display_name: string;
  status: UserStatus;
  created_at: string;
}

export interface Workspace {
  id: string;
  name: string;
  slug: string;
  role: WorkspaceRole;
  plan: Plan;
  created_at: string;
}

export interface TokenBudget {
  input_tokens: number;
  output_tokens: number;
}

export interface Limits {
  review_concurrency: number;
  daily_budget: TokenBudget;
  monthly_budget: TokenBudget;
  max_zip_bytes: number;
  max_search_top_k: number;
}

export interface SessionInfo {
  expires_at: string;
  active_workspace_id: string;
  csrf_token: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name: string;
  invite_code: string;
}

export interface RegisterResponse {
  user: User;
  workspace: Workspace;
  session: SessionInfo;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface LoginResponse {
  user: User;
  workspaces: Workspace[];
  active_workspace_id: string;
  csrf_token: string;
}

export interface MeResponse {
  user: User;
  workspaces: Workspace[];
  active_workspace_id: string;
  capabilities: string[];
  limits: Limits;
}

export interface SettingsSummary {
  timezone: string;
  daily_at: string;
  weekly_at: string;
  yearly_at: string;
  reviews_root: string;
}

export interface WorkspaceCounts {
  documents: number;
  reviews: number;
  open_tasks: number;
}

export interface WorkspaceDetail {
  workspace: Workspace;
  settings_summary: SettingsSummary;
  counts: WorkspaceCounts;
}

export interface TreeNode {
  type: "dir" | "file";
  path: string;
  parent_path: string | null;
  name: string;
  title: string | null;
  document_id: string | null;
  size: number | null;
  mtime: string | null;
  occurred_on: string | null;
  tags: string[];
  child_count: number | null;
  source_type: "user" | "generated";
}

export interface DocumentTree {
  root: string;
  nodes: TreeNode[];
  total_documents: number;
  truncated: boolean;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details: unknown;
  retryable: boolean;
}

export interface ErrorEnvelope {
  error: ApiErrorBody;
  request_id: string;
}
