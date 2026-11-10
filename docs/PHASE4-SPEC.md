# Phase 4 施工规格（交付、专题页、调度、仪表盘）

> 版本：v1.0 ｜ 日期：2026-11-07 ｜ 阶段：Phase 4 / M4.5–M5
>
> 本文是 Phase 4 的**施工级规格**：把设计文档中的门禁条款提取为可直接实现、可测试的条目，
> 并登记文档冲突与实现裁决。设计语义仍以 `ARCHITECTURE.md` / `API-CONTRACTS.md` /
> `DATA-MODEL.md` / `ACCEPTANCE-CRITERIA.md` / `SECURITY-BOUNDARY.md` /
> `DIRECTORY-CONSTRAINTS.md` 为准；本文只做“落到哪个文件、哪个函数、断言是什么”。
>
> 施工约定：**每个小节（§12 的 C1–C13）独立提交**，不把多个小节混进同一 commit。

---

## 0. 范围、门禁与验收口径

### 0.1 本阶段门禁（硬门）

| 类别 | 清单 |
|---|---|
| AC | AC-39、AC-40、AC-43、AC-44、AC-45、AC-46、AC-47、AC-48、AC-51、AC-52、AC-67 |
| SEC | SEC-27（Phase 3 的 SEC-16/17/23 继续回归） |
| FX | FX-07、FX-09；FX-08 在配置 Git 连接时必跑 |
| 质量 | 同日重跑 1 文件 version+1；专题不重复累加；定时按 workspace 时区触发误差 <60s；引用覆盖率、章节完整率延续 Phase 3 |

### 0.2 明确不在 Phase 4 范围

| 项 | 原因 |
|---|---|
| 真实生成式 LLM Writer | P3-2 保持确定性装配器；`model_profile` 仅扩展点 |
| 独立 worker-review 进程 | 见 D1：Phase 4 仍由 API 进程执行，sweeper + 启动恢复满足 FX-09 |
| 完整导出 ZIP（AC-55 导出半部）、管理后台（AC-54）、蓝绿换维（AC-50）、备份/恢复演练（AC-61）、PWA/i18n（AC-62/63） | Phase 5 |
| 账户级跨 workspace 删除的完整产品语义 | Phase 4 仅实现 SEC-27 所需最小链路（见 D17） |
| `data/reviews` 双写 | 决策 1D 禁止；只做兼容读取 |
| 删除 Git 历史 / force push | 决策 7A 永久排除 |

### 0.3 验收证据形态

| 证据 | 位置 |
|---|---|
| AC 逐条测试 | `apps/api/tests/test_delivery_*.py`、`test_topics_render.py`、`test_schedule.py`、`test_dashboard.py`、`test_settings_*.py`、`test_deletion.py` |
| FX report | `docs/evidence/phase4/fx-*.json` |
| 交付清单 | `docs/evidence/phase4/delivery-*.json` |
| 调度日志 | `docs/evidence/phase4/dispatch.log.jsonl`（或 report 内嵌） |

---

## 1. 文档冲突裁决（实施前冻结）

> 规则：文档未写死但实现必须二选一的点。每条给出裁决；与本文不符的实现以本文为准，
> 并在 `IMPLEMENTATION-NOTES §2D` 登记。

