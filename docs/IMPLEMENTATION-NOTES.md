# mylife 实现说明与偏差登记（Phase 0）

> 版本：v0.1 ｜ 日期：2026-11-07 ｜ 对应代码：`apps/api`、`apps/web`、`packages/contracts`、`infra/`
>
> 本文只记录「实现与设计文档的差异」与「文档内部冲突的裁决」，不重复设计本身。
> 未列入本文的内容，均按 `docs/` 基线实现。

---

## 1. 文档冲突与裁决

| # | 冲突 | 裁决 | 理由 |
|---|---|---|---|
| C1 | `DIRECTORY-CONTRACTS §2` 写 `packages/contracts`，`API-CONTRACTS §9.1` 写 `packages/contract` | 采用 `packages/contracts`，Python 包名 `mylife_contract` | `DIRECTORY-CONSTRAINTS.md` 是 CI 可执行的目录规范，优先级更高；导入名沿用 §9.1 的 `mylife_contract` |
| C2 | `API-CONTRACTS §3.10` 要求 `users` 有 `platform_admin` 角色，`DATA-MODEL §3.1` 的 `users` 无角色列，`workspace_members.role` CHECK 只允许 owner/admin/member | 新增 `users.is_platform_admin boolean NOT NULL DEFAULT false` | 最小扩展；不把平台角色混入租户成员角色（避免跨租户语义污染） |
| C3 | `SEC-06` 要求跨租户 404，`SEC-25` 要求跨租户命中 `security_abort` | 对外 404；内部写 `audit_logs.action=security_abort`、`detail.reason=TENANT_LEAK` | Phase 0 尚无 agent run，`security_abort` 以审计事件表达；审批入口 Phase 3 才出现，不存在“可 approve”路径 |
| C4 | `DATA-MODEL §6.4` 规定 `workspaces` 策略为 `USING (id = app_workspace_id())`，但 `GET /workspaces` 必须列出用户全部 workspace（AC-03/AC-04） | `workspaces` 的 SELECT 策略额外允许“我是成员的 workspace”；`workspace_members` 的 SELECT 允许本人成员行 | 否则 RLS 下只能看到 active workspace，AC-03/AC-04 无法通过；策略仍保证用户只能看到自己的 workspace |
| C5 | `REQUIREMENTS §6.3` 与 `FR-E9` 的复盘目录冲突 | 未在 Phase 0 实现；按 `DECISIONS.md` 1D（`wiki/复盘`），Phase 3/4 落地 | 已有决策，无需新增 |
| C6 | `API-CONTRACTS §1.3` 要求 7 天空闲滑动续期，`DATA-MODEL sessions` 无 `last_used_at` | Phase 0 仅实现 30 天绝对有效期 | 避免先加无来源字段；Phase 1 增加 `last_used_at` 并补滑动续期与测试 |

---

## 2. 有意偏离（Phase 0 范围裁剪）

| # | 项目 | 现状 | 后续 |
|---|---|---|---|
| D1 | 会话空闲滑动续期 | 未实现，仅绝对 30 天 | Phase 1 加 `sessions.last_used_at` |
| D2 | 审计五类全覆盖（AC-07 / SEC-24） | 已覆盖注册、登录成功/失败、登出、登出所有设备、workspace 创建/重命名、邀请码创建、security_abort；导入/删除/复盘生成随对应功能在 Phase 1–4 接入 | 对应阶段补齐 |
| D3 | 文档树之外的文档/搜索/导入 API | 仅 `GET /documents/tree`（Phase 0 读路径） | Phase 1 |
| D4 | MinIO | compose 已声明，但 Phase 0 API 不依赖；本机镜像仓库不可达，未纳入本次全栈启动 | Phase 1 接入导入与对象存储 |
| D5 | `provider_credentials` API | 仅落库 + 信封加密模块 + 集成测试；无设置页/端点 | Phase 5（FR-G1） |
| D6 | `sessions.token_version` 的实际校验语义 | `logout-all` 对全部会话 `token_version += 1` 且 `revoked_at = now()`；校验以 `revoked_at` 为失效判定 | token 为不透明随机串、服务端只存哈希，版本号用于审计与 AC-06 断言；若后续改为带版本 token，再启用版本比对 |
| D7 | CSRF token | 纯 double-submit（Cookie 与头相等），未做 HMAC 签名或会话绑定 | 与 §1.3 一致；如需强化，Phase 2 增加签名并保持兼容 |
| D8 | `GET /workspaces/{id}` 的 `If-Match` 弱校验 | 未实现（`PATCH` 接受可选头但未校验） | Phase 1 补 ETag/If-Match |

