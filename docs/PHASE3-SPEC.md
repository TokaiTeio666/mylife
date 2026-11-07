# Phase 3 施工规格（复盘运行时）

> 版本：v1.0 ｜ 日期：2026-11-07 ｜ 阶段：Phase 3 / M4
>
> 本文是 Phase 3 的**施工级规格**：把设计文档中的门禁条款提取为可直接实现、可测试的条目，
> 并登记文档冲突与实现裁决。设计语义仍以 `ARCHITECTURE.md` / `API-CONTRACTS.md` /
> `DATA-MODEL.md` / `ACCEPTANCE-CRITERIA.md` / `SECURITY-BOUNDARY.md` 为准；本文只做
> “落到哪个文件、哪个函数、断言是什么”。
>
> 施工约定：**每个小节（§1–§12）独立提交**，不把多个小节混进同一 commit。

---

## 0. 范围、门禁与验收口径

### 0.1 本阶段门禁（硬门）

| 类别 | 清单 |
|---|---|
| AC | AC-25（create_review 确认协议）、AC-31…AC-38、AC-41、AC-42、AC-60、AC-66、AC-68 |
| SEC | SEC-16、SEC-17、SEC-23 |
| FX | FX-01、FX-02、FX-03、FX-04、FX-10、FX-12 |
| 质量 | 引用覆盖率 100%；章节完整率 100%；FX-01 同快照重跑 diff ≤10%；每阶段抽样 20 条人工复核（≥98%） |

### 0.2 明确不在 Phase 3 范围

| 项 | 原因 |
|---|---|
| Git 分支/commit/PR 与 `delivery_records` 回写 | Phase 4（ARCHITECTURE §13）；Phase 3 的 `DELIVERING` 只做本地/对象存储落盘 + DB 登记 |
| 专题页 md 渲染、`索引.md`、`GIT_MARKER_BROKEN` | Phase 4；Phase 3 只落 `topic_items/topic_occurrences` 与确认端点（AC-68） |
| 仪表盘、日历、用量页 | Phase 4/5（AC-45…AC-48 属 Phase 4） |
| `review_search` 的查询接入搜索页 | Phase 4（AC-48）；Phase 3 建表并随版本写入，保证 `source_type=review` 未来可用 |
| 定时调度 `workspace_schedules.next_run_at` | Phase 4（AC-43） |
| 回滚产物交付 | Phase 4；Phase 3 只保留版本指针语义（AC-42 只要求“旧 version 仍可查”） |

### 0.3 验收证据形态

| 证据 | 位置 |
|---|---|
| AC 逐条测试 | `apps/api/tests/test_review_*.py`、`test_task_ledger.py`、`test_fixture_fx*.py` |
| FX report | `docs/evidence/phase3/fx_*.json` |
| 运行可回放 | `scripts/verify_replay.py` 输出 |
| 质量抽样 | `docs/evidence/phase3/sampling.md`（人工，20 条/阶段） |

---

## 1. 文档冲突裁决（实施前需确认）

> 规则：以下裁决是“文档未写死、但实现必须二选一”的点。每条给出推荐值；若评审不同意，
> 需先改文档再改代码，避免实现与验收口径漂移。

| # | 冲突/空白 | 出处 | 裁决（推荐） | 影响 |
|---|---|---|---|---|
| D1 | `reviews.status` 取值：DB CHECK 为 `draft/in_review/published/superseded/manual`，API 为 `generating/needs_human/approved/delivered/failed/security_abort/superseded` | DATA-MODEL §3.8 vs API-CONTRACTS §3.7.2 | 迁移把 CHECK 扩为**并集**；服务端只写 API 七值：准入 `generating`、人工门 `needs_human`、人工通过 `approved`、本地交付完成 `delivered`、失败 `failed`、安全终止 `security_abort`；旧值保留仅为兼容读 | AC-42、AC-36/37/38 的 `ReviewSummary.status` |
| D2 | `is_active_state` 含 `needs_human`，但 API §7.1 把 `needs_human` 列为终态 | migrations 0003 vs API-CONTRACTS §7.1 | 以 API §7.1 为准：创建迁移 0004 `CREATE OR REPLACE FUNCTION is_active_state`，**移除 `needs_human`**。活跃态 = `created,snapshotting,planning,retrieving,drafting,reviewing,revising,human_gate,delivering,retry_wait` | AC-42 409、FX-12 并发、SSE 关闭 |
| D3 | `reviews` 是“每版本一行”还是“每 period 一行 + version 指针” | DATA-MODEL §3.8 `UNIQUE(ws,kind,period,version)` + `superseded_by uuid` vs DATA-MODEL §7.1 “回滚按 version 指针” + API `versions[]` | **每 (workspace,kind,period_key) 一行**；`reviews.version` 是当前指针；历史在 `review_versions`；`superseded_by` 保留 NULL 不用。`UNIQUE(ws,kind,period,version)` 仍成立（单行更新 version） | AC-42、AC-44、回滚 |
| D4 | 预算累计无存储落点；`usage_events` 无 `run_id` | ARCHITECTURE §9.4、API §4.4 未给落点 | 迁移 0004：`usage_events` 增 `run_id uuid`、`attempt_id uuid`、`role text`、索引 `(workspace_id, run_id, created_at)`；run 预算 = `SUM` 聚合。**不**写 `state_payload`（整块 JSON 覆盖会丢计数） | AC-66、FX-10、AC-29 |
| D5 | SEC-16/17 要求 Phase 3 通过，但 webhook 回写依赖 Phase 4 的 `delivery_records` | SECURITY-BOUNDARY §11 vs ARCHITECTURE §13 | Phase 3 实现**验签 + `delivery_events` 幂等落库 + 无业务副作用**；不实现 `delivery_records` 回写。`pull_request.merged` 事件在 Phase 3 只记 `status=ignored`，Phase 4 再回写 | SEC-16/17 |
| D6 | `POST /reviews` 请求体没有 `confirmation_token` 字段，无法证明“已 confirm” | AC-25 vs API-CONTRACTS §3.7.1/§5.7.2 | 请求体增加**可选** `confirmation_token: string`（`extra=forbid` 仍拒绝其它未知字段）；有 token 时校验签名/TTL/未消费；无 token 时视为人工显式确认（手动触发）。登记 `IMPLEMENTATION-NOTES §2C` | AC-25 |
| D7 | §5.1 只给中文必填字段名，未给英文键名与类型；`SECTION_SCHEMAS`/`ITEM_SCHEMAS` 未定义 | ARCHITECTURE §5.1/§9.1 | 采用 §5 的 `SECTION_SCHEMAS`/`ITEM_SCHEMAS`（详见 §5.2/§5.3），字段名与类型**在此冻结**；`review-profile.yml` 是镜像，启动时逐项校验 | AC-36/37/38、G1/G2 |
| D8 | 复盘执行位置文档写 worker-review（Celery），但 Phase 3 无该 worker 容器 | ARCHITECTURE §11/§13 | Phase 3 用 **API 进程内 `asyncio`**（与 chat 同模式，`review_runner.py`），持久化事件与状态不变；Phase 4 迁 worker-review。登记 `IMPLEMENTATION-NOTES §2C` | 全部复盘 run |
| D9 | `RunOptions.require_human` 默认 true，但 AC-42 要求 daily 成功自动完成 | ARCHITECTURE §5/§4.5 | 语义收敛为：`yearly` 默认进 `human_gate`；`daily/weekly` 在 G0–G9 无 blocking 时直接 `delivering`；`require_human=true` 仅对 `yearly` 生效（或 gate 失败升级人工） | AC-36/37/38/42 |
| D10 | `carried` 在 REQUIREMENTS §6.1 列为状态，DATA-MODEL §10.2 用查询派生 | REQUIREMENTS §6.1 vs DATA-MODEL §10.2 | `tasks.status` 只写 `open/done`；`carried` 一律按 `status='open' AND first_seen < period_start` 派生；`dropped` Phase 3 不写 | AC-32/33 |
| D11 | `tasks.key` 是归一化原文还是 sha256 | REQUIREMENTS §6.1 vs DATA-MODEL §3.7/§7.3 | 取**归一化原文**（`normalize_task_text`），便于人工排障；`canonical_key` 仅用于 `review_entries`/专题 | AC-31/32 |
| D12 | `coverage_gaps` 无存储落点 | ARCHITECTURE §9.1 G8、AC-37/38 | 存在 `review_sections.structured_json.coverage_gaps`（weekly/yearly 对应章节）并同步写一条 `run_events`（`event=review.coverage_gap`）；不新增表 | AC-37/38、FX-02/03 |
| D13 | `reviews` 缺 ReviewDetail 所需列（`snapshot_id`/`degraded`/`degraded_reasons`/`verdict`） | API-CONTRACTS §3.7.3 vs DATA-MODEL §3.8 | 迁移 0004 增列：`snapshot_id uuid`、`degraded boolean default false`、`degraded_reasons text[] default '{}'`、`verdict_json jsonb` | AC-36…38 |
| D14 | `run_events` 缺 `stream_epoch`（API §7.2 要求） | API-CONTRACTS §7.2 vs migrations 0003 | 迁移 0004 增 `stream_epoch int`；写入时取 `runtime.STREAM_EPOCH` | AC-60、SSE |
| D15 | `documents` 无日期冲突 warning 落点 | AC-34 只说“记 warning” | 迁移 0004 增 `documents.parse_warnings jsonb default '[]'`，并写结构化日志 + `job_runs.counters_json.date_conflicts` | AC-34 |
| D16 | Fixture 目录规范写仓库根 `tests/fixtures/`，但 pytest `testpaths=apps/api/tests` | ACCEPTANCE §4.1 vs apps/api/pyproject.toml | 落在 `apps/api/tests/fixtures/<fx>/`，并在 `pyproject.toml` 增加 `fixture` marker；登记偏差 | FX-01…04/10/12 |
| D17 | `topic_items.topic` DB CHECK 为 `errors/methods/actions`，API 用中文 `错误清单/正确方法论/行动清单` | DATA-MODEL §4.14 vs API-CONTRACTS §3.7.13 | DB 保持英文枚举；API 层做双向映射（中文↔英文），未知值 404/422 | AC-68 |
| D18 | `review_entries` 行号列：DATA-MODEL 用 `line_start/line_end`，API 用 `line_range` | DATA-MODEL §4.10 vs API §3.7.3 | DB 用 `line_start/line_end`；API 序列化为 `line_range:[start,end]` | AC-41 |
| D19 | `estimated_tokens`：contracts 现为 `int`，§11.6 示例为 `{input,output}` | API §5.7.1/§11.6 vs `tools.py` | 改成对象 `{input:int,output:int}`；preview 同时与 kind 默认预算比较，超限置 `blocked_reason="BUDGET_EXCEEDED"` | AC-25 |
| D20 | `ReviewDetail.superseded_by` 为 int（版本号），DB 为 uuid | API §3.7.3 vs DATA-MODEL §3.8 | 采用 D3 后该字段恒为 `null`（单行指针模型）；Phase 4 需要时再补 | AC-42 |