| # | 冲突/空白 | 出处 | 裁决 |
|---|---|---|---|
| D1 | 复盘执行位置：ARCH §11 要求 worker-review；P3-1 目前是 API 进程内 asyncio | ARCH §11/§13 vs P3-1 | Phase 4 **继续在 API 进程执行**；新增 `mylife_api.dispatcher`（独立 container，同 API 镜像）负责调度/回收。`review-sweeper` 在 API lifespan 内周期运行，回收过期租约并从 `retry_from_state` 续跑，满足 FX-09。迁 worker-review 登记为 Phase 5 偏差 |
| D2 | 专题 target mode：ARCH §16.2/API §4.8 写 `append_only`，ARCH §7.3 要求“每次全量重渲染” | ARCH §16.2 vs §7.3 | **occurrence 行 append-only（幂等 upsert），专题 md 文件 `update_allowed`**：只重写 `mylife:auto` 区内，区外保留；修正 API §4.8/§11.8 语义并登记 |
| D3 | `topic_status`：DATA-MODEL §1.1 无 `pending`，§4.14 CHECK 有 | §1.1 vs §4.14/AC-68 | 以 §4.14 为准：`pending/open/resolved/stale`；Phase 4 追加 `dropped`（见 D19） |
| D4 | `topic_occurrences.line_start` 可空破坏唯一键 | DATA-MODEL §4.15 | 迁移把 `line_start` 设为 `NOT NULL DEFAULT 0`，唯一键保持 `(item_id, source_date, source_path, line_start)` |
| D5 | `occurrence_count` 无条件 +1，重跑虚增 | 现有实现 vs AC-40 | **计数由 `COUNT(topic_occurrences)` 派生**，写入后重算；`last_seen=MAX(source_date)` |
| D6 | 专题“转 manual”无落点；标记内人工修改无哈希基线 | ARCH §7.3/§8.4、DIRECTORY §4.4 | 新增 `topic_files(workspace_id, topic, path, auto_region_hash, status, last_review_id, last_version, updated_at)`；`status ∈ auto/manual`，`manual` 时不再覆盖 |
| D7 | 专题冲突字段无落点 | FX-07 | `topic_items` 增 `fields_json/conflicts_json`；物化时比较同 `canonical_key` 字段，差异写入 `conflicts_json`（各值并列），渲染为显式冲突块，不静默择一 |
| D8 | `confirm(decision=drop)` 语义未定义 | API §3.7.13 | `drop` = tombstone：`status=dropped` + `dropped_at`；复发不自动复活；不计入专题页 |
| D9 | `topic_items` 无可编辑字段 | API §3.7.13 `edited_fields` | 增 `fields_json jsonb`；`confirm` 的 `edited_fields` 合并进 `fields_json`（白名单：`title` + `SECTION` 字段键），优先级高于物化值 |
| D10 | `索引.md` 内容/格式未定义 | DIRECTORY §4 | 冻结格式见 §5.5：frontmatter + auto 区；最新日/月/年报各 1 条 + 三个专题链接与 open 计数；确定性排序 |
| D11 | 专题 frontmatter `kind` 枚举无 topic/index | DIRECTORY §4.3 | 生成文件允许 `kind ∈ daily/monthly/yearly/topic/index` |
| D12 | `monthly_at=每月 1 日 02:30`、`yearly_at=1/1 03:00` 暗示可配置日期 | AC-51 vs API §3.9.7 | MVP **月固定 1 日、年固定 1 月 1 日**，只允许配置 `HH:MM`；AC-51 的日期是固定语义 |
| D13 | `catchup_days`/`enabled` 存哪 | API §3.9.7 vs DATA-MODEL §4.18/§4.20 | `workspace_settings` 存 timezone/三时间；`workspace_schedules` 存 per-kind `local_time/timezone/catchup_days/enabled/next_run_at`。PUT 同事务写两表；单值 `catchup_days` 应用到三行 |
| D14 | 停机补算“不批量”与“≤catchup_days” | ARCH §11 vs AC-43 | 生成窗口内**最近 catchup_days 个**周期，按 oldest→newest；更早的只记 `catchup_window_exceeded`，不生成 |
| D15 | 定时触发无内部认证 | API §3.7.1 | 公开 `POST /reviews` 拒绝 `trigger=schedule`；dispatcher 调用服务层 `admit_scheduled_review()`（内部函数，不经过 HTTP） |
| D16 | 定时触发空周期 | AC-43/ARCH §4.5 vs API §3.7.1 | **scheduled 触发固定 `allow_empty=true`**（合法空日复盘）；manual/api 保持默认 false |
| D17 | SEC-27 是 Phase 4 门禁，但 AC-55 删除流程在 Phase 5 | SECURITY §11 vs ACCEPTANCE §5 | Phase 4 实现 SEC-27 子集：`deletion_requests` 状态机（requested→cooling）、到期 purge、删除页告知、Git 历史正向断言；导出 ZIP/撤销演练/法律页留 Phase 5 |
| D18 | 账户级删除无表结构 | API §3.11.4 vs DATA-MODEL §4.25 | `deletion_requests` 以 workspace 行承载；`scope=account` 时**每 owned workspace 一行**，purge 后禁用用户；不新增 account 级表 |
| D19 | purge 集合不一致 | API §3.11.7 vs DATA-MODEL §9.2 | 取**并集**：补 `git_connections/workspace_schedules/job_runs/review_confirmations/idempotency_keys/usage_events`；保留 `audit_logs`（脱敏）与 Git |
| D20 | 导出前置是否阻塞删除 | DATA-MODEL §9.1 vs API §3.11.4 | Phase 4 **不硬阻塞**；响应 `export_recommended=true` 并写审计；Phase 5 再改为强制 |
| D21 | Git `auth_type` 枚举：wire `app` vs DB `github_app` | API §3.3.1 vs DATA-MODEL §4.19 | 服务层映射：wire `app ↔ github_app`；读写都做转换 |
| D22 | Git credential AAD | ARCH §8.5 vs `runtime.crypto` | 使用 `provider=f"git:{conn_id}"` 绑定连接（现有实现为 workspace|provider|version）；登记偏差 |
| D23 | `GET /settings/git` 单对象 vs source/output 双连接 | API §3.9.8 vs AC-52 | 返回 `{items: [GitConnectionSummary]}`；请求可带 `role`（默认 `source`）；summary 增 `role/auth_type/sync_enabled/next_sync_at/credential_status` |
| D24 | `sync_enabled` 无请求字段 | DATA-MODEL §4.19 vs API §3.9.8 | 派生 `sync_enabled = interval > 0`（仅 source）；interval=0 → `next_sync_at=NULL` |
| D25 | 调度触发无持久化来源 | DATA-MODEL §1.1 `trigger_type` vs `agent_runs` | 迁移增 `agent_runs.trigger_type text NOT NULL DEFAULT 'manual'` + 索引 |
| D26 | 跨租户到期扫描与 FORCE RLS | ARCH §11 vs DATA-MODEL §6 | 新增 `SECURITY DEFINER` 函数 `dispatch_due_schedules(limit)` / `dispatch_due_git_syncs(limit)`，owner 为 BYPASSRLS 的 `mylife_dispatch`，只暴露调度列；`GRANT EXECUTE` 给 `mylife_app` |
| D27 | outbox/heartbeat 无落点 | ARCH §11/§16.6 | 迁移新增 `outbox_events`、`dispatcher_heartbeats`；dispatcher 每 tick upsert 心跳；入队失败写 outbox 并在后续 tick 重投 |
| D28 | 退役 reason `lease_expired` 未列出 | API §7.1 vs PHASE3-SPEC §4.5 | 允许 `{active} → retry_wait`，reason=`lease_expired`（仅 sweeper）；`retry_from_state` 记录原状态 |
| D29 | `agent_runs.trigger_type` 缺失导致调度证据不可查 | AC-43/AC-52 | 同 D25；`admit_scheduled_review` 写 `trigger_type='schedule'` |
| D30 | 专题 stale 的 N/TTL 未定义 | ARCH §16.5/FE08 | Phase 4 不自动转 stale；`stale` 仅由人工/显式操作产生（`drop` 改为 `dropped`）；N/TTL 留 Phase 5 并登记 |
| D31 | 交付 provider `generic` | DATA-MODEL 允许 vs API 拒绝 | 公开 API 只接受 `github/gitlab`；`generic` 仅作为本地/自托管测试保留在 DB，不暴露 |
| D32 | `delivery_id` 生成 | API §7.4 | 使用 `uuid5(NAMESPACE_URL, "{ws}|{review}|{version}|{target}")`；重试同一记录 |
| D33 | `review_search` 从未写入 | P1-12/P3-06 | Phase 4 在每次交付时 upsert 当前版本，旧版本 `is_current=false`；搜索走 tsv |
| D34 | `POST /reviews/{id}/rollback` 依赖交付 | API §3.7.5 | 回滚生成 **version+1 的新版本行**（内容=目标版本），更新指针并交付；旧行不删 |
| D35 | `edited_content_md` 重评审 | API §3.7.7 | 按 `## ` 标题拆分并映射回 section_key，重跑 G0–G9；通过则交付编辑后内容（同一 run/version），审计 `edited=true`；失败 `REVIEW_GATE_FAILED` |
| D36 | `delivery_records` 缺 manifest/error 落点 | API §3.7.11 | 迁移增 `manifest_json/diff_summary_json/error_code/auto_merge/attempts/content_hash/base_branch/merged_at` |
| D37 | 对象存储交付无下载端点 | AC-39 | 新增 `GET /deliveries/{id}/download`（ZIP：文件 + `manifest.json`），登记为契约扩展 |
| D38 | `data/reviews` 兼容读别名 | AC-39/决策 1D | `GET /documents/by-path` 与搜索的 `path_prefix` 把 `data/reviews/...` 映射到 `{reviews_root}/...`；只读，不双写 |
| D39 | `last_skip_reason` 只能存一个值 | DATA-MODEL §4.18 | 保留单列（最新原因），优先级 `catchup_window_exceeded > period_already_succeeded`；两者同时发生时另写结构化日志 |
| D40 | `topic_occurrences` 无版本关联，无法“按当前版本重算” | ARCH §7.3 | 增 `review_id/version/review_entry_id/fields_json`；重跑同 period 时先删除该 `(review_id)` 的旧 occurrence 再按当前版本重建 |
| D41 | `delivery_records.status` 与 UI 枚举并集 | DATA-MODEL §4.16/API §3.7.10 | DB 保留并集；服务层只写 `pending/committing/pr_open/merged/closed/noop/blocked/conflict/failed`；`DeliveryRecord` 序列化映射 `committing→pending`、`conflict→blocked` |
| D42 | 仪表盘缓存 | API §3.8 | 进程内 TTL 60s，key=`(workspace_id, endpoint, params)`；测试可 `clear_dashboard_cache()` |
| D43 | `review_search` 与文档结果融合 | AC-48 | 文档结果沿用 RRF 分数；复盘结果按 tsv rank 归一化；`both` 合并后按 `(score desc, path asc)` 排序，`total_estimated` 为两者之和 |

