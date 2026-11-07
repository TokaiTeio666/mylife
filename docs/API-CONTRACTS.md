# mylife 接口契约规范（API Contracts）

> 版本：v0.1 ｜ 日期：2026-11-06 ｜ 状态：设计评审稿（待评审）
>
> 基线：`REQUIREMENTS.md` v1.0（已冻结）、`ARCHITECTURE.md` v0.4、`DECISIONS.md`（1D 2B 3B 4B 5B 6A 7A）
>
> 读者：前端（Next.js）、后端（FastAPI）、Agent 运行时（Planner/Worker/Writer/Reviewer）、运维。
>
> 契约权威顺序：`ARCHITECTURE.md` §5 数据契约 → 本文 → 生成的 OpenAPI 文档。本文若与 §5 字段名不一致，以 §5 为准并在 §13 记录；本文只补充 §5 未展开的子模型（SectionPlan、RetrievalTask 等），不改变既有字段语义。

---

## 1. 通用约定

### 1.1 Base URL 与版本

| 项 | 值 |
|---|---|
| 生产 Base URL | `https://{domain}/api/v1` |
| 本地 Base URL | `http://localhost:8000/api/v1` |
| SSE 端点 | `GET /api/v1/runs/{run_id}/events`（同源，不单独域名） |
| 内部工具端点 | `POST /internal/v1/tools/{tool_name}`（仅容器内网，Caddy 不暴露） |
| 健康检查 | `GET /healthz`（存活）、`GET /readyz`（就绪，含 DB/Redis/MinIO 探针） |
| 版本策略 | 仅 URL 版本；`/api/v1` 内只做向后兼容变更；破坏性变更开 `/api/v2` |

- 所有业务路径均不带 `workspace_id`，见 §1.4。
- 路径参数 `{id}` 一律为 UUID 字符串，不做数字自增 ID。
- 版本号以 `X-API-Version: 1` 响应头回显，便于排障。
- 架构文档中的路径（如 §2.5、§16.7 的 `/api/runs/{id}/events`）均省略版本段；规范路径一律补 `/v1`，即 `/api/v1/runs/{id}/events`。
- 兼容别名：`DELETE /api/v1/workspace` ≡ `POST /api/v1/deletions`（`scope=workspace`）；`DELETE /api/v1/account` ≡ `POST /api/v1/deletions`（`scope=account`）。别名仅做参数映射，状态机与响应以 §3.11 为准。

### 1.2 传输、编码与数据类型

| 项 | 规范 |
|---|---|
| 媒体类型 | 请求与响应 `application/json; charset=utf-8`；仅 ZIP 上传为 `multipart/form-data`；导出下载为 `application/zip` |
| 字符集 | UTF-8；服务端拒绝非 UTF-8 的 JSON |
| 字段命名 | `snake_case`（与 `ARCHITECTURE.md` §5 完全一致） |
| 布尔 | JSON `true` / `false`，不接受 `"true"` 字符串 |
| 整数 | token 数、计数、耗时均为 64 位整数；耗时单位毫秒（字段名后缀 `_ms`） |
| 时间戳 | RFC3339 UTC，秒级或毫秒级均可，服务端统一输出 `2026-11-06T01:30:00Z` |
| 业务日期 | `YYYY-MM-DD`（`date` 类型），不带时区 |
| 周期键 `period_key` | daily `YYYY-MM-DD`；weekly ISO `YYYY-Www`（如 `2026-W43`）；yearly `YYYY` |
| 时区 | IANA 名称（如 `Asia/Shanghai`），不接受 `UTC+8` 形式 |
| ID | UUIDv7 小写字符串（36 字符，带连字符）；`event_id` 为 int64；`client_turn_id` 由客户端生成 UUIDv4/v7 |
| 枚举 | 服务端严格校验，未知值返回 `VALIDATION_ERROR`；客户端必须容忍新增枚举（见 §10） |
| 状态字符串 | API/JSON 一律小写（`created`、`human_gate`），与 `agent_runs.state` 列一致；架构 §4.1 表使用大写仅为可读写法，二者为同一枚举 |
| 可选字段 | 请求中省略 = 使用默认；显式 `null` = 明确置空，仅对声明可空的字段合法 |
| 响应字段 | 所有可空字段必须显式输出 `null`，不得省略键（前端类型生成依赖此约定） |
| 最大请求体 | 普通 JSON 1 MB；`POST /ingest/zip` 200 MB（解压上限见 §7.2）；其余 multipart 20 MB |

### 1.3 认证与会话

| 项 | 规范 |
|---|---|
| 会话载体 | HttpOnly Cookie `mylife_session`（`Secure; HttpOnly; SameSite=Lax; Path=/`） |
| Cookie 值 | 不透明随机 token（32 字节 base64url）；服务端只存 `sessions.token_hash` |
| 有效期 | 绝对 30 天；空闲 7 天滑动续期；每次请求命中后续期 |
| 会话校验 | `token_hash` 匹配 + `token_version` 等于当前 + `expires_at > now` + `revoked_at IS NULL` |
| CSRF | double-submit：非 HttpOnly Cookie `mylife_csrf` + 请求头 `X-CSRF-Token`，两者必须相等 |
| CSRF 适用范围 | 所有非安全方法（POST/PUT/PATCH/DELETE），`/auth/login`、`/auth/register` 也要求 |
| CSRF 豁免 | `POST /webhooks/git/{provider}`（用签名校验，见 §3.12） |
| 会话固定防护 | 登录成功后轮换 session token 与 CSRF token |
| 退出所有设备 | `POST /auth/logout-all` 使 `token_version += 1`，该用户全部旧会话立即失效（FR-A6） |
| 密码 | Argon2id；最少 8 位；不返回具体强度原因以外的信息 |
| 登录失败 | 统一文案“邮箱或密码不正确”；按 IP + 邮箱双维度限流与指数锁定（§8） |

前端实现要求：所有请求 `credentials: "include"`；启动时先 `GET /auth/csrf` 取 token 并写入内存 + 回读 Cookie；401 时只触发一次静默刷新（重新登录），不得循环重试。

### 1.4 Workspace 注入（不可协商）

1. `workspace_id` 只来自服务端会话行 `sessions.active_workspace_id`，由 `RunContext` 注入。
2. HTTP 请求体、查询参数、路径、请求头**一律不声明** `workspace_id`；Pydantic 模型 `extra="forbid"`，出现该字段即 `VALIDATION_ERROR`（`details.reason = WORKSPACE_ID_NOT_ALLOWED`）。
3. 切换 Workspace 只通过 `POST /workspaces/{id}/switch`，服务端校验 `workspace_members` 后写入会话。
4. 跨租户访问统一返回 `404 NOT_FOUND`，不返回 403，避免资源探测；内部审计记录 `WORKSPACE_MISMATCH`。
5. 工具调用同理：`ToolGateway` 的入参模型不含 `workspace_id`，由 `ToolContext` 强制注入（FR-D7）。

### 1.5 成功响应与错误信封

单个资源：直接返回资源对象，不套 `data`。列表：返回分页对象。所有响应带 `X-Request-Id`。