---

## 2. 数据模型增量（迁移 `0004_phase3_review`）

### 2.1 新建表

全部表带 `workspace_id NOT NULL`，`ENABLE + FORCE RLS`，策略
`USING (workspace_id = app_workspace_id()) WITH CHECK (workspace_id = app_workspace_id())`。
`reviews` 先补 `UNIQUE (workspace_id, id)`，所有 `review_*` 子表用 `(workspace_id, review_id)`
复合外键指向它（ARCHITECTURE §6 子表复合外键要求）。

| 表 | 关键列 | 约束/索引 |
|---|---|---|
| `evidence_items` | run_id, evidence_id, snapshot_id, source_type, document_id, chunk_id, source_path, source_date, occurred_on, document_content_hash, heading, tags text[], line_start, line_end, quote, quote_hash | `UNIQUE(run_id,evidence_id)`；`INDEX(run_id,source_type)`；`CHECK(source_type<>'stats' OR source_path IS NULL)`；document/chunk/review 必须有 `document_content_hash` |
| `review_sections` | review_id, version, section_key, ordinal, content_md, structured_json | `UNIQUE(review_id,version,section_key)`、`UNIQUE(review_id,version,ordinal)` |
| `review_entries` | review_id, version, section_key, item_type, canonical_key, fields_json, source_path, line_start, line_end, evidence_id, source_date, confidence | `UNIQUE(review_id,version,section_key,item_type,canonical_key)`；`INDEX(ws,item_type,canonical_key)` |
| `review_citations` | review_id, version, claim_id, evidence_id, source_path, source_date, document_content_hash, line_start, line_end, quote, quote_hash | `UNIQUE(review_id,version,claim_id,evidence_id)` |
| `review_versions` | review_id, version, content_md, content_hash, git_sha, model, prompt_version, snapshot_id | `UNIQUE(review_id,version)` |
| `review_search` | review_id, version, kind, period_key, title, path, search_text, tsv(gen), occurred_on, content_hash, is_current | `UNIQUE(review_id,version)`；部分唯一 `(ws,kind,period_key) WHERE is_current`；`GIN(tsv)` |
| `topic_items` | topic, canonical_key, title, first_seen, last_seen, occurrence_count, status | `UNIQUE(ws,topic,canonical_key)`；status ∈ pending/open/resolved/stale |
| `topic_occurrences` | item_id, source_date, source_path, line_start, line_end, quote, evidence_id | `UNIQUE(item_id,source_date,source_path,line_start)`；append-only |
| `delivery_events` | provider_delivery_id, conn_id, event, payload_digest, status, received_at | `UNIQUE(provider_delivery_id)`；status ∈ received/handled/ignored/failed |
| `delivery_records` | review_id, version, target, provider, branch, commit_sha, pr_number, status, merged_commit | `UNIQUE(ws,review_id,version,target)`（Phase 4 才写） |
| `review_confirmations` | token_hash, workspace_id, user_id, kind, period_key, expires_at, consumed_at | `UNIQUE(token_hash)`；用于 create_review confirm 单次消费 |
| `idempotency_keys` | workspace_id, user_id, endpoint, key, request_hash, response_status, response_body, expires_at | `UNIQUE(ws,user_id,endpoint,key)` |

### 2.2 扩展列

| 表 | 列 | 说明 |
|---|---|---|
| `reviews` | `snapshot_id uuid`、`degraded boolean NOT NULL DEFAULT false`、`degraded_reasons text[] NOT NULL DEFAULT '{}'`、`verdict_json jsonb` | ReviewDetail 所需（D13） |
| `reviews` | 放宽 `status` CHECK 为并集（D1） | 不删除旧值 |
| `agent_runs` | `review_id uuid REFERENCES reviews(id)` | run → review 直连；`state_payload` 仍保留 attempt 细节 |
| `run_events` | `stream_epoch int` | AC-60 / SSE epoch（D14） |
| `usage_events` | `run_id uuid`、`attempt_id uuid`、`role text` | 预算按 run 累计（D4） |
| `documents` | `parse_warnings jsonb NOT NULL DEFAULT '[]'` | 日期冲突 warning（D15） |
| `git_connections` | `webhook_secret_ciphertext bytea` | SEC-16 验签；env `MYLIFE_WEBHOOK_SECRET` 作为兜底 |