---

## 2. 数据模型增量（迁移 `0005_phase4_delivery`）

所有新租户表 `ENABLE + FORCE RLS`，策略与 0004 相同；新表加入 `test_rls_matrix.py`。

### 2.1 新表

| 表 | 关键列 | 约束/索引 |
|---|---|---|
| `topic_files` | workspace_id, topic, path, auto_region_hash, status(enum auto/manual), last_review_id, last_version, updated_at | `UNIQUE(ws,topic)`；`CHECK(status IN ('auto','manual'))` |
| `outbox_events` | workspace_id, topic, payload_json, status(pending/sent/failed/dead), attempts, available_at, idempotency_key, created_at, sent_at | `UNIQUE(idempotency_key)`；`INDEX(status, available_at)` |
| `deletion_requests` | workspace_id, scope(workspace/account), state(requested/cooling/purged/cancelled), purge_at, requested_by, cancelled_at, created_at | `INDEX(workspace_id, state)`；`purge_at` 索引 |
| `export_jobs` | workspace_id, requested_by, status(queued/running/succeeded/failed/expired), artifact_key, expires_at, error, created_at | `INDEX(workspace_id, created_at desc)`（Phase 5 使用；Phase 4 只为删除建议留位） |
| `dispatcher_heartbeats` | id text PK, last_tick_at, version | 单行 `id='default'`；非租户表 |

### 2.2 扩展列

| 表 | 列 | 说明 |
|---|---|---|
| `agent_runs` | `trigger_type text NOT NULL DEFAULT 'manual' CHECK (IN manual/schedule/api/chat)` | D25；`INDEX(workspace_id, trigger_type, created_at)` |
| `git_connections` | `remote_url text`、`api_base_url text`、`credential_status text CHECK(valid/invalid/untested) DEFAULT 'untested'`、`last_tested_at timestamptz`、`last_test_error text` | 本地/自托管与连接测试矩阵（D21–D23） |
| `git_connections` | `CHECK (sync_interval_minutes BETWEEN 0 AND 1440)` | 与 API 对齐 |
| `delivery_records` | `manifest_json jsonb`、`diff_summary_json jsonb`、`error_code text`、`auto_merge boolean NOT NULL DEFAULT false`、`attempts int NOT NULL DEFAULT 0`、`content_hash text`、`base_branch text`、`merged_at timestamptz` | D36 |
| `topic_items` | `fields_json jsonb NOT NULL DEFAULT '{}'`、`conflicts_json jsonb NOT NULL DEFAULT '[]'`、`dropped_at timestamptz`；status CHECK 追加 `dropped` | D3/D7/D8/D9 |
| `topic_occurrences` | `review_id uuid`、`version int`、`review_entry_id uuid`、`fields_json jsonb`；`line_start int NOT NULL DEFAULT 0` | D4/D40 |
| `reviews` | `delivery_status text`（派生缓存，可空） | 列仅作查询加速；以 `delivery_records` 为准 |

### 2.3 函数与角色

```sql
-- 到期调度扫描（跨租户只读，只暴露调度列）
CREATE ROLE mylife_dispatch NOLOGIN BYPASSRLS;  -- 不存在时创建
CREATE FUNCTION dispatch_due_schedules(p_limit int)
RETURNS TABLE (workspace_id uuid, kind text, local_time time, timezone text,
               next_run_at timestamptz, catchup_days int, enabled boolean)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT workspace_id, kind, local_time, timezone, next_run_at, catchup_days, enabled
  FROM workspace_schedules
  WHERE enabled AND next_run_at IS NOT NULL AND next_run_at <= now()
  ORDER BY next_run_at ASC
  LIMIT p_limit
$$;
ALTER FUNCTION dispatch_due_schedules(int) OWNER TO mylife_dispatch;

CREATE FUNCTION dispatch_due_git_syncs(p_limit int)
RETURNS TABLE (workspace_id uuid, conn_id uuid, sync_interval_minutes int, next_sync_at timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT id, workspace_id, sync_interval_minutes, next_sync_at
  FROM git_connections
  WHERE role = 'source' AND sync_enabled AND next_sync_at IS NOT NULL AND next_sync_at <= now()
  ORDER BY next_sync_at ASC LIMIT p_limit
$$;
```

- `GRANT EXECUTE ... TO mylife_app`；`REVOKE ALL FROM PUBLIC`。
- `GRANT SELECT, INSERT, UPDATE ON outbox_events, dispatcher_heartbeats TO mylife_app`（其余按默认）。