---

## 2A. Phase 1 实现偏差（2026-11-07）

| # | 项目 | 现状 | 理由 / 后续 |
|---|---|---|---|
| P1-1 | ZIP 原件与快照存储 | 本地数据根（`MYLIFE_DATA_ROOT`）而非 MinIO | MinIO 镜像在本机不可达；DIRECTORY-CONSTRAINTS §3 已定义本地快照目录，Phase 2 再接 MinIO |
| P1-2 | `documents.raw_hash` / `parser_version` / `tokenizer_version` | 新增列 | AC-12 要求同 commit 重同步“parser 调用增量 0”，必须在解析前判断原始文件是否变化 |
| P1-3 | `document_chunks.line_start/line_end` | 新增列 | 搜索结果 `line_range` 与后续证据行号 |
| P1-4 | `job_runs.progress_json/counters_json/failed_items_json/payload_json/parent_job_id/snapshot_id` | 新增列 | API-CONTRACTS §3.3 的进度、失败明细、重试链没有对应列 |
| P1-5 | `source_snapshots.storage_path` | 新增列 | 资产代理需要定位该快照的解包目录 |
| P1-6 | 导入任务执行 | Celery worker（`apps/worker`），API 只投递任务、不导入 worker 代码 | 保持依赖方向；`MYLIFE_QUEUE_BACKEND=off` 时任务保持 queued，供测试单独执行 |
| P1-7 | Git 克隆 | `--depth 1` 仅用于网络源；本地 `file://` 源关闭浅克隆 | Windows 上 `file://` + 浅克隆出现 `invalid index-pack output` |
| P1-8 | 向量路超时 | Postgres `statement_timeout=250ms` + `plan_cache_mode=force_custom_plan`，超时记 `degraded=vector_timeout` | 用 `asyncio.wait_for` 取消 in-flight 查询会把连接/事务置于不可用状态 |
| P1-9 | 向量检索 SQL | 内层只按距离排序（HNSW 可用），外层再按 path/ordinal 稳定排序 | 附加排序键会让 planner 放弃 HNSW，退化为 80k 行全表距离计算 |
| P1-10 | 资产存储路径 | 每个 job 使用 `snapshots/{job_id}` 短目录 | Windows MAX_PATH：`workspaces/{uuid}/snapshots/git-{conn}-{job}` 下 `.git/objects/...` 超长 |
| P1-11 | 分块 ID | 修改文档时删除并重建 chunks（新 ID）；重命名文档保留 ID 与 embedding | AC-12 只要求重命名稳定；修改文档不保证 chunk ID 稳定 |
| P1-12 | 复盘搜索 | `source_type=review` 返回空集合 | `review_search` 在 Phase 3 落地 |
| P1-13 | 目录扫描 | 不递归子模块、不读 LFS（`GIT_LFS_SKIP_SMUDGE=1`） | 与 SEC-15 一致 |
| P1-14 | 分词实现 | 中文片段用 jieba `cut_for_search`；ASCII/数字片段直接按字母数字切分 | jieba 处理 40KB 纯 ASCII（代码/哈希/日志）会显著变慢；直接切分对检索语义无影响，且 40KB 文档解析从百毫秒级降到毫秒级 |
| P1-15 | 进度与批量性能 | 每 10 个文件提交一次进度；chunk 与 snapshot_documents 使用 executemany | 进度需对 API 可见；逐行往返在 40k chunk 时不可接受 |
| P1-16 | 单 Workspace 单 Vault | 一次导入的 snapshot 视为该 workspace 的完整集合；未出现的文档会被删除 | 与“首版只支持单 Vault”一致；多 Vault 留二期（ARCHITECTURE §14） |