### 2.3 函数与授权

```sql
CREATE OR REPLACE FUNCTION is_active_state(state text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
  SELECT state IN (
    'created','snapshotting','planning','retrieving','drafting','reviewing',
    'revising','human_gate','delivering','retry_wait'
  )   -- 移除 needs_human（D2）
$$;
```

- 新表 `GRANT SELECT, INSERT, UPDATE, DELETE` 给 `mylife_app`；`review_search` 的 `tsv` 生成列无需额外授权。
- `agent_runs` 三把索引保持：`uq_runs_review_active` 依赖修改后的 `is_active_state`。
- `usage_events` 新索引：`idx_usage_run (workspace_id, run_id, created_at)`。

### 2.4 迁移验收

- `alembic upgrade head` 与 `downgrade -1` 均可执行（CI 已跑生产路径）。
- 新表 RLS 矩阵加入 `apps/api/tests/test_rls_matrix.py::TENANT_TABLES`。
- `is_active_state` 的终态断言：`needs_human` 返回 false。

---

## 3. 契约增量（`packages/contracts/mylife_contract`）

### 3.1 新文件

| 文件 | 内容 |
|---|---|
| `review.py` | `ReviewCreateRequest/Response`、`ReviewSummary/ReviewDetail/ReviewSection/ReviewEntry/ReviewCitation/VersionRef`、`Plan/SectionPlan`、`EvidenceBundle/EvidenceItem/Coverage/Missing`、`Draft/DraftSection/Claim`、`ReviewVerdict/GateIssue`、`SECTION_SCHEMAS`、`ITEM_SCHEMAS`、`RollbackRequest/Response`、`ApproveRequest/RejectRequest` |
| `budget.py` | `Budget`、`BudgetUsage`、`BudgetState`；默认值见 §8.1 |
| `topics.py` | `TopicSummary`、`TopicItem`、`TopicOccurrence`、`ConfirmRequest`、`ResolveRequest` |
| `delivery.py` | `DeliveryRecord`、`DeliveryManifest`、`DeliveryTarget`、`PullRequestSpec`、`ReviewRowRef`（Phase 4 使用，先冻结契约） |
| `review_events.py` | 复盘事件常量与 payload 模型（`run.state`/`tool.call`/`tool.result`/`citation`/`degraded`/`done`/`error`/`cancelled`） |

### 3.2 现有文件修改

| 文件 | 修改 |
|---|---|
| `constants.py` | 增 `DEFAULT_BUDGET_BY_KIND`、`CHAT_DEFAULT_BUDGET`、`WORKSPACE_DAILY_DIVISOR=20`、`LEARNING_SUMMARY_FIELDS`、`PUNCT_NORMALIZATION`、`ROLE_BUDGETS`；`SENTINELS`/`DAILY_SECTIONS`/`WEEKLY_SECTIONS`/`YEARLY_SECTIONS` 已存在，不改 |
| `tools.py` | `CreateReviewArgs` 增 `confirmation_token`；新增 `CreateReviewConfirmArgs/Result`；`CreateReviewPreview.estimated_tokens` 改为 `{input,output}`；`TaskItem` 增 `occurrence_count`、`days_open` |
| `chat.py` | `RunDetail` 增 `version`、`lease_expires_at`、`budget: BudgetUsage`、`error`、`finished_at`；`ChatOptions` 增 `budget: Budget|null` |
| `enums.py` | 增 `ReviewStatus`、`DeliveryStatus`（API 值）、`GateSeverity`、`AbortReason` |
| `errors.py` | 已含所需错误码；仅补 `REVIEW_TOPIC_NEEDS_CONFIRM` 的 details 约定（不改码） |

### 3.3 契约测试

- `test_contract.py` 增：`SECTION_SCHEMAS` 覆盖全部 16 个 section_key；每个 schema `extra="forbid"`；`ITEM_SCHEMAS` 覆盖 error/method/action；`Budget` 默认值与 `REVISION_LIMIT` 一致；`ReviewStatus` 七值。
- 所有请求模型继承 `ApiModel`（拒绝 `workspace_id`）。

---

## 4. 复盘 FSM

### 4.1 状态与终态

- 复盘状态：`created → snapshotting → planning → retrieving → drafting → reviewing`，之后按门禁分流。
- 终态集合（API §7.1）：`succeeded`、`failed`、`cancelled`、`needs_human`、`security_abort`。
- chat 不进入 `snapshotting/planning/reviewing/revising/human_gate/delivering`；review 不进入 `verifying`。

### 4.2 合法迁移与 reason

| 迁移 | reason | 守卫 |
|---|---|---|
| `created→snapshotting` | `admit_ok` | 幂等键无活跃 run；workspace 活跃复盘 run ≤2 |
| `snapshotting→planning` | `snapshot_ok` | 快照存在且 `source_snapshots.id` 可用；`index_lag ≤1800s` |
| `planning→retrieving` | `plan_valid` | Plan 的 section 集合与 ordinal 等于常量；预算内 |
| `retrieving→drafting` | `quorum_ok` / `quorum_partial_degraded` | required task 成功或已标 degraded |
| `drafting→reviewing` | `draft_schema_ok` | Draft schema 合法；未知 evidence_id = 0；行动 `- [ ]` |
| `reviewing→delivering` | `gate_pass` | 无 blocking 且 `kind != yearly`（D9） |
| `reviewing→human_gate` | `yearly_default` / `reviewer_unavailable` / `revision_exhausted` / `conflict` | yearly 或门禁无法判定 |
| `reviewing→revising` | `blocking_found` | round < REVISION_LIMIT 且指纹变化且 blocker 数下降 |
| `revising→drafting` | `revised` | draft_version+1 |
| `revising→retrieving` | `missing_evidence` | G3b `EVIDENCE_STALE` 定向补检索，计一轮 |
| `human_gate→delivering` | `human_approved` | 审批人 ∈ workspace 且 role ∈ {owner,admin}；带 `edited_content_md` 时重跑 G0–G9 |
| `human_gate→cancelled` | `human_rejected` | — |
| `delivering→succeeded` | `local_only` / `noop` | 本地渲染与 DB 登记成功 |
| `delivering→retry_wait` | `transient_failure` | — |
| `retry_wait→delivering` | `retry` | 退避到期 |
| `→needs_human` | `schema_retry_exhausted` / `required_missing` / `attempts_exhausted` / `gate_unavailable` | — |
| `→security_abort` | `tenant_leak` / `fabrication` | 不可人工放行 |
| `→failed` | `budget_exceeded` / `unhandled` | — |
| `→cancelled` | `user_cancel` | review 仅 `created/snapshotting/planning` 可取消 |

### 4.3 超时与尝试

| 状态 | 超时 | 最大尝试 |
|---|---|---|
| created | 30s | 1 |
| snapshotting | 90s | 3 |
| planning | 180s | 2（schema 修复） |
| retrieving | 420s | 3 |
| drafting | 240s | 2 |
| reviewing | 180s | 2 |
| revising | 180s | 1 |
| delivering | 180s | 3 |
| retry_wait | 60s | 3 |

### 4.4 准入协议（`POST /reviews`）

顺序固定：