### 2.4 迁移验收

- `alembic upgrade head` / `downgrade -1` 均通过。
- RLS 矩阵覆盖新表（`dispatch_*` 函数单独断言跨租户返回多行但列受限）。
- `topic_occurrences.line_start` 回填：`UPDATE ... SET line_start = 0 WHERE line_start IS NULL` 后再设 NOT NULL。
- 既有 `topic_items.occurrence_count` 回填为 `COUNT(topic_occurrences)`。

---

## 3. 契约增量（`packages/contracts/mylife_contract`）

### 3.1 新文件

| 文件 | 内容 |
|---|---|
| `dashboard.py` | `DashboardLatest`、`TaskItem`、`TaskList`、`CalendarResponse`（`DayCell`/`MonthCell`）、`DashboardStats` |
| `settings.py` | `ScheduleSettings`、`ScheduleUpdate`、`GitConnectionSummary`、`GitConnectionList`、`GitSettingsUpdate`、`UsageResponse`、`RetrievalSettings` |
| `deletions.py` | `DeletionRequest`、`DeletionCreate`、`DeletionCurrent`、`DeletionNotice` |

### 3.2 现有文件修改

| 文件 | 修改 |
|---|---|
| `delivery.py` | `DeliveryRecord` 增 `pr_url/base_branch/auto_merge/error_code`；`DeliveryManifest` 增 `targets[].mode` 语义注释；新增 `DeliveryDetail {delivery, manifest, diff_summary}` |
| `review.py` | `ReviewSummary.delivery_status` 扩展取值映射；`ReviewDetail.versions[].git_sha` 已有 |
| `search.py` | `SearchResult` 已有 `review_id`；新增 `matched_by` 允许 `lexical`（复盘仅词法） |
| `errors.py` | 复用现有 `DELIVERY_*`/`GIT_MARKER_BROKEN`；新增 `SETTINGS_INVALID_TIMEZONE` 不新增（用 `VALIDATION_ERROR` details） |
| `constants.py` | 新增 `TOPIC_FILES`、`INDEX_PATH`、`AUTO_MARKER_START/END`、`PR_BODY_SECTIONS`、`TOPIC_DROP_STATUS` |

### 3.3 契约测试

- `test_contract.py` 断言新模型 `extra="forbid"` 与字段名。
- OpenAPI 含 `/dashboard/*`、`/settings/schedule`、`/settings/git`、`/deliveries*`、`/deletions*`。

---

## 4. 交付层（`packages/delivery` + API）

### 4.1 渲染与标记

| 能力 | 入口 | 规则 |
|---|---|---|
| 复盘 Markdown | 已有 `render_review_markdown` | 不变 |
| 专题 Markdown | `mylife_delivery.topics.render_topic_markdown(topic, items)` | 只渲染 `open/stale`（`pending`、`dropped`、`resolved` 不进入 auto 区，`resolved` 单列“已解决”）；冲突块显式列出各值 |
| auto 区合并 | `mylife_delivery.markers.merge_auto_region(existing, generated)` | 标记必须单对；缺失/嵌套/重复 → `MarkerBroken`；有 `topic_files.auto_region_hash` 且与现有区内哈希不一致 → `ManualDetected`，不覆盖 |
| 索引页 | `mylife_delivery.index_page.render_index_markdown(...)` | 格式见 §5.5 |
| PR 正文 | `mylife_delivery.pr_body.build_pr_body(...)` | 六段：30 秒结论 / 章节自检 / 证据前 10 条 / 变更文件 / 风险与回滚 / GateResult；`degraded` 时首行 `degraded: <reason>` |
| 提交信息 | `mylife_delivery.manifest.build_commit_message(...)` | 主题 + 四个 trailer |

### 4.2 Git 操作

`mylife_delivery.git_ops.GitRepo`：

- 硬化参数与 worker `git_source` 一致（`core.hooksPath=/dev/null`、`protocol.ext.allow=never`、`safe.directory=*`、禁用 LFS/filter/submodule、`GIT_TERMINAL_PROMPT=0`）。
- `clone_or_fetch(remote, base_branch)`：本地 `file://` 仅在 `MYLIFE_ALLOW_LOCAL_GIT=true` 允许。
- `ensure_branch(name, base)`：存在则切到远程同名分支；不存在创建。
- `write_files(files)`、`commit(message)`、`push(branch)`：**永不 force push**，push 前检查 `--force-with-lease` 无参。
- 非快进：`git rebase base` 重试 2 次，仍失败保留分支、状态 `conflict`。
- 分支名：`review/{kind}/{period_key}`。

### 4.3 Provider（PR）

`mylife_delivery.providers`：

- `GitHubProvider(api_base_url, token)`：`GET /repos/{owner}/{repo}` 校验 `permissions.push`；`POST /repos/{o}/{r}/pulls`；`PATCH /pulls/{n}`（复用 open PR）；`PUT /pulls/{n}/merge`（squash）。
- `GitLabProvider`：`GET /projects/{urlencoded}` 校验 `permissions.project_access.access_level >= 30`；`POST /merge_requests`；`PUT /merge_requests/{iid}/merge`。
- `test_connection()` 返回 `{ok, reason}`；缺 `contents:write` → 422 `INGEST_AUTH_FAILED` + `details.reason="MISSING_CONTENTS_WRITE"`。
- 测试用 `httpx` 注入 base_url；FX-08 用本地 fake GitHub HTTP server + 本地裸仓库。

### 4.4 对象存储降级

- 无 output 连接或 Git 不可用：写 `{data_root}/workspaces/{ws}/artifacts/deliveries/{delivery_id}/files/...` + `manifest.json` + `artifact.zip`，`target=object_store`，`status=committed`。
- `GET /deliveries/{id}/download`：返回 ZIP（`Content-Disposition: attachment`）。
- `degraded=true` 且 `degraded_reasons` 含 `git_unavailable`（若原配置了 Git）。

### 4.5 交付编排 `services_delivery.deliver_review()`