---

## 2B. Phase 2 实现偏差（2026-11-07）

| # | 项目 | 现状 | 理由 / 后续 |
|---|---|---|---|
| P2-1 | `agent_runs.finished_at` | 新增列 | DATA-MODEL §4.1 无该列，但 API 的 `RunDetail` 需要终态时间 |
| P2-2 | `messages.usage_json` | 新增列 | `Message.usage` 契约需要持久化 token/模型/耗时 |
| P2-3 | `conversations.deleted_at` | 新增列 | §3.6.4 要求软删除，原表无该列 |
| P2-4 | chat 执行位置 | API 进程内 `asyncio` 任务（不是 Celery） | chat 是低延迟交互，跨进程会失去 token 流；持久化事件仍写 `run_events`，重启后可回放 |
| P2-5 | token 流传输 | 进程内总线（`runbus`） | 多副本部署时 token 不跨进程；客户端以最终 `messages` 为准 |
| P2-6 | `create_review` | 只实现 preview（HMAC token，10 分钟） | confirm/enqueue 依赖 Phase 3 的 `POST /reviews` |
| P2-7 | `get_review` 的 sections/entries | 返回空数组 | 结构化复盘数据在 Phase 3 写入 |
| P2-8 | 事件归档 | 未实现 | 保留期与归档任务随运维阶段补充 |
| P2-9 | 注入检测 | 8 类模式（覆盖 FX-05） | 检测命中即剔除证据并写事件，不静默过滤 |
| P2-10 | 引用编号 | 按首次出现重排为 1..k | 与正文 `[n]` 保持一致，且不保留悬空引用 |

---

## 2C. Phase 3 实现偏差（2026-11-07）

| # | 项目 | 现状 | 理由 / 后续 |
|---|---|---|---|
| P3-1 | 复盘执行位置 | API 进程内 `asyncio`（`review_runner`），非 Celery worker-review | 与 chat 同模式；事件/状态持久化不变。Phase 4 迁 worker-review（PHASE3-SPEC D8） |
| P3-2 | Writer 形态 | 确定性装配器（`review_composer`），不调用生成式 LLM | Phase 3 的可验收性依赖确定性输出；真实 LLM 写作留 `model_profile` 扩展点。因此 `review_sections` 的叙述较模板化 |
| P3-3 | 错误/方法论抽取 | 不做自动抽取；`mistakes`/`methods` 当日无条目时使用哨兵 | 抽取规则需需求 v1.1 明确（PHASE3-SPEC §6/§13）；`ITEM_SCHEMAS` 与物化链路已就绪 |
| P3-4 | `reviews` 版本模型 | 每 `(ws,kind,period)` 一行，`version` 为当前指针，历史在 `review_versions` | 同时满足 DATA-MODEL §3.8 唯一约束与 §7.1 回滚指针；`superseded_by` 恒 NULL（PHASE3-SPEC D3） |
| P3-5 | `reviews.status` | CHECK 扩为 DATA-MODEL 与 API 两套取值的并集，服务端只写 API 七值 | 兼容旧值与 `ReviewSummary.status`（D1） |
| P3-6 | `is_active_state` | 移除 `needs_human` | API §7.1 将 `needs_human` 列为终态；0003 与之冲突（D2） |
| P3-7 | 预算累计落点 | `usage_events` 新增 `run_id/attempt_id/role/cost`，按 run 聚合 | `state_payload` 是整块覆盖写，并发/回收会丢计数（D4） |
| P3-8 | `run_events.stream_epoch` | 新增列 | API §7.2 要求，0003 未建（D14） |
| P3-9 | `documents.parse_warnings` | 新增列，存日期来源冲突/非法候选 | AC-34 只要求“记 warning”，落点由实现确定（D15） |
| P3-10 | SEC-16/17 范围 | 只做验签 + `delivery_events` 幂等；`pull_request.merged` 记 `ignored` | `delivery_records` 回写属 Phase 4（D5） |
| P3-11 | webhook 租户定位 | `SECURITY DEFINER` 函数 `webhook_connection_lookup`，owner 为 `mylife_webhook`（BYPASSRLS，仅 `SELECT git_connections`） | 匿名请求无租户上下文；不放开表级 RLS |
| P3-12 | `create_review` 确认通道 | `POST /reviews` 新增可选 `confirmation_token`；无 token 视为人工显式确认 | API §3.7.1 原请求体无该字段（D6） |
| P3-13 | 复盘取消 | 仅 `created/snapshotting/planning`；之后走 `reject` | API §3.6.7 语义；`/runs/{id}/cancel` 按 kind 分流 |
| P3-14 | `edited_content_md` | 拒绝绕过（`REVIEW_GATE_FAILED`） | 完整“编辑 → 新 draft → 重评审”链依赖 Phase 4 交付 |
| P3-15 | 租约续租 | runner 内迁移时续租；独立 sweeper 留 Phase 4 | 单进程内不存在僵尸 run；多副本需 beat |
| P3-16 | fixture 目录 | `apps/api/tests/fixtures/`，pytest marker `fixture` | ACCEPTANCE §4.1 写仓库根 `tests/fixtures/`（D16） |
| P3-17 | `coverage_gaps` | 存 `review_sections.structured_json` + `run_events`，不新增表 | 文档未定义落点（D12） |
| P3-18 | 年报学习地图 | 从 weekly `method_landing` 聚合，而非 daily `learning_summary` | 只读下级 rollup（AC-38）；无周报时为哨兵 |
| P3-19 | 专题 topic 取值 | DB 英文枚举，API 中文 ↔ 英文双向映射 | DATA-MODEL §4.14 vs API §3.7.13（D17） |
| P3-20 | `review_entries` 行号 | DB `line_start/line_end`，API 序列化为 `line_range` | 两处文档命名不一致（D18） |
| P3-21 | `estimated_tokens` | 对象 `{input,output}`，并与 kind 预算比较 | §11.6 示例 vs 原 `int`（D19） |