1. 校验 `kind/period_key/timezone/options/allow_empty`；`max_revisions > REVISION_LIMIT[kind]` → 422。
2. 幂等键（`Idempotency-Key`，作用域 `(ws,user,endpoint,key)`，24h）：同键同 hash → 返回首次响应 + `Idempotent-Replayed: true`；同键不同 hash → 409 `IDEMPOTENCY_KEY_CONFLICT`。
3. `pg_try_advisory_xact_lock(hashtextextended(ws||kind||period_key,0))`。
4. 同 period 活跃 run → 409 `ACTIVE_RUN_EXISTS`，`details.existing_run_id` 必填。
5. workspace 活跃复盘 run ≥2 → 409 `ACTIVE_RUN_EXISTS`（返回最早活跃 run）。
6. 已有 `succeeded` → `version = max(review_versions.version)+1`，返回 200；否则 `version=1`，返回 201。
7. 无素材且 `allow_empty=false` → 422 `REVIEW_NO_SOURCE_DATA`（`details.reason="no_documents_no_tasks_no_journal"`）。
8. 插入 `reviews`（status=`generating`，`content=''` 占位以满足 `NOT NULL`）与 `agent_runs`
   （state=`created`，`review_id` 回填）；提交后启动 `review_runner`。一次成功准入恰好新增
   1 行 `reviews` 与 1 行 `agent_runs`。
9. 写 `audit_logs(action="review.created")`；`create_review` 被拒写 `review.create_rejected`。

### 4.5 租约、CAS 与续跑

- 准入时写 `lease_owner = "{hostname}:{pid}:{uuid4}"`、`lease_expires_at = now()+90s`。
- 每次迁移：`UPDATE agent_runs SET state=:to, lease_expires_at=now()+90s WHERE id=:id AND state=:from AND lease_owner=:owner RETURNING id`；返回 0 行 → 放弃本次迁移，不写事件。
- **长状态必须续租**：`retrieving(420s)` 等超时远大于 90s 租约，runner 在租约剩余 ≤45s 时执行
  `UPDATE ... SET lease_expires_at=now()+90s WHERE id=:id AND lease_owner=:owner AND state=:current`；
  `human_gate` 不持有租约（`lease_expires_at=NULL`）。
- 状态迁移与 `run_events` 写在同一事务。
- sweeper：每 60s 扫描 `is_active_state(state) AND state NOT IN ('human_gate','needs_human')
  AND lease_expires_at < now()`，逐行 CAS 到 `retry_wait`，写 `retry_from_state`、新 `attempt_id`、
  清空租约，并写 `run.state`（reason=`lease_expired`）。Phase 3 由 API 进程内定时任务承担，
  Phase 4 迁 Celery beat。
- 退避 1s/4s/16s，上限 60s；`attempts` 计数存 `state_payload.attempts`。
- `attempt_id` 每次重试新值，不参与去重。

### 4.6 修订循环

- `REVISION_LIMIT = {daily:2, weekly:2, yearly:3}`。
- 继续修订必须**同时**满足：`round < limit`、违规集合指纹变化、blocking 数下降；否则 `ESCALATE → human_gate`。
- `fingerprint = sha256(排序后的 (gate,code,section_key,claim_id,evidence_id))`。

### 4.7 人工门

- 审批人必须是该 workspace 的 `owner` 或 `admin`（`workspace_members` 校验）；`platform_admin` 代操作写审计并二次确认。
- `approve` 带 `edited_content_md`：服务端重跑 G0–G9；仍有 blocking → 422 `REVIEW_GATE_FAILED`，不推进。
- `reject`：`reason_code ∈ {CONTENT_WRONG,CITATIONS_WRONG,TONE,NOT_NEEDED,OTHER}` → `cancelled`。
- `TENANT_LEAK`/`FABRICATION` 的 run 不可 approve（403 `SECURITY_ABORT`）。
- `human_gate` 超时 72h（259200s）→ `needs_human`（reason=`gate_unavailable`），不得自动 approve/reject。
- 进入 `human_gate`/`needs_human` 时 `state_payload` 必须含
  `{reason_code, state, draft_id, verdict_id, retryable, trace_id}`。

### 4.8 事件与可回放（AC-60）

- 每次迁移写且只写一条 `run_events`：列 `event='run.state'`，payload 含
  `{event:'state.transition', from_state, to_state, attempt_id, reason, at}`，同时写 `stream_epoch`。
- 回放校验：从 `run_events` 取 `run.state` 序列，与 `agent_runs` 状态迁移轨迹比较，必须 100% 一致且无副作用；脚本 `scripts/verify_replay.py`。
- 结构化日志：每行 `ts/level/service/trace_id/workspace_id/run_id/event`，不含正文/密钥。实现：`logging.Filter` 从 `contextvars` 注入 `trace_id`（复用 `request_id`）与 `workspace_id/run_id`（runner 设置）。

---

## 5. §5.1 章节契约

### 5.1 常量（已存在，不改）

- `SENTINELS` 16 条精确文案；`DAILY_SECTIONS` 7 / `WEEKLY_SECTIONS` 4 / `YEARLY_SECTIONS` 5（含 ordinal、中文名、单章上限）。
- `MAX_EVIDENCE_PER_RUN = 80`。
- 单章上限：daily 每章 12（`one_line_summary` 为 1）；weekly 每章 12；yearly 每章 20（`next_year_direction` 为 10）。

### 5.2 `SECTION_SCHEMAS`（冻结）

> 所有模型 `extra="forbid"`；`min_length=1` 表示非空字符串。

**daily**

| section_key | 模型字段 |
|---|---|
| `today_tasks` | `completed: list[TaskRef]`、`open: list[TaskRef]`、`carried_over: list[TaskRef]`；`TaskRef{text:str, task_id:str|None, source_date:date|None, source_path:str|None, line_range:list[int]|None, evidence_id:str|None}` |
| `learning_summary` | `topic:str`、`concept_or_method:str`、`output:str`、`open_questions:str`、`field_evidence: dict[Literal['topic','concept_or_method','output','open_questions'], list[str]]`（每键 ≥1 evidence_id） |
| `journal_events` | `items: list[JournalEvent]`；`JournalEvent{event,person,decision,emotion,result}`（均 str 非空） |
| `mistakes` | `items: list[MistakeItem]`；`MistakeItem{event,root_cause,cost,correct_action,recurrence_trigger}` |
| `methods` | `items: list[MethodItem]`；`MethodItem{name,source,applicable_scenario,steps:list[str],boundaries,landing_action,verification_criteria}` |
| `tomorrow_actions` | `items: list[ActionItem]`；`ActionItem{text,trigger,verification}` |
| `one_line_summary` | `summary:str` |

**weekly**

| section_key | 模型字段 |
|---|---|
| `completion_rate` | `completed:int`、`total:int`、`rate:float`、`ledger_recalc: LedgerRecalc{completed,total,rate}` |
| `recurring_mistakes` | `top_n:int`、`items: list[RecurringMistake{canonical_key,occurrences,first_seen,last_seen}]` |
| `method_landing` | `items: list[MethodLanding{method,action_count,verification_status}]` |
| `next_week_actions` | `items: list[ActionItem]` |

**yearly**

| section_key | 模型字段 |
|---|---|
| `learning_map` | `topics:list[str]`、`concepts:list[str]`、`outputs:list[str]`、`related_weeks:list[str]`（元素匹配 `^\d{4}-W\d{2}$`） |
| `event_timeline` | `items: list[TimelineEvent{date,event,result}]`，按 date 升序 |
| `top_mistakes` | `items: list[TopMistake{rank,item,occurrences,cost,source}]`，rank 1..10 连续 |
| `method_system` | `items: list[MethodSystem{category,name,scenario,verification}]` |
| `next_year_direction` | `direction:str`、`basis:str`、`first_action:str` |