错误响应统一为：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请求参数不合法",
    "details": [
      { "field": "period_key", "reason": "must match YYYY-MM-DD" }
    ],
    "retryable": false
  },
  "request_id": "req_01J9Z3K4M5N6P7Q8R9S0T1V2W3"
}
```

| 字段 | 类型 | 约束 |
|---|---|---|
| `error.code` | string | 稳定机器码，见 §2；客户端据此分支，禁止解析 `message` |
| `error.message` | string | 简体中文，面向用户；不得含堆栈、SQL、token、路径以外的内部信息 |
| `error.details` | object 或 array 或 null | 字段级校验错误、冲突对象、可用替代值；不得回显凭据 |
| `error.retryable` | bool | 与 §2 表一致；客户端不得自行推断 |
| `request_id` | string | `req_` 前缀 + ULID；同时写入 `X-Request-Id` |

补充头：`Retry-After`（秒，仅可重试错误）、`X-RateLimit-*`（§8）、`Idempotent-Replayed: true`（幂等重放）。

### 1.6 分页

| 项 | 规范 |
|---|---|
| 方式 | 游标分页（cursor），不用 offset |
| 请求参数 | `limit` 默认 20，范围 1–100；`cursor` 为服务端签发的 base64url 不透明串 |
| 响应 | `{ "items": [...], "next_cursor": string 或 null, "has_more": bool }` |
| 排序 | 稳定键 `(created_at desc, id desc)`；同一 cursor 重放结果一致 |
| 游标失效 | 返回 `VALIDATION_ERROR`，`details.reason = CURSOR_INVALID`；客户端从第一页重取 |
| 大数据集 | 导出、审计使用异步任务，不走分页拉全量 |

### 1.7 幂等

| 项 | 规范 |
|---|---|
| 请求头 | `Idempotency-Key: <UUID>`，长度 16–64，字符 `[A-Za-z0-9_-]` |
| 适用端点 | 所有产生副作用的 POST：`/reviews`、`/ingest/zip`、`/ingest/sync`、`/ingest/git`、`/exports`、`/deletions`、`/conversations/{id}/turns`、`/runs/{id}/approve` |
| 作用域 | `(workspace_id, user_id, endpoint, Idempotency-Key)` |
| 相同键 + 相同请求体哈希 | 返回首次响应，附 `Idempotent-Replayed: true`；不重复执行 |
| 相同键 + 不同请求体哈希 | `409 IDEMPOTENCY_KEY_CONFLICT` |
| 保留期 | 24 小时；过期后同键视为新请求 |
| 缺失键 | 允许，但按端点自身的业务幂等键去重（如 `client_turn_id`、`(kind, period_key)`） |

业务幂等键（与架构 §4.3 一致）：

| 场景 | 幂等键 |
|---|---|
| 复盘 run | `(workspace_id, kind, period_key)` 活跃唯一；已成功则新建 v(n+1) |
| chat turn | `(workspace_id, conversation_id, client_turn_id)` |
| 导入 | `(workspace_id, source_kind, source_ref, snapshot_digest)` |
| 交付 | `(workspace_id, review_id, version, target)` |
| webhook | `provider_delivery_id` |

### 1.8 并发与冲突

| 场景 | 行为 |
|---|---|
| 同 period 已有活跃复盘 run | `409 ACTIVE_RUN_EXISTS`，`details.existing_run_id` 必填；客户端跳转该 run |
| 同会话已有活跃 chat run | 服务端先 `CANCELLED` 旧 run 并发 `cancelled` 事件，再接受新 turn；仅当取消失败返回 `409 ACTIVE_RUN_EXISTS` |
| 同 turn 重发 | 返回已有 run，`Idempotent-Replayed: true` |
| 人工门重复审批 | 第二次返回 `409 CONFLICT`，`details.decided_by`、`details.decided_at` |
| Git 写并发 | 每 Workspace 单写锁；冲突返回 `409 DELIVERY_PR_CONFLICT`，不自动 force push |
| 专题首见待确认 | `409 REVIEW_TOPIC_NEEDS_CONFIRM`，`details.pending_items[]` |

### 1.9 请求头与响应头清单

| 头 | 方向 | 必填 | 说明 |
|---|---|---|---|
| `X-CSRF-Token` | 请求 | 非安全方法必填 | 与 `mylife_csrf` Cookie 相等 |
| `Idempotency-Key` | 请求 | 见 §1.7 | 幂等 |
| `X-Request-Id` | 请求/响应 | 可选 | 客户端可传入 ULID；服务端回显 |
| `X-Client-Version` | 请求 | 可选 | 前端版本，用于灰度与排障 |
| `Accept-Language` | 请求 | 可选 | 首版仅 `zh-CN`，其他回退中文 |
| `Last-Event-ID` | 请求 | SSE 重连 | 等价 `after` 参数 |
| `X-Stream-Epoch` | 响应 | SSE | 当前进程事件纪元 |
| `X-RateLimit-Limit` / `Remaining` / `Reset` | 响应 | 受限端点 | 见 §8 |
| `Retry-After` | 响应 | 429/503 | 秒 |
| `Deprecation` / `Sunset` | 响应 | 弃用端点 | RFC 8594 / RFC 9745 |
| `Cache-Control` | 响应 | 全部 | 见 §1.11 |

### 1.10 状态码使用约定

| 码 | 用途 |
|---|---|
| 200 | 查询成功、同步动作成功、SSE 建流 |
| 201 | 创建资源（register、conversation、review run、export、deletion） |
| 202 | 已接受异步任务（ingest、sync、retry） |
| 204 | 无响应体（logout、delete conversation、cancel） |
| 206 | 部分成功（导入部分失败、搜索降级）；同时返回 `degraded=true` |
| 304 | 条件请求命中（文档内容 ETag 未变） |
| 400 | 请求格式错误、游标非法 |
| 401 | 未认证、会话过期、webhook 签名失败 |
| 403 | 已认证但无权限、CSRF 失败、安全终止 |
| 404 | 资源不存在、跨租户（统一）、路径越界 |
| 405 | 方法不允许 |
| 409 | 状态冲突、活跃 run、幂等键冲突、PR 冲突 |
| 410 | 导出已过期、资源已 purge |
| 412 | 前置条件失败（如审批前 Gate 未通过） |
| 413 | 请求体/ZIP 超限 |
| 415 | 媒体类型不支持 |
| 422 | 语义校验失败、schema 校验失败、Gate 失败 |
| 429 | 限流、配额、预算硬停 |
| 500 | 未预期错误 |
| 501 | 未实现（Phase 2 能力，如 Web 搜索开关） |
| 502 | 上游失败（Git 提供方、LLM 网关） |
| 503 | 依赖不可用（LLM、Embedding、Git、DB 只读） |
| 504 | 上游超时 |

### 1.11 缓存与条件请求

| 资源 | `Cache-Control` | ETag |
|---|---|---|
| `/dashboard/*` | `private, max-age=60` | 有（弱 ETag，按 workspace + 参数） |
| `/documents/tree` | `private, max-age=30` | 有 |
| `/documents/{id}` | `private, max-age=300, must-revalidate` | 有（`content_hash` 前 16 位） |
| `/search` | `private, no-store` | 无 |
| `/runs/{id}/events` | `no-store`，`X-Accel-Buffering: no` | 无 |
| `/auth/me`、`/settings/*` | `private, no-store` | 无 |
| 静态资源 | 由 web 层处理，不经 api | — |

条件请求：`If-None-Match` 命中返回 `304` 且无响应体；`If-Match` 用于 `PUT /settings/*` 与 `PATCH /conversations/{id}` 的乐观并发，不匹配返回 `412 PRECONDITION_FAILED`。

### 1.12 上传与下载

| 项 | 规范 |
|---|---|
| ZIP 上传 | `multipart/form-data`，字段 `file`；文件名仅用于展示，服务端不信任；`Content-Type` 允许 `application/zip`、`application/x-zip-compressed` |
| 上传校验 | 压缩 ≤200 MB、解压 ≤2 GB、单文件 ≤20 MB、文件数 ≤50k、压缩比 >100:1 拒绝、拒绝符号链接与路径穿越 |
| 上传进度 | 前端用 `XMLHttpRequest` 上传进度；服务端进度走 `GET /ingest/jobs/{id}` 轮询（1s 起指数退避至 10s） |
| 导出下载 | `GET /exports/{id}/download` 返回 302 到 MinIO 预签名 URL（TTL 10 分钟）；或直传流式 `application/zip` |
| 文档附件 | `GET /documents/{id}/asset?path=...` 经服务端代理，校验路径属于该文档所属 Vault；设置 `Content-Security-Policy: sandbox` |

### 1.13 审计

以下动作必须写 `audit_logs`（FR-A7）：登录成功/失败、退出所有设备、导入开始与完成、删除请求与撤销、配置变更、复盘生成与审批、Git 连接变更、导出。审计写入与业务事务同事务（outbox 模式），失败不阻断主流程但记 `audit_write_failed` 指标。

---

## 2. 错误模型与错误码表

### 2.1 错误码命名规则

`{DOMAIN}_{REASON}`：`AUTH_`、`INGEST_`、`REVIEW_`、`DELIVERY_`、`GIT_`、`EXPORT_`、`ADMIN_`、`TOOL_`，通用错误无前缀。错误码一经发布不得改语义；新增只能追加。

### 2.2 通用错误码

| code | HTTP | 可重试 | Retry-After | 触发条件 | 客户端动作 |
|---|---|---|---|---|---|
| `VALIDATION_ERROR` | 422 | 否 | — | 字段缺失/类型错/枚举未知/`extra` 字段 | 展示 `details` 字段错误 |
| `BAD_REQUEST` | 400 | 否 | — | JSON 解析失败、游标格式非法、参数组合非法 | 修正请求 |
| `NOT_FOUND` | 404 | 否 | — | 资源不存在、跨租户、路径越界 | 提示不存在；不重试 |
| `FORBIDDEN` | 403 | 否 | — | 已认证但角色不足 | 提示无权限 |
| `WORKSPACE_MISMATCH` | 404 | 否 | — | 服务端检测到跨租户访问（仅内部/审计可见，对外呈现 `NOT_FOUND`） | 同 `NOT_FOUND` |
| `METHOD_NOT_ALLOWED` | 405 | 否 | — | 方法不支持 | 修正 |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | 否 | — | Content-Type 不符 | 修正 |
| `PAYLOAD_TOO_LARGE` | 413 | 否 | — | 超过 §1.12 上限 | 提示压缩/拆分 |
| `CONFLICT` | 409 | 视场景 | — | 通用状态冲突 | 读取 `details` 后重取状态 |
| `PRECONDITION_FAILED` | 412 | 否 | — | `If-Match` 不匹配、Gate 未通过 | 刷新后重试 |
| `IDEMPOTENCY_KEY_CONFLICT` | 409 | 否 | — | 同键不同请求体 | 换键或放弃 |
| `RATE_LIMITED` | 429 | 是 | 有 | 超过 §8 限额 | 按 `Retry-After` 退避 |
| `QUOTA_EXCEEDED` | 429 | 否 | 无 | 日/月配额耗尽（非 token 预算） | 提示配额与重置时间 |
| `BUDGET_EXCEEDED` | 429 | 否 | 无 | run 或 workspace token 预算 100% 硬停（决策 5B） | 提示预算；不重试 |
| `INTERNAL_ERROR` | 500 | 是 | 有（3s） | 未预期异常 | 指数退避重试 ≤3 |
| `SERVICE_UNAVAILABLE` | 503 | 是 | 有 | 依赖不可用、维护窗口 | 退避重试 |
| `NOT_IMPLEMENTED` | 501 | 否 | — | Phase 2 能力被调用 | 隐藏入口 |
| `DEGRADED` | 200/206 | — | — | 非致命降级，见 `degraded[]` 与 `DegradedReason`（§4.5） | 展示横幅，不阻断 |
| `SECURITY_ABORT` | 403 | 否 | — | `TENANT_LEAK`/`FABRICATION` 命中，不可人工放行 | 终止，展示安全提示 |
| `TENANT_LEAK` | 403 | 否 | — | 跨租户引用/写入（内部码，对外 `SECURITY_ABORT`） | 终止并上报 |
| `FABRICATION` | 422 | 否 | — | 引用无法解析或证据不存在（内部码） | 终止当前 run |
| `PROMPT_INJECTION_BLOCKED` | 422 | 否 | — | 笔记内容触发指令性模式/外链 | 提示该文档被隔离 |
| `SCHEMA_MIGRATION_FAILED` | 422 | 否 | — | `schema_version` 无法迁移 | 转人工 |
| `EVENT_CURSOR_EXPIRED` | 410 | 否 | — | SSE `after` 早于保留窗口 | 从 run 状态重建 |
| `CURSOR_INVALID` | 400 | 否 | — | 分页游标被篡改/过期 | 从第一页重取 |

### 2.3 认证与账户

| code | HTTP | 可重试 | Retry-After | 触发条件 | 客户端动作 |
|---|---|---|---|---|---|
| `AUTH_INVALID_CREDENTIALS` | 401 | 否 | — | 邮箱或密码错误 | 统一提示，不区分 |
| `AUTH_SESSION_EXPIRED` | 401 | 是（重登） | — | 会话过期 | 跳登录 |
| `AUTH_SESSION_REVOKED` | 401 | 否 | — | `token_version` 失效（退出所有设备） | 跳登录并说明 |
| `AUTH_CSRF_INVALID` | 403 | 是（刷新后） | — | CSRF 头缺失或不匹配 | 重新拉取 CSRF 后重试一次 |
| `AUTH_ACCOUNT_DISABLED` | 403 | 否 | — | 账号被管理员禁用 | 提示联系管理员 |
| `AUTH_ACCOUNT_LOCKED` | 429 | 是 | 有 | 连续失败触发临时锁定 | 等待解锁 |
| `AUTH_EMAIL_EXISTS` | 409 | 否 | — | 注册邮箱已存在 | 提示改用登录 |
| `AUTH_WEAK_PASSWORD` | 422 | 否 | — | 密码 <8 位或超出 128 位 | 提示规则 |
| `AUTH_INVITE_REQUIRED` | 403 | 否 | — | 注册未带邀请码 | 提示需邀请码 |
| `AUTH_INVITE_INVALID` | 400 | 否 | — | 邀请码不存在 | 提示无效 |
| `AUTH_INVITE_EXPIRED` | 400 | 否 | — | 邀请码过期 | 提示申请新码 |
| `AUTH_INVITE_USED` | 400 | 否 | — | 邀请码已被使用（一次性） | 提示已使用 |
| `AUTH_RATE_LIMITED` | 429 | 是 | 有 | 登录/注册限流 | 退避 |
| `AUTH_EMAIL_NOT_VERIFIED` | 403 | 否 | — | 首版保留，未启用邮件验证 | 不应出现 |

### 2.4 导入（INGEST_）

| code | HTTP | 可重试 | Retry-After | 触发条件 | 客户端动作 |
|---|---|---|---|---|---|
| `INGEST_REPO_UNREACHABLE` | 502 | 是 | 有 | Git 域名不可达、TLS 失败 | 退避重试；检查网络 |
| `INGEST_AUTH_FAILED` | 422 | 否 | — | 凭据无效、无 `contents:write`（交付场景）或读权限不足 | 重新配置凭据 |
| `INGEST_BRANCH_NOT_FOUND` | 404 | 否 | — | 分支不存在 | 选择其他分支 |
| `INGEST_IGNORE_ALL` | 422 | 否 | — | 忽略规则过滤后无 Markdown | 提示检查忽略规则 |
| `INGEST_ZIP_INVALID` | 422 | 否 | — | ZIP 损坏、非 ZIP、含符号链接/穿越 | 重新打包 |
| `INGEST_ZIP_LIMIT` | 413 | 否 | — | 压缩/解压/单文件/文件数/压缩比超限 | 拆分或清理 |
| `INGEST_JOB_NOT_FOUND` | 404 | 否 | — | 任务不存在 | — |
| `INGEST_JOB_NOT_RETRYABLE` | 409 | 否 | — | 任务为排队/运行中或已成功 | 等待或新建任务 |
| `INGEST_PARTIAL` | 206 | 是（仅失败项） | — | 部分文件失败；响应 `failed_count > 0` | 调 retry 端点 |
| `INGEST_SNAPSHOT_STALE` | 409 | 是 | — | 快照落后超过 `index_lag_sec` 1800s 且未 `force_refresh` | 触发同步后重试 |
| `INGEST_CONCURRENCY_LIMIT` | 429 | 是 | 有 | 同 Workspace 并发导入 >1 | 等待当前任务 |

### 2.5 复盘与交付（REVIEW_ / DELIVERY_ / GIT_）

| code | HTTP | 可重试 | Retry-After | 触发条件 | 客户端动作 |
|---|---|---|---|---|---|
| `ACTIVE_RUN_EXISTS` | 409 | 否 | — | 同 period 有活跃 run（架构 §2.5/§4.3）；`REVIEW_ACTIVE_RUN_EXISTS` 为保留别名，响应统一返回 `ACTIVE_RUN_EXISTS` | 跳转 `details.existing_run_id` |
| `REVIEW_PERIOD_INVALID` | 422 | 否 | — | `period_key` 与 `kind` 不匹配、未来日期、时区非法 | 修正 |
| `REVIEW_PERIOD_TOO_OLD` | 422 | 否 | — | 超出 `catchup_days` 或历史窗口 | 说明范围 |
| `REVIEW_NO_SOURCE_DATA` | 422 | 否 | — | 周期内无任何素材（允许生成空日复盘，但需 `allow_empty=true`） | 确认后重试 |
| `REVIEW_GATE_FAILED` | 422 | 否 | — | 确定性门禁 blocking 且不可修订 | 查看 verdict |
| `REVIEW_REVISION_LIMIT` | 422 | 否 | — | 修订轮次耗尽（daily/weekly 2、yearly 3） | 转人工 |
| `REVIEW_HUMAN_REQUIRED` | 409 | 否 | — | 需人工审批（yearly 默认） | 展示审批页 |
| `REVIEW_ALREADY_DECIDED` | 409 | 否 | — | 人工门已审批 | 刷新状态 |
| `REVIEW_TOPIC_NEEDS_CONFIRM` | 409 | 否 | — | 专题首见条目待人工确认（决策 4B） | 打开确认列表 |
| `REVIEW_VERSION_NOT_FOUND` | 404 | 否 | — | 指定 version 不存在 | 刷新版本列表 |
| `DELIVERY_GIT_UNAVAILABLE` | 503 | 是 | 有 | Git 提供方不可用 | 退避；可下载产物 |
| `DELIVERY_CREDENTIAL_INVALID` | 422 | 否 | — | 交付凭据失效 | 重配 Git |
| `DELIVERY_PR_CONFLICT` | 409 | 否 | — | PR 冲突、非快进、同 workspace 已有 open PR | 人工处理 |
| `DELIVERY_NOOP` | 200 | — | — | `content_hash` 未变，跳过提交 | 视为成功 |
| `DELIVERY_NOT_FOUND` | 404 | 否 | — | 交付记录不存在 | — |
| `GIT_MARKER_BROKEN` | 409 | 否 | — | 专题文件 `mylife:auto` 标记丢失/嵌套/被改 | 转 manual |
| `GIT_FORCE_PUSH_DENIED` | 403 | 否 | — | 任何 force push 尝试 | 拒绝并告警 |
| `GIT_PROVIDER_UNSUPPORTED` | 422 | 否 | — | provider 非 github/gitlab | 修正 |
| `WEBHOOK_SIGNATURE_INVALID` | 401 | 否 | — | 签名缺失或不匹配 | 拒绝，不重试 |
| `WEBHOOK_REPLAY` | 200 | — | — | 重复 `provider_delivery_id` | 已处理，返回 200 |

### 2.6 工具、搜索与模型依赖

| code | HTTP | 可重试 | Retry-After | 触发条件 | 客户端动作 |
|---|---|---|---|---|---|
| `TOOL_NOT_FOUND` | 404 | 否 | — | 工具名不在注册表 | 不重试 |
| `TOOL_NOT_ALLOWED` | 403 | 否 | — | 当前角色/阶段无该工具权限 | 不重试 |
| `TOOL_TIMEOUT` | 504 | 是 | — | 工具超过超时（默认 10s） | 由编排层重试 1 次 |
| `TOOL_ARGS_INVALID` | 422 | 否 | — | 工具参数校验失败（含 `extra`） | 修复调用 |
| `TOOL_DRIFT` | 503 | 否 | — | 声明能力与实现不一致，工具被禁用 | 告警 |
| `SEARCH_VECTOR_TIMEOUT` | 200 | — | — | 向量路超 250ms，降级 lexical-only | 展示 `degraded` |
| `SEARCH_EMPTY_QUERY` | 422 | 否 | — | 查询为空或全为停用词 | 修正 |
| `SEARCH_TOO_MANY_TERMS` | 422 | 否 | — | 分词后 >64 个词 | 缩短查询 |
| `LLM_UNAVAILABLE` | 503 | 是 | 有 | LLM 超时/5xx/熔断打开 | 退避；UI 标降级 |
| `LLM_RATE_LIMITED` | 429 | 是 | 有 | 供应商限流 | 退避 |
| `EMBEDDING_UNAVAILABLE` | 503 | 是 | 有 | Embedding 服务不可用 | 退避；检索降级 |
| `EMBEDDING_PROFILE_MISMATCH` | 409 | 否 | — | 查询维度与 active profile 不一致 | 重建索引 |
| `MODEL_OUTPUT_INVALID` | 422 | 是（内部） | — | 结构化输出 schema 非法 | 编排层修复重试 |
| `PROVIDER_KEY_MISSING` | 422 | 否 | — | 未配置 LLM/Embedding 凭据 | 跳设置页 |

### 2.7 管理、导出与删除

| code | HTTP | 可重试 | Retry-After | 触发条件 | 客户端动作 |
|---|---|---|---|---|---|
| `ADMIN_REQUIRED` | 403 | 否 | — | 非 `platform_admin` | 隐藏入口 |
| `ADMIN_USER_NOT_FOUND` | 404 | 否 | — | 用户不存在 | — |
| `ADMIN_USER_DISABLED` | 409 | 否 | — | 目标用户已禁用 | 刷新 |
| `ADMIN_INVITE_LIMIT` | 429 | 是 | 有 | 邀请码创建超限 | 退避 |
| `ADMIN_SYSTEM_CONFIG_LOCKED` | 409 | 否 | — | 系统配置被部署级环境变量锁定 | 说明只读 |
| `EXPORT_NOT_READY` | 409 | 是 | 有 | 导出任务仍在运行 | 轮询 |
| `EXPORT_EXPIRED` | 410 | 否 | — | 下载链接过期（7 天） | 重新导出 |
| `EXPORT_LIMIT` | 429 | 是 | 有 | 并发/频率超限 | 退避 |
| `DELETE_ALREADY_REQUESTED` | 409 | 否 | — | 已有删除请求 | 查看状态 |
| `DELETE_IN_COOLING` | 409 | 否 | — | 处于 30 天冷却（决策 7A） | 可撤销 |
| `DELETE_NOT_CANCELLABLE` | 409 | 否 | — | 已进入 purged | 不可撤销 |

> `DEGRADED` 不是失败：HTTP 仍为 200/206，响应体含 `degraded: true` 与 `degraded_reasons[]`；前端必须展示横幅，不得静默。`SECURITY_ABORT` 与 `TENANT_LEAK` 不提供人工放行入口（反目标 A5/C1）。

---

## 3. 端点契约

通用规则：除 `auth` 与 `webhooks` 外，所有端点都要求已认证会话，并由服务端注入 `workspace_id`（§1.4）。下表“权限”列中的 `owner` 指当前 Workspace 的 owner；`platform_admin` 为平台级角色。所有端点均返回 §1.5 错误信封。

### 3.1 auth

#### 3.1.1 `POST /auth/register`

| 项 | 值 |
|---|---|
| 权限 | 匿名（需 CSRF） |
| 请求 | `{ "email": string, "password": string, "display_name": string, "invite_code": string }` |
| 响应 201 | `{ "user": User, "workspace": Workspace, "session": SessionInfo }`，同时 `Set-Cookie` 两个 Cookie |
| 幂等 | `Idempotency-Key` 可选；同邮箱并发注册由 `users.email` 唯一约束兜底 |
| 错误码 | `AUTH_INVITE_REQUIRED`、`AUTH_INVITE_INVALID`、`AUTH_INVITE_EXPIRED`、`AUTH_INVITE_USED`、`AUTH_EMAIL_EXISTS`、`AUTH_WEAK_PASSWORD`、`VALIDATION_ERROR`、`AUTH_RATE_LIMITED` |

校验：`email` RFC 5322 且 ≤254 字符并小写归一；`password` 8–128 字符；`display_name` 1–64 字符（去首尾空白后非空）；`invite_code` 8–32 字符。

行为：校验邀请码 → 创建 `users` → 置 `invite_codes.used_by/used_at`（同事务，失败回滚）→ 自动创建个人 Workspace（名称 `我的知识库`，slug 由 `ws-{短id}` 生成，FR-A3）→ 写入 `workspace_members(role=owner)` → 创建 `sessions` → 写 `audit_logs(action=user.registered)`。

`User` 模型：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | UUID | 否 | 用户 ID |
| `email` | string | 否 | 归一化小写 |
| `display_name` | string | 否 | 展示名 |
| `status` | enum `active`/`disabled` | 否 | 账号状态 |
| `created_at` | timestamp | 否 | UTC |

`Workspace` 模型：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | UUID | 否 | — |
| `name` | string | 否 | 1–64 字符 |
| `slug` | string | 否 | `[a-z0-9-]{3,48}`，全局唯一 |
| `role` | enum `owner`/`admin`/`member` | 否 | 当前用户在该 workspace 的角色 |
| `plan` | enum `free` | 否 | 首版仅 `free` |
| `created_at` | timestamp | 否 | — |

`SessionInfo`：`{ "expires_at": timestamp, "active_workspace_id": UUID, "csrf_token": string }`。`csrf_token` 同时写入非 HttpOnly Cookie，便于前端读取。

#### 3.1.2 `POST /auth/login`

| 项 | 值 |
|---|---|
| 权限 | 匿名（需 CSRF） |
| 请求 | `{ "email": string, "password": string }` |
| 响应 200 | `{ "user": User, "workspaces": Workspace[], "active_workspace_id": UUID, "csrf_token": string }` |
| 会话 | 轮换 session token（防会话固定），旧会话 `revoked_at` 置位 |
| 错误码 | `AUTH_INVALID_CREDENTIALS`（401，统一文案）、`AUTH_ACCOUNT_DISABLED`、`AUTH_ACCOUNT_LOCKED`、`AUTH_RATE_LIMITED` |

登录失败计数：同一 `(email, ip)` 组合，5 次/15 分钟 → 锁 15 分钟（`AUTH_ACCOUNT_LOCKED`）；同一 IP 20 次/小时 → 429。计数在 Redis，键含 `workspace` 无关（登录前无 workspace）。审计记录成功与失败（脱敏邮箱）。

#### 3.1.3 `POST /auth/logout`

| 项 | 值 |
|---|---|
| 权限 | 已认证 |
| 请求 | 无体 |
| 响应 204 | 无体，清除两个 Cookie |
| 语义 | 仅当前会话 `revoked_at=now`；其他设备不受影响 |

#### 3.1.4 `POST /auth/logout-all`

| 项 | 值 |
|---|---|
| 权限 | 已认证 |
| 请求 | 无体 |
| 响应 204 | 无体 |
| 语义 | `sessions.token_version += 1`（对该用户所有会话行），旧 token 全部失效（FR-A6）；写审计 `user.logout_all` |
| 错误码 | `AUTH_SESSION_EXPIRED` |

#### 3.1.5 `GET /auth/me`

| 项 | 值 |
|---|---|
| 权限 | 已认证 |
| 响应 200 | `{ "user": User, "workspaces": Workspace[], "active_workspace_id": UUID, "capabilities": string[], "limits": Limits }` |
| 缓存 | `no-store` |

`capabilities` 为当前角色可用的功能键，如 `chat`、`review_manual`、`git_delivery`、`admin`、`export`；前端据此隐藏入口，服务端仍独立校验（前端不作为安全边界）。

`Limits`：`{ "review_concurrency": 2, "daily_budget": {"input_tokens": 100000, "output_tokens": 20000}, "monthly_budget": {"input_tokens": 5000000, "output_tokens": 1000000}, "max_zip_bytes": 209715200, "max_search_top_k": 50 }`。

#### 3.1.6 `GET /auth/csrf`

| 项 | 值 |
|---|---|
| 权限 | 匿名可调（有会话时绑定会话，无会话时签发匿名 CSRF） |
| 响应 200 | `{ "csrf_token": string }`，并写 `mylife_csrf` Cookie |
| 语义 | 前端启动时调用一次；token 轮换后重新获取 |

### 3.2 workspaces

#### 3.2.1 `GET /workspaces`

| 项 | 值 |
|---|---|
| 权限 | 已认证 |
| 响应 200 | `{ "items": Workspace[] }`（不分页，首版每用户 ≤5） |
| 说明 | 按 `workspace_members` 过滤；`platform_admin` 不因此看到他人 Workspace 的业务数据 |

#### 3.2.2 `POST /workspaces`

| 项 | 值 |
|---|---|
| 权限 | 已认证（首版 UI 仅暴露个人 Workspace 与创建入口） |
| 请求 | `{ "name": string }`（1–64 字符） |
| 响应 201 | `Workspace` |
| 幂等 | `Idempotency-Key` 推荐 |
| 错误码 | `VALIDATION_ERROR`、`CONFLICT`（slug 冲突，服务端自动重试加后缀）、`QUOTA_EXCEEDED`（每用户 Workspace 上限 5） |
| 行为 | 创建 `workspaces` + `workspace_members(owner)` + 默认 `workspace_settings` + 默认 `workspace_schedules`（daily 01:00、weekly 周一 01:30、yearly 1/1 02:00，时区默认 `Asia/Shanghai`，FR-E13） |

#### 3.2.3 `POST /workspaces/{id}/switch`

| 项 | 值 |
|---|---|
| 权限 | 已认证且为该 workspace 成员 |
| 请求 | 无体 |
| 响应 200 | `{ "active_workspace_id": UUID, "csrf_token": string }`（轮换 CSRF） |
| 语义 | 写 `sessions.active_workspace_id`；不轮换 session token |
| 错误码 | `NOT_FOUND`（非成员，统一 404）、`AUTH_SESSION_EXPIRED` |
| 并发 | 同会话并发切换以最后一次提交为准，前端需串行 |

#### 3.2.4 `GET /workspaces/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 响应 200 | `{ "workspace": Workspace, "settings_summary": SettingsSummary, "counts": {"documents": int, "reviews": int, "open_tasks": int} }` |

#### 3.2.5 `PATCH /workspaces/{id}`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "name"?: string }` |
| 响应 200 | `Workspace` |
| 条件 | `If-Match` 可选（对 `updated_at` 弱校验） |
| 错误码 | `FORBIDDEN`、`PRECONDITION_FAILED`、`VALIDATION_ERROR` |
| 审计 | `workspace.renamed` |

> 成员管理（邀请成员、角色变更）属 Phase 2（M7）；首版 `workspace_members` 仅 owner 一行。端点预留：`GET /workspaces/{id}/members` 返回 `{ "items": [{"user_id", "role", "joined_at"}] }`，首版恒为 1 条。

### 3.3 ingest

#### 3.3.1 `POST /ingest/git`

| 项 | 值 |
|---|---|
| 权限 | `owner`（Git 凭据属敏感配置） |
| 请求 | 见下表 |
| 响应 202 | `{ "job": IngestJob, "connection": GitConnection }` |
| 幂等 | `Idempotency-Key`；业务键 `(workspace, provider, repo_full_name, branch, commit_sha)` |
| 错误码 | `INGEST_REPO_UNREACHABLE`、`INGEST_AUTH_FAILED`、`INGEST_BRANCH_NOT_FOUND`、`INGEST_CONCURRENCY_LIMIT`、`GIT_PROVIDER_UNSUPPORTED`、`PAYLOAD_TOO_LARGE` |

请求字段：

| 字段 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `provider` | enum `github`/`gitlab` | 是 | — | — |
| `repo_full_name` | string | 是 | — | `owner/repo`，≤200 字符 |
| `base_branch` | string | 是 | — | 1–255 字符 |
| `auth_type` | enum `app`/`deploy_key`/`pat` | 是 | — | `app` 仅 GitHub |
| `credential` | string | 条件 | — | `deploy_key` 为私钥 PEM、`pat` 为 token；`app` 不传；**只在 TLS 内传输，服务端加密后立即丢弃明文** |
| `reviews_root` | string | 否 | `wiki/复盘` | 1–200 字符，仅 `[^/]+` 段，禁止 `..` |
| `mode` | enum `managed`/`vault_writeback`/`separate` | 否 | `managed` | 决策 1D |
| `sync_interval_minutes` | int | 否 | 0（仅手动） | 0–1440；0 表示不自动 |
| `initial_sync` | bool | 否 | true | 是否立即入队首次全量导入 |

响应 `IngestJob`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | UUID | 任务 ID |
| `kind` | enum `git`/`zip`/`sync`/`resync` | — |
| `status` | enum `queued`/`running`/`succeeded`/`partial`/`failed`/`cancelled` | — |
| `progress` | object | `{ "total_files", "processed_files", "failed_files", "skipped_files" }` |
| `snapshot_id` | UUID/null | 成功后绑定 |
| `created_at` / `started_at` / `finished_at` | timestamp/null | — |

#### 3.3.2 `POST /ingest/zip`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `multipart/form-data`：`file`（必填，≤200 MB）；`mode`、`reviews_root` 可选同 §3.3.1 |
| 响应 202 | `{ "job": IngestJob }` |
| 幂等 | `Idempotency-Key` 必填（大文件重传常见）；业务键 `(workspace, sha256(zip))` 24 小时内复用任务 |
| 错误码 | `INGEST_ZIP_INVALID`、`INGEST_ZIP_LIMIT`、`PAYLOAD_TOO_LARGE`、`INGEST_CONCURRENCY_LIMIT` |

上传即返回，解析异步；进度靠 `GET /ingest/jobs/{id}`。ZIP 原件存 MinIO 私有桶，键 `ws/{workspace_id}/ingest/{job_id}/source.zip`，生命周期 30 天。

#### 3.3.3 `POST /ingest/sync`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "connection_id": UUID, "force_refresh": bool = false }` |
| 响应 202 | `{ "job": IngestJob }` |
| 语义 | 对已有 Git 连接拉取 `base_branch` 最新 commit，`git diff --name-status A..B` 增量；`force_refresh=true` 时忽略 `index_lag_sec` 守卫并全量重扫 |
| 幂等 | 同一 connection 的活跃 sync 任务唯一；重复请求返回现有 job（200 而非 202） |
| 错误码 | `INGEST_REPO_UNREACHABLE`、`INGEST_AUTH_FAILED`、`INGEST_SNAPSHOT_STALE`、`INGEST_CONCURRENCY_LIMIT`、`NOT_FOUND` |

#### 3.3.4 `GET /ingest/jobs`

| 项 | 值 |
|---|---|
| 权限 | 成员（只读） |
| 查询 | `status?`、`kind?`、`limit`、`cursor` |
| 响应 200 | `{ "items": IngestJob[], "next_cursor": string/null, "has_more": bool }` |
| 缓存 | `no-store`（进度实时） |

#### 3.3.5 `GET /ingest/jobs/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 响应 200 | `{ "job": IngestJob, "counters": {"created": int, "updated": int, "unchanged": int, "skipped": int, "failed": int}, "failed_items": IngestFailure[], "snapshot": SnapshotSummary/null }` |
| 错误码 | `INGEST_JOB_NOT_FOUND`、`NOT_FOUND` |

`IngestFailure`：`{ "path": string, "line": int/null, "reason_code": string, "message": string }`，`reason_code` ∈ `PARSE_ERROR`/`ENCODING_ERROR`/`FILE_TOO_LARGE`/`HASH_MISMATCH`/`UNSUPPORTED_EXT`/`PATH_OUTSIDE_ROOT`。最多返回前 200 条，其余用 `failed_count` 汇总。

`SnapshotSummary`：`{ "snapshot_id": UUID, "commit_sha": string/null, "doc_count": int, "as_of": timestamp, "manifest_digest": string }`。

#### 3.3.6 `POST /ingest/jobs/{id}/retry`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "only_failed": bool = true }` |
| 响应 202 | `{ "job": IngestJob }`（新 job，`kind=resync`，`parent_job_id` 指向原任务） |
| 语义 | 仅重跑失败文件；`only_failed=false` 时对同一快照全量重扫（仍走增量哈希） |
| 错误码 | `INGEST_JOB_NOT_RETRYABLE`（任务非 `failed`/`partial`）、`INGEST_JOB_NOT_FOUND`、`INGEST_CONCURRENCY_LIMIT` |

#### 3.3.7 `GET /ingest/sources`

| 项 | 值 |
|---|---|
| 权限 | 成员（凭据字段脱敏） |
| 响应 200 | `{ "git_connections": GitConnectionSummary[], "zip_imports": ZipImportSummary[] }` |
| 说明 | `GitConnectionSummary` 不含 `credential_ciphertext`，仅 `credential_status`（`valid`/`invalid`/`untested`） |

`GitConnectionSummary`：`{ "id", "provider", "repo_full_name", "base_branch", "mode", "reviews_root", "sync_interval_minutes", "last_sync_at", "last_sync_status", "credential_status" }`。

### 3.4 documents

#### 3.4.1 `GET /documents/tree`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `root?`（路径前缀，默认空 = 全树）、`depth?`（1–10，默认全展开）、`include_generated?`（默认 `false`，排除 `source_type=generated`） |
| 响应 200 | `DocumentTree` |
| 缓存 | `private, max-age=30`，ETag |
| 错误码 | `VALIDATION_ERROR`、`NOT_FOUND`（`root` 不存在） |

`DocumentTree`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `root` | string | 请求的根前缀 |
| `nodes` | TreeNode[] | 扁平化数组，前端按 `parent_path` 建树 |
| `total_documents` | int | 当前 workspace 可见文档数 |
| `truncated` | bool | 超过 5000 节点时为 true，需按 `root` 分段拉取 |

`TreeNode`：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `type` | enum `dir`/`file` | 否 | — |
| `path` | string | 否 | 相对 Vault 根，POSIX 分隔 |
| `parent_path` | string | 是 | 根节点为 `null` |
| `name` | string | 否 | 末段文件名或目录名 |
| `title` | string | 是 | `file` 时取 frontmatter `title` 或首个 H1 或文件名 |
| `document_id` | UUID | 是 | `file` 必填 |
| `size` | int | 是 | 字节；`file` 必填 |
| `mtime` | timestamp | 是 | **仅展示**，业务日期用 `occurred_on`（架构 §2.1 禁用 mtime 判定） |
| `occurred_on` | date | 是 | 业务日期 |
| `tags` | string[] | 否 | 空数组而非 null |
| `child_count` | int | 是 | `dir` 时必填 |
| `source_type` | enum `user`/`generated` | 否 | 生成物默认不返回 |

#### 3.4.2 `GET /documents/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `include?`（逗号分隔：`meta,content,links,chunks`，默认 `meta,content,links`） |
| 响应 200 | `DocumentDetail` |
| 缓存 | `private, max-age=300`，ETag = `content_hash` 前 16 位；`If-None-Match` 命中 304 |
| 错误码 | `NOT_FOUND`（含跨租户）、`SCHEMA_MIGRATION_FAILED`（frontmatter 解析异常时仍返回原文 + 警告） |

`DocumentDetail`：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | UUID | 否 | — |
| `path` | string | 否 | — |
| `title` | string | 否 | — |
| `content` | string | 是 | 原始 Markdown（`include` 含 `content` 时） |
| `content_html` | string | 是 | 服务端渲染 + sanitize 后的 HTML |
| `frontmatter` | object | 否 | 原样 JSON，无则 `{}` |
| `tags` | string[] | 否 | — |
| `links` | DocumentLink[] | 否 | `{ "target_path", "link_text", "resolved": bool, "target_document_id": UUID/null }` |
| `backlinks` | DocumentLink[] | 否 | 指向本文的链接（一跳） |
| `content_hash` | string | 否 | sha256 |
| `size` | int | 否 | — |
| `mtime` | timestamp | 否 | 展示用 |
| `occurred_on` | date | 是 | 业务日期 |
| `indexed_at` | timestamp | 否 | — |
| `source_type` | enum `user`/`generated` | 否 | — |
| `chunks` | ChunkSummary[] | 是 | `include` 含 `chunks` 时：`{ "chunk_id", "heading", "line_range", "token_count" }` |

渲染要求：`content_html` 必须过 Markdown sanitize（禁 `<script>`、事件属性、`javascript:` URL），图片经 `/documents/{id}/asset` 代理，链接外链加 `rel="noopener noreferrer"`。禁止前端直接 `dangerouslySetInnerHTML` 未净化内容。

#### 3.4.3 `GET /documents/{id}/content`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `format?` ∈ `markdown`(默认)/`html` |
| 响应 200 | `text/markdown` 或 `text/html`，非 JSON |
| 用途 | 前端“查看原文/复制”、下载 |
| 错误码 | `NOT_FOUND` |

#### 3.4.4 `GET /documents/{id}/meta`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 响应 200 | `{ "id", "path", "title", "tags", "size", "content_hash", "occurred_on", "mtime", "indexed_at", "source_type", "snapshot_id", "chunk_count", "link_count" }` |
| 用途 | 列表页轻量元数据，避免拉正文 |

#### 3.4.5 `GET /documents/{id}/asset`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `path`（相对 Vault 的资源路径，必填） |
| 响应 200 | 二进制流，`Content-Type` 按扩展名白名单（png/jpg/jpeg/gif/webp/svg），`X-Content-Type-Options: nosniff` |
| 校验 | 路径必须在该文档所属 Vault 的 manifest 内；`..`、绝对路径、非白名单扩展 → `404` |
| 错误码 | `NOT_FOUND` |

#### 3.4.6 `GET /documents/by-path`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `path`（必填，精确匹配） |
| 响应 200 | 同 `DocumentDetail`（默认 `include=meta,content`） |
| 错误码 | `NOT_FOUND` |

### 3.5 search

#### 3.5.1 `GET /search`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询参数 | 见下表 |
| 响应 200 | `{ "items": SearchResult[], "total_estimated": int, "degraded": bool, "degraded_reasons": string[], "took_ms": int }` |
| 缓存 | `no-store` |
| 错误码 | `SEARCH_EMPTY_QUERY`、`SEARCH_TOO_MANY_TERMS`、`VALIDATION_ERROR`、`RATE_LIMITED`、`EMBEDDING_PROFILE_MISMATCH` |

查询参数：

| 参数 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `q` | string | 是 | — | 1–500 字符，去空白后非空 |
| `top_k` | int | 否 | 20 | 1–50 |
| `mode` | enum `hybrid`/`lexical`/`vector` | 否 | `hybrid` | `vector` 在 embedding 不可用时降级 |
| `source_type` | enum `both`/`document`/`review` | 否 | `both` | FR-F4 |
| `path_prefix` | string | 否 | — | ≤200 字符，禁止 `..` |
| `tags` | string[] | 否 | — | 逗号分隔，≤10 个，OR 语义 |
| `date_from` / `date_to` | date | 否 | — | 按 `occurred_on`；闭区间；`date_from <= date_to` |
| `extensions` | string[] | 否 | — | 白名单 `md`、`markdown`；其他 → `VALIDATION_ERROR` |
| `include_snippet_html` | bool | 否 | `true` | 工具调用恒为 `false` |

`SearchResult` 字段严格采用 §5：`source_type`、`document_id`、`chunk_id`、`review_id`、`path`、`title`、`heading`、`snippet`、`snippet_html`、`score`、`matched_by`、`rank_lexical`、`rank_vector`、`line_range`、`occurred_on`。补充约束：`source_type=document` 时 `document_id`/`chunk_id` 必填且 `review_id=null`；`source_type=review` 时 `review_id` 必填且 `document_id`/`chunk_id=null`。`snippet_html` 已先 HTML 转义再插 `<mark>`。

降级：向量路超过 250ms 或 embedding 不可用 → `degraded=true`、`degraded_reasons=["vector_timeout"]` 或 `["embedding_unavailable"]`，HTTP 仍 200，`matched_by` 只会是 `lexical`。

#### 3.5.2 `GET /search/suggest`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `q`（1–100 字符）、`limit`（1–10，默认 5） |
| 响应 200 | `{ "items": [{"text": string, "path": string, "document_id": UUID}] }` |
| 用途 | 标题/路径前缀补全；只走词法路，不触发 embedding |
| 错误码 | `VALIDATION_ERROR`、`RATE_LIMITED` |

### 3.6 chat

chat 走架构 §4.6 精简分支：`created → retrieving → drafting → verifying → succeeded/failed/cancelled`，不进复盘门禁。

#### 3.6.1 `GET /conversations`

| 项 | 值 |
|---|---|
| 权限 | 成员（仅自己的会话） |
| 查询 | `limit`、`cursor`、`q?`（标题模糊，≤100 字符） |
| 响应 200 | `{ "items": ConversationSummary[], "next_cursor", "has_more" }` |
| 排序 | `updated_at desc, id desc` |

`ConversationSummary`：`{ "id", "title", "created_at", "updated_at", "message_count", "last_message_preview" }`（预览截断 120 字符，不含引用正文）。

#### 3.6.2 `POST /conversations`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 请求 | `{ "title"?: string }`（1–100 字符；省略则服务端用首条提问生成，首条前为 `新对话`） |
| 响应 201 | `ConversationSummary` |
| 幂等 | `Idempotency-Key` 推荐（防双击创建空会话） |
| 错误码 | `VALIDATION_ERROR`、`QUOTA_EXCEEDED`（每 workspace 会话上限 5000） |

#### 3.6.3 `PATCH /conversations/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员（会话属主） |
| 请求 | `{ "title": string }` |
| 响应 200 | `ConversationSummary` |
| 条件 | `If-Match` 可选 |
| 错误码 | `NOT_FOUND`、`PRECONDITION_FAILED`、`VALIDATION_ERROR` |

#### 3.6.4 `DELETE /conversations/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员（会话属主） |
| 响应 204 | 无体 |
| 语义 | 软删除 `deleted_at`；活跃 chat run 先 `CANCELLED`；关联 `messages` 级联不可见，30 天后物理清理 |
| 错误码 | `NOT_FOUND` |

#### 3.6.5 `GET /conversations/{id}/messages`

| 项 | 值 |
|---|---|
| 权限 | 成员（会话属主） |
| 查询 | `limit`（1–100，默认 30）、`cursor`、`before?`（timestamp，取更早消息） |
| 响应 200 | `{ "items": Message[], "next_cursor", "has_more" }` |
| 排序 | `created_at asc`（对话阅读顺序）；分页仍用 cursor |

`Message`：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | UUID | 否 | — |
| `conversation_id` | UUID | 否 | — |
| `role` | enum `user`/`assistant`/`system` | 否 | `system` 不返回给前端 |
| `content` | string | 否 | Markdown 正文；`status=partial` 时为已生成部分 |
| `status` | enum `drafting`/`complete`/`partial`/`cancelled` | 否 | 与 `messages.status` 一致 |
| `references` | Reference[] | 否 | 空数组而非 null |
| `tool_events` | ToolEvent[] | 否 | 工具轨迹摘要（FR-D5） |
| `usage` | object/null | 是 | `{ "prompt_tokens", "completion_tokens", "model", "latency_ms" }` |
| `run_id` | UUID/null | 是 | 产生该消息的 run |
| `created_at` | timestamp | 否 | — |

`Reference`：`{ "index": int, "document_id": UUID, "chunk_id": UUID, "path": string, "title": string, "heading": string/null, "line_range": [int,int]/null, "snippet": string }`。`index` 与正文 `[n]` 标注一致，从 1 开始连续；引用闭包失败时该条不出现，正文改为哨兵。

`ToolEvent`：`{ "seq": int, "tool_name": string, "args_digest": string, "args_summary": string, "status": enum `ok`/`error`/`timeout`/`skipped`, "latency_ms": int }`。`args_summary` 是脱敏摘要（如 `query="缓存穿透", top_k=10`），不回显完整参数与证据正文。

#### 3.6.6 `POST /conversations/{id}/turns`

| 项 | 值 |
|---|---|
| 权限 | 成员（会话属主） |
| 请求 | `ChatTurnRequest`（见下表） |
| 响应 202 | `{ "run_id": UUID, "message_id": UUID, "stream": {"events_url": string, "epoch": int, "after": int} }` |
| 幂等 | `client_turn_id` 为业务幂等键；`Idempotency-Key` 可叠加 |
| 并发 | 同会话串行：先取消旧 run，再建新 run（§1.8） |
| 错误码 | `ACTIVE_RUN_EXISTS`（取消失败兜底）、`NOT_FOUND`、`VALIDATION_ERROR`、`RATE_LIMITED`、`BUDGET_EXCEEDED`、`QUOTA_EXCEEDED`、`LLM_UNAVAILABLE`、`PROVIDER_KEY_MISSING` |

请求字段（`ChatTurnRequest`，字段名与架构 §5 一致；`run_id`/`workspace_id`/`conversation_id` 由服务端注入，不在请求体）：

| 字段 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `client_turn_id` | UUID | 是 | — | 客户端每次“发送”生成，重试复用 |
| `message` | string | 是 | — | 1–4000 字符，去空白后非空 |
| `options` | RunOptions | 否 | 服务端默认 | 见架构 §5 `RunOptions`；chat 只取 `retrieval_top_k`/`context_window_tokens`/`web_search_enabled`/`model_profile`/`budget` |

语义：返回 202 后前端立即建立 SSE；`message_id` 指向已创建的 assistant 占位消息（`status=drafting`）。若同 `client_turn_id` 已存在 run，返回原 `run_id` 与 `Idempotent-Replayed: true`。

#### 3.6.7 `POST /runs/{id}/cancel`

| 项 | 值 |
|---|---|
| 权限 | 成员（run 属主或 workspace owner） |
| 请求 | 无体 |
| 响应 202 | `{ "run_id": UUID, "state": "cancelled" }` |
| 语义 | chat 立即取消（下一事件边界生效）；review 仅在 `created`/`snapshotting`/`planning` 可取消，之后需走 reject |
| 错误码 | `NOT_FOUND`、`CONFLICT`（已终态）、`FORBIDDEN`（review 已进入不可取消阶段） |

### 3.7 review

#### 3.7.1 `POST /reviews`

| 项 | 值 |
|---|---|
| 权限 | `owner`（手动触发）；`api` 触发需 workspace API key（Phase 2） |
| 请求 | `{ "kind": enum, "period_key": string, "timezone"?: string, "trigger"?: enum, "options"?: RunOptions, "allow_empty"?: bool }` |
| 响应 201 | `{ "run_id": UUID, "review_id": UUID/null, "state": "created", "existing_run_id": null }` |
| 响应 200（已成功过） | 新建 v(n+1)：`{ "run_id", "review_id", "state": "created", "version": int }` |
| 幂等 | `Idempotency-Key` 必填；业务键 `(workspace, kind, period_key, 活跃态)` |
| 错误码 | `ACTIVE_RUN_EXISTS`、`REVIEW_PERIOD_INVALID`、`REVIEW_PERIOD_TOO_OLD`、`REVIEW_NO_SOURCE_DATA`、`BUDGET_EXCEEDED`、`QUOTA_EXCEEDED`、`FORBIDDEN`、`VALIDATION_ERROR`、`INGEST_SNAPSHOT_STALE` |

请求字段约束：

| 字段 | 类型 | 必填 | 默认 | 约束 |
|---|---|---|---|---|
| `kind` | enum `daily`/`weekly`/`yearly` | 是 | — | — |
| `period_key` | string | 是 | — | 与 `kind` 严格匹配格式；不得为未来周期（按 workspace 时区） |
| `timezone` | string | 否 | workspace 设置 | IANA；影响 period 边界计算 |
| `trigger` | enum `manual`/`schedule`/`api` | 否 | `manual` | `schedule` 仅 dispatcher 可传 |
| `options` | RunOptions | 否 | 默认 | `max_revisions` 不得高于 `REVISION_LIMIT[kind]`；超出 → `VALIDATION_ERROR` |
| `allow_empty` | bool | 否 | `false` | 周期内无素材时需显式 true 才生成全哨兵空日复盘 |

行为：准入（幂等 + `pg_try_advisory_xact_lock`）→ 检查复盘并发 ≤2 → 若已有 `succeeded` 则分配 `version+1` → 写 `agent_runs(state=created)` → 返回。**不在请求内同步执行**。

`allow_empty=false` 且无素材时返回 `REVIEW_NO_SOURCE_DATA`（`details.period_key`、`details.reason="no_documents_no_tasks_no_journal"`）。`allow_empty=true` 时生成 7 章全哨兵，合法交付（反目标 A2）。

#### 3.7.2 `GET /reviews`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `kind?`、`from?`、`to?`、`status?`、`limit`、`cursor` |
| 响应 200 | `{ "items": ReviewSummary[], "next_cursor", "has_more" }` |
| 排序 | `period_key desc, version desc` |

`ReviewSummary`：`{ "id", "kind", "period_key", "title", "version", "status", "generated_at", "path", "degraded", "delivery_status" }`。`status` ∈ `generating`/`needs_human`/`approved`/`delivered`/`failed`/`security_abort`/`superseded`。`delivery_status` ∈ `none`/`pending`/`pr_open`/`merged`/`noop`/`blocked`/`ui_diff`。

#### 3.7.3 `GET /reviews/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `version?`（默认 current） |
| 响应 200 | `ReviewDetail` |
| 错误码 | `NOT_FOUND`、`REVIEW_VERSION_NOT_FOUND` |

`ReviewDetail`：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | UUID | 否 | — |
| `kind` / `period_key` | enum/string | 否 | — |
| `version` | int | 否 | 当前读取版本 |
| `current_version` | int | 否 | 最新版本号 |
| `title` | string | 否 | — |
| `content_md` | string | 否 | 渲染前 Markdown |
| `content_html` | string | 否 | 已 sanitize |
| `sections` | ReviewSection[] | 否 | `{ "section_key", "ordinal", "content_md", "structured" }`，顺序即 `ordinal` |
| `citations` | ReviewCitation[] | 否 | `{ "claim_id", "evidence_id", "source_path", "source_date", "line_range", "quote" }` |
| `entries` | ReviewEntry[] | 否 | 错误/方法论/行动条目，`{ "section_key", "item_type", "canonical_key", "fields", "source_path", "line_range", "confidence" }` |
| `verdict` | ReviewVerdict/null | 是 | 门禁结果 |
| `model` | string | 否 | 生成模型标识 |
| `snapshot_id` | UUID | 否 | — |
| `generated_at` | timestamp | 否 | — |
| `degraded` | bool | 否 | — |
| `degraded_reasons` | string[] | 否 | `DegradedReason` 枚举值 |
| `git_path` | string | 是 | 如 `wiki/复盘/每日复盘/2026/2026-10-27.md` |
| `versions` | VersionRef[] | 否 | `{ "version", "content_hash", "git_sha", "generated_at", "is_current" }` |
| `superseded_by` | int/null | 是 | 被哪个版本取代 |

#### 3.7.4 `GET /reviews/{id}/versions/{version}`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 响应 200 | 同 `ReviewDetail`（该 version 的 sections/entries/citations 一并按 version 读取） |
| 错误码 | `REVIEW_VERSION_NOT_FOUND` |

#### 3.7.5 `POST /reviews/{id}/rollback`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "version": int, "confirm": true }` |
| 响应 202 | `{ "run_id": UUID, "target_version": int }` |
| 语义 | 按 version 指针恢复 `content_md` 并设为 current，sections/entries/citations 按该 version 读取；生成一次交付以同步 Git（若已配置）。不删除任何旧行（架构 §6 版本隔离规则） |
| 错误码 | `REVIEW_VERSION_NOT_FOUND`、`CONFLICT`（已有活跃 run）、`VALIDATION_ERROR`（`confirm` 非 true） |

#### 3.7.6 `GET /runs/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员（chat run 仅属主与 owner；review run 全体成员） |
| 响应 200 | `RunDetail` |
| 缓存 | `no-store` |

`RunDetail`：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `run_id` | UUID | 否 | — |
| `kind` | enum `chat`/`daily`/`weekly`/`yearly` | 否 | — |
| `state` | enum | 否 | 见 §7.1 状态机；`chat` 用架构 §4.6 子集 |
| `period_key` | string | 是 | chat 为 null |
| `conversation_id` | UUID | 是 | chat 必填 |
| `version` | int | 是 | 复盘版本 |
| `attempt_id` | UUID | 否 | 当前尝试 |
| `retry_from_state` | enum/null | 是 | — |
| `snapshot_id` | UUID | 是 | — |
| `lease_expires_at` | timestamp | 是 | — |
| `budget` | BudgetUsage | 否 | 见 §4.4 |
| `degraded` / `degraded_reasons` | bool/string[] | 否 | — |
| `steps` | RunStep[] | 否 | 最近 100 条：`{ "seq", "role", "tool_name", "status", "model", "tokens", "latency_ms" }` |
| `created_at` / `started_at` / `finished_at` | timestamp/null | 是 | — |
| `error` | object/null | 是 | `{ "code", "message", "state" }` |

#### 3.7.7 `POST /runs/{id}/approve`

| 项 | 值 |
|---|---|
| 权限 | 该 workspace 的 `owner`（或 `admin`）；`platform_admin` 代操作需二次确认 + 审计（架构 §4.5） |
| 请求 | `{ "decision_note"?: string, "edited_content_md"?: string, "confirm"?: bool }` |
| 响应 202 | `{ "run_id", "state": "delivering", "delivery_id": UUID/null }` |
| 前置 | run 必须处于 `human_gate`；`TENANT_LEAK`/`FABRICATION` 的 run 不可审批（返回 `SECURITY_ABORT`） |
| 错误码 | `CONFLICT`（状态不符）、`REVIEW_ALREADY_DECIDED`、`SECURITY_ABORT`、`REVIEW_GATE_FAILED`（编辑后仍含 blocking）、`FORBIDDEN` |
| 审计 | `review.approved`，含 `actor`、`run_id`、`note`、`edited`（bool） |

若带 `edited_content_md`，服务端重新跑确定性门禁 G0–G9；不通过返回 `REVIEW_GATE_FAILED` 且不推进状态（编辑后通过 = 新 draft version + 重新评审，非绕过）。

#### 3.7.8 `POST /runs/{id}/reject`

| 项 | 值 |
|---|---|
| 权限 | 同 approve |
| 请求 | `{ "reason_code": enum, "note"?: string }` |
| 响应 202 | `{ "run_id", "state": "cancelled" }` |
| `reason_code` | `CONTENT_WRONG`/`CITATIONS_WRONG`/`TONE`/`NOT_NEEDED`/`OTHER` |
| 错误码 | `CONFLICT`、`REVIEW_ALREADY_DECIDED`、`VALIDATION_ERROR` |
| 审计 | `review.rejected` |

#### 3.7.9 `GET /runs/{id}/events`（SSE）

见 §6 完整契约。

#### 3.7.10 `GET /deliveries`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `review_id?`、`status?`、`limit`、`cursor` |
| 响应 200 | `{ "items": DeliveryRecord[], "next_cursor", "has_more" }` |

`DeliveryRecord`：

| 字段 | 类型 | 可空 | 说明 |
|---|---|---|---|
| `id` | UUID | 否 | 幂等 delivery id，格式见 §7.4 |
| `review_id` | UUID | 否 | — |
| `version` | int | 否 | — |
| `target` | enum `git`/`object_store`/`ui_diff` | 否 | 交付目标 |
| `provider` | enum `github`/`gitlab`/null | 是 | `target=git` 必填 |
| `branch` | string | 是 | 如 `review/daily/2026-10-27` |
| `commit_sha` | string | 是 | — |
| `pr_number` | int | 是 | — |
| `pr_url` | string | 是 | — |
| `status` | enum `pending`/`committed`/`pr_open`/`merged`/`closed`/`noop`/`blocked`/`failed` | 否 | — |
| `merged_commit` | string | 是 | — |
| `degraded` | bool | 否 | Git 不可用时降级为 `ui_diff` |
| `created_at` / `updated_at` | timestamp | 否 | — |

#### 3.7.11 `GET /deliveries/{id}`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 响应 200 | `{ "delivery": DeliveryRecord, "manifest": DeliveryManifest, "diff_summary": {"files_added": int, "files_modified": int, "files_unchanged": int} }` |
| 错误码 | `DELIVERY_NOT_FOUND` |

#### 3.7.12 `POST /deliveries/{id}/retry`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | 无体（复用原 manifest 与 branch） |
| 响应 202 | `{ "delivery_id": UUID, "status": "pending" }` |
| 幂等 | 同 `(workspace, review_id, version, target)` 已有活跃交付时返回现有记录 |
| 错误码 | `DELIVERY_CREDENTIAL_INVALID`、`DELIVERY_PR_CONFLICT`、`DELIVERY_GIT_UNAVAILABLE`、`CONFLICT` |
| 语义 | PR API 失败重试 3 次保留分支；非快进 rebase 2 次；绝不 force push（反目标 C4） |

#### 3.7.13 专题确认端点（决策 4B）

| 方法 | 路径 | 权限 | 请求 | 响应 | 错误码 |
|---|---|---|---|---|---|
| `GET` | `/topics` | 成员 | — | `{ "items": [{"topic","title","path","open_count","last_updated"}] }` | — |
| `GET` | `/topics/{topic}/items` | 成员 | `status?`（`open`/`resolved`/`stale`/`pending`） | `{ "items": TopicItem[], "next_cursor" }` | `NOT_FOUND` |
| `POST` | `/topics/{topic}/items/{item_id}/confirm` | `owner` | `{ "decision": "accept"/"drop", "edited_fields"?: object }` | 200 `TopicItem` | `NOT_FOUND`、`CONFLICT`（已确认） |
| `POST` | `/topics/{topic}/items/{item_id}/resolve` | `owner` | `{ "note"?: string }` | 200 `TopicItem`（`status=resolved`） | `NOT_FOUND`、`CONFLICT` |

`topic` ∈ `错误清单`/`正确方法论`/`行动清单`（URL 需 percent-encode）。`TopicItem`：`{ "item_id", "canonical_key", "title", "status", "first_seen", "last_seen", "occurrence_count", "occurrences": [{"source_date","source_path","line_range","quote","evidence_id"}] }`。规则：`open → resolved` 只能由显式 resolve 或人工确认触发；未被提及不自动 resolved（架构 §16.5）。

### 3.8 dashboard

所有 dashboard 端点只读、不调 LLM，60s 缓存（FR-F1~F4）。

#### 3.8.1 `GET /dashboard/latest`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `timezone?`（默认 workspace 设置） |
| 响应 200 | `{ "daily": ReviewSummary/null, "weekly": ReviewSummary/null, "yearly": ReviewSummary/null, "generated_at": timestamp, "completion_rate": float, "open_tasks": int }` |
| 说明 | 各 kind 取 `period_key` 最大且 `status` 为 `delivered` 或 `approved` 的版本；`completion_rate` 与 `open_tasks` 与 `/dashboard/stats` 同源、同 60s 缓存，保证 FR-F1 一次取齐 |

#### 3.8.2 `GET /dashboard/tasks`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `status?`（`open`/`done`/`carried`/`dropped`，默认 `open,carried`）、`limit`（1–200，默认 50）、`cursor` |
| 响应 200 | `{ "items": TaskItem[], "next_cursor", "has_more", "counts": {"open": int, "carried": int, "done_today": int} }` |
| 排序 | `first_seen asc, id asc`（最久未完成在前） |

`TaskItem`：`{ "id", "text", "status", "first_seen", "last_seen", "days_open", "done_date", "source_path", "line_number", "occurrence_count" }`。`days_open = (today_local - first_seen).days`，`today_local` 按 workspace 时区。

#### 3.8.3 `GET /dashboard/calendar`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `year`（必填，2000–2100）、`month?`（1–12；省略返回全年月度聚合） |
| 响应 200 | `{ "year": int, "months": MonthCell[], "days": DayCell[] }` |
| 说明 | 月度视图返回 `days`；年度视图返回 `months` 且 `days` 为空数组 |

`DayCell`：`{ "date", "has_daily": bool, "daily_status": enum/null, "degraded": bool, "open_tasks": int }`。`MonthCell`：`{ "month", "has_weekly": bool, "weekly_period_key": string/null, "completion_rate": float/null }`。

#### 3.8.4 `GET /dashboard/stats`

| 项 | 值 |
|---|---|
| 权限 | 成员 |
| 查询 | `range?` ∈ `7d`/`30d`/`90d`/`year`（默认 `30d`） |
| 响应 200 | `{ "range", "completion_rate": float, "tasks": {"completed", "total", "carried_over"}, "reviews": {"daily", "weekly", "yearly"}, "no_evidence_ratio": float, "degraded_rate": float, "tokens": {"prompt", "completion"} }` |

统计口径：`completion_rate = completed / total`，`total` 为区间内出现过的任务数；与周报 `ledger_recalc` 同源（FR-E7）。`no_evidence_ratio` 与 `degraded_rate` 来自 `usage_events`，用于质量看板（架构 §16.9）。

### 3.9 settings

#### 3.9.1 `GET /settings/llm`

| 项 | 值 |
|---|---|
| 权限 | `owner`（凭据状态可见，明文永不可见） |
| 响应 200 | `provider`（`deepseek`/`openai_compatible`/null）、`base_url`（string/null）、`model`（string/null）、`model_profile`（string）、`credential_status`（`valid`/`invalid`/`untested`/`missing`）、`last_tested_at`（timestamp/null）、`supports_json_mode`（bool） |

#### 3.9.2 `PUT /settings/llm`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "provider", "base_url", "model", "api_key"?: string, "test_before_save"?: bool = true }` |
| 响应 200 | 同 GET |
| 语义 | `api_key` 省略 = 保持现有；提供则信封加密后存 `provider_credentials`（`key_ciphertext`，AAD 为 `workspace_id` 加 provider）；**测试失败不保存**（FR-G1） |
| 错误码 | `PROVIDER_KEY_MISSING`、`VALIDATION_ERROR`（`base_url` 必须 https 或内网白名单）、`LLM_UNAVAILABLE`（测试失败）、`SECURITY_ABORT`（检测到凭据回显） |
| 审计 | `settings.llm.updated`（不记录 key） |

`base_url` 约束：仅 `https://`；允许 `http://` 仅当主机在 `ALLOWED_PRIVATE_PROVIDER_HOSTS` 环境变量内（本地 Ollama 场景）。禁止 `file://`、`ext::`、`gopher://`（SSRF）。

#### 3.9.3 `POST /settings/llm/test`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "provider", "base_url", "model", "api_key"?: string }` |
| 响应 200 | `{ "ok": bool, "latency_ms": int, "model_echo": string/null, "error": {"code","message"}/null }` |
| 说明 | 不落库；超时 15s；错误信息脱敏（不回显 key） |
| 错误码 | `LLM_UNAVAILABLE`、`LLM_RATE_LIMITED`、`VALIDATION_ERROR` |

#### 3.9.4 `GET /settings/embedding`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 响应 200 | `{ "active_profile": EmbeddingProfile/null, "profiles": EmbeddingProfile[], "credential_status": enum, "last_tested_at": timestamp/null }` |

`EmbeddingProfile`：`{ "id", "provider", "model", "dimension", "index_name", "status" ∈ active/building/retired, "created_at", "coverage": float }`。`coverage` 为已 embedding chunk 占比（building 阶段用于校验）。

#### 3.9.5 `POST /settings/embedding/profiles`

| 项 | 值 |
|---|---|
| 权限 | `owner`（在线换维在 MVP 为运维任务，此端点创建 `building` profile 并触发全量 re-embed） |
| 请求 | `{ "provider", "model", "dimension", "api_key"?: string, "confirm": true }` |
| 响应 202 | `{ "profile": EmbeddingProfile, "job_id": UUID }` |
| 语义 | 决策 2B 蓝绿：新建 `document_chunks_v{n}` + HNSW → 全量 re-embed → 覆盖率 ≥0.995 后原子切换 `workspace_settings.embedding_profile_id` → 旧表延迟 7 天删除 |
| 错误码 | `CONFLICT`（已有 `building` profile）、`VALIDATION_ERROR`（dimension 不在 256–3072）、`EMBEDDING_UNAVAILABLE`、`PAYLOAD_TOO_LARGE` |
| 说明 | `activate` 由后台任务自动完成；前端只展示进度。禁止原地改维 |

#### 3.9.6 `POST /settings/embedding/test`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "provider", "model", "api_key"?: string }` |
| 响应 200 | `{ "ok": bool, "dimension_observed": int/null, "latency_ms": int, "error": object/null }` |
| 错误码 | `EMBEDDING_UNAVAILABLE`、`VALIDATION_ERROR` |

#### 3.9.7 `GET /settings/schedule` / `PUT /settings/schedule`

| 项 | 值 |
|---|---|
| 权限 | 成员读 / `owner` 写 |
| 请求（PUT） | `{ "timezone": string, "daily_at": "HH:MM", "weekly_at": "HH:MM", "yearly_at": "HH:MM", "catchup_days": int, "enabled": {"daily","weekly","yearly"} }` |
| 响应 200 | `{ "timezone", "daily_at", "weekly_at", "yearly_at", "catchup_days", "enabled", "next_run_at": {"daily","weekly","yearly"} }` |
| 默认 | `Asia/Shanghai`；daily `01:00`、weekly 周一 `01:30`、yearly 1/1 `02:00`；`catchup_days=7`（0–30） |
| 语义 | 写 `workspace_settings` + `workspace_schedules`；重算 `next_run_at`（DST 不存在时刻顺延、重复时刻取较早 offset） |
| 错误码 | `VALIDATION_ERROR`（时间格式/时区非法）、`FORBIDDEN` |
| 审计 | `settings.schedule.updated` |

#### 3.9.8 `GET /settings/git` / `PUT /settings/git`

| 项 | 值 |
|---|---|
| 权限 | 成员读 / `owner` 写 |
| 请求（PUT） | `{ "provider", "repo_full_name", "base_branch", "auth_type", "credential"?, "reviews_root"?, "mode"?, "sync_interval_minutes"?, "auto_merge_daily"? }` |
| 响应 200 | `GitConnectionSummary` |
| 语义 | 连接测试须校验 `contents:write`，缺失即拒绝启用（架构 §8.5）；`auto_merge_daily=true` 仅 daily 生效且 Gate 全通过（决策 3B） |
| 错误码 | `INGEST_AUTH_FAILED`、`INGEST_REPO_UNREACHABLE`、`INGEST_BRANCH_NOT_FOUND`、`GIT_PROVIDER_UNSUPPORTED`、`FORBIDDEN`、`VALIDATION_ERROR`（`reviews_root` 含 `..`） |
| 审计 | `settings.git.updated` |

#### 3.9.9 `DELETE /settings/git`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 响应 204 | 删除 `git_connections` 与凭据；已交付的 `delivery_records` 保留；后续交付降级为 `object_store` |
| 错误码 | `NOT_FOUND` |
| 审计 | `settings.git.removed` |

#### 3.9.10 `GET /settings/usage`

| 项 | 值 |
|---|---|
| 权限 | 成员（本人用量）/ `owner`（全 workspace） |
| 查询 | `from`、`to`（必填，≤366 天）、`group_by?` ∈ `day`/`kind`/`model`（默认 `day`） |
| 响应 200 | `{ "totals": {"prompt_tokens","completion_tokens","calls","review_runs","cost"}, "series": [{"bucket","prompt_tokens","completion_tokens","calls"}], "budget": {"daily": BudgetUsage, "monthly": BudgetUsage} }` |
| 说明 | 来自 `usage_events`；`cost` 为估算值，币种 `USD`，保留 4 位小数 |
| 错误码 | `VALIDATION_ERROR`、`FORBIDDEN` |

#### 3.9.11 `GET /settings/retrieval` / `PUT /settings/retrieval`

| 项 | 值 |
|---|---|
| 权限 | 成员读 / `owner` 写 |
| 请求（PUT） | `{ "retrieval_top_k": int, "context_window_tokens": int, "web_search_enabled": bool }` |
| 响应 200 | 同请求 + `"vector_enabled": bool` |
| 约束 | `retrieval_top_k` 1–50；`context_window_tokens` 8000–128000；`web_search_enabled` 首版必须 false，传 true → `NOT_IMPLEMENTED`（Phase 2） |

### 3.10 admin（`platform_admin` 专用）

所有 admin 端点双重校验：`users` 角色 `platform_admin` + 会话 `token_version` 有效；每次操作写 `audit_logs`（`actor` 为管理员）。管理员**不进入**用户 Workspace 业务数据面：不提供读取笔记正文、会话内容、复盘正文的端点。

| 方法 | 路径 | 权限 | 请求 | 响应 | 错误码 |
|---|---|---|---|---|---|
| `GET` | `/admin/users` | platform_admin | `q?`、`status?`、`limit`、`cursor` | `{ "items": AdminUser[], "next_cursor" }` | `ADMIN_REQUIRED` |
| `GET` | `/admin/users/{id}` | platform_admin | — | `AdminUser`（仅元数据，无业务内容） | `ADMIN_USER_NOT_FOUND` |
| `POST` | `/admin/users/{id}/disable` | platform_admin | `{ "reason": string }` | 200 `AdminUser`（`status=disabled`） | `ADMIN_USER_NOT_FOUND`、`ADMIN_USER_DISABLED`、`CONFLICT`（禁用自己） |
| `POST` | `/admin/users/{id}/enable` | platform_admin | 无体 | 200 `AdminUser` | `ADMIN_USER_NOT_FOUND`、`CONFLICT` |
| `GET` | `/admin/workspaces` | platform_admin | `q?`、`limit`、`cursor` | `{ "items": AdminWorkspace[], "next_cursor" }` | `ADMIN_REQUIRED` |
| `GET` | `/admin/workspaces/{id}` | platform_admin | — | `AdminWorkspace`（含 counts 与 usage 聚合，不含正文） | `NOT_FOUND` |
| `POST` | `/admin/invite-codes` | platform_admin | `{ "count": int (1–50), "expires_in_days": int (1–365), "note"?: string }` | 201 `{ "items": [{"code","expires_at"}] }` | `ADMIN_INVITE_LIMIT`、`VALIDATION_ERROR` |
| `GET` | `/admin/invite-codes` | platform_admin | `status?`（`unused`/`used`/`expired`） | `{ "items": InviteCode[], "next_cursor" }` | — |
| `DELETE` | `/admin/invite-codes/{code}` | platform_admin | 无体 | 204（未使用才可撤销） | `CONFLICT`（已使用） |
| `GET` | `/admin/audit-logs` | platform_admin | `workspace_id?`（仅管理员可传）、`action?`、`from`、`to`、`limit`、`cursor` | `{ "items": AuditLog[], "next_cursor" }` | `VALIDATION_ERROR` |
| `GET` | `/admin/system-config` | platform_admin | — | `{ "items": [{"key","value","locked","source"}] }`（`locked` 为环境变量锁定） | — |
| `PUT` | `/admin/system-config` | platform_admin | `{ "key": string, "value": any }` | 200 单项 | `ADMIN_SYSTEM_CONFIG_LOCKED`、`VALIDATION_ERROR` |
| `GET` | `/admin/usage` | platform_admin | `from`、`to`、`group_by?` | 同 §3.9.10 聚合 + 跨 workspace 汇总 | `VALIDATION_ERROR` |

`AdminUser`：`{ "id", "email", "display_name", "status", "created_at", "workspace_count", "last_login_at" }`。`AdminWorkspace`：`{ "id", "name", "slug", "owner_id", "plan", "created_at", "counts": {"documents","reviews","tokens_month"}, "deletion_state": enum/null }`。

约束：`platform_admin` 禁用用户时，该用户所有会话 `revoked_at` 置位；不允许禁用最后一个 `platform_admin`。

### 3.11 exports 与 delete

#### 3.11.1 `POST /exports`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 请求 | `{ "scope": enum `workspace`/`account`, "include": ["documents","reviews","conversations","settings","usage"] , "format": "zip" }` |
| 响应 202 | `{ "export_id": UUID, "status": "queued", "expires_at": timestamp }` |
| 幂等 | `Idempotency-Key` 必填；同 scope 活跃任务唯一 |
| 错误码 | `EXPORT_LIMIT`、`FORBIDDEN`、`VALIDATION_ERROR`、`QUOTA_EXCEEDED`（并发导出上限 1） |

#### 3.11.2 `GET /exports/{id}`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 响应 200 | `{ "id", "scope", "status" ∈ queued/running/ready/failed/expired, "progress": {"files", "bytes"}, "artifact_bytes": int/null, "created_at", "expires_at", "error": object/null }` |
| 错误码 | `NOT_FOUND` |

#### 3.11.3 `GET /exports/{id}/download`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 响应 302 | 跳转 MinIO 预签名 URL（TTL 10 分钟） |
| 响应 200（直传模式） | `application/zip`，`Content-Disposition: attachment; filename="mylife-{slug}-{date}.zip"` |
| 错误码 | `EXPORT_NOT_READY`、`EXPORT_EXPIRED` |

导出包结构：`documents/`（原 Markdown 与附件）、`reviews/`（按 `reviews_root` 结构）、`conversations/*.md`、`settings.json`（脱敏，无凭据）、`usage.csv`、`manifest.json`（含 `workspace_id`、导出时间、文件哈希）。

#### 3.11.4 `POST /deletions`

| 项 | 值 |
|---|---|
| 权限 | `owner`（删除账号需本人 `platform_admin` 不可代办） |
| 请求 | `{ "scope": enum `workspace`/`account`, "confirm_text": string, "reason"?: string }` |
| 响应 201 | `{ "deletion_id": UUID, "state": "cooling", "purge_at": timestamp, "cancellable_until": timestamp }` |
| 校验 | `confirm_text` 必须精确等于 workspace 名或 `DELETE MY ACCOUNT`；否则 `VALIDATION_ERROR` |
| 语义 | 决策 7A：30 天冷却；立即停用所有写操作与定时任务；生成导出建议 |
| 错误码 | `DELETE_ALREADY_REQUESTED`、`DELETE_IN_COOLING`、`FORBIDDEN`、`VALIDATION_ERROR`、`CONFLICT`（有活跃 run 需先取消） |
| 审计 | `deletion.requested` |

#### 3.11.5 `GET /deletions/current`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 响应 200 | `{ "deletion_id", "scope", "state" ∈ requested/cooling/purged, "purge_at", "requested_at", "requested_by", "can_cancel": bool }` 或 `null` |

#### 3.11.6 `POST /deletions/{id}/cancel`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 响应 200 | `{ "state": "cancelled" }` |
| 语义 | 仅 `cooling` 可撤销；恢复写操作与调度 |
| 错误码 | `DELETE_NOT_CANCELLABLE`、`NOT_FOUND` |
| 审计 | `deletion.cancelled` |

#### 3.11.7 删除告知（不可删除项）

删除确认页与 API 响应 `details` 必须包含：

```json
{
  "will_delete": [
    "documents", "document_chunks", "document_links", "tasks", "task_occurrences",
    "reviews", "review_versions", "review_sections", "review_entries",
    "review_citations", "review_search", "topic_items", "topic_occurrences",
    "conversations", "messages", "agent_runs", "run_steps", "run_events",
    "evidence_items", "source_snapshots", "snapshot_documents", "document_changes",
    "delivery_records", "delivery_events", "provider_credentials",
    "embedding_profiles", "export_jobs", "workspace_settings",
    "sessions", "workspace_members", "usage_events", "vectors", "minio_objects"
  ],
  "will_retain": ["audit_logs(脱敏元数据)", "merged_git_history"],
  "notice": "已合并到 Git 的历史提交会被保留且仍可通过 git log 读取；mylife 不承诺从 Git 历史抹除，仅保证 mylife 侧数据删除。"
}
```

### 3.12 webhooks（Git 提供方）

#### 3.12.1 `POST /webhooks/git/{provider}`

| 项 | 值 |
|---|---|
| 权限 | 匿名，签名校验（`X-Hub-Signature-256` for GitHub、`X-Gitlab-Token` for GitLab） |
| 请求 | 原始 body（必须按原始字节校验，不经 JSON 反序列化再序列化） |
| 响应 200 | `{ "received": true, "delivery_id": string, "handled": bool }` |
| 幂等 | `provider_delivery_id` 唯一（`delivery_events`），重复返回 200 + `handled=false` |
| 重放保护 | 签名时间戳窗口 5 分钟（GitLab token 无时间戳，用 `delivery_id` + 事件时间） |
| 错误码 | `WEBHOOK_SIGNATURE_INVALID`（401）、`VALIDATION_ERROR`、`WEBHOOK_REPLAY`（200） |

支持事件：

| provider | 事件 | 动作 |
|---|---|---|
| github | `pull_request`（`closed` + `merged=true`） | 回写 `delivery_records.status=merged`、`merged_commit`；若文件 `content_hash` 与 DB 不一致 → 以 Git 为准生成新 version 并标 `human_edited`，或转 conflict（架构 §8.3） |
| github | `push`（目标为 `base_branch` 且触及 `reviews_root`） | 触发重索引 + 内容哈希比对 |
| github | `ping` | 返回 200 |
| gitlab | `Merge Request Hook`（`state=merged`） | 同 github merged |
| gitlab | `Push Hook` | 同 github push |

签名校验失败不写业务表，只写 `audit_logs(action=webhook.rejected)` 与告警指标；响应体不含内部原因。

#### 3.12.2 `GET /webhooks/deliveries`

| 项 | 值 |
|---|---|
| 权限 | `owner` |
| 查询 | `limit`、`cursor`、`status?` |
| 响应 200 | `{ "items": [{"provider_delivery_id","event","received_at","handled","status"}], "next_cursor" }` |
| 用途 | 排障：确认 webhook 是否到达与被处理 |

---

## 4. Agent 数据契约（架构 §5 扩展子模型）

本节只补齐 `ARCHITECTURE.md` §5 已声明但未展开字段的类型（`SectionPlan`、`RetrievalTask`、`CorrelationTarget`、`Budget`、`DraftSection`、`Claim`、`GateIssue`、`DeliveryTarget`、`PullRequestSpec`、`Coverage`、`Missing`）。**字段名与架构 §5 完全一致**；本文只新增架构 §5 中已引用未定义的子模型，不修改既有字段。

### 4.0 架构 §5 六个顶层对象（逐字段复述，权威仍为架构 §5）

#### Plan

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `plan_version` | int | 是 | 从 1 递增；修复重试时递增 |
| `period_start` | date | 是 | 按 `timezone` 计算，daily 为当日 |
| `period_end` | date | 是 | `>= period_start`；daily 与 start 相等 |
| `sections` | SectionPlan[] | 是 | daily 7 / weekly 4 / yearly 5；`ordinal` 连续 |
| `tasks` | RetrievalTask[] | 是 | ≥1；`task_id` 唯一 |
| `correlations` | CorrelationTarget[] | 是 | 可为空数组；每项 `max_items` ≤20 |
| `budget` | Budget | 是 | 见 §4.4 |

#### EvidenceBundle

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `bundle_id` | string | 是 | run 内唯一，`b{run_short}` 形式 |
| `run_id` | UUID | 是 | — |
| `snapshot_id` | UUID | 是 | 所有 `EvidenceItem` 的 `document_content_hash` 均来自该快照 |
| `items` | EvidenceItem[] | 是 | 全局 ≤80；单章节 ≤12（yearly 20） |
| `coverage` | object（`dict[str, Coverage]`） | 是 | key ∈ 对应 kind 的 SectionId 集合 |
| `missing` | Missing[] | 是 | 空数组而非 null |
| `degraded` | bool | 是 | 任一 required task 失败即为 true |

#### EvidenceItem

| 字段 | 类型 | 可空 | 约束 |
|---|---|---|---|
| `evidence_id` | string | 否 | run 内唯一，`e{n}` |
| `source_type` | enum `document`/`chunk`/`review`/`task`/`stats` | 否 | — |
| `document_id` | UUID | 是 | `document`/`chunk` 必填 |
| `chunk_id` | UUID | 是 | `chunk` 必填 |
| `source_path` | string | 是 | `document`/`chunk`/`review`/`task` 必填；`stats` 必须为 null |
| `source_date` | date | 是 | 来源文档业务日期 |
| `occurred_on` | date | 是 | 事件发生日期，可与 `source_date` 不同 |
| `document_content_hash` | string | 是 | 快照冻结时哈希；`stats` 用规范统计载荷哈希 |
| `heading` | string | 是 | — |
| `line_range` | [int, int] | 是 | 闭区间；`stats` 为 null |
| `quote` | string | 否 | 原文片段；`stats` 为规范载荷摘要 |
| `quote_hash` | string | 否 | `sha256(parser_version + "\x1f" + normalize(quote))` |
| `tags` | string[] | 否 | 空数组而非 null |

#### Draft

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `draft_version` | int | 是 | 从 1 递增；每次 `revising` 递增 |
| `sections` | DraftSection[] | 是 | 与 Plan 的 `section_key` 集合与顺序完全相等 |
| `claims` | Claim[] | 是 | 每个非哨兵句必须对应 ≥1 条 |

#### ReviewVerdict

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `decision` | enum `APPROVE`/`REVISE`/`REJECT`/`NEEDS_HUMAN` | 是 | `SECURITY_ABORT` 不在此枚举，由状态机直接终止 |
| `blocking` | GateIssue[] | 是 | 空数组 = 通过 |
| `warnings` | GateIssue[] | 是 | 不阻断交付 |
| `revision_round` | int | 是 | 从 0 起 |
| `fingerprint` | string | 是 | 违规集合指纹，用于判断“是否越改越差” |

#### DeliveryManifest

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `branch` | string | 是 | `review/{kind}/{period_key}`；`target=object_store` 时为占位串 |
| `targets` | DeliveryTarget[] | 是 | 至少 1 个；路径互不重复 |
| `commit_message` | string | 是 | 带 `Run-Id`/`Review-Id`/`Version`/`Workspace` trailer |
| `pr` | PullRequestSpec 或 null | 是 | `target` 非 `git` 时为 null |
| `review_row` | ReviewRowRef | 是 | 与 `reviews` 当前版本一致 |

### 4.1 SectionPlan

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `section_key` | string | 是 | daily ∈ `SectionId`；weekly ∈ `WeeklySectionId`；yearly ∈ `YearlySectionId`（架构 §5.1） |
| `ordinal` | int | 是 | 从 1 连续，按 kind 固定基数：daily 1–7、weekly 1–4、yearly 1–5 |
| `title` | string | 是 | 中文名，与架构 §5.1 表一致 |
| `required_fields` | string[] | 是 | 与架构 §5.1 必填字段逐项相等 |
| `evidence_task_ids` | string[] | 是 | 指向 `RetrievalTask.task_id`，≥1（required 章节） |
| `max_evidence` | int | 是 | 单章上限：daily 12（`one_line_summary` 为 1）、weekly 12、yearly 20（`next_year_direction` 为 10） |
| `required` | bool | 是 | 所有章节均为 true；缺失即 G1 blocking |

### 4.2 RetrievalTask

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `task_id` | string | 是 | run 内唯一，`t{n}` 形式 |
| `section_keys` | string[] | 是 | 该任务服务的章节，可多章共享 |
| `tool` | enum | 是 | `search_notes`/`read_note`/`list_tasks`/`get_review`/`get_stats` |
| `args` | object | 是 | 该工具入参（不含 `workspace_id`，见 §5 工具契约） |
| `required` | bool | 是 | required 任务全失败 → 章节 `degraded` |
| `timeout_ms` | int | 否 | 默认 10000，上限 20000 |
| `max_results` | int | 否 | 默认 20，上限 50 |

### 4.3 CorrelationTarget

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `correlation_type` | enum | 是 | 八类之一：`PRIOR_REVIEW`/`TOPIC_PAGE`/`WIKILINK_NEIGHBORHOOD`/`TAG_COHORT`/`RECURRING_ERROR`/`RECURRING_METHOD`/`TASK_CARRY`/`STATS_BASELINE`（架构 §7.1） |
| `section_keys` | string[] | 是 | 关联到哪些章节 |
| `params` | object | 是 | 按类型不同：`days`、`top_n`、`tags`、`window_start` 等 |
| `max_items` | int | 是 | 默认 12，上限 20 |

硬规则（代码断言，非 Prompt）：`PRIOR_REVIEW` 只提供“上次结论 + 原始 `evidence_id`/`path`”，Writer 必须解析回原始 document chunk，否则显式标“历史复盘转述”；`source_type=generated` 的文档默认不进普通证据池。

### 4.4 Budget

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `max_input_tokens` | int | 是 | 按 kind：daily 100000 / weekly 300000 / yearly 1000000 | 决策 5B |
| `max_output_tokens` | int | 是 | daily 20000 / weekly 60000 / yearly 200000 | — |
| `max_cost_usd` | float | 否 | null | 可选货币上限，优先级低于 token 上限 |
| `warn_ratio` | float | 否 | 0.8 | 80% 告警 |
| `hard_stop_ratio` | float | 否 | 1.0 | 100% 硬停，终止为 `failed` 或 `needs_human` |

`BudgetUsage`（响应侧）：`{ "input_tokens", "output_tokens", "cost_usd", "ratio", "state" ∈ ok/warn/exceeded }`。预算**跨重试与修订累计**，不允许每步重置；每次 LLM 调用前检查。

### 4.5 Coverage 与 Missing

`Coverage`：`{ "required_tasks": int, "succeeded_tasks": int, "failed_task_ids": string[], "evidence_count": int, "status" ∈ ok/partial/none }`。

`Missing`：`{ "section_key": string, "reason" ∈ no_evidence/timeout/disabled/profile_mismatch/io_error/runtime_unavailable, "detail": string }`。`reason` 取值与 `DegradedReason` 枚举同源（架构 §16.2）。

`DegradedReason` 全量枚举（`degraded_reasons[]` 只允许以下值；客户端必须容忍新增值并按 `unknown` 展示）：

| 值 | 作用域 | 含义 | 消费行为 |
|---|---|---|---|
| `missing` | 复盘章节 | 该章节无可用证据 | 章节写哨兵，其余正常 |
| `disabled` | workspace | 向量检索或某能力被 workspace 关闭 | lexical-only，UI 提示 |
| `profile_mismatch` | embedding | 查询维度与 active profile 不一致 | 检索降级，提示重建索引 |
| `io_error` | 导入/交付 | MinIO/Git/文件系统 IO 失败 | 重试或转人工 |
| `runtime_unavailable` | 全层 | 检索/评审/LLM 运行时不可用 | 转人工或 `evidence_only` |
| `vector_timeout` | 搜索 | 向量路超过 250ms | lexical-only |
| `embedding_unavailable` | 搜索/导入 | Embedding 服务不可用 | lexical-only / 延后 embedding |
| `ingest_partial_failed` | 导入 | 部分文件解析失败 | 显示失败清单 + 重试入口 |
| `llm_unavailable` | 生成 | LLM 不可用 | 转人工，不占位冒充成功 |
| `git_unavailable` | 交付 | Git 不可用 | 降级 `ui_diff`/对象存储 |
| `epoch_mismatch` | SSE | 客户端 epoch 与服务端不同 | 从 run 状态重建 |
| `cursor_expired` | SSE | `after` 早于保留窗口 | 从 run 状态重建 |
| `budget_warn` | run | 预算达 80% | 继续执行，UI 提示 |
| `topic_needs_confirm` | 专题 | 首见条目待人工确认（决策 4B） | 转人工，不落库 |

降级**永不**把 HTTP 200 变成错误；`SECURITY_ABORT`/`TENANT_LEAK`/`FABRICATION` 不属于降级，是终止（反目标 A5/C1）。

### 4.6 DraftSection 与 Claim

`DraftSection`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `section_key` | string | 是 | 与 Plan 章节集合逐项相等 |
| `ordinal` | int | 是 | 与 Plan 一致 |
| `content_md` | string | 是 | Markdown 正文；无证据时为架构 §5.1 对应哨兵 |
| `structured` | object | 是 | 按 `SECTION_SCHEMAS[kind][section_key]` 校验，`extra="forbid"` |
| `claim_ids` | string[] | 是 | 本章引用的 claim，可空数组（哨兵章） |
| `sentinel_used` | bool | 是 | 是否使用了哨兵文案 |
| `degraded` | bool | 是 | 本章是否因证据缺失降级 |

`Claim`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `claim_id` | string | 是 | run 内唯一，`c{n}` |
| `section_key` | string | 是 | 所属章节 |
| `text` | string | 是 | 正文句子（不含引用序号标记） |
| `evidence_ids` | string[] | 是 | ≥1；为空即 G5 blocking |
| `citation_indexes` | int[] | 是 | 正文 `[n]` 序号，从 1 连续 |
| `claim_type` | enum | 是 | `fact`/`task_status`/`event`/`mistake`/`method`/`action` |

### 4.7 GateIssue

`{ "gate": string（G0–G9/R1–R9）, "severity" ∈ blocking/warning, "code": string, "message": string, "section_key": string/null, "claim_id": string/null, "evidence_id": string/null, "must_fix": string/null }`。

`ReviewVerdict.decision` 映射：无 blocking → `APPROVE`；有 blocking 且 `revision_round < REVISION_LIMIT[kind]` 且指纹变化 → `REVISE`；轮次耗尽或规则引擎异常 → `NEEDS_HUMAN`；`TENANT_LEAK`/`FABRICATION` → 独立 `SECURITY_ABORT`（verdict 不出现该 decision，由状态机直接终止）。

### 4.8 DeliveryTarget / PullRequestSpec / ReviewRowRef

`DeliveryTarget`：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | enum `git`/`object_store`/`ui_diff` | 是 | `ui_diff` 为 Git 不可用降级 |
| `path` | string | 是 | 相对 `reviews_root`，如 `每日复盘/2026/2026-10-27.md` |
| `content_hash` | string | 是 | sha256 渲染结果 |
| `mode` | enum `create_only`/`update_allowed`/`append_only` | 是 | 日报/周报/年报 `update_allowed`；专题 `append_only`；快照 `create_only`（架构 §16.2） |

`PullRequestSpec`：`{ "title", "body_md", "base_branch", "head_branch", "draft": bool=false, "auto_merge": bool }`。`auto_merge` 仅 `kind=daily` 且 workspace 显式开启且 Gate 全通过时为 true（决策 3B）；weekly/yearly/topic 恒 false。

`ReviewRowRef`：`{ "review_id": UUID, "version": int, "content_hash": string, "git_path": string }`。

`DeliveryManifest.commit_message` 必须带 trailer：`Run-Id: {run_id}`、`Review-Id: {review_id}`、`Version: {n}`、`Workspace: {workspace_id}`；PR 正文首行在 `degraded=true` 时为 `degraded: {reason}`（反目标 D4）。

---

## 5. ToolGateway 内部契约

`ToolGateway` 是 Planner/Worker/Writer/Reviewer 与检索/统计服务的唯一入口；SaaS 搜索页与 chat 共用同一 `retrieval-service`（禁止三套检索，架构 §2.6）。

### 5.1 通用规则

| 项 | 规范 |
|---|---|
| 传输 | 进程内函数调用（同容器）或 `POST /internal/v1/tools/{tool_name}`（跨容器，仅内网，mTLS 或共享密钥） |
| 上下文 | `ToolContext { workspace_id, user_id, run_id, role, snapshot_as_of, deadline_ms, trace_id }`，由调用方注入，**不入参模型** |
| 入参模型 | Pydantic `extra="forbid"`，不含 `workspace_id`；出现即 `TOOL_ARGS_INVALID` |
| 出参 | 纯 JSON，不含渲染 HTML（`snippet` 为纯文本）；`snippet_html` 仅 SaaS 搜索页 |
| 超时 | 默认 10s，`search_notes` 15s；超时 `TOOL_TIMEOUT` |
| 重试 | 编排层对幂等只读工具重试 1 次；写工具不重试 |
| 计量 | 每次调用写 `run_steps`（`input_digest`/`output_digest`/`latency_ms`），不写正文 |
| 降级 | 向量不可用 → lexical-only + `degraded`；全部失败 → 返回 `missing` 而非抛异常（由编排层决定章节降级） |

能力声明与实现比对：启动时比对声明清单与已实现工具，缺失即 `TOOL_DRIFT`，该工具不可被 Planner 规划、不可被 Worker 调用（架构 §16.2）。

### 5.2 `search_notes`

| 项 | 值 |
|---|---|
| 入参 | `SearchQuery`（架构 §5）：`query`、`filters`、`top_k`(≤50)、`mode` |
| 出参 | `{ "items": SearchResult[], "degraded": bool, "degraded_reasons": string[], "took_ms": int }` |
| 权限 | Planner：禁止；Worker：允许；Writer：禁止；Reviewer：禁止（只读复核用 `read_note`） |
| 默认 | `top_k=20`，`mode=hybrid`，`source_type=document` |
| 约束 | 工具侧 `include_snippet_html=false`；`snippet` 纯文本；结果按 `(score desc, path asc, chunk_ordinal asc)` 稳定排序 |

### 5.3 `read_note`

| 项 | 值 |
|---|---|
| 入参 | `{ "document_id"?: UUID, "path"?: string, "heading"?: string, "line_range"?: [int,int], "window"?: int }`；`document_id` 与 `path` 二选一 |
| 出参 | `{ "document_id", "path", "title", "heading", "line_range", "content", "content_hash", "truncated": bool }` |
| 权限 | Planner：禁止；Worker：允许；Writer：仅经 `expand_evidence` 间接；Reviewer：允许（只读复核） |
| 约束 | `window` 1–200 行；单次返回 ≤8000 字符；路径必须在 `snapshot` manifest 内，否则 `TOOL_NOT_ALLOWED` |
| 安全 | 返回内容包 `<<<EVIDENCE id=...>>>` 定界；工具不做摘要、不改写 |

### 5.4 `list_tasks`

| 项 | 值 |
|---|---|
| 入参 | `{ "status"?: enum[], "from"?: date, "to"?: date, "section_keys"?: string[], "limit"?: int }` |
| 出参 | `{ "items": TaskItem[], "counts": {"open","done","carried","dropped"}, "ledger_recalc": {"completed","total","rate"} }` |
| 权限 | Planner：允许（仅计数与聚合，**不可读文本**）；Worker：允许；Writer/Reviewer：禁止 |
| 约束 | Planner 调用时 `include_text` 强制 false（返回项只有 `id/status/dates/count`）；`limit` ≤200 |

Planner 的 `list_tasks` 出参裁剪版：`{ "counts": {...}, "ledger_recalc": {...}, "buckets": [{"status","count","oldest_first_seen"}] }`，不含任务文本（架构 §3.1“仅计数与聚合”）。

### 5.5 `get_review`

| 项 | 值 |
|---|---|
| 入参 | `{ "review_id"?: UUID, "kind"?: enum, "period_key"?: string, "version"?: int, "include"?: ["summary","sections","entries"] }` |
| 出参 | `{ "review_id", "kind", "period_key", "version", "title", "sections": [{section_key, ordinal, structured}], "entries": ReviewEntry[], "citations": ReviewCitation[] }` |
| 权限 | Planner：允许（仅 `include=["summary"]`，返回标题/版本/章节计数）；Worker：允许（用于 PRIOR_REVIEW 指针）；Writer：禁止；Reviewer：允许 |
| 约束 | **历史复盘只作指针**：`include=["sections"]` 返回正文时 `source_type=generated` 标记必带；Writer 不得直接引用，必须经 PRIOR_REVIEW 解析回原始 chunk |

### 5.6 `get_stats`

| 项 | 值 |
|---|---|
| 入参 | `{ "metric": enum, "from": date, "to": date, "group_by"?: enum }`；`metric` ∈ `completion_rate`/`task_counts`/`review_counts`/`token_usage`/`no_evidence_ratio` |
| 出参 | `{ "metric", "value": number/object, "sample_size": int, "as_of": timestamp, "snapshot_id": UUID, "payload_hash": string }` |
| 权限 | Planner：允许；Worker：允许；Writer/Reviewer：允许（Reviewer 用于交叉核对） |
| 约束 | 确定性 SQL，不接受自由查询；`payload_hash` 用于 `source_type=stats` 的证据校验（G3b） |

### 5.7 `create_review`（唯一写工具）

三步协议（架构 §2.5）：**preview → confirm → enqueue**。

#### 5.7.1 `preview`

| 项 | 值 |
|---|---|
| 入参 | `{ "kind": enum, "period_key": string, "timezone"?: string }` |
| 出参 | `{ "preview": {"kind","period_key","title","path","estimated_evidence_count","estimated_tokens"}, "confirmation_token": string, "expires_at": timestamp, "blocked_reason": string/null }` |
| 权限 | 仅 chat 精简分支内的 L2 可调用；Planner/Worker/Writer/Reviewer 禁止 |
| 语义 | 零副作用；计算目标路径、素材计数、预算估算；若已有活跃 run 则 `blocked_reason=ACTIVE_RUN_EXISTS` |
| 约束 | `confirmation_token` 为 HMAC 签名串，TTL 10 分钟，绑定 `(workspace_id, user_id, kind, period_key)` |

#### 5.7.2 `confirm`

| 项 | 值 |
|---|---|
| 入参 | `{ "confirmation_token": string }` |
| 出参 | `{ "confirmed": true, "confirmation_token": string（已消费标记） }` |
| 语义 | 服务端校验 token 签名、TTL、未被使用；标记为已确认但**仍不入队**，由前端调用 `POST /reviews` |
| 错误码 | `TOOL_ARGS_INVALID`（token 无效/过期/已用）、`CONFLICT`（状态已变） |

#### 5.7.3 `enqueue`

| 项 | 值 |
|---|---|
| 触发 | 前端 `POST /reviews`（携带同一 `Idempotency-Key`），**不是**工具直接调用 |
| 出参 | `{ "run_id": UUID, "state": "created" }` |
| 约束 | 禁止 review run 内递归调用 `create_review`；违反即 `TOOL_NOT_ALLOWED` + 审计 |

前端交互：SSE 中收到 `tool.result`（`tool_name=create_review`，`status=preview`）→ 渲染确认卡片（kind、period、路径、预计证据数）→ 用户点“确认生成” → 调 `POST /reviews` → 收到 `run_id` → 订阅该 run 的 SSE。

### 5.8 工具权限矩阵

| 工具 | Planner | Worker | Writer | Reviewer | Chat L2 |
|---|---|---|---|---|---|
| `search_notes` | 否 | 是 | 否 | 否 | 是 |
| `read_note` | 否 | 是 | 仅 expand | 是（只读复核） | 是 |
| `list_tasks` | 是（仅聚合） | 是 | 否 | 否 | 是 |
| `get_review` | 是（仅 summary） | 是 | 否 | 是 | 是 |
| `create_review` | 否 | 否 | 否 | 否 | 是（preview/confirm） |
| `get_stats` | 是 | 是 | 是 | 是 | 是 |
| `expand_evidence` | 否 | 否 | 是 | 否 | 否 |

---

## 6. SSE 事件契约

### 6.1 建流

`GET /api/v1/runs/{run_id}/events?after={event_id}&epoch={stream_epoch}`

| 项 | 规范 |
|---|---|
| 响应头 | `Content-Type: text/event-stream; charset=utf-8`、`Cache-Control: no-store`、`X-Accel-Buffering: no`、`Connection: keep-alive` |
| 认证 | 同 Cookie 会话；EventSource 同源自动携带；跨域不允许 |
| 查询参数 | `after`（int64，默认 0 = 从头回放）、`epoch`（int，默认 0）、`types?`（逗号分隔过滤，可选） |
| 重连 | 客户端用 `Last-Event-ID` 头或 `after` 参数；服务端以 `id:` 字段回填 |
| 关闭 | run 进入终态后发送 `done` 并关闭连接；客户端不得无限重连（收到 `done` 后停止） |

### 6.2 事件类型

| event | 持久化 | 触发 | 关键字段 |
|---|---|---|---|
| `run.state` | 是 | 每次状态迁移 | `from_state`、`to_state`、`attempt_id`、`at` |
| `tool.call` | 是 | 工具调用开始 | `seq`、`role`、`tool_name`、`args_digest`、`args_summary` |
| `tool.result` | 是 | 工具调用结束 | `seq`、`status`、`latency_ms`、`result_digest`、`result_summary` |
| `citation` | 是 | 产生/校验引用 | `claim_id`、`evidence_id`、`path`、`heading`、`line_range`、`index` |
| `token` | 否（仅 SSE） | 流式增量 | `delta`、`message_id`、`seq` |
| `progress` | 否（仅 SSE） | 长阶段进度 | `stage`、`processed`、`total` |
| `degraded` | 是 | 降级发生 | `reason`（`DegradedReason`）、`scope`、`section_key`/`null` |
| `heartbeat` | 否（仅 SSE） | 每 15s 空闲 | `at`、`state` |
| `done` | 是 | run 成功结束 | `state`、`message_id`/`review_id`、`usage` |
| `error` | 是 | run 失败/终止 | `code`、`message`、`state`、`retryable` |
| `cancelled` | 是 | 被取消 | `by`、`reason` |
| `chat.tool_call` / `chat.tool_result` | 是 | chat 别名（架构 §4.6） | 同 `tool.call`/`tool.result` |
| `chat.citation` / `chat.done` / `chat.error` / `chat.cancelled` | 是 | chat 别名 | 同对应通用事件 |

兼容规则：客户端必须忽略未知 `event` 类型（§10.4）；不得因未知事件断开连接。

### 6.3 帧格式

```
id: 1042
event: tool.call
data: {"seq":3,"role":"worker","tool_name":"search_notes","args_digest":"sha256:9f2a...","args_summary":"query=\"缓存穿透\" top_k=10","at":"2026-11-06T01:30:02Z"}

```

`data` 必须为单行 JSON（`\n` 转义）；`retry:` 字段固定 3000ms，在首个事件前发送一次。

### 6.4 cursor 与 epoch

| 场景 | 行为 |
|---|---|
| `after` 命中保留窗口 | 从 `event_id > after` 回放持久化事件，再续实时流 |
| `after=0` 且 run 未终态 | 回放全部持久化事件（上限 2000 条，超出则返回 `EVENT_CURSOR_EXPIRED` 前先发 `degraded`） |
| `epoch` 与会话当前 epoch 不同 | 拒绝混合历史：发 `degraded`（`reason=epoch_mismatch`）后要求客户端从 `GET /runs/{id}` 重建状态 |
| `after` 早于保留窗口 | 建流前校验失败 → HTTP `410 EVENT_CURSOR_EXPIRED`（`details.oldest_available_event_id`）；已建流后才发现 → 发 `error` 事件后关闭 |
| 断线重连 | 客户端指数退避 1s/2s/4s/8s，最多 5 次；期间以 `GET /runs/{id}` 兜底刷新状态 |

`event_id` 全局单调（`run_events.event_id`），非 run 内自增；同一 run 内严格递增。`stream_epoch` 为进程启动时生成的 int，进程重启即变。

### 6.5 保留策略

| 事件 | 保留 |
|---|---|
| 持久化事件（已完成 run） | 30 天，可配置（`run_events_retention_days`） |
| 超期归档 | 写对象存储 `ws/{ws}/runs/{run_id}/events.jsonl.gz`，DB 删除 |
| `token`/`progress`/`heartbeat` | 不持久化，仅 SSE |
| 活跃 run | 全量保留至终态 |

### 6.6 示例帧（完整一次 chat run）

```
retry: 3000

id: 1
event: run.state
data: {"from_state":"created","to_state":"retrieving","attempt_id":"0192...","at":"2026-11-06T01:30:00Z"}

id: 2
event: chat.tool_call
data: {"seq":1,"role":"worker","tool_name":"search_notes","args_summary":"query=\"Redis 缓存\" top_k=10","at":"2026-11-06T01:30:01Z"}

id: 3
event: chat.tool_result
data: {"seq":1,"status":"ok","latency_ms":412,"result_summary":"3 hits: notes/Redis/缓存穿透.md#L12"}

id: 4
event: chat.citation
data: {"index":1,"evidence_id":"e1","document_id":"0192...","chunk_id":"0193...","path":"notes/Redis/缓存穿透.md","heading":"缓存穿透","line_range":[12,28]}

id: 5
event: token
data: {"delta":"你在 ","message_id":"0194...","seq":1}

id: 6
event: heartbeat
data: {"at":"2026-11-06T01:30:16Z","state":"drafting"}

id: 7
event: chat.done
data: {"state":"succeeded","message_id":"0194...","usage":{"prompt_tokens":1820,"completion_tokens":240,"model":"deepseek-chat","latency_ms":6100}}
```

---

## 7. 状态机事件契约

### 7.1 run 状态转移事件

`run_events` payload 统一结构：

```json
{
  "event": "state.transition",
  "from_state": "retrieving",
  "to_state": "drafting",
  "attempt_id": "0192...",
  "reason": "quorum_ok",
  "at": "2026-11-06T01:30:05Z"
}
```

| 转移 | `reason` 取值 |
|---|---|
| `created → snapshotting` | `admit_ok` |
| `snapshotting → planning` | `snapshot_ok` |
| `planning → retrieving` | `plan_valid` |
| `retrieving → drafting` | `quorum_ok` / `quorum_partial_degraded` |
| `drafting → reviewing` | `draft_schema_ok` |
| `reviewing → delivering` | `gate_pass` |
| `reviewing → revising` | `blocking_found` |
| `revising → drafting` | `revised` |
| `revising → retrieving` | `missing_evidence` |
| `reviewing → human_gate` | `reviewer_disagreement` / `reviewer_unavailable` / `revision_exhausted` / `conflict` |
| `human_gate → delivering` | `human_approved` |
| `human_gate → cancelled` | `human_rejected` |
| `delivering → succeeded` | `commit_and_pr_ok` / `local_only` / `noop` |
| `delivering → retry_wait` | `transient_failure` |
| `retry_wait → delivering` | `retry` |
| `→ needs_human` | `schema_retry_exhausted` / `required_missing` / `attempts_exhausted` / `gate_unavailable` |
| `→ security_abort` | `tenant_leak` / `fabrication` |
| `→ failed` | `invalid_initial_state` / `budget_exceeded` / `unhandled` |
| `→ cancelled` | `user_cancel` / `chat_superseded` |

线值（wire value）一律小写；架构 §4.1 的大写为同一枚举的可读别名。终态集合：`succeeded`、`failed`、`cancelled`、`needs_human`、`security_abort`。`is_active_state(state)` 为单一 DB 函数，活跃态 = `created,snapshotting,planning,retrieving,drafting,reviewing,revising,human_gate,delivering,retry_wait`；`chat` 活跃态子集 = `created,retrieving,drafting,verifying`（架构 §4.3 索引 2 原文，含 `verifying` 与 §4.6 一致）。

> 注：`verifying` 仅 chat 使用；复盘不用该状态（复盘引用校验并入 `reviewing`）。

### 7.2 `run_events` 持久化模型

| 字段 | 类型 | 约束 |
|---|---|---|
| `event_id` | bigint | 全局单调，主键 |
| `run_id` | UUID | 复合唯一 `(run_id, event_id)` |
| `workspace_id` | UUID | NOT NULL，RLS |
| `event` | string | 事件类型 |
| `from_state` / `to_state` | enum/null | 状态迁移事件必填 |
| `payload` | jsonb | 按事件类型 schema 校验 |
| `created_at` | timestamptz | — |
| `stream_epoch` | int | 写入时的进程 epoch |

写入要求：状态迁移与 `agent_runs` CAS 同一事务（`UPDATE ... WHERE state=:from AND lease_owner=:owner` 成功才写事件），保证事件与状态真相一致；工具事件在调用完成后写入（开始事件可先写 `tool.call`，结果事件后写）。

### 7.3 webhook 事件契约

`delivery_events`：

| 字段 | 类型 | 约束 |
|---|---|---|
| `provider_delivery_id` | string | 唯一；幂等键 |
| `conn_id` | UUID | `git_connections.id` |
| `event` | string | `pull_request.merged` / `push` / `ping` / `merge_request.merged` |
| `payload_digest` | string | sha256 原始 body |
| `status` | enum `received`/`handled`/`ignored`/`failed` | — |
| `received_at` | timestamptz | — |

处理规则：重复 `provider_delivery_id` → 200 + `handled=false`；签名失败 → 401，不写业务表；`pull_request.merged` 回写 `delivery_records`（CAS：仅 `status ∈ {pr_open, committed}` 可转 `merged`）。

### 7.4 幂等 delivery id

`delivery_id = uuid5(NAMESPACE_URL, "{workspace_id}|{review_id}|{version}|{target}")`，与 `delivery_records` 唯一约束 `(workspace_id, review_id, version, target)` 对齐；重试不产生新记录，只更新 `status` 与 `updated_at`。`commit_sha` 变化但 `content_hash` 未变时，状态为 `noop`（`DELIVERY_NOOP`）。

---

## 8. 限流与配额

### 8.1 限流维度与默认值

| 端点组 | 维度 | 限额 | 窗口 | 触发码 |
|---|---|---|---|---|
| `POST /auth/login` | `ip + email` | 5 次 | 15 分钟 | `AUTH_RATE_LIMITED` |
| `POST /auth/login` | `ip` | 20 次 | 1 小时 | `AUTH_RATE_LIMITED` |
| `POST /auth/register` | `ip` | 3 次 | 1 小时 | `AUTH_RATE_LIMITED` |
| `GET /search` | `workspace + user` | 60 次 | 1 分钟 | `RATE_LIMITED` |
| `GET /search/suggest` | `workspace + user` | 120 次 | 1 分钟 | `RATE_LIMITED` |
| `POST /conversations/{id}/turns` | `workspace` | 20 次 | 1 分钟 | `RATE_LIMITED` |
| `POST /conversations/{id}/turns` | `user` | 200 次 | 1 天 | `QUOTA_EXCEEDED` |
| `POST /reviews` | `workspace` | 10 次 | 1 小时 | `RATE_LIMITED` |
| `POST /reviews` | `workspace` | 50 次 | 1 天 | `QUOTA_EXCEEDED` |
| 复盘并发 | `workspace` | 2 个活跃 run | — | `ACTIVE_RUN_EXISTS` |
| chat 并发 | `workspace` | 5 个活跃 run | — | `RATE_LIMITED` |
| `POST /exports` | `workspace` | 1 个活跃任务 | — | `EXPORT_LIMIT` |
| `POST /exports` | `workspace` | 3 次 | 1 天 | `EXPORT_LIMIT` |
| `POST /ingest/zip` | `workspace` | 1 个活跃任务 | — | `INGEST_CONCURRENCY_LIMIT` |
| `POST /ingest/*` | `workspace` | 20 次 | 1 小时 | `RATE_LIMITED` |
| `POST /deletions` | `user` | 3 次 | 1 天 | `RATE_LIMITED` |
| 工具调用（chat） | `run` | 10 次 / 4 轮 | 单 run | `TOOL_TIMEOUT` 或编排终止 |
| LLM 调用 | `workspace` | 按 token 预算 | 日/月 | `BUDGET_EXCEEDED` |
| 管理端点 | `platform_admin` | 100 次 | 1 分钟 | `RATE_LIMITED` |

实现：Redis 滑动窗口（`INCR` + `EXPIRE`），键含 `ws:{workspace_id}` 前缀（旁路隔离）；失败打开（Redis 不可用）时**保守放行读、拒绝写**，并记 `ratelimit_degraded` 指标。

### 8.2 返回头

| 头 | 说明 |
|---|---|
| `X-RateLimit-Limit` | 当前窗口上限 |
| `X-RateLimit-Remaining` | 剩余次数 |
| `X-RateLimit-Reset` | 窗口重置的 Unix 秒 |
| `X-RateLimit-Policy` | 如 `60;w=60` |
| `Retry-After` | 429/503 时的等待秒数（整数，向上取整，最小 1） |
| `X-Quota-Limit` / `X-Quota-Remaining` / `X-Quota-Reset` | 日/月配额端点额外返回 |

预算响应：`BUDGET_EXCEEDED` 时 `details` 为 `{ "scope": "run"|"workspace_daily"|"workspace_monthly", "used": {"input_tokens","output_tokens"}, "limit": {...}, "resets_at": timestamp }`。

### 8.3 预算数值（决策 5B）

| 范围 | 输入上限 | 输出上限 | 行为 |
|---|---|---|---|
| daily run | 100k | 20k | 80% 告警（`degraded` 事件 + UI 提示），100% 硬停 |
| weekly run | 300k | 60k | 同上 |
| yearly run | 1M | 200k | 同上 |
| workspace 月 | 5M | 1M | 100% 硬停，所有新 run 返回 `BUDGET_EXCEEDED` |
| workspace 日 | 由月预算派生（默认月 1/20） | 同 | 100% 硬停 |

---

## 9. OpenAPI 生成与契约共享

### 9.1 目录与包结构

```text
packages/
├─ contract/                  # 单一事实源（Python 包，前后端共享）
│  ├─ pyproject.toml
│  ├─ mylife_contract/
│  │  ├─ __init__.py
│  │  ├─ common.py            # ErrorEnvelope, Page, Idempotency, Timestamps
│  │  ├─ enums.py             # RunState, ErrorCode, DegradedReason, SectionId...
│  │  ├─ auth.py              # User, Workspace, SessionInfo, Limits
│  │  ├─ documents.py         # TreeNode, DocumentDetail, DocumentLink
│  │  ├─ search.py            # SearchQuery, SearchFilters, SearchResult
│  │  ├─ chat.py              # ChatTurnRequest, Message, Reference, ToolEvent
│  │  ├─ review.py            # ReviewJobRequest, Plan, Draft, ReviewVerdict...
│  │  ├─ agent.py             # EvidenceBundle, EvidenceItem, DeliveryManifest
│  │  ├─ settings.py          # LLM/Embedding/Schedule/Git settings
│  │  ├─ events.py            # SSE 事件 discriminated union
│  │  └─ tools.py             # ToolContext, 六个工具的入参/出参
│  └─ tests/
│     └─ test_schema_freeze.py
├─ api/                       # FastAPI，import mylife_contract
└─ web/                       # Next.js，生成 TS 类型
```

规则：`contract` 包**不含业务逻辑与 IO**，只含 Pydantic v2 模型、枚举、常量（`REVISION_LIMIT`、`SENTINELS`、`SECTION_SCHEMAS`、`ITEM_SCHEMAS`）；`api` 与 `worker` 同版本依赖，CI 锁定同一 commit。

### 9.2 FastAPI 组织

| 项 | 规范 |
|---|---|
| 路由 | `api/routers/{auth,workspaces,ingest,docs,search,chat,review,dashboard,settings,admin,export_delete,webhooks}.py` |
| 依赖注入 | `get_run_context()` 产出 `RunContext`；`get_db()` 绑定 RLS `set_config`；`require_role()` 声明权限 |
| 错误 | 统一 `AppError(code, http_status, message, details, retryable)` 异常处理器，输出 §1.5 信封 |
| 响应模型 | 每个端点显式 `response_model=`，禁止 `dict` 直出 |
| 版本 | `APIRouter(prefix="/api/v1")`；`/internal/v1` 单独 router，OpenAPI `include_in_schema=False` |
| 文档 | `GET /api/v1/openapi.json`（生产只对 `platform_admin` 开放或走内部网络）；`/docs` 生产关闭 |
| 幂等 | 中间件按 `Idempotency-Key` 存 Redis（`24h`），记录请求体 sha256 与响应快照 |
| SSE | `StreamingResponse` + `text/event-stream`；禁用 GZip 与缓冲 |

### 9.3 前端类型生成

| 项 | 规范 |
|---|---|
| 工具 | `openapi-typescript`（或 `orval`）生成 `web/src/lib/api/schema.d.ts` |
| 客户端 | TanStack Query + 生成的 `paths` 类型；不手写接口类型 |
| 校验 | CI 中 `pnpm gen:api && git diff --exit-code`，防止手改生成文件 |
| 枚举 | 生成 union 类型；额外生成 `z.enum` 供运行时表单校验 |
| 错误 | 生成 `ErrorCode` union，前端 `switch` 穷举（新增码时 TS 报错提醒） |

### 9.4 契约测试

| 层 | 测试 | 断言 |
|---|---|---|
| 单元 | `test_schema_freeze.py` | 字段名/类型/默认值与本文快照一致；快照文件 `contract/schema_snapshot.json` 变更需显式批准 |
| 单元 | 枚举完整性 | `ErrorCode`、`RunState`、`SectionId` 与本文表格逐项相等 |
| 集成 | `test_openapi_conformance.py` | 实际路由的请求/响应与 `response_model` 一致；未知字段被拒（`extra="forbid"`） |
| 集成 | `test_tenant_isolation.py` | 每张租户表跨租户 0 行；跨租户 HTTP 404 |
| 集成 | `test_sse_contract.py` | 事件 schema、`after` 回放、`epoch` 不匹配、保留窗口耗尽 |
| 集成 | `test_idempotency.py` | 同键同体重放、同键异体 409、24h 过期 |
| 契约 | `test_tool_gateway.py` | 六工具入参不含 `workspace_id`；越权角色被拒；超时 |
| 契约 | `test_error_codes.py` | 每个错误码至少一个触发用例，HTTP 状态与 `retryable` 匹配 |
| 黄金 | `fx_*` fixtures | 与架构 §9.3 同批；断言 7/4/5 章节、哨兵、引用闭包 |

---

## 10. 兼容策略

### 10.1 变更分类

| 变更 | 允许在 v1 内 | 要求 |
|---|---|---|
| 新增可选请求字段 | 是 | 服务端必须有默认值；旧客户端不受影响 |
| 新增响应字段 | 是 | 客户端必须容忍未知字段（见 §10.3） |
| 新增枚举值 | 是（仅响应侧） | 客户端必须有 `unknown` 分支；请求侧新增枚举需版本化 |
| 新增端点 | 是 | — |
| 新增错误码 | 是 | 客户端按 HTTP 状态兜底；`ErrorCode` union 更新 |
| 字段重命名 | 否 | 需 `/v2` |
| 删除字段 | 否 | 需 `/v2`；过渡期双写 + `Deprecation` 头 |
| 改变字段类型/语义 | 否 | 需 `/v2` |
| 改变默认值 | 谨慎 | 需公告 + 至少 30 天双行为期 |
| 收紧校验（如新增必填） | 否 | 需 `/v2` |
| 改变 HTTP 状态码 | 否 | 需 `/v2` |

### 10.2 弃用流程

1. 标记 `deprecated: true` 于 OpenAPI + 响应头 `Deprecation: true`、`Sunset: <RFC1123 日期>`。
2. 至少保留 90 天（跨版本 ≥2 个发布周期）。
3. 文档与变更日志公告；监控旧字段调用量，降至 0 后移除。

### 10.3 客户端容忍规则（强制）

| 场景 | 前端行为 | 后端行为 |
|---|---|---|
| 响应含未知字段 | 忽略，不报错 | 正常返回 |
| 响应含未知枚举值 | 映射为 `unknown` 并展示降级 UI | 保证新增枚举语义可安全降级 |
| SSE 未知事件类型 | 忽略，不断开 | 可发新事件 |
| SSE 未知字段 | 忽略 | — |
| 错误 `details` 结构变化 | 只读已知键，其余忽略 | — |
| 分页 `next_cursor` 格式变化 | 当作不透明串，不解析 | — |
| `schema_version` 不匹配（内部对象） | — | 走 `migrate(payload)`；失败 → `NEEDS_HUMAN/STATE_SCHEMA_MIGRATED`，不猜字段 |

### 10.4 SSE 新事件类型规则

- 新事件必须落在既有 `event` 命名空间（`run.state`、`tool.*`、`chat.*` 等），不得复用已有事件名表达新语义。
- 新事件不得改变既有事件的必填字段；新增字段必须可选。
- 客户端若实现“按事件白名单过滤”，必须默认放行未知事件（仅记录指标），不得静默丢弃 `done`/`error`/`degraded`。
- 事件顺序保证：同一 run 内 `event_id` 严格递增；`done`/`error`/`cancelled` 为最后一条持久化事件。

### 10.5 版本化发布

| 项 | 规范 |
|---|---|
| API 版本 | URL `/api/v1`；OpenAPI `info.version` 与发布 tag 一致 |
| 契约版本 | `contract` 包版本 `0.x`，与 `ARCHITECTURE.md` §5 的 `schema_version` 分离（前者是包版本，后者是 LLM 输出载荷版本） |
| 发布门禁 | 契约快照 diff 需 2 人评审；错误码新增自动通过，删除/改语义阻断 |
| 回滚 | API 无状态，回滚即回滚镜像；DB 迁移只前进（`alembic downgrade` 仅限非破坏性） |

---

## 11. 完整示例

### 11.1 注册（含错误）

请求：

```http
POST /api/v1/auth/register HTTP/1.1
Content-Type: application/json
X-CSRF-Token: 8f3c2a91d4e5b6c7

{"email":"alice@example.com","password":"correct-horse-9","display_name":"Alice","invite_code":"INV-8F2K3M"}
```

响应 `201`：

```json
{
  "user": {"id":"0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5b","email":"alice@example.com","display_name":"Alice","status":"active","created_at":"2026-11-06T01:00:00Z"},
  "workspace": {"id":"0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c","name":"我的知识库","slug":"ws-0192a1b2","role":"owner","plan":"free","created_at":"2026-11-06T01:00:00Z"},
  "session": {"expires_at":"2026-12-06T01:00:00Z","active_workspace_id":"0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c","csrf_token":"8f3c2a91d4e5b6c7"}
}
```

错误（邀请码已用）：

```json
{
  "error": {"code":"AUTH_INVITE_USED","message":"该邀请码已被使用","details":{"invite_code":"INV-8F2K3M"},"retryable":false},
  "request_id": "req_01J9Z3K4M5N6P7Q8R9S0T1V2W3"
}
```

### 11.2 登录失败限流

```http
POST /api/v1/auth/login HTTP/1.1
Content-Type: application/json
X-CSRF-Token: 8f3c2a91d4e5b6c7

{"email":"alice@example.com","password":"wrong-password"}
```

响应 `429`：

```json
{
  "error": {"code":"AUTH_ACCOUNT_LOCKED","message":"尝试次数过多，请在 15 分钟后重试","details":{"locked_until":"2026-11-06T01:15:00Z"},"retryable":true},
  "request_id": "req_01J9Z4A1B2C3D4E5F6G7H8J9K0"
}
```

响应头：`Retry-After: 900`、`X-RateLimit-Limit: 5`、`X-RateLimit-Remaining: 0`、`X-RateLimit-Reset: 1793940900`。

### 11.3 导入 ZIP 并轮询进度

```http
POST /api/v1/ingest/zip HTTP/1.1
Content-Type: multipart/form-data; boundary=----X
Idempotency-Key: 0192a1b2-1111-7222-8333-444455556666

------X
Content-Disposition: form-data; name="file"; filename="vault.zip"
Content-Type: application/zip

<binary>
------X--
```

响应 `202`：

```json
{"job":{"id":"0192a1b2-aaaa-7bbb-8ccc-ddddeeeeffff","kind":"zip","status":"queued","progress":{"total_files":0,"processed_files":0,"failed_files":0,"skipped_files":0},"snapshot_id":null,"created_at":"2026-11-06T01:05:00Z","started_at":null,"finished_at":null}}
```

轮询 `GET /ingest/jobs/{id}` 的部分成功（`206`）：

```json
{
  "job": {"id":"0192a1b2-aaaa-7bbb-8ccc-ddddeeeeffff","kind":"zip","status":"partial","progress":{"total_files":128,"processed_files":128,"failed_files":2,"skipped_files":14},"snapshot_id":"0192a1b2-bbbb-7ccc-8ddd-eeeeffff0000","created_at":"2026-11-06T01:05:00Z","started_at":"2026-11-06T01:05:03Z","finished_at":"2026-11-06T01:07:40Z"},
  "counters": {"created":112,"updated":0,"unchanged":0,"skipped":14,"failed":2},
  "failed_items": [
    {"path":"notes/broken.md","line":null,"reason_code":"PARSE_ERROR","message":"YAML frontmatter 未闭合"},
    {"path":"notes/too-big.md","line":null,"reason_code":"FILE_TOO_LARGE","message":"单文件超过 20MB"}
  ],
  "snapshot": {"snapshot_id":"0192a1b2-bbbb-7ccc-8ddd-eeeeffff0000","commit_sha":null,"doc_count":112,"as_of":"2026-11-06T01:07:40Z","manifest_digest":"sha256:1a2b3c..."},
  "degraded": true,
  "degraded_reasons": ["ingest_partial_failed"]
}
```

### 11.4 搜索（含降级）

```http
GET /api/v1/search?q=缓存穿透&top_k=10&tags=Redis,后端&date_from=2026-01-01&source_type=both HTTP/1.1
Cookie: mylife_session=...
```

响应 `200`（`degraded`）：

```json
{
  "items": [
    {"source_type":"document","document_id":"0192a1b2-cccc-7ddd-8eee-ffff00001111","chunk_id":"0192a1b2-dddd-7eee-8fff-000011112222","review_id":null,"path":"notes/Redis/缓存穿透.md","title":"缓存穿透","heading":"什么是缓存穿透","snippet":"缓存穿透指查询一个一定不存在的数据，缓存不命中……","snippet_html":"缓存穿透指查询一个<mark>一定不存在</mark>的数据，缓存不命中……","score":0.0328,"matched_by":"lexical","rank_lexical":1,"rank_vector":null,"line_range":[12,28],"occurred_on":"2026-03-14"},
    {"source_type":"review","document_id":null,"chunk_id":null,"review_id":"0192a1b2-eeee-7fff-8000-111122223333","path":"wiki/复盘/每日复盘/2026/2026-03-14.md","title":"2026-03-14 每日复盘","heading":"学习内容摘要","snippet":"学习了 Redis 缓存穿透与布隆过滤器……","snippet_html":"学习了 Redis <mark>缓存穿透</mark>与布隆过滤器……","score":0.0164,"matched_by":"lexical","rank_lexical":2,"rank_vector":null,"line_range":[8,15],"occurred_on":"2026-03-14"}
  ],
  "total_estimated": 2,
  "degraded": true,
  "degraded_reasons": ["vector_timeout"],
  "took_ms": 287
}
```

### 11.5 chat turn 与 SSE

请求：

```http
POST /api/v1/conversations/0192a1b2-1234-7abc-8def-0123456789ab/turns HTTP/1.1
Content-Type: application/json
X-CSRF-Token: 8f3c2a91d4e5b6c7
Idempotency-Key: 0192a1b2-2222-7333-8444-555566667777

{"client_turn_id":"0192a1b2-2222-7333-8444-555566667777","message":"我最近一次学习 Redis 缓存是什么时候，学了什么？","options":{"retrieval_top_k":10,"context_window_tokens":16000}}
```

响应 `202`：

```json
{"run_id":"0192a1b2-3333-7444-8555-666677778888","message_id":"0192a1b2-4444-7555-8666-777788889999","stream":{"events_url":"/api/v1/runs/0192a1b2-3333-7444-8555-666677778888/events","epoch":7,"after":0}}
```

随后 SSE 帧见 §6.6。

### 11.6 `create_review` 预览与确认

SSE 中的预览结果（`tool.result` 别名 `chat.tool_result`）：

```json
{
  "seq": 4,
  "tool_name": "create_review",
  "status": "preview",
  "result": {
    "preview": {"kind":"daily","period_key":"2026-10-27","title":"2026-10-27 每日复盘","path":"wiki/复盘/每日复盘/2026/2026-10-27.md","estimated_evidence_count":37,"estimated_tokens":{"input":42000,"output":9000}},
    "confirmation_token": "crt_0192a1b2.eyJ3cyI6...",
    "expires_at": "2026-11-06T01:20:00Z",
    "blocked_reason": null
  }
}
```

用户确认后调用：

```http
POST /api/v1/reviews HTTP/1.1
Content-Type: application/json
X-CSRF-Token: 8f3c2a91d4e5b6c7
Idempotency-Key: 0192a1b2-5555-7666-8777-888899990000

{"kind":"daily","period_key":"2026-10-27","timezone":"Asia/Shanghai","trigger":"manual","options":{"max_revisions":2,"require_human":true,"budget":{"max_input_tokens":100000,"max_output_tokens":20000}}}
```

响应 `201`：

```json
{"run_id":"0192a1b2-6666-7777-8888-999900001111","review_id":"0192a1b2-7777-8888-9999-000011112222","state":"created","existing_run_id":null}
```

若已有活跃 run：

```json
{
  "error": {"code":"ACTIVE_RUN_EXISTS","message":"该周期已有正在生成的复盘","details":{"existing_run_id":"0192a1b2-6666-7777-8888-999900001111","state":"drafting","started_at":"2026-11-06T01:10:00Z"},"retryable":false},
  "request_id": "req_01J9Z5B2C3D4E5F6G7H8J9K0L1"
}
```

### 11.7 人工门审批

```http
POST /api/v1/runs/0192a1b2-6666-7777-8888-999900001111/approve HTTP/1.1
Content-Type: application/json
X-CSRF-Token: 8f3c2a91d4e5b6c7

{"decision_note":"第 4 节代价描述可更具体，但引用完整，予以通过"}
```

响应 `202`：

```json
{"run_id":"0192a1b2-6666-7777-8888-999900001111","state":"delivering","delivery_id":"0192a1b2-8888-9999-0000-111122223333"}
```

安全终止时（不可审批）：

```json
{
  "error": {"code":"SECURITY_ABORT","message":"该运行因引用越权被安全终止，不可人工放行","details":{"reason":"TENANT_LEAK","trace_id":"tr_0192a1b2"},"retryable":false},
  "request_id": "req_01J9Z5C3D4E5F6G7H8J9K0L1M2"
}
```

### 11.8 PR 交付记录

`GET /api/v1/deliveries/{id}` 响应 `200`：

```json
{
  "delivery": {"id":"0192a1b2-8888-9999-0000-111122223333","review_id":"0192a1b2-7777-8888-9999-000011112222","version":2,"target":"git","provider":"github","branch":"review/daily/2026-10-27","commit_sha":"a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0","pr_number":128,"pr_url":"https://github.com/alice/mylife-output/pull/128","status":"merged","merged_commit":"f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d","degraded":false,"created_at":"2026-11-06T01:32:00Z","updated_at":"2026-11-06T01:40:12Z"},
  "manifest": {
    "branch": "review/daily/2026-10-27",
    "targets": [
      {"kind":"git","path":"每日复盘/2026/2026-10-27.md","content_hash":"sha256:7f8e9d...","mode":"update_allowed"},
      {"kind":"git","path":"专题/错误清单.md","content_hash":"sha256:1c2d3e...","mode":"append_only"},
      {"kind":"git","path":"专题/正确方法论.md","content_hash":"sha256:4a5b6c...","mode":"append_only"},
      {"kind":"git","path":"专题/行动清单.md","content_hash":"sha256:9d0e1f...","mode":"append_only"},
      {"kind":"git","path":"索引.md","content_hash":"sha256:2e3f4a...","mode":"update_allowed"}
    ],
    "commit_message": "chore(review): 生成 2026-10-27 每日复盘 (v2)\n\nRun-Id: 0192a1b2-6666-7777-8888-999900001111\nReview-Id: 0192a1b2-7777-8888-9999-000011112222\nVersion: 2\nWorkspace: 0192a1b2-c3d4-7e5f-8a9b-0c1d2e3f4a5c",
    "pr": {"title":"[复盘] 2026-10-27 每日复盘 (v2)","body_md":"## 30 秒结论\n...\n## GateResult\nAPPROVE (0 blocking, 2 warnings)","base_branch":"main","head_branch":"review/daily/2026-10-27","draft":false,"auto_merge":false},
    "review_row": {"review_id":"0192a1b2-7777-8888-9999-000011112222","version":2,"content_hash":"sha256:7f8e9d...","git_path":"wiki/复盘/每日复盘/2026/2026-10-27.md"}
  },
  "diff_summary": {"files_added":1,"files_modified":4,"files_unchanged":0}
}
```

### 11.9 预算硬停

```json
{
  "error": {"code":"BUDGET_EXCEEDED","message":"本次生成的 token 预算已用尽，任务已终止","details":{"scope":"run","used":{"input_tokens":100240,"output_tokens":19880},"limit":{"input_tokens":100000,"output_tokens":20000},"resets_at":null,"run_id":"0192a1b2-6666-7777-8888-999900001111"},"retryable":false},
  "request_id": "req_01J9Z6D4E5F6G7H8J9K0L1M2N3"
}
```

### 11.10 跨租户访问（统一 404）

```http
GET /api/v1/documents/0192a1b2-dead-7beef-8cafe-000000000000 HTTP/1.1
Cookie: mylife_session=<belongs to workspace A>
```

响应 `404`：

```json
{
  "error": {"code":"NOT_FOUND","message":"文档不存在","details":null,"retryable":false},
  "request_id": "req_01J9Z6E5F6G7H8J9K0L1M2N3P4"
}
```

服务端同时写审计 `audit_logs(action=access.cross_tenant_denied, target=document:{id})` 并递增 `tenant_leak_attempt_total` 指标（不暴露给调用方）。

### 11.11 webhook（PR 合并）

```http
POST /api/v1/webhooks/git/github HTTP/1.1
Content-Type: application/json
X-Hub-Signature-256: sha256=5d41402abc4b2a76b9719d911017c592...
X-GitHub-Delivery: 72d3162e-cc78-11e3-81ab-4c9367dc0958
X-GitHub-Event: pull_request

{"action":"closed","pull_request":{"number":128,"merged":true,"merge_commit_sha":"f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d","base":{"ref":"main"},"head":{"ref":"review/daily/2026-10-27"}}}
```

响应 `200`：

```json
{"received":true,"delivery_id":"72d3162e-cc78-11e3-81ab-4c9367dc0958","handled":true}
```

重复投递：同 `delivery_id` 再次到达，返回 `200` + `{"received":true,"delivery_id":"...","handled":false}`。

### 11.12 导出与删除

`POST /api/v1/exports` 响应 `202`：

```json
{"export_id":"0192a1b2-aaaa-7000-8111-222233334444","status":"queued","expires_at":"2026-11-13T01:00:00Z"}
```

`POST /api/v1/deletions` 响应 `201`：

```json
{
  "deletion_id":"0192a1b2-bbbb-7000-8222-333344445555",
  "state":"cooling",
  "purge_at":"2026-12-06T01:00:00Z",
  "cancellable_until":"2026-12-06T01:00:00Z"
}
```

### 11.13 SSE 断线重连（epoch 不匹配）

```http
GET /api/v1/runs/0192a1b2-3333-7444-8555-666677778888/events?after=88&epoch=6 HTTP/1.1
```

服务端发：

```
id: 89
event: degraded
data: {"reason":"epoch_mismatch","scope":"stream","message":"事件流已重启，请从运行状态重建"}

```

随后关闭连接；客户端应调用 `GET /runs/{id}` 重建状态，再以新 `epoch` 重连。

---

## 12. 验收清单（FR → 端点映射）

| FR | 端点 | 关键断言 |
|---|---|---|
| FR-A1 | `POST /auth/register`、`POST /auth/login` | 密码 ≥8；Argon2id；登录 429 + 锁定 |
| FR-A2 | `POST /auth/register`（`invite_code`） | 一次性码；未带码 `AUTH_INVITE_REQUIRED` |
| FR-A3 | `POST /auth/register` 响应 `workspace` | 自动创建“我的知识库”，role=owner |
| FR-A4 | `GET /workspaces`、`POST /workspaces/{id}/switch` | 切换后后续请求数据变化；`active_workspace_id` 更新 |
| FR-A5 | `GET /auth/me`（`role`、`capabilities`） | 首版 owner + platform_admin |
| FR-A6 | `POST /auth/logout`、`POST /auth/logout-all`、`GET /auth/me` | HttpOnly Cookie；`token_version+1` 后旧 token 401 |
| FR-A7 | `GET /admin/audit-logs` | 登录/导入/删除/配置/复盘五类事件可查 |
| FR-B1 | `POST /ingest/git`、`POST /ingest/sync`、`GET /ingest/sources` | HTTPS/SSH、分支选择、手动同步、定时拉取 |
| FR-B2 | `POST /ingest/zip`、`GET /ingest/jobs/{id}` | 200MB 上限、进度可见 |
| FR-B3 | `POST /ingest/zip`（`GET job.failed_items`） | 忽略 `.obsidian/` 等；`skipped_count` 可见 |
| FR-B4 | `GET /documents/{id}`（`frontmatter`、`tags`、`links`） | frontmatter/标题/标签/wikilink 解析 |
| FR-B5 | `POST /ingest/sync`（`counters.unchanged`） | 未变化文件不重复解析/embedding |
| FR-B6 | `GET /documents/tree`、`GET /documents/{id}`（`content_html`） | 目录结构、渲染、代码块、表格、图片 |
| FR-B7 | `GET /documents/{id}/meta` | 路径/标题/标签/大小/更新时间/哈希/workspace |
| FR-B8 | `GET /ingest/jobs/{id}`、`POST /ingest/jobs/{id}/retry` | 排队/处理/成功/失败/跳过；失败可重试 |
| FR-C1 | `GET /search`（`mode=hybrid`） | 词法+向量合并、统一排序 |
| FR-C2 | `GET /search` | 中文查询命中（jieba 两段式） |
| FR-C3 | `GET /search`（`snippet_html`） | 标题/路径/片段/`<mark>` 高亮 |
| FR-C4 | `GET /search`（`path_prefix`/`tags`/`date_from`/`extensions`） | 四类过滤器 |
| FR-C5 | `GET /search`（`SearchResult`） | `path/title/heading/snippet/score` 五字段 |
| FR-C6 | `GET /search`（`took_ms`） | 10k 文档 P95 < 1000ms |
| FR-D1 | `POST /conversations/{id}/turns` + SSE | 首 token 目标 <3s；流式 |
| FR-D2 | `/conversations` CRUD + `GET .../messages` | 多轮持久化、新建/重命名/删除 |
| FR-D3 | `Message.references`、`chat.citation` | 引用路径+片段；正文 `[n]` |
| FR-D4 | SSE `chat.tool_call`/`chat.tool_result`、`ToolGateway` | 六工具可调 |
| FR-D5 | `Message.tool_events`、`GET /runs/{id}`（`steps`） | 名称/参数摘要/状态/耗时 |
| FR-D6 | `chat.done`、`Message.content` | 无依据返回哨兵，不编造 |
| FR-D7 | `ToolGateway` 入参无 `workspace_id` + 跨租户测试 | 服务端注入，LLM 不可指定 |
| FR-D8 | `GET /settings/usage`、`Message.usage` | prompt/completion/model/耗时入库 |
| FR-D9 | `GET/PUT /settings/retrieval`、`RunOptions` | 检索条数/上下文窗口/Web 开关（首版关闭） |
| FR-E1 | `GET /dashboard/tasks`、`list_tasks` | 复选框唯一来源；`10.27.md` 解析 |
| FR-E2 | `TaskItem`（`first_seen`/`last_seen`/`done_date`/`source_path`/`line_number`） | 台账字段完整 |
| FR-E3 | `GET /dashboard/tasks`（`carried`） | 顺延与回填 |
| FR-E4 | `POST /reviews`（日记目录匹配） | 按日期匹配日记 |
| FR-E5 | `GET /reviews/{id}`（`learning_summary`） | 主题/概念/产出/待解决问题 |
| FR-E6 | `POST /reviews`（daily）+ `ReviewDetail.sections` | 7 章齐全（含一句话总结） |
| FR-E7 | `POST /reviews`（weekly）+ `completion_rate` | 7 天汇总、完成率与台账一致 |
| FR-E8 | `POST /reviews`（yearly）+ 5 章 | 分层聚合、错误 Top10 |
| FR-E9 | `GET /reviews/{id}`（`git_path`）、`DeliveryTarget.path` | `wiki/复盘/...` 中文目录 |
| FR-E10 | `GET /topics`、`GET /topics/{topic}/items` | 三专题页 |
| FR-E11 | `ReviewDetail.citations`、`ReviewEntry` | 日期+路径+行号；无证据不写入 |
| FR-E12 | `POST /reviews` | 指定日期/周/年手动触发 |
| FR-E13 | `GET/PUT /settings/schedule` | 时区与生成时间；默认 01:00/01:30/02:00 |
| FR-E14 | `POST /reviews`（同 period）、`GET /reviews/{id}/versions` | 覆盖同路径、version+1、历史保留 |
| FR-F1 | `GET /dashboard/latest` | 最新日报/周报/年报 + 完成率 + 未完成数 |
| FR-F2 | `GET /dashboard/tasks` | 未完成 Todo + 首次出现 + 持续天数 |
| FR-F3 | `GET /dashboard/calendar` | 按日/周/年浏览 |
| FR-F4 | `GET /search`（`source_type=both`） | 笔记 + 复盘独立索引 |
| FR-G1 | `GET/PUT /settings/llm`、`POST /settings/llm/test` | 兼容接口、加密存储、测试连接 |
| FR-G2 | `GET/POST /settings/embedding*` | 模型与维度可配；蓝绿换维 |
| FR-G3 | `GET/PUT /settings/schedule` | 时间与时区可改 |
| FR-G4 | `GET/PUT /settings/git` | 仓库/分支/凭据/频率 |
| FR-G5 | `GET /settings/usage` | token/次数/复盘次数 |
| FR-G6 | `/admin/*` | 用户/Workspace/邀请码/禁用/系统配置 |
| FR-G7 | `POST /exports`、`POST /deletions`、`POST /deletions/{id}/cancel` | ZIP 导出；二次确认；30 天冷却 |
| NFR-1 | 全部端点 + 跨租户测试 | 跨租户统一 404，0 行 |
| NFR-2 | `POST /auth/login`、webhook 签名、ZIP 校验 | 限流、CSRF、签名、大小限制 |
| NFR-4 | `GET /runs/{id}`、SSE `after` | 重试、幂等、失败可见、可续跑 |
| NFR-5 | `GET /runs/{id}`、SSE | 结构化轨迹与事件可回放 |
| NFR-9 | `POST /exports`、`POST /deletions` | 导出全部数据、删除账号 |

覆盖率检查：REQUIREMENTS §5 共 55 条 FR（A1–A7、B1–B8、C1–C6、D1–D9、E1–E14、F1–F4、G1–G7），上表逐条覆盖，无遗漏。

---

## 13. 开放问题

| # | 问题 | 影响 | 建议 |
|---|---|---|---|
| 1 | `ARCHITECTURE.md` §4.3 索引 2 的 chat 活跃态含 `verifying`，而 §4.6 状态链为 `RETRIEVING → DRAFTING → VERIFYING → SUCCEEDED`，两处一致但 §4.1 状态表未列 `VERIFYING` | 状态机实现与 `is_active_state` 函数需明确 | 在架构 v0.5 的 §4.1 状态表补 `VERIFYING`（仅 chat，超时 60s，最大尝试 1）；本文 §7.1 已按 §4.6 处理 |
| 2 | `section_keys` 在 `RetrievalTask` 与 `DraftSection` 中的命名：架构 §5 只给出 `SectionId` 字面量集合，未定义子模型的键名 | 契约包字段命名 | 本文采用 `section_key`（与架构 §5.1 表列名一致）；需架构 v0.5 确认 |
| 3 | chat 的 `RunOptions.budget` 默认值未在架构 §5 给出（架构 §9.4 只定义复盘预算） | 单 turn 预算与 `BUDGET_EXCEEDED` 行为 | 建议 chat 默认 `max_input_tokens=32000`、`max_output_tokens=4000`（含 4 轮工具），需产品确认 |
| 4 | `platform_admin` 能否读取用户复盘正文（当前明确禁止）与 FR-G6“查看 Workspace 使用量”的边界 | 管理后台功能范围 | 本文按架构 §1.4.1 边界取“仅元数据与聚合”；若需正文需改架构并加审计 |
| 5 | `data/reviews` 兼容读取的 API 表达：本文未提供独立端点，仅通过 `GET /reviews` 与 `GET /documents` 统一呈现 | 迁移期用户可见性 | 建议在 `GET /reviews` 增加 `source_root` 只读字段（`wiki`/`data`），需架构 v0.5 确认 |

---

## 14. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-06 | 首版：通用约定、错误码表（约 90 个码）、端点契约、Agent 数据契约、ToolGateway、SSE、状态机事件、限流配额、OpenAPI 生成、兼容策略、13 组示例、FR 映射 |