---

## 2D. Phase 4 实现偏差（2026-11-08）

> 完整裁决见 `docs/PHASE4-SPEC.md §1`（D1–D43）；此处只记录**实现层**与文档的差异。

| # | 项目 | 现状 | 理由 / 后续 |
|---|---|---|---|
| P4-1 | 复盘执行位置 | 仍在 API 进程内 asyncio；新增独立 `dispatcher` 容器负责调度/回收 | 迁移 worker-review 成本高且不影响门禁；Phase 5 可迁（D1） |
| P4-2 | 对象存储交付 | 本地数据根 `artifacts/deliveries/{id}/`，非 MinIO | MinIO 镜像本机不可达（P1-1）；ZIP 作为 AC-39 等效证据 |
| P4-3 | `DeliveryRecord.target` | DB 允许 `db_only`；API 映射为 `object_store` | 兼容历史枚举（D41） |
| P4-4 | `ReviewSummary.delivery_status` | 对象存储交付显示 `ui_diff` | API 枚举无 `object_store`；`ui_diff` 语义即“无 PR 的本地交付” |
| P4-5 | 专题 target mode | 文件为 `update_allowed`，只有 occurrence 行是 append-only | ARCH §7.3 要求全量重渲染（D2） |
| P4-6 | `topic_items.status` | 增 `dropped`（tombstone），`confirm(drop)` 写它 | 原实现写 `stale`，与“陈旧”语义冲突（D8） |
| P4-7 | `topic_occurrences.line_start` | `NOT NULL DEFAULT 0`；唯一键含 0 哨兵 | NULL 使唯一约束失效（D4） |
| P4-8 | `occurrence_count` | 物化后由 `COUNT(*)` 重算 | 原实现无条件 +1，重跑虚增（D5） |
| P4-9 | 错误/方法论来源 | 从当天笔记的“做错的事 / 正确方法论”小节确定性解析（无 LLM） | 原 Phase 3 只走哨兵；解析规则为 `- 字段：值 | …` 形式，登记为临时契约 |
| P4-10 | Git 凭据 AAD | 新写入用 `provider=f"git:{conn_id}"`，解密兼容旧的 `"git"` | ARCH §8.5 要求绑定连接（D22） |
| P4-11 | Git auth_type | wire `app` ↔ DB `github_app` 服务层映射 | 两处枚举不一致（D21） |
| P4-12 | `GET/PUT /settings/git` | 返回 `{items:[...]}`；`role` 由请求指定（默认 source） | 原契约单对象无法表达 source+output（D23） |
| P4-13 | `sync_enabled` | 派生自 `interval > 0`（仅 source） | 请求体无该字段（D24） |
| P4-14 | 调度触发入口 | `trigger=schedule` 在公开 `POST /reviews` 被拒绝；dispatcher 直接调用服务层 `admit_scheduled_review()` | 无内部认证通道（D15） |
| P4-15 | 定时空周期 | scheduled 固定 `allow_empty=true` | 空日是合法复盘（D16） |
| P4-16 | 跨租户调度扫描 | `SECURITY DEFINER` 函数（`mylife_dispatch` 角色），只暴露调度列 | FORCE RLS 下普通 SELECT 返回 0 行（D26） |
| P4-17 | 续跑扫描 | `dispatch_resumable_review_runs` 返回 `has_review`；调用方不得再 JOIN `agent_runs` | 外部 JOIN 会重新受 RLS（迁移 0007） |
| P4-18 | `catchup_days`/`enabled` 落点 | `workspace_schedules` 每行；PUT 同时写 `workspace_settings` | 两表职责分工（D13） |
| P4-19 | 周/年日期 | 周固定周一、年固定 1 月 1 日，仅时间可配 | 表结构无 weekday 列（D12） |
| P4-20 | `agent_runs.trigger_type` | 新增列；调度 run 写 `schedule` | 原表无调度来源（D25） |
| P4-21 | outbox / heartbeat | 新增 `outbox_events`、`dispatcher_heartbeats` | ARCH §11/§16.6 无落点（D27） |
| P4-22 | Celery 投递时机 | 导入任务在事务提交后投递（after_commit） | 提交前投递会被 worker 读成 job not found（静默丢失） |
| P4-23 | `POST /deliveries/{id}/download` | 新增端点返回本地 ZIP | 契约只定义 302/直传，无本地降级下载（D37） |
| P4-24 | 删除最小链路 | Phase 4 只做 requested→cooling→purged/cancelled + 告知；不强制先导出 | AC-55 属 Phase 5（D17/D20） |
| P4-25 | `delivery_records.status` | DB 保留并集；服务层只写 API 子集，`conflict→blocked`、`committing→pending` | 两处枚举并集（D41） |
| P4-26 | 编辑重评审 | `edited_content_md` 按 `## 标题` 拆分并重跑 G0–G9；通过后更新章节再交付 | 章节映射按 §5.1 标题（D35） |
| P4-27 | 回滚 | 生成 version+1（复制目标版本内容与子表），再交付 | 单行指针模型下无法直接改回（D34） |
| P4-28 | `索引.md` 格式 | 冻结为 frontmatter + auto 区：最新日/周/年 + 三专题 open 计数 | 文档未定义内容（D10） |
| P4-29 | 专题 `stale` 自动迁移 | 未实现，只有人工/显式操作产生 | N/TTL 未定义（D30） |
| P4-30 | 删除 purge 表集合 | 取 API §3.11.7 与 DATA-MODEL §9.2 并集，补 `git_connections/workspace_schedules/job_runs/...` | 两份清单不一致（D19） |