### 5.3 `ITEM_SCHEMAS`（冻结）

| item_type | 字段 |
|---|---|
| `error` | `event, root_cause, cost, correct_action, recurrence_trigger` |
| `method` | `name, source, applicable_scenario, steps:list[str], boundaries, landing_action, verification_criteria` |
| `action` | `text, trigger, verification` |

### 5.4 物化与指纹

- `canonical_key = sha256(item_type + "\x1f" + normalize(title | root_cause))`：
  - error 用 `root_cause`；method 用 `name`；action 用 `text`。
- `root_cause_fingerprint = sha256(normalize(root_cause))`。
- 同一 `(review_id,version)` 内按 canonical_key 幂等 upsert；跨版本只追加。
- `RECURRING_ERROR`/`RECURRING_METHOD` 只读 current version。

### 5.5 渲染

- `content_md` 由 sections 按 ordinal 升序拼装：
  frontmatter（`mylife:true, kind, period_key, review_id, version, snapshot_id, generated_at, generator`）+ 每节 `## {title}\n\n{content_md}`，节间一个空行。
- 空素材日：7 节 `content_md` 逐字等于 `SENTINELS[key]`，`claim_ids=[]`，引用 0；`structured_json` 允许为 `{}` 且跳过 G2 必填（`sentinel_used=true`）。
- `no_evidence_ratio = 使用哨兵的章节数 / 章节总数`（4 位小数）；空日 = 1.0。

### 5.6 `review-profile.yml`

- 位置：`packages/agent/review-profile.yml`。
- 内容：`sections`（三 kind 的 section_key/ordinal/title/required_fields/sentinel/max_evidence）、`record_types`、`write_modes`（中文目录→模式）、`sentinels`、`context_rules`。
- 启动时 `validate_review_profile()` 逐项比对常量，不等则抛 `StartupValidationError`（API lifespan 与 worker 启动调用）。

---

## 6. Todo 台账与日期

### 6.1 复选框文法

只扫描 frontmatter 之后的正文行：

```text
^(?P<indent>[ \t]*)(?P<bullet>[-*+])(?P<sep1>[ \t]+)\[(?P<mark>[ xX])\](?P<sep2>[ \t]+)(?P<text>\S.*?)[ \t]*$
```

- `mark ∈ {x,X}` → `checked=true`；`mark=' '` → `checked=false`。
- `[] | [  ] | [-] | [✓] | [v] | [ x]` 不识别。
- 跳过 fenced code（``` / ~~~，同 marker 成对）。
- 缩进不建立父子关系。
- 不含复选框的自然语言句**不产生任何行**（AC-31 第三条）。

### 6.2 归一化与 key

`normalize_task_text(raw)`：NFC → 去首尾空白 → 连续空白（含 U+3000）折叠为单空格 → 全角标点映射半角（，。！？；：（）【】“”‘’、）→ 仅 ASCII A-Z 小写。
`tasks.key = normalize_task_text(text)`（原文，不哈希）。
展示 `text` 保留原始大小写，仅去首尾空白并折叠空白。

### 6.3 occurrence 写入

- `occurrence_date = documents.occurred_on`；`line_number` 为 1-based 绝对行号（含 frontmatter 偏移）；`raw_text` 为 `]` 后文本。
- `occurred_on` 为 NULL 的文档不写 occurrence，`job_runs.counters_json.tasks_skipped_no_date += 1`，记 warning。
- added/modified/renamed：先 `DELETE FROM task_occurrences WHERE workspace_id=:ws AND document_id=:doc`，再批量 INSERT。
- 文档删除：先删其 occurrence，再删文档，最后删无 occurrence 的 tasks。
- 唯一键 `(workspace_id, document_id, occurrence_date, line_number)`；重复重建冲突数必须为 0。

### 6.4 `tasks` 重建（确定性）

```sql
WITH agg AS (
  SELECT task_id, min(occurrence_date) AS first_seen, max(occurrence_date) AS last_seen,
         max(occurrence_date) FILTER (WHERE checked) AS last_done
  FROM task_occurrences WHERE workspace_id = :ws GROUP BY task_id
), latest AS (
  SELECT DISTINCT ON (o.task_id) o.task_id, o.raw_text, o.checked, d.path, o.line_number
  FROM task_occurrences o JOIN documents d ON d.id = o.document_id
  WHERE o.workspace_id = :ws
  ORDER BY o.task_id, o.occurrence_date DESC, d.path ASC, o.line_number ASC
)
UPDATE tasks t
SET first_seen = agg.first_seen, last_seen = agg.last_seen,
    status = CASE WHEN latest.checked THEN 'done' ELSE 'open' END,
    done_date = CASE WHEN latest.checked THEN agg.last_done ELSE NULL END,
    text = latest.raw_text, source_path = latest.path, line_number = latest.line_number