1. 读取 review + review_version（目标版本）+ verdict；
2. 解析 `workspace_settings.reviews_root` 与 output 连接（`role='output'`）；
3. 构建 targets：复盘文件（`update_allowed`）+ 三个专题（`update_allowed`）+ `索引.md`（`update_allowed`）；
4. `content_hash` 与最近一次成功交付相同 → `noop`（`DELIVERY_NOOP`）；
5. git：clone/fetch → 同一 branch → 写文件 → commit（trailer）→ push → PR（复用 open PR）→ 可能 auto-merge；
6. 写 `delivery_records`（uuid5 id，`attempts++`），更新 `review_versions.git_sha`，写 `review_search`，重算 `reviews.delivery_status`；
7. 失败按类型写 `failed/blocked/conflict`，保留分支，不 force push；
8. 审计：`delivery.created` / `delivery.merged` / `delivery.auto_merged` / `delivery.failed`。

### 4.6 Auto-merge（AC-67）

- 仅当 `kind=daily` 且 `workspace_settings.auto_merge_daily=true` 且 `verdict.decision=APPROVE`（0 blocking）时 `pr.auto_merge=true` 并调用 merge API；
- monthly/yearly 恒 false；任何请求（若未来加入字段）为 true 被忽略并审计 `delivery.auto_merge_denied`；
- 每次自动合并写 `audit_logs(action='delivery.auto_merged')`，记录 PR/commit。

### 4.7 回滚与编辑重评审

- `POST /reviews/{id}/rollback {version, confirm:true}`：校验 existence/confirm；无活跃 run；以目标版本内容创建 `version+1`（复制 content_md/hash），更新 `reviews.version`；新建 run `delivering → succeeded`；返回 202。
- `POST /runs/{id}/approve` 带 `edited_content_md`：按 `## ` 拆分；按 `SECTION_ORDINALS[kind]` 顺序映射；重跑 G0–G9（使用已存 evidence/claims/citations）；通过则更新 `review_sections` 并交付；失败 `REVIEW_GATE_FAILED` 且不推进。

### 4.8 webhook 回写（Phase 4）

- `pull_request.closed + merged` / GitLab `merge_request merged`：按 `head.ref` 找到 `delivery_records.branch` 且 `status IN ('pr_open','committed')` → CAS 更新 `merged` + `merged_commit` + `merged_at`；审计 `delivery.merged_webhook`。
- `push` 到 base 且触及 `reviews_root`：写 `delivery_events(status=handled)`，若存在 source 连接则入队一次 sync（复用 D17 内部函数）；内容哈希比对由导入任务完成并标 `human_edited`（`review_versions` 新增 `source='human_edited'` 不新增列，写 `reviews.status='manual'`）。
- 签名失败仍无副作用（SEC-16 回归）。

---

## 5. 专题页重渲染（AC-40 / FX-07）

### 5.1 数据口径

- 重渲染输入：`topic_items`（排除 `pending/dropped`）与 `topic_occurrences`（按 `source_date asc, source_path asc, line_start asc`）。
- `occurrence_count = COUNT(topic_occurrences)`；`last_seen = MAX(source_date)`。
- 冲突：`topic_items.fields_json` 与同 `canonical_key` 的新条目字段比较；差异字段写入 `conflicts_json`，值并列保留。

### 5.2 重跑不重复累加

- 物化时对 `(review_id, canonical_key)` 的 occurrence：先 `DELETE FROM topic_occurrences WHERE review_id=:rid AND item_id=:iid`，再按当前版本 upsert；
- 同一 `(item_id, source_date, source_path, line_start)` 幂等；
- 计数在事务内由 `COUNT(*)` 回写。

### 5.3 标记保护

| 情况 | 行为 |
|---|---|
| 文件不存在 | 新建，写入完整 auto 区，`topic_files.status=auto` |
| 恰好一对标记 | 替换区内容；区外逐字保留；更新 `auto_region_hash` |
| 无标记/嵌套/重复 | 不覆盖；`topic_files.status=manual`；`delivery_records.status=conflict`；错误码 `GIT_MARKER_BROKEN`；审计 |
| 区内哈希 ≠ `topic_files.auto_region_hash` | 视为人工修改：不覆盖；转 manual；同 `GIT_MARKER_BROKEN` |

### 5.4 渲染格式

```markdown
---
mylife: true
kind: topic
topic: errors
generated_at: ...
generator: mylife/0.4
---

<!-- mylife:auto:start -->
# 错误清单

> 共 N 条（open X / stale Y）。

## [E-<key8>] <title>

- 首次：2026-10-25 ｜ 最近：2026-10-27 ｜ 出现 3 次
- 冲突：`cost` 在 2026-10-25 为 “2h”，在 2026-10-26 为 “半天”（未自动择一）   ← 仅冲突时
- 出现记录：
  - 2026-10-25 · `path#L12` · “quote”
  - 2026-10-26 · `path#L20` · “quote”
<!-- mylife:auto:end -->

（标记外人工内容原样保留）
```

### 5.5 `索引.md`

```markdown
---
mylife: true
kind: index
generated_at: ...
generator: mylife/0.4
---

<!-- mylife:auto:start -->
# 复盘索引

- 最新日报：2026-10-27（v2）
- 最新月报：2026-10（v1）
- 最新年报：2026（v1）

