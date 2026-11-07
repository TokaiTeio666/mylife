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

export interface ConversationSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
  last_message_preview: string;
}

export interface Reference {
  index: number;
  document_id: string;
  chunk_id: string;
  path: string;
  title: string;
  heading: string | null;
  line_range: number[] | null;
  snippet: string;
}

export interface ToolEvent {
  seq: number;
  tool_name: string;
  args_digest: string;
  args_summary: string;
  status: "ok" | "error" | "timeout" | "skipped";
  latency_ms: number;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status: "drafting" | "complete" | "partial" | "cancelled";
  references: Reference[];
  tool_events: ToolEvent[];
  usage: {
    prompt_tokens: number;
    completion_tokens: number;
    model: string;
    latency_ms: number;
  } | null;
  run_id: string | null;
  created_at: string;
}

export interface ChatTurnResponse {
  run_id: string;
  message_id: string;
  stream: { events_url: string; epoch: number; after: number };
}

export interface RunDetail {
  run_id: string;
  kind: string;
  state: string;
  conversation_id: string | null;
  steps: Array<{
    seq: number;
    role: string;
    tool_name: string | null;
    status: string;
    model: string | null;
    tokens: number;
    latency_ms: number | null;
  }>;
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

// ---------------------------------------------------------------------------
// Phase 4：复盘、专题、交付、仪表盘与设置
// ---------------------------------------------------------------------------

export interface ReviewSummary {
  id: string;
  kind: "daily" | "weekly" | "yearly";
  period_key: string;
  title: string;
  version: number;
  status: string;
  generated_at: string;
  path: string | null;
  degraded: boolean;
  delivery_status: string;
}

export interface ReviewSection {
  section_key: string;
  ordinal: number;
  content_md: string;
  structured: Record<string, unknown>;
}

export interface ReviewCitation {
  claim_id: string;
  evidence_id: string;
  source_path: string | null;
  source_date: string | null;
  line_range: number[] | null;
  quote: string;
}

export interface ReviewEntry {
  section_key: string;
  item_type: string;
  canonical_key: string;
  fields: Record<string, unknown>;
  source_path: string | null;
  line_range: number[] | null;
  confidence: number | null;
}

export interface VersionRef {
  version: number;
  content_hash: string;
  git_sha: string | null;
  generated_at: string;
  is_current: boolean;
}

export interface ReviewDetail extends ReviewSummary {
  current_version: number;
  content_md: string;
  content_html: string;
  sections: ReviewSection[];
  citations: ReviewCitation[];
  entries: ReviewEntry[];
  verdict: Record<string, unknown> | null;
  model: string | null;
  snapshot_id: string | null;
  degraded_reasons: string[];
  git_path: string | null;
  versions: VersionRef[];
}

export interface TopicSummary {
  topic: string;
  title: string;
  path: string;
  open_count: number;
  last_updated: string | null;
}

export interface TopicOccurrence {
  source_date: string;
  source_path: string;
  line_range: number[] | null;
  quote: string;
  evidence_id: string | null;
}

export interface TopicItem {
  item_id: string;
  canonical_key: string;
  title: string;
  status: "pending" | "open" | "resolved" | "stale" | "dropped";
  first_seen: string;
  last_seen: string;
  occurrence_count: number;
  occurrences: TopicOccurrence[];
}

export interface DeliveryRecord {
  id: string;
  review_id: string;
  version: number;
  target: "git" | "object_store" | "ui_diff";
  provider: "github" | "gitlab" | null;
  branch: string | null;
  base_branch: string | null;
  commit_sha: string | null;
  pr_number: number | null;
  pr_url: string | null;
  status: string;
  merged_commit: string | null;
  auto_merge: boolean;
  error_code: string | null;
  degraded: boolean;
  created_at: string;
  updated_at: string;
}

export interface DeliveryDetail {
  delivery: DeliveryRecord;
  manifest: Record<string, unknown>;
  diff_summary: { files_added: number; files_modified: number; files_unchanged: number };
}

export interface TaskItem {
  id: string;
  text: string;
  status: "open" | "done" | "carried" | "dropped";
  first_seen: string;
  last_seen: string;
  days_open: number;
  done_date: string | null;
  source_path: string | null;
  line_number: number | null;
  occurrence_count: number;
}

export interface DashboardLatest {
  daily: ReviewSummary | null;
  weekly: ReviewSummary | null;
  yearly: ReviewSummary | null;
  generated_at: string;
  completion_rate: number;
  open_tasks: number;
}

export interface DashboardTaskList {
  items: TaskItem[];
  next_cursor: string | null;
  has_more: boolean;
  counts: { open: number; carried: number; done_today: number };
}

export interface DayCell {
  date: string;
  has_daily: boolean;
  daily_status: string | null;
  degraded: boolean;
  open_tasks: number;
}

export interface MonthCell {
  month: number;
  has_weekly: boolean;
  weekly_period_key: string | null;
  completion_rate: number | null;
}

export interface DashboardCalendar {
  year: number;
  months: MonthCell[];
  days: DayCell[];
}

export interface DashboardStats {
  range: string;
  completion_rate: number;
  tasks: { completed: number; total: number; carried_over: number };
  reviews: { daily: number; weekly: number; yearly: number };
  no_evidence_ratio: number;
  degraded_rate: number;
  tokens: { prompt: number; completion: number };
}

export interface ScheduleSettings {
  timezone: string;
  daily_at: string;
  weekly_at: string;
  yearly_at: string;
  catchup_days: number;
  enabled: { daily: boolean; weekly: boolean; yearly: boolean };
  next_run_at: { daily: string | null; weekly: string | null; yearly: string | null };
}

export interface GitConnectionSummary {
  id: string;
  role: "source" | "output";
  provider: "github" | "gitlab" | "generic";
  repo_full_name: string;
  base_branch: string;
  auth_type: "github_app" | "deploy_key" | "pat";
  mode: string | null;
  reviews_root: string | null;
  sync_interval_minutes: number;
  sync_enabled: boolean;
  next_sync_at: string | null;
  last_sync_at: string | null;
  last_sync_status: string | null;
  credential_status: "valid" | "invalid" | "untested";
  last_tested_at: string | null;
}

export interface DeletionRequest {
  deletion_id: string;
  scope: "workspace" | "account";
  state: "requested" | "cooling" | "purged" | "cancelled";
  purge_at: string;
  requested_at: string;
  requested_by: string;
  can_cancel: boolean;
  export_recommended: boolean;
  notice: { will_delete: string[]; will_retain: string[]; notice: string };
}