FROM agg JOIN latest USING (task_id)
WHERE t.id = agg.task_id AND t.workspace_id = :ws;
```

新 key 先 `INSERT ... ON CONFLICT (workspace_id,key) DO NOTHING`，再执行上述 UPDATE。

### 6.5 状态派生

- `tasks.status` 只写 `open/done`；`carried` 不落库：
  `status='open' AND first_seen < :period_start`（DATA-MODEL §10.2）。
- `dropped` Phase 3 不写（无配置天数）。
- AC-33 推演：D0(10-26) open → D1(10-27) 仍 open 且计入“以往顺延” → D2(10-28) 勾选后 done，`done_date=10-28`，从逾期移除；未完成计数 D0=1、D1=1、D2=0（单调不降）。

### 6.6 日期解析

优先级（严格）：

1. frontmatter 显式日期：`date > occurred_on > created > created_at`；
2. 目录段完整日期（最近的 `^\d{4}[-_.]\d{1,2}[-_.]\d{1,2}$`）；
3. 文件名为完整日期；
4. 文件名短日期 `^\d{1,2}\.\d{1,2}$` + 最近祖先年份段 `^(19|20)\d{2}$`。

- 非法候选跳过并记 `DATE_INVALID`；缺年份目录记 `DATE_YEAR_MISSING`（不猜当前年）。
- 冲突产生 `DATE_SOURCE_CONFLICT` warning（chosen/ignored source+date），写 `documents.parse_warnings`、结构化日志与 `job_runs.counters_json.date_conflicts`。
- 不使用 mtime / ingest 时间。

### 6.7 日记与学习笔记

- 日记：`source_type='user' AND occurred_on=:period_key AND path` 首段 `日记`；证据带完整 `source_path`、`occurred_on`、`line_range`、`quote`、`quote_hash`。
- 学习笔记：`document_changes.source_date=:period_key AND change_type ∈ {added,modified}`，排除 `日记/` 与 `reviews_root/`；覆盖 3 篇时四字段各有 ≥1 evidence_id。

### 6.8 ingest 零 LLM

- worker 不得 import `mylife_agent.llm`；AC-31 用计数断言 ingest 120 篇后 LLM 调用为 0。

---

## 7. 确定性门禁 G0–G9

| 门 | 判定 | 断言 | 级别 | 失败迁移 | issue code |
|---|---|---|---|---|---|
| G0 | 代码 | 证据 `document_id/review_id` 属于当前 workspace | blocking | `→security_abort` | `TENANT_LEAK` |
| G1 | 代码 | section_key 集合与 ordinal 序列按 kind 等值（7/4/5，连续无重复） | blocking | `→revising` | `SECTION_SET_MISMATCH` |
| G2 | 代码 | `structured_json` 按 `SECTION_SCHEMAS`、`fields_json` 按 `ITEM_SCHEMAS`，`extra=forbid` | blocking | `→revising` | `SCHEMA_INVALID` |
| G3a | 代码 | 所有证据 `snapshot_id == bundle.snapshot_id` | blocking | `→revising` | `SNAPSHOT_MISMATCH` |
| G3b | 代码 | document/chunk/review/task 的 `document_content_hash` 与冻结清单一致 | blocking | `revising→retrieving` | `EVIDENCE_STALE`（**不得判 FABRICATION**） |
| G3c | 代码 | 按 `heading/line_range` 在冻结原文定位 `quote`（连续子串） | blocking | `→revising` | `QUOTE_NOT_FOUND` |
| G3d | 代码 | `quote_hash == sha256(parser_version+"\x1f"+normalize(quote))` | blocking | `→revising` | `QUOTE_HASH_MISMATCH` |
| G4 | 代码 | error/method 条目均有 `source_date+source_path+line_range` | blocking | `→revising` | `TRACEABILITY_MISSING` |
| G5 | 代码 | 每个非哨兵 claim 绑定 ≥1 evidence_id；citation_indexes 1..n 可解析 | blocking | `→revising` | `UNSUPPORTED_CLAIM` |
| G6 | 代码 | 行动行匹配 `^- \[ \] .+` 且 trigger/verification 非空 | blocking | `→revising` | `ACTION_NOT_EXECUTABLE` |
| G7 | 代码 | 无证据章节逐字等于哨兵；有证据章节不含哨兵 | blocking | `→revising` | `SENTINEL_VIOLATION` |
| G8 | 代码 | weekly 7 天 / yearly 周覆盖缺口记 `coverage_gaps` | warning | 继续 | `COVERAGE_GAP` |
| G9 | 代码 | 路径白名单 + content_hash + 分支预检（Phase 3 本地目标） | blocking | `→needs_human` | `PATH_NOT_ALLOWED`/`CONTENT_HASH_MISMATCH` |

- **FABRICATION 边界**：仅“不可解析的引用被当作事实输出且无法通过定向补检索修复”才 `SECURITY_ABORT`；单纯哈希不一致/定位失败走 `EVIDENCE_STALE`/`QUOTE_NOT_FOUND`。
- `normalize`：NFC → 去首尾空白 → 连续空白折叠 → 中文标点统一；`quote_hash` 不改英文大小写；仅 `canonical_key/root_cause_fingerprint` 做 ASCII 小写。
- `GateIssue` 字段冻结：`gate, severity, code, message, section_key, claim_id, evidence_id, must_fix`（`extra=forbid`）。
- `ReviewVerdict` 映射：无 blocking → `APPROVE`；有 blocking 且可修订 → `REVISE`；耗尽/异常 → `NEEDS_HUMAN`；`SECURITY_ABORT` 不入枚举，由状态机直接终止。

---

## 8. 预算 guard

### 8.1 数据结构与默认值

`Budget`：`max_input_tokens`、`max_output_tokens`、`max_cost_usd|null`、`warn_ratio=0.8`、`hard_stop_ratio=1.0`。

| kind | 输入 | 输出 |
|---|---|---|
| daily | 100,000 | 20,000 |
| weekly | 300,000 | 60,000 |
| yearly | 1,000,000 | 200,000 |
| chat | 32,000 | 4,000（D22 待拍板） |
| workspace 月 | 5,000,000 | 1,000,000 |
| workspace 日 | 月/20 = 250,000 | 50,000 |

`BudgetUsage`：`input_tokens`、`output_tokens`、`cost_usd`（4 位）、`ratio`（4 位）、`state ∈ ok/warn/exceeded`。
`ratio = max(input_used/max_input, output_used/max_output, cost_used/max_cost)`（cost 为 null 时不参与）。

### 8.2 累计与存储

- run 已用 = `SUM(usage_events.prompt_tokens/completion_tokens WHERE workspace_id=:ws AND run_id=:run AND kind IN ('chat','review'))`。
- 跨 attempt、跨阶段累计；version+1 生成新 run_id，run 预算重新起算；workspace 日/月跨 version 累计。
- `kind ∈ {embedding,tool}` 不计入 LLM token 预算。

### 8.3 检查点

- 所有 LLM 调用（Planner/Writer/Reviewer/chat `_draft`）在构造 provider 前调用 `BudgetService.preflight`；重试前同样。
- 检查 run 与 workspace 日/月两个 scope；任一 `exceeded` 抛 `BudgetExceeded`，不得构造 provider。
- 不预留 token（允许在途调用越界一次，结束后立即记账并再次检查，与 API §11.9 一致）。

### 8.4 80% / 100%

- 首次 `ratio ≥ warn_ratio` 且 `< hard_stop_ratio`：写一条 `run_events(event="degraded", payload.reason="budget_warn", scope="run")`，`agent_runs.state_payload.degraded_reasons += ["budget_warn"]`，run 继续；同一 run 只写一次。
- `ratio ≥ hard_stop_ratio`：CAS 到 `failed`（不在 `human_gate` 时）或 `needs_human`（在 `human_gate` 时），reason=`budget_exceeded`，写 `error` 事件 `BUDGET_EXCEEDED`（retryable=false），置 `finished_at`；**此后该 run 任何路径不得再发起 LLM 调用**（preflight 先读终态短路）。
- workspace 日/月超限：新 run 准入返回 429 `BUDGET_EXCEEDED`，`details={scope,used,limit,resets_at}`，不写 `agent_runs`。
- 硬停路径不得写入 `reviews/review_versions/review_sections/review_citations/review_entries` 或交付文件（“不输出数据版复盘”）。

### 8.5 配额（SEC-23）

- `POST /reviews` workspace 10 次/小时 → 429 `RATE_LIMITED`；50 次/天 → 429 `QUOTA_EXCEEDED`。
- `create_review` preview 共享同一计数器，不能绕过。
- 每次拒绝写 `audit_logs(action="review.create_rejected")`。
- 响应头：429 返回 `X-Quota-*`；`BUDGET_EXCEEDED` 不返回 `Retry-After`。

---

## 9. API 表面

### 9.1 端点清单

| 方法/路径 | 权限 | 要点 |
|---|---|---|
| `POST /reviews` | owner（manual）；api key（api） | 准入协议见 §4.4；201 新建 / 200 version+1；`Idempotency-Key` 建议必填；可选 `confirmation_token`（D6） |
| `GET /reviews` | 成员 | 分页；排序 `period_key desc, version desc`；游标不透明串 |
| `GET /reviews/{id}` | 成员 | `version?` 默认 current；聚合 sections/entries/citations/versions |
| `GET /reviews/{id}/versions/{version}` | 成员 | 子表按该 version 读取，不混入 current |
| `POST /reviews/{id}/rollback` | owner | **Phase 4**（依赖交付）；Phase 3 返回 `NOT_IMPLEMENTED` 并在状态文档标注 |
| `GET /runs/{id}` | chat：属主/owner；review：全体成员 | 增 `version/lease_expires_at/budget/degraded/degraded_reasons/error/finished_at` |
| `POST /runs/{id}/approve` | owner/admin | 前置 `human_gate`；`edited_content_md` 重跑 G0–G9 |
| `POST /runs/{id}/reject` | owner/admin | `reason_code` 枚举；→ `cancelled` |
| `GET /runs/{id}/events` | 同 run | 复用 chat SSE；`needs_human` 属终态，发 `done` 后关闭 |
| `POST /runs/{id}/cancel` | review 仅 `created/snapshotting/planning` | 之后走 reject |
| `GET /topics`、`GET /topics/{topic}/items`、`POST .../confirm`、`POST .../resolve` | 成员/owner | 中文 topic ↔ 英文 DB 枚举映射（D17） |
| `POST /webhooks/git/{provider}` | 匿名 + 签名 | SEC-16/17；只写 `delivery_events` 与审计 |
| `GET /deliveries*` | — | **Phase 4**；Phase 3 不注册 |

### 9.2 `ReviewDetail` 字段来源

| 字段 | 来源 |
|---|---|
| `sections` | `review_sections(review_id,version)` 按 ordinal |
| `entries` | `review_entries(review_id,version)` |
| `citations` | `review_citations(review_id,version)` |
| `content_md/model/snapshot_id` | `review_versions` |
| `title/git_path/current_version` | `reviews` |
| `content_html` | `rendering.render_markdown(content_md)`（sanitize） |
| `degraded/degraded_reasons/verdict` | `reviews` 新列 |
| `versions[]` | `review_versions` |

### 9.3 `create_review` 三步

- preview：零副作用；返回 `preview{..., estimated_tokens:{input,output}}`、`confirmation_token`（HMAC，TTL 600s，绑定 `ws|user|kind|period` + jti）、`expires_at`、`blocked_reason`（`ACTIVE_RUN_EXISTS`/`BUDGET_EXCEEDED`）。
- confirm：校验签名/TTL/未消费/上下文一致；标记 `review_confirmations.consumed_at`；仍不入队。
- enqueue：仅 `POST /reviews`；`review run` 内再调 `create_review` → `TOOL_NOT_ALLOWED` + 审计。`ToolContext` 增 `run_kind`，`execute_tool` 入口拦截。

---

## 10. 安全门禁

| SEC | 实现 | 证据 |
|---|---|---|
| SEC-16 | `POST /webhooks/git/{provider}` 读原始 body；GitHub `X-Hub-Signature-256` HMAC-SHA256 / GitLab `X-Gitlab-Token` 常量时间比较；缺失/错误 → 401 `WEBHOOK_SIGNATURE_INVALID`；只写 `audit_logs(action="webhook.rejected")`；5 分钟时间窗 | `test_webhooks.py::test_signature_invalid_no_side_effect` |
| SEC-17 | `delivery_events.provider_delivery_id` 唯一；重复 → 200 `{received:true, handled:false}`；`payload_digest=sha256(raw body)`；append-only；`pull_request.merged` 在 Phase 3 仅记 `ignored` | `test_webhooks.py::test_replay_idempotent` |
| SEC-23 | §8.5 配额 + 审计；preview 共享计数 | `test_review_quota.py` |

---

## 11. 文件级施工清单

### 11.1 新建

| 文件 | 内容 |
|---|---|
| `apps/api/migrations/versions/0004_phase3_review.py` | §2 全部 DDL/列/函数 |
| `packages/contracts/mylife_contract/review.py` | §3.1 契约与 schema |
| `packages/contracts/mylife_contract/budget.py` | Budget/BudgetUsage |
| `packages/contracts/mylife_contract/topics.py` | 专题契约 |
| `packages/contracts/mylife_contract/delivery.py` | 交付契约（Phase 4 用） |
| `packages/knowledge-runtime/mylife_runtime/hashing.py` | `normalize_text()`、`quote_hash()`、`content_hash()` |
| `packages/knowledge-runtime/mylife_runtime/snapshot_text.py` | 冻结原文读取（本地数据根/快照副本） |
| `packages/agent/mylife_agent/task_ledger.py` | 复选框/归一化/occurrence/rebuild |
| `packages/agent/mylife_agent/evidence.py` | EvidenceBundle 装配与 caps |
| `packages/agent/mylife_agent/gates.py` | G0–G9、GateIssue、verdict |
| `packages/agent/mylife_agent/reviewer.py` | rubric + verdict 映射 |
| `packages/agent/mylife_agent/writer.py` | Draft 生成（mock 确定性 + 真实 LLM 结构化输出） |
| `packages/agent/mylife_agent/planner.py` | Plan 生成（固定模板兜底） |
| `packages/agent/mylife_agent/entry_materializer.py` | review_entries/主题物化 |
| `packages/agent/mylife_agent/review_fsm.py` | 迁移表/校验 |
| `packages/agent/mylife_agent/budget.py` | BudgetGuard（纯策略） |
| `packages/agent/mylife_agent/profile.py` | review-profile 加载与 fail-fast |
| `packages/agent/review-profile.yml` | 领域契约镜像 |
| `packages/delivery/mylife_delivery/renderer.py` | content_md 渲染与 hash |
| `packages/delivery/mylife_delivery/pyproject.toml` | 新包 |
| `apps/api/mylife_api/repo_review.py` | 复盘数据访问 |
| `apps/api/mylife_api/services_review.py` | 准入/版本/审批/列表 |
| `apps/api/mylife_api/review_runner.py` | 复盘 FSM 执行器（进程内） |
| `apps/api/mylife_api/services_budget.py` | 预算与配额 |
| `apps/api/mylife_api/routers/reviews.py` | §9.1 端点 |
| `apps/api/mylife_api/routers/topics.py` | 专题端点 |
| `apps/api/mylife_api/routers/webhooks.py` | SEC-16/17 |
| `apps/api/mylife_api/services_webhooks.py` | 验签与幂等 |
| `apps/api/mylife_api/idempotency.py` | `Idempotency-Key` |
| `apps/api/mylife_api/logging_setup.py` | 结构化日志字段 |
| `scripts/verify_replay.py` | AC-60 回放校验 |
| `apps/api/tests/test_task_ledger.py` | AC-31/32/33/34 |
| `apps/api/tests/test_review_schemas.py` | G1/G2/G7、SECTION_SCHEMAS |
| `apps/api/tests/test_review_gates.py` | G0–G9、AC-41 |
| `apps/api/tests/test_review_api.py` | AC-25/42/68 |
| `apps/api/tests/test_review_budget.py` | AC-66 |
| `apps/api/tests/test_webhooks.py` | SEC-16/17 |
| `apps/api/tests/test_replays.py` | AC-60 |
| `apps/api/tests/fixtures/fx_*` | FX-01/02/03/04/10/12 |
| `apps/api/tests/test_fixture_fx0*.py` | fixture 断言与 report.json |

### 11.2 修改

| 文件 | 修改 |
|---|---|
| `apps/worker/mylife_worker/markdown.py` | 复选框提取、日期来源与 warning、保留 `body_start_line` |
| `apps/worker/mylife_worker/indexer.py` | 写 occurrence、重建 tasks、warning 汇总 |
| `apps/worker/mylife_worker/jobs.py` | 汇总 counters `date_conflicts`/`tasks_skipped_no_date` |
| `packages/contracts/mylife_contract/constants.py` | 新常量（见 §3.2） |
| `packages/contracts/mylife_contract/tools.py` | confirm、estimated_tokens、TaskItem 扩展 |
| `packages/contracts/mylife_contract/chat.py` | RunDetail/ChatOptions 扩展 |
| `packages/contracts/mylife_contract/enums.py` | ReviewStatus 等 |
| `packages/agent/mylife_agent/llm.py` | 结构化输出辅助；budget 调用点由 API 侧包装 |
| `apps/api/mylife_api/tools.py` | `create_review` confirm、递归拒绝、previews_root、estimated_tokens |
| `apps/api/mylife_api/services_chat.py` | RunDetail budget/degraded/finished_at；TERMINAL_STATES 加 `needs_human` |
| `apps/api/mylife_api/chat_runner.py` | 预算 preflight、usage 带 run_id/attempt_id |
| `apps/api/mylife_api/repo_chat.py` | `insert_usage` 扩参、`sum_run_usage`、`sum_workspace_usage`、`metrics_snapshot(from,to)` |
| `apps/api/mylife_api/main.py` | 注册 reviews/topics/webhooks；启动校验 profile；日志初始化 |
| `apps/api/mylife_api/deps.py` | `require_owner` 扩展为 owner/admin 的审批依赖 |
| `apps/api/mylife_api/rendering.py` | 复盘 Markdown 渲染入口 |
| `apps/api/tests/test_rls_matrix.py` | 新表加入矩阵 |
| `apps/api/pyproject.toml` | 增加 `fixture` marker |

---

## 12. 测试与 fixture 计划

### 12.1 AC → 测试

| AC | 测试 |
|---|---|
| AC-25 | `test_review_api.py::test_create_review_preview_confirm_enqueue`、`test_review_recursion_rejected` |
| AC-31 | `test_task_ledger.py::test_checkbox_variants`、`test_natural_language_no_task`、`test_ingest_zero_llm_calls` |
| AC-32 | `test_task_ledger.py::test_cross_day_merge`、`test_rebuild_is_deterministic` |
| AC-33 | `test_task_ledger.py::test_ac33_three_day` |
| AC-34 | `test_task_ledger.py::test_date_conflict_warning`、`test_fixture_fx01.py::test_journal_evidence` |
| AC-35 | `test_fixture_fx01.py::test_learning_summary_evidence` |
| AC-36 | `test_fixture_fx01.py`（7 节、ordinal、caps、引用闭包） |
| AC-37 | `test_fixture_fx02.py`（4 节、完成率复算、重复错误 Top、缺日） |
| AC-38 | `test_fixture_fx03.py`（5 节、只读 rollup、Top10 排序、token 上限） |
| AC-41 | `test_review_gates.py::test_g3_g4_traceability`、`test_source_deleted_stale` |
| AC-42 | `test_review_api.py::test_trigger_admission_and_version` |
| AC-60 | `test_replays.py::test_event_replay_matches_db`、`test_logging_fields` |
| AC-66 | `test_fixture_fx10.py`（80% 告警、100% 硬停、累计、0 增量） |
| AC-68 | `test_review_api.py::test_topic_pending_confirm_recurrence` |
| SEC-16/17 | `test_webhooks.py` |
| SEC-23 | `test_review_api.py::test_review_quota_and_audit` |
| FX-12 | `test_fixture_fx12.py`（10 并发 × 2 workspace） |

### 12.2 Fixture 场景

| FX | 输入 | 关键断言 |
|---|---|---|
| FX-01 | 120 md；10-25…27 三日 Todo；10-27 日记；3 篇学习笔记变更；2 错误 + 1 方法论 | 7/7 节；每节 ≤12、全局 ≤80；顺延 2、回填 1；错误带 path+line_range；G0–G9 无 blocking；重跑 diff ≤10% |
| FX-02 | W43 7 份日报（含 1 degraded）+ 台账 | 4 节；完成率逐位复算；重复错误 ≥2 标出；缺 1 日 → `coverage_gaps`（拆为“齐备”与“缺日”两个子场景） |
| FX-03 | 52 周（含 3 缺失）+ 全年台账 | 5 节；只读下级 rollup；Top10 `occurrences desc, canonical_key asc`；输入 token ≤1M |
| FX-04 | 空日（`allow_empty=true`） | 7 节逐字哨兵；虚构 claim 0；引用 0；`succeeded`；`no_evidence_ratio=1.0` |
| FX-10 | 注入固定用量的 mock provider | 80% `budget_warn` 且继续；100% `failed/needs_human`；跨重试累计；0 次后续 LLM 调用 |
| FX-12 | 同 period 10 并发 + 另一 workspace 10 并发 | 每 ws 1 活跃 run；其余 409 同一 `existing_run_id`；跨 ws 不阻塞；10 分钟内结束 |

---

## 13. 施工顺序（每小节一个提交）

| 提交 | 内容 | 可独立验证 |
|---|---|---|
| C1 | 本文档 `docs/PHASE3-SPEC.md` | 文档评审 |
| C2 | 迁移 0004（表/列/函数/RLS） | `alembic upgrade head` + RLS 矩阵 |
| C3 | contracts：review/budget/topics/delivery + constants/tools/chat 扩展 | `test_contract.py` |
| C4 | knowledge-runtime：hashing/snapshot_text | 单元测试 |
| C5 | worker：复选框、日期 warning、occurrence、tasks 重建 | AC-31/32/33/34 |
| C6 | agent：gates/reviewer/entry_materializer/profile/review-profile.yml | G0–G9 单测 |
| C7 | agent：budget、fsm、planner/writer/evidence | 预算单测、plan/draft schema |
| C8 | api：repo_review + renderer + review_runner（FSM、本地交付） | AC-36/37/38/41/42 初步 |
| C9 | api：reviews/topics 路由 + services_review + idempotency + tools confirm | AC-25/42/68 |
| C10 | api：budget service + usage/run detail + 日志/回放 | AC-66/60 |
| C11 | api：webhooks（SEC-16/17） | SEC-16/17 |
| C12 | fixtures FX-01…04 | FX-01/02/03/04 |
| C13 | fixtures FX-10/FX-12 + 质量抽样 | AC-66、FX-12 |
| C14 | 文档：`PHASE3-STATUS.md`、`IMPLEMENTATION-NOTES §2C`、`HANDOFF.md`、`README` 索引 | 状态签收 |

---

## 14. 待拍板问题（按影响排序）

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| Q1 | `SECTION_SCHEMAS` 英文键名与子结构（文档只给中文字段名） | (a) 采用本文 §5.2 冻结；(b) 等需求 v1.1 补字段规范 | (a)，并在 `IMPLEMENTATION-NOTES §2C` 登记为“契约冻结” |
| Q2 | `create_review` confirm 的传输通道 | (a) `POST /reviews` 可选 `confirmation_token`；(b) 仅内部工具 confirm，前端直接 `POST /reviews`；(c) 新增 `POST /tools/create_review/confirm` | (a)，改动最小且可自动化断言 |
| Q3 | 复盘执行位置 | (a) Phase 3 API 进程内 asyncio；(b) 新增 worker-review/Celery | (a)，Phase 4 迁移 |
| Q4 | SEC-16/17 范围 | (a) 只做验签+`delivery_events` 幂等；(b) 连同 `delivery_records` 回写一起做 | (a)，避免跨阶段 |
| Q5 | yearly 默认人工门 | (a) `yearly` 默认 `human_gate`；(b) 全部自动，gate 失败才转人工 | (a)，符合 ARCHITECTURE §4.5 |
| Q6 | chat 默认预算 | (a) 32k/4k；(b) 沿用 daily 100k/20k；(c) 不设 run 预算 | (a)，与 §13 开放问题建议一致 |
| Q7 | fixture 目录 | (a) `apps/api/tests/fixtures/`；(b) 仓库根 `tests/fixtures/` 并改 pytest 路径 | (a)，改动最小 |
| Q8 | `REJECT` verdict 是否保留 | (a) 保留枚举但代码不产生（人工 reject 走 cancelled）；(b) 删除枚举 | (a)，保持 API 兼容 |