## 专题
- [错误清单](专题/错误清单.md)：open 3
- [正确方法论](专题/正确方法论.md)：open 2
- [行动清单](专题/行动清单.md)：open 1
<!-- mylife:auto:end -->
```

排序固定：日/月/年各取 `period_key desc, version desc` 第一条；专题固定 errors→methods→actions。

---

## 6. 调度与 dispatcher（AC-43 / AC-51 / AC-52 / FX-09）

### 6.1 纯函数

`apps/api/mylife_api/schedule.py`：

```python
def localize(day: date, at: time, tz: str) -> datetime      # DST：不存在→顺延；歧义→fold=0
def next_run_at(kind, at, tz, after_utc) -> datetime        # daily/monthly(1st)/yearly(Jan 1)
def due_period(kind, run_local_date) -> str                 # daily D-1 / monthly 上一个月 / yearly 去年
def missed_periods(kind, at, tz, since_utc, now_utc, catchup_days) -> (list, bool)
```

- daily：本地日期 + HH:MM。
- monthly：下一个自然月 1 日 HH:MM（D12 已被 Phase 6 取代）。
- yearly：下一个 1 月 1 日 HH:MM。
- `missed_periods`：返回窗口内最近 `catchup_days` 个周期（oldest→newest）与 `exceeded` 标志。

### 6.2 Dispatcher

`apps/api/mylife_api/dispatcher.py`（`python -m mylife_api.dispatcher`）：

- 单副本；Postgres advisory lock `pg_try_advisory_lock(hashtext('mylife-dispatch'))` 防多实例。
- tick 每 60s（可配 `MYLIFE_DISPATCH_INTERVAL_SECONDS`）：
  1. upsert `dispatcher_heartbeats`;
  2. `dispatch_due_schedules(50)` → 每个到期：
     - 计算 `due_period`；catchup 超窗 → `last_skip_reason=catchup_window_exceeded`；
     - 已有成功复盘 → `period_already_succeeded`；
     - 否则调用 `admit_scheduled_review()`（`trigger_type='schedule'`、`allow_empty=true`、幂等键 `schedule:{kind}:{period}`）；
     - 推进 `next_run_at`；
  3. `dispatch_due_git_syncs(50)` → 内部 `create_sync_job_internal()`，推进 `next_sync_at=now+interval`，写 `last_sync_status='skipped'` 于入队失败；
  4. `sweep_expired_leases()`：活跃态且租约过期 → `retry_wait` + `retry_from_state` + `attempts+1` + `run_events(reason=lease_expired)`；
  5. `purge_due_deletions()`（SEC-27）；
  6. `relay_outbox()`：重投 pending/failed（指数退避，>5 次转 dead）。
- 启动时：对所有 `next_run_at IS NULL OR < now - catchup` 的行重算并补算窗口，写结构化日志 `dispatch.startup_recompute`。
- 结构化日志字段：`ts, level, service=dispatcher, workspace_id, kind, period_key, event, action, reason`。

### 6.3 启动恢复与 sweeper（FX-09）

- API lifespan 启动 `review_sweeper_loop`（`MYLIFE_REVIEW_SWEEPER=true`，默认 true）：
  - 每 15s 扫描 `state IN (created, retry_wait)` 的 review run，CAS 领取后调用 `run_review(resume_from=retry_from_state)`；
  - 同时调用 `sweep_expired_leases()`；
- 进程重启后未完成 run 在 5 分钟内续跑或转 `retry_wait`；
- 同 period 不产生重复产物（唯一索引 + 版本指针 + 文件覆盖）；
- SSE `stream_epoch` 变化即触发客户端重建（Phase 3 已有）。

### 6.4 设置端点

| 端点 | 权限 | 行为 |
|---|---|---|
| `GET /settings/schedule` | 成员 | 返回 `timezone/daily_at/monthly_at/yearly_at/catchup_days/enabled{3}/next_run_at{3}` |
| `PUT /settings/schedule` | owner | 校验时区 IANA、时间 `HH:MM`、`catchup_days 0–30`；同事务更新 `workspace_settings` + 三条 `workspace_schedules`；重算 `next_run_at`；审计 `settings.schedule.updated`；活跃 run 不受影响 |
| `GET /settings/git` | 成员 | `{items:[GitConnectionSummary]}`（凭据只给状态） |
| `PUT /settings/git` | owner | `role/provider/repo_full_name/base_branch/auth_type/credential?/reviews_root?/mode?/sync_interval_minutes?/auto_merge_daily?/remote_url?/api_base_url?`；先连接测试（`contents:write`），失败不保存并返回 `INGEST_AUTH_FAILED details.reason`；成功后加密存储、`next_sync_at=now+interval`、审计 `settings.git.updated` |
| `DELETE /settings/git` | owner | 删除连接与凭据；保留历史 `delivery_records`；后续交付降级 `object_store` |
| `GET /settings/usage` | 成员/owner | 来自 `usage_events` 聚合；`cost` 4 位小数 |
| `GET/PUT /settings/retrieval` | 成员/owner | `retrieval_top_k 1–50`、`context_window_tokens 8000–128000`（API 边界，D42 备注）、`web_search_enabled=true` → `VALIDATION_ERROR details.reason=FEATURE_DISABLED` |

---

## 7. 仪表盘与搜索（AC-45…AC-48）

### 7.1 端点

| 端点 | 口径 |
|---|---|
| `GET /dashboard/latest` | 三类各取 `status IN ('delivered','approved')` 且 `period_key` 最大、当前 version；`completion_rate=completed/total`（tasks）；`open_tasks` 同源 |
| `GET /dashboard/tasks` | 默认 `open,carried`；`days_open=(today_local - first_seen).days`；排序 `first_seen asc, id asc`；`counts{open,carried,done_today}` |
| `GET /dashboard/calendar?year&month?` | 月度：该月每一天一个 `DayCell`（31/30/28）；年度：`months[12]` 且 `days=[]` |
| `GET /dashboard/stats?range` | `completion_rate`、tasks、reviews 计数、`no_evidence_ratio`、`degraded_rate`、tokens（`usage_events`） |

- 只读、零 LLM；60s 进程内缓存（D42）。
- 数值必须可由 SQL 复算（测试用同一查询对照）。
- 时区：`today_local` 与 `days_open` 用 `workspace_settings.timezone`。

### 7.2 搜索接入 `review_search`（AC-48）

- `source_type=review`：`SELECT ... FROM review_search WHERE workspace_id=:ws AND is_current AND tsv @@ to_tsquery('simple', :q)`，`score=ts_rank`，`matched_by='lexical'`。
- `source_type=document`：现有逻辑。
- `source_type=both`：两路结果合并；文档分数按 RRF，复盘分数按 `ts_rank` 归一化；按 `(score desc, path asc)` 排序，`top_k` 截断。
- `review_id` 非空仅复盘结果；`line_range=None`（复盘无行号）。
- `path_prefix` 对复盘按 `review_search.path` 过滤；`data/reviews` 前缀映射到 `reviews_root`（D38）。

### 7.3 兼容读取别名

- `GET /documents/by-path?path=data/reviews/每日复盘/2026/2026-10-27.md` → 映射到 `{reviews_root}/...`，若文档不存在则从 `reviews.git_path` 命中并返回 `content`；不双写。

---

## 8. SEC-27 删除冷却（Phase 4 子集）

- 表：`deletion_requests`（§2.1）。
- 端点：`POST /deletions`、`GET /deletions/current`、`POST /deletions/{id}/cancel`。
- `POST`：`confirm_text` 精确匹配 workspace 名或 `DELETE MY ACCOUNT`；有活跃 run → `CONFLICT`；插入 `requested` 并同事务转 `cooling`；`purge_at=now+30d`；返回 `cooling`；`details` 含 `will_delete/will_retain/notice`（D19）。
- `GET`：返回当前记录（含 `cancelled`，D-conflict）；无则 `null`。
- `cancel`：仅 `cooling`；→ `cancelled`；恢复写操作与调度。
- dispatcher `purge_due_deletions()`：`state='cooling' AND purge_at<=now` → 删除并集表（保留 `audit_logs`、Git 历史、`workspaces/users` 行）、删除数据根目录、清理 Redis 前缀；`state='purged'`；审计 `deletion.purged`。
- 删除告知常量集中在 `mylife_contract.constants.DELETION_NOTICE`；前端删除页原样展示。
- Git 历史正向断言：本地裸仓库 purge 后 `git log` 仍可读到已合并提交。

---

## 9. 复盘链路改动

| 改动 | 位置 |
|---|---|
| `_deliver` 调 `services_delivery.deliver_review()`，失败按 D41 映射 | `review_runner.py` |
| 交付成功写 `review_search`（is_current 翻转） | `repo_review.upsert_review_search` |
| `ReviewSummary.delivery_status` 从 `delivery_records` 派生 | `services_review._summary` |
| `admit_scheduled_review()` 内部准入（trigger=schedule、allow_empty=true、idempotency=schedule:{kind}:{period}） | `services_review.py` |
| `review_sweeper` / `sweep_expired_leases` | `services_lease.py` |
| 回滚 / edited_content_md | `services_review.py` |
| topic 物化：occurrence 版本列、计数派生、字段冲突 | `repo_review.py`、`review_runner._materialize_topics` |
| topic 文件重渲染在交付阶段触发 | `services_topics.py` + `services_delivery.py` |

---

## 10. 安全门禁

| SEC | 实现 | 证据 |
|---|---|---|
| SEC-27 | 删除冷却 + purge；正文表 0 行；Git 历史保留正向断言；删除页告知 | `test_deletion.py` |
| SEC-16/17 回归 | webhook 签名/幂等不变，新增回写不改变失败无副作用 | `test_webhooks.py` |
| 路径安全 | 所有生成路径走 `reviews_root` 白名单；提交前 `Path` 校验禁止 `..` | `test_delivery_paths.py` |
| 凭据 | Git 凭据信封加密（AAD 绑定 conn_id）、日志/响应不回显 | `test_settings_git.py` |
| SSRF | `remote_url/api_base_url` 复用 `validate_git_target`；仅 `file://` 在 allow_local 下允许 | `test_settings_git.py` |