---

## 3. 运行时与平台注意事项

1. **Windows + async psycopg**：psycopg 异步模式不支持 `ProactorEventLoop`。
   - 本机启动 API 请用 `uv run python -m mylife_api.devserver`（已固定 `SelectorEventLoop`）；
   - 生产为 Linux，不受影响；
   - 测试在 `tests/conftest.py` 顶部切换事件循环策略。
2. **本地端口**：Windows 的 Hyper-V 保留端口段包含 5432。`docker-compose.dev.yml` 将 Postgres 映射到 `25432`；
   对应 `.env` 中 `MYLIFE_DB_PORT=25432`（容器内仍使用 `postgres:5432`）。
3. **Docker 端口发布与 internal 网络**：仅接入 `internal: true` 网络的容器不会发布宿主端口。
   dev override 将 `internal` 设为 `internal: false`；生产 base compose 保持隔离。
4. **Alembic 文件编码**：`alembic.ini` 必须保持纯 ASCII。Windows 中文环境读取配置默认使用 GBK，
   非 ASCII 字符会导致 `UnicodeDecodeError`。
5. **日志**：默认不记录未处理异常的堆栈（可能含 SQL 参数与密码哈希）。排查时显式设置
   `MYLIFE_LOG_EXCEPTIONS=true`，且仅在受控环境使用。

