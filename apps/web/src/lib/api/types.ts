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

export interface DocumentLink {
  target_path: string;
  link_text: string | null;
  resolved: boolean;
  target_document_id: string | null;
}

export interface DocumentDetail {
  id: string;
  path: string;
  title: string;
  content: string | null;
  content_html: string | null;
  frontmatter: Record<string, unknown>;
  tags: string[];
  links: DocumentLink[];
  backlinks: DocumentLink[];
  content_hash: string;
  size: number;
  mtime: string;
  occurred_on: string | null;
  indexed_at: string;
  source_type: "user" | "generated";
  chunks: unknown[] | null;
}

export interface SearchResult {
  source_type: "document" | "review";
  document_id: string | null;
  chunk_id: string | null;
  review_id: string | null;
  path: string;
  title: string;
  heading: string | null;
  snippet: string;
  snippet_html: string | null;
  score: number;
  matched_by: "lexical" | "vector" | "both";
  rank_lexical: number | null;
  rank_vector: number | null;
  line_range: number[] | null;
  occurred_on: string | null;
}

export interface SearchResponse {
  items: SearchResult[];
  total_estimated: number;
  degraded: boolean;
  degraded_reasons: string[];
  took_ms: number;
}

export interface IngestJob {
  id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "partial" | "failed" | "cancelled";
  progress: {
    total_files: number;
    processed_files: number;
    failed_files: number;
    skipped_files: number;
  };
  snapshot_id: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface IngestJobDetail {
  job: IngestJob;
  counters: {
    created: number;
    updated: number;
    unchanged: number;
    skipped: number;
    failed: number;
  };
  failed_items: Array<{
    path: string;
    line: number | null;
    reason_code: string;
    message: string;
  }>;
  snapshot: {
    snapshot_id: string;
    commit_sha: string | null;
    doc_count: number;
    as_of: string;
    manifest_digest: string;
  } | null;
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