---

## 11. 前端（Phase 4）

| 路由 | 内容 |
|---|---|
| `/reviews` | 列表（kind/状态/降级/交付状态）+ 触发入口（owner） |
| `/reviews/[kind]/[period]` | 章节、来源行号、版本、GateResult、`DeliveryStatus`、approve/reject/rollback |
| `/topics`、`/topics/[topic]` | 专题条目、pending 确认/丢弃、resolve、occurrence 时间线 |
| `/settings` 及子页 | schedule、git、retrieval、deletion（含不承诺抹除告知） |
| `/dashboard` | latest/tasks/calendar/stats 接入真实 API |
| `/search` | `source_type` 三模式切换 |

约束：文案进 `copy/zh-CN.ts`；所有数据区有 loading/empty/error；`tsc + lint + build` 通过。

---

## 12. 文件级施工清单

### 12.1 新建

| 文件 | 内容 |
|---|---|
| `apps/api/migrations/versions/0005_phase4_delivery.py` | §2 全部 DDL/函数/授权 |
| `packages/contracts/mylife_contract/dashboard.py`、`settings.py`、`deletions.py` | §3 |
| `packages/delivery/mylife_delivery/markers.py` | auto 区解析/合并 |
| `packages/delivery/mylife_delivery/topics.py` | 专题渲染 |
| `packages/delivery/mylife_delivery/index_page.py` | 索引页 |
| `packages/delivery/mylife_delivery/pr_body.py` | PR 六段 |
| `packages/delivery/mylife_delivery/manifest.py` | target/commit/PR 构建 |
| `packages/delivery/mylife_delivery/git_ops.py` | 硬化 git 操作 |
| `packages/delivery/mylife_delivery/providers.py` | GitHub/GitLab PR + 连接测试 |
| `packages/delivery/mylife_delivery/object_store.py` | 本地对象存储与 ZIP |
| `apps/api/mylife_api/services_delivery.py` | 交付编排 |
| `apps/api/mylife_api/repo_delivery.py` | delivery_records/events 访问 |
| `apps/api/mylife_api/routers/deliveries.py` | §4 端点 + download |
| `apps/api/mylife_api/services_topics.py` | 专题文件重渲染 |
| `apps/api/mylife_api/schedule.py` | §6.1 纯函数 |
| `apps/api/mylife_api/dispatcher.py` | §6.2 |
| `apps/api/mylife_api/services_lease.py` | §6.3 sweeper |
| `apps/api/mylife_api/services_dashboard.py`、`routers/dashboard.py` | §7 |
| `apps/api/mylife_api/services_settings.py`、`routers/settings.py` | §6.4 |
| `apps/api/mylife_api/services_deletion.py`、`routers/deletions.py` | §8 |
| `apps/api/tests/test_delivery_git.py`、`test_topics_render.py`、`test_schedule.py`、`test_dashboard.py`、`test_settings_schedule.py`、`test_settings_git.py`、`test_search_review.py`、`test_deletion.py`、`test_fixture_phase4.py` | 门禁 |
| `apps/web/src/app/(app)/reviews/**`、`topics/**`、`settings/**`、`components/review-*`、`components/topic-*`、`components/delivery-status.tsx` | §11 |

### 12.2 修改