---

## 4. 事务与审计语义

- 每个 HTTP 请求一个事务；成功提交。
- 已处理的业务错误（`AppError`）也提交事务，以便同事务的 `audit_logs`（登录失败、跨租户尝试等）落库；
  未处理异常一律回滚。
- 预期内的唯一约束冲突（并发同邮箱注册）使用 `SAVEPOINT`，避免事务进入 aborted 状态。
- 该语义是本项目“审计与业务同事务”要求的实现方式；若后续引入明确的“业务失败必须回滚所有写入”，
  需把审计拆到独立 outbox 写入点。

---

## 5. 安全实现要点（Phase 0）

| 控制 | 实现位置 |
|---|---|
| Argon2id 密码哈希 | `mylife_api/security.py` |
| 会话 token 只存 SHA-256 | `mylife_api/security.py`、`sessions.token_hash` |
| HttpOnly + Secure + SameSite=Lax Cookie | `session_cookie_kwargs` |
| double-submit CSRF（含 login/register） | `middleware.CsrfMiddleware` |
| 登录失败限流与锁定 | `ratelimit.py` + `services.login` |
| workspace 只来自服务端会话 | `deps.get_run_context`、`RunContext` |
| 入参禁止 `workspace_id`（`extra=forbid` + 稳定 reason） | `common.ApiModel`、`errors._validation_details` |
| RLS ENABLE + FORCE + 默认拒绝 | 迁移 `0001_phase0` |
| 应用角色 `NOBYPASSRLS` | `infra/postgres/init/001-roles.sh`、迁移兜底 |
| 凭据信封加密（KEK→DEK，AES-256-GCM，AAD 绑定租户/用途） | `mylife_api/crypto.py` |
| 日志脱敏（不记录堆栈/SQL 参数） | `errors.install_error_handlers` |
| Git 凭据信封加密（AAD=`git:{conn_id}`） | `mylife_api/services_settings.put_git_connection` |
| 交付路径白名单（禁止 `..`/绝对路径） | `mylife_delivery.git_ops.validate_relative_path` |
| 永不 force push（rebase 重试 2 次后保留分支） | `mylife_delivery.git_ops.GitRepo.push` |
| 删除冷却与 Git 历史保留告知 | `mylife_api/services_deletion`、`DELETION_NOTICE` |

---

## 6. 待决策 / 风险

| # | 事项 | 影响 | 建议 |
|---|---|---|---|
| O1 | `security_abort` 是否应对外返回 403（而非 404） | 与 SEC-06 冲突 | 保持 404 + 内部审计；如安全评审要求显式 403，需同步修改 SEC-06 |
| O2 | 平台管理员是否允许读取审计中的 `target`（可能含邮箱） | 隐私 | Phase 5 管理后台只展示脱敏邮箱 |
| O3 | MinIO 镜像源迁移 | 部署可用性 | 部署时确认可用的 MinIO 镜像/版本，并锁定 digest |
| O4 | Windows 本地开发是否强制 Docker | 开发体验 | README 已建议用容器运行 API；如需原生运行，保留 `devserver` 入口 |

---

## 7. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-07 | Phase 0 初版：记录 6 项文档冲突裁决、8 项范围裁剪、5 条运行时注意事项 |
| v0.4 | 2026-11-08 | Phase 4：新增 §2D（30 项实现偏差）、安全实现要点补充 |