| 文件 | 修改 |
|---|---|
| `packages/contracts/mylife_contract/delivery.py` | §3.2 |
| `packages/contracts/mylife_contract/review.py` | `RollbackRequest` 已有；补 `delivery_status` 字符串映射 |
| `packages/contracts/mylife_contract/constants.py` | §3.2 常量 |
| `packages/delivery/mylife_delivery/__init__.py` | 导出新模块 |
| `apps/api/mylife_api/review_runner.py` | 交付、专题、review_search、租约 |
| `apps/api/mylife_api/services_review.py` | scheduled 准入、回滚、编辑重评审、delivery_status |
| `apps/api/mylife_api/repo_review.py` | topic occurrence 版本/计数、review_search、delivery 辅助 |
| `apps/api/mylife_api/services_search.py` | §7.2 |
| `apps/api/mylife_api/services_webhooks.py`、`routers/webhooks.py` | §4.8 回写 |
| `apps/api/mylife_api/main.py` | 注册新路由；启动 sweeper |
| `apps/api/mylife_api/settings.py` | dispatcher/sweeper/allow_local 配置 |
| `apps/api/mylife_api/services_ingest.py` | 内部定时 sync 入口 |
| `apps/api/tests/test_rls_matrix.py` | 新表 |
| `infra/compose/docker-compose.yml` | `dispatcher` 服务（API 镜像，单副本，`restart: unless-stopped`） |
| `apps/web/src/components/app-shell.tsx`、`copy/zh-CN.ts`、`lib/api/client.ts`、`types.ts`、`query-keys.ts` | 页面接入 |
| `.env.example` | `MYLIFE_DISPATCH_*`、`MYLIFE_GIT_API_BASE`、`MYLIFE_ALLOW_LOCAL_GIT` |

---

## 13. 测试与 fixture 计划

### 13.1 AC → 测试

| AC | 测试 |
|---|---|
| AC-39 | `test_delivery_git.py::test_object_store_fallback_zip_matches_db`、`test_git_files_at_wiki_paths`、`test_compat_alias_reads_same_content` |
| AC-40 | `test_topics_render.py::test_three_files_and_dedupe`、`test_rerun_does_not_double_count`、`test_human_region_preserved_and_marker_broken`、`test_conflict_fields_explicit` |
| AC-43 | `test_schedule.py::test_next_run_at_matrix`、`test_dst_nonexistent_and_ambiguous`、`test_catchup_window` |
| AC-44 | `test_delivery_git.py::test_same_day_rerun_single_file_version_inc`（并发沿用 FX-12） |
| AC-45 | `test_dashboard.py::test_latest_and_completion_rate_matches_sql` |
| AC-46 | `test_dashboard.py::test_days_open_by_timezone` |
| AC-47 | `test_dashboard.py::test_calendar_month_and_year` |
| AC-48 | `test_search_review.py::test_three_source_modes` |
| AC-51 | `test_settings_schedule.py::test_put_recomputes_next_run`、`test_active_run_not_interrupted` |
| AC-52 | `test_settings_git.py::test_connection_test_matrix`、`test_sync_interval_recompute_and_dispatch` |
| AC-67 | `test_delivery_git.py::test_auto_merge_matrix_and_audit` |
| SEC-27 | `test_deletion.py::test_cooling_purge_and_git_history_retained` |
| FX-07 | `test_fixture_phase4.py::test_fx07_topic_merge` |
| FX-08 | `test_fixture_phase4.py::test_fx08_pr_delivery`（fake GitHub + 本地裸仓库） |
| FX-09 | `test_fixture_phase4.py::test_fx09_restart_and_lease_sweeper` |

### 13.2 Fixture 场景

| FX | 输入 | 断言 |
|---|---|---|
| FX-07 | 三日错误条目（同 canonical_key 2 次、冲突字段 1 处、人工段落 1 段） | 三文件存在；条目唯一；冲突块显式；重跑计数=实际出现；区外文本保留；标记丢失→manual 不覆盖 |
| FX-08 | 本地裸仓库 + fake GitHub；daily 与 monthly 各 1 | 分支 `review/daily/2026-10-27`；trailer 四项；PR 六段；同 period 复用 PR；无 force push；monthly 不自动合并 |
| FX-09 | 过期租约 + dispatcher 启动 | run→retry_wait→succeeded；无重复产物；sweeper 记 `retry_from_state`；启动补算 next_run_at；epoch 变化 |

---

## 14. 施工顺序（每小节一个提交）

| 提交 | 内容 | 可独立验证 |
|---|---|---|
| C1 | 本文档 `docs/PHASE4-SPEC.md` | 文档评审 |
| C2 | 迁移 0005 + 契约（delivery/dashboard/settings/deletions）+ RLS 矩阵 | `alembic upgrade/downgrade` + `test_contract` + RLS |
| C3 | delivery 包：markers/topics/index/pr_body/manifest/git_ops/providers/object_store + 单测 | `pytest packages` 级单测 |
| C4 | API 交付：services_delivery、repo_delivery、routers/deliveries、runner 接入、rollback、edited 重评审 | AC-39/44 初步 |
| C5 | webhook 回写 + review_search + 兼容别名 | SEC-16/17 回归、AC-48 预备 |
| C6 | 专题重渲染（AC-40/FX-07） | FX-07 |
| C7 | 调度：schedule/dispatcher/sweeper/settings schedule/git/usage/retrieval | AC-43/51/52/FX-09 |
| C8 | 仪表盘 + 搜索三模式 | AC-45…AC-48 |
| C9 | SEC-27 删除冷却 + purge + 告知页 | SEC-27 |
| C10 | Fixtures FX-07/08/09 + evidence | FX 全绿 |
| C11 | 前端页面（reviews/topics/settings/dashboard/search） | `tsc/lint/build` |
| C12 | 容器 E2E（dispatcher 服务、AC-39 对象存储等效证据） | E2E |
| C13 | 文档：`PHASE4-STATUS.md`、`IMPLEMENTATION-NOTES §2D`、`HANDOFF.md`、`README` 索引 | 状态签收 |

---

## 15. 待拍板问题

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| Q1 | Phase 4 是否必须把复盘执行迁到 worker-review | (a) API 进程 + sweeper；(b) 新增 worker-review | (a)，Phase 5 迁 |
| Q2 | 专题 `drop` 语义 | (a) tombstone `dropped`；(b) 复用 stale | (a) |
| Q3 | 删除是否硬阻塞于导出 | (a) 不阻塞，提示；(b) 强制导出 | (a)，Phase 5 再收紧 |
| Q4 | `索引.md` 格式 | (a) §5.5；(b) 等需求补充 | (a)，冻结 |
| Q5 | Git 连接凭据 AAD | (a) `workspace|conn`；(b) 沿用 `workspace|provider` | (a)，符合 ARCH §8.5 |
