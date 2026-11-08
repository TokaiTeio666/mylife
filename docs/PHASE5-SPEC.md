# Phase 5 施工规格（管理后台、导出与删除、LLM/Embedding 设置、备份与签收）

> 版本：v1.0 ｜ 日期：2026-11-08 ｜ 阶段：Phase 5 / M6
>
> 本文是 Phase 5 的**施工级规格**：把设计文档中的门禁条款提取为可直接实现、可测试的条目，
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
| AC | AC-07、AC-49、AC-50、AC-53、AC-54、AC-55、AC-57、AC-59、AC-61、AC-62、AC-63、AC-64 |
| SEC | **SEC-01…SEC-30 全集**（Phase 5 = Phase 4 + SEC-28/29 + 全量回归） |
| FX | FX-11（蓝绿换维）；FX-09 的容器级重跑（AC-59） |
| 质量 | 审计五类全覆盖；导出 ZIP 条目数对账 0 差；删除冷却可撤销且 purge 全链路 0 命中；恢复演练 RTO ≤4h、RPO ≤24h；FX-11 全绿 |

### 0.2 明确不在 Phase 5 范围

| 项 | 原因 |
|---|---|
| 真实生成式 LLM Writer | P3-2 保持确定性装配器；本阶段只补“配置与连接测试”链路 |
| MinIO 作为导出/交付存储 | 本机镜像不可达（P1-1/P4-2）；导出走本地数据根 + 直传下载，登记偏差 |
| 向量数据库外接（Qdrant/Milvus） | 决策 2B 排除 |
| 在线热切换（双写双读） | 决策 2B 排除，蓝绿即可 |
| 多语言完整翻译 | AC-63 只要求“可切换且至少 3 个页面文案变化”，英文资源可只覆盖公共壳与 3 个页面 |
| 团队协作 / 收费 | 首版非目标 |

### 0.3 验收证据形态

| 证据 | 位置 |
|---|---|
| AC 逐条测试 | `apps/api/tests/test_*.py`（新增 `test_admin.py`、`test_export.py`、`test_settings_llm.py`、`test_settings_embedding.py`、`test_usage.py`、`test_audit_ac07.py`、`test_backup_drill.py`） |
| FX report | `docs/evidence/phase5/fx11-embedding-migration.json` |
| AC-55 导出对账 | `docs/evidence/phase5/export-reconciliation.json` |
| AC-59 容器演练 | `docs/evidence/phase5/ac59-container.json` |
| AC-61 恢复演练 | `docs/evidence/phase5/restore-drill.json` |
| AC-62/63/64 前端 | `docs/evidence/phase5/frontend/*.json` + 截图 |
| SEC 汇总 | `docs/evidence/phase5/sec-summary.json` + `PHASE5-STATUS.md §3` |

---

## 1. 文档冲突裁决（实施前冻结）

> 规则：文档未写死但实现必须二选一的点。每条给出裁决；与本文不符的实现以本文为准，
> 并在 `IMPLEMENTATION-NOTES §2E` 登记。

| # | 冲突/空白 | 出处 | 裁决 |
|---|---|---|---|
| D1 | 导出下载：API §3.11.3 要求 302 到 MinIO 预签名（TTL 10 分钟），仓库无 S3 客户端 | API §3.11.3 vs 现状 | **直传 200 `application/zip` + `Content-Disposition`**（与 `GET /deliveries/{id}/download` 同构）；302 预签名登记为部署期可选增强 |
| D2 | 导出存储位置 | DIRECTORY §3/§9 vs 本地降级 | `{data_root}/workspaces/{ws}/exports/{export_id}.zip`；保留 7 天，由 dispatcher sweeper 删除 |
| D3 | `export_jobs.status`：DB `succeeded` vs API `ready` | DATA-MODEL §4.24 vs API §3.11.2 | DB CHECK 追加 `ready`；服务层**写入 `ready`**；读取时把历史 `succeeded` 映射为 `ready`（与 D41 同风格） |
| D4 | 管理端跨租户读：`workspaces`/`audit_logs`/`usage_events` 均 FORCE RLS | DATA-MODEL §6 vs AC-54 | 新增 `SECURITY DEFINER` 函数（owner=`mylife_admin`，BYPASSRLS），**只暴露元数据列**；不给 `mylife_app` 任何 BYPASSRLS |
| D5 | 系统配置落点 | API §3.10 要求逐键 `{key,value,locked,source}`，现只有全局布尔 | 新增非租户表 `platform_settings(key PK, value_json, updated_at, updated_by)`；环境变量锁定的键**不落库**，读取时以 `source=env, locked=true` 合成 |
| D6 | `provider_credentials.status`：DB `active/invalid/disabled` vs wire `valid/invalid/untested/missing` | DATA-MODEL §4.22 vs API §3.9.1 | DB CHECK 追加 `untested`；映射 `active→valid`、`invalid→invalid`、`disabled→missing`、`untested→untested`；无行 → `missing` |
| D7 | LLM/Embedding 连接参数落点 | 现为纯环境变量 | `provider_credentials` 增 `base_url/model/model_profile`；**有行则以 DB 为准，无行回落环境变量**；API Key 仍只存密文 |
| D8 | 蓝绿换维的表名 | DATA-MODEL §4.26 只写 `document_chunks_v{n}` | `embedding_profiles` 增 `chunk_table text`；新建 profile 时创建 `document_chunks_v{n}`（含 HNSW），切换时改 `workspace_settings.embedding_profile_id`；**旧表 7 天后删除**，删除前写快照清单 |
| D9 | 覆盖率的定义 | API §3.9.4 只写“已 embedding chunk 占比” | `coverage = count(chunks in profile table with embedding) / count(chunks in profile table)`；阈值 **≥0.995** 才允许自动切换（API 写 0.995，AC-50 写 100%；取 **1.0** 作为 AC-50 断言，自动切换阈值用 0.995 并在响应里回显实际值） |
| D10 | AC-53 `review_runs` 口径 | AC-53 vs 现实现（`usage_events.kind='review'` 恒 0） | **改由 `agent_runs` 派生**：`kind IN ('daily','weekly','yearly') AND state='succeeded'`；`usage_events` 只负责 token/calls |
| D11 | 失败/部分成功的 chat 是否计量 | AC-29/AC-53 vs 现状 | **是**：provider 已产生 usage 即写 `usage_events`（`degraded=true` 或 `role='partial'`），保证对账 |
| D12 | `cost` 估算 | API §3.9.10 要求 USD 估算 | 用 `usage_events.cost`（numeric(12,6)）；**未知单价记 0 并在响应 `cost_source="unconfigured"`**，不伪造数字 |
| D13 | 用量日桶时区 | API §3.9.10 未写死 | 按 workspace 时区分桶（与预算窗口同源），不按 UTC |
| D14 | 删除冷却期“停用所有写操作” | API §3.11.4 vs 现只停调度 | **middleware 级只读**：`deletion_requests.state='cooling'` 时除 `GET`、`/deletions/*`、`/auth/*` 外全部 403 `DELETE_IN_COOLING` |
| D15 | 导出是否为删除前置 | DATA-MODEL §9.1 vs Phase 4 D20 | Phase 5 **改为软前置**：无“7 天内成功导出”时 `POST /deletions` 返回 409 `CONFLICT`，`details.reason="EXPORT_REQUIRED"`，除非请求带 `skip_export=true` 且 owner 二次确认（审计记录） |
| D16 | 账户级删除范围 | PHASE4-STATUS §8“部分” | `scope=account` 为**每个 owned workspace 各插一行**，purge 逐 workspace 执行；完成后 `users.status='deleted'` 且撤销全部会话 |
| D17 | `cancellable_until` | API §3.11.4 响应含，契约缺 | 响应补 `cancellable_until = purge_at` |
| D18 | 审计五类 action 命名 | AC-07 只写“5 类” | 冻结为族：`user.login`、`ingest.started`、`deletion.requested`、`settings.*.updated`、`review.created`；AC-07 测试按族断言 |
| D19 | i18n 机制 | FRONTEND §9 vs 现静态 `copy` | 引入 `lib/copy/index.ts`（locale 注册表）+ `LocaleProvider`/`useCopy()`；保留 `export const copy` 指向 zh-CN 以兼容 server components；英文资源覆盖公共壳 + 3 个页面 |
| D20 | PWA 实现方式 | FRONTEND FE13 vs Next 15 无内置 SW | 手写 `public/sw.js` + `app/manifest.ts` + 注册组件；**只预缓存静态壳，绝不缓存 `/api/*`** |
| D21 | 隐私/协议页位置 | AC-64 要求匿名 200 | 放根级 `app/privacy`、`app/terms`（**不在 `(app)` 组**，避免 401 重定向） |
| D22 | 备份加密 | SEC-28 | `age` 或 `openssl enc -aes-256-ctr -pbkdf2`（取本机可用者），密钥不入仓；目录 0700，文件 0600 |
| D23 | MinIO 备份 | AC-61 要求对象数一致 | 本机无 MinIO 对象（交付走本地）；脚本对 `MYLIFE_DATA_ROOT` 做 `tar` 归档并在报告标注 `minio: not_applicable`，不伪造计数 |
| D24 | SEC-29 扫描工具 | SEC-29 “CI 阻断；SBOM 可查” | CI 增 `pip-audit`（Python）、`npm audit --audit-level=high`（Node）、`cyclonedx-py` 生成 SBOM；trivy 作为可选 job（镜像不可达时不阻断） |
| D25 | `job_runs` 僵死 `running` | AC-59 “无静默丢失” | dispatcher 每 tick 回收 `status='running' AND started_at < now()-interval '30 min'` → `failed`（`INGEST_STALE_RUNNING`），并允许重试 |
| D26 | `provider_credentials` 的 `base_url` 白名单 | API §3.9.2 SSRF 约束 | 新增 `MYLIFE_ALLOWED_PRIVATE_PROVIDER_HOSTS`（逗号分隔）；仅这些主机允许 `http://`；其余必须 `https://` |
| D27 | 管理端限流 | API §8.1（100 次/分钟） | 所有 `/admin/*` 共用 `rl:admin:{user_id}` 100/60s，超限 `RATE_LIMITED` |
| D28 | 删除后 `users` 行保留 | DATA-MODEL §9.2 vs 现实现 | 保留行（`status='deleted'`），审计需要；`email` 保留供管理员核对，不进入任何业务查询 |
| D29 | 导出包含审计 | AC-64 要求五类 | `include` 含 `audit` 时导出**本人/本 workspace 可见的 `audit_logs`**（脱敏 `detail`，不含他人 workspace 行） |
| D30 | FX-11 的“旧表延迟删除” | DATA-MODEL §7 | 切换后写 `retire_at=now()+7d`；dispatcher 到期 `DROP TABLE IF EXISTS document_chunks_v{n}`，删除前把行数/哈希写入 `outbox_events(payload)` 作为快照清单 |
| D31 | 编辑重评审的 G4 断链 | Phase 4 遗留缺陷 | `repo_review.list_entries` 补 `source_date`/`evidence_id`；`_edited_sections` 补 `sentinel_used`（由 `content_md == SENTINELS[key]` 派生）；作为 C2 的前置修复 |
| D32 | 管理端是否可读正文 | SEC-30 | **不可**；`/admin/*` 响应只含计数与聚合；新增负向测试 |

---

## 2. 数据模型增量（迁移 `0011_phase5_admin_settings_export`）

所有新租户表 `ENABLE + FORCE RLS`，策略与 0005 相同；新表加入 `test_rls_matrix.py`。

### 2.1 新表

| 表 | 关键列 | 约束/索引 |
|---|---|---|
| `platform_settings` | `key text PK`、`value_json jsonb NOT NULL`、`updated_at`、`updated_by uuid` | **非租户表**（不加 RLS）；仅 `SECURITY DEFINER` 函数与 `mylife_admin` 可读写 |
| `embedding_reembed_jobs` | `id`、`workspace_id`、`profile_id`、`status(queued/running/succeeded/failed)`、`processed_chunks`、`total_chunks`、`error`、`created_at`、`finished_at` | `INDEX(workspace_id, status)`；RLS |

### 2.2 扩展列

| 表 | 列 | 说明 |
|---|---|---|
| `users` | `last_login_at timestamptz`、`disabled_reason text`、`disabled_at timestamptz` | AC-54 / AdminUser |
| `provider_credentials` | `base_url text`、`model text`、`model_profile text`；`status` CHECK 追加 `untested` | D6/D7 |
| `embedding_profiles` | `chunk_table text`、`coverage numeric(5,4)`、`previous_profile_id uuid`、`activated_at timestamptz`、`retire_at timestamptz`、`updated_at timestamptz NOT NULL DEFAULT now()` | D8/D30；`UNIQUE(workspace_id, chunk_table)` |
| `export_jobs` | `scope text NOT NULL DEFAULT 'workspace'`、`include_json jsonb NOT NULL DEFAULT '[]'`、`format text NOT NULL DEFAULT 'zip'`、`progress_json jsonb NOT NULL DEFAULT '{}'`、`artifact_bytes bigint`、`updated_at timestamptz NOT NULL DEFAULT now()`；`status` CHECK 追加 `ready` | D3；`INDEX(workspace_id, created_at DESC)`；部分唯一 `uq_export_active ON export_jobs(workspace_id, scope) WHERE status IN ('queued','running')` |
| `workspace_settings` | `embedding_profile_id` 补 FK（`ON DELETE SET NULL`） | D8 |

### 2.3 函数与角色

```sql
-- 管理端跨租户只读（只暴露元数据列）
CREATE ROLE mylife_admin NOLOGIN BYPASSRLS;  -- 不存在时创建

CREATE FUNCTION admin_list_users(p_limit int, p_offset int)
RETURNS TABLE (id uuid, email text, display_name text, status text,
               created_at timestamptz, last_login_at timestamptz,
               workspace_count bigint)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT u.id, u.email, u.display_name, u.status, u.created_at, u.last_login_at,
         (SELECT count(*) FROM workspace_members wm WHERE wm.user_id = u.id)
  FROM users u ORDER BY u.created_at DESC LIMIT p_limit OFFSET p_offset
$$;
ALTER FUNCTION admin_list_users(int,int) OWNER TO mylife_admin;

CREATE FUNCTION admin_list_workspaces(p_limit int, p_offset int) ... ;
CREATE FUNCTION admin_list_audit_logs(p_limit int, p_offset int, p_action text, p_ws uuid) ... ;
CREATE FUNCTION admin_usage_totals(p_from timestamptz, p_to timestamptz) ... ;
```

- `REVOKE ALL ... FROM PUBLIC`；`GRANT EXECUTE ... TO mylife_app`。
- `GRANT SELECT ON users, workspaces, workspace_members, audit_logs, usage_events, reviews, documents TO mylife_admin`（仅函数需要）。
- **不给 `mylife_app` 任何 BYPASSRLS**（DATA-MODEL §6.5）。

### 2.4 迁移验收

- `alembic upgrade head` / `downgrade -1` / `downgrade base` 均通过（CI 跑 base）。
- `export_jobs` 历史 `succeeded` 行在读取时映射为 `ready`（服务层，不改数据）。
- `provider_credentials.status` 历史值保持；CHECK 只追加 `untested`。
- `embedding_profiles.chunk_table` 回填为 `'document_chunks'`。
- 新表加入 RLS 矩阵；`platform_settings` 显式标注“非租户表，不进矩阵”。

---

## 3. 契约增量（`packages/contracts/mylife_contract`）

### 3.1 新文件

| 文件 | 内容 |
|---|---|
| `exports.py` | `ExportCreate`、`ExportJob`、`ExportProgress`、`ExportCreated` |
| `legal.py` | `LegalPage`（`slug/title/updated_at/sections`）——仅供前端静态渲染，不由 API 提供 |

### 3.2 现有文件修改

| 文件 | 修改 |
|---|---|
| `settings.py` | `LlmSettings`、`LlmUpdate`、`LlmTestRequest`、`LlmTestResult`、`EmbeddingProfile`、`EmbeddingSettings`、`EmbeddingProfileCreate`、`EmbeddingProfileCreated`、`EmbeddingTestRequest`、`EmbeddingTestResult`；`UsageTotals` 增 `review_runs`（已有）与 `cost_source` |
| `admin.py` | `AdminUserDisableRequest`、`AdminWorkspaceListResponse`、`AdminAuditLog`/`AdminAuditLogList`、`AdminSystemConfigItem`/`List`/`Update`、`AdminUsageResponse` |
| `deletions.py` | `DeletionRequest` 增 `cancellable_until`；`DeletionCreate` 增 `skip_export: bool = false` |
| `auth.py` | `User` 增 `is_platform_admin: bool = false`；`MeResponse.capabilities` 追加 `admin`（仅 platform_admin） |
| `enums.py` | `ExportStatus`（`queued/running/ready/failed/expired`）、`ProviderCredentialStatus`（`valid/invalid/untested/missing`） |
| `constants.py` | `AUDIT_AC07_FAMILIES`、`EXPORT_INCLUDE_KEYS`、`LEGAL_SLUGS`、`EMBEDDING_COVERAGE_THRESHOLD = 0.995` |

### 3.3 契约测试

- `test_contract.py` 断言新模型 `extra="forbid"`、字段名与 `cancellable_until` 存在。
- OpenAPI 含 `/exports*`、`/settings/llm*`、`/settings/embedding*`、`/admin/users*`、`/admin/workspaces*`、`/admin/audit-logs`、`/admin/system-config`、`/admin/usage`。

---

## 4. LLM / Embedding 设置（AC-49 / AC-50 / FX-11）

### 4.1 LLM 端点

| 端点 | 语义 |
|---|---|
| `GET /settings/llm` | `provider/base_url/model/model_profile/credential_status/last_tested_at/supports_json_mode`；**不返回明文** |
| `PUT /settings/llm` | `test_before_save=true`（默认）：**先测试，成功才落库**；失败返回 `LLM_UNAVAILABLE` 且 `provider_credentials` 行数不变 |
| `POST /settings/llm/test` | 不落库；超时 15s；错误信息脱敏（不回显 key） |

- 加密：`provider_credentials.key_ciphertext`，AAD = `workspace_id|llm`（沿用 `crypto.encrypt_credential`）。
- `base_url` 校验：仅 `https://`；`http://` 仅当主机在 `ALLOWED_PRIVATE_PROVIDER_HOSTS`（D26）。
- 审计：`settings.llm.updated`，`detail` 只含 provider/model/base_url 主机，**不含 key**。
- 运行时读取顺序（D7）：DB 行 → 环境变量 → `mock`。
- `chat_runner` / `review_runner` 的 provider 构建改为 `services_settings.build_llm_for_workspace(db, workspace_id)`。

### 4.2 Embedding 端点

| 端点 | 语义 |
|---|---|
| `GET /settings/embedding` | `active_profile`、`profiles[]`（含 `coverage`）、`credential_status`、`last_tested_at` |
| `POST /settings/embedding/profiles` | `confirm=true` 必需；创建 `building` profile + `document_chunks_v{n}` + HNSW + `embedding_reembed_jobs`；已有 `building` → `CONFLICT` |
| `POST /settings/embedding/test` | 返回 `dimension_observed`；不落库 |

### 4.3 蓝绿流程（FX-11）

1. 校验 `dimension ∈ [256, 3072]`；`model/dimension` 已存在 → `CONFLICT`。
2. `CREATE TABLE document_chunks_v{n} (LIKE document_chunks INCLUDING ALL)` 后 **重建 `embedding` 列维度**（新表，非 ALTER）与 HNSW 索引；`chunk_table` 写入 profile。
3. 后台 job：逐文档 re-embed 到新表；每批更新 `processed_chunks/total_chunks`。
4. `coverage = 已写入新表且 embedding 非空的 chunk 数 / document_chunks 总数`；`coverage >= 0.995` 才允许切换。
5. **原子切换**：单事务 `UPDATE workspace_settings SET embedding_profile_id = :new`；旧 profile `status='retired'`、`retire_at=now()+7d`、`previous_profile_id` 保留。
6. 回滚：`POST /settings/embedding/rollback`（运维端点，owner）把 `embedding_profile_id` 指回 `previous_profile_id`；旧表在 7 天内仍存在，查询立即可用。
7. **禁止** `ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(768)`；测试用 `pg_depend`/`information_schema` 断言不存在对该列的 `ALTER`（以迁移脚本 grep 断言）。
8. 检索与索引器通过 `services_search._chunk_table(workspace_id)` / `indexer` 的 profile 解析器取表名，不再硬编码 `document_chunks`。

### 4.4 FX-11 断言

`docs/evidence/phase5/fx11-embedding-migration.json`：

- 新表存在且维度为 768；旧表仍为 1024。
- 覆盖率从 <1 到 1.0 的序列；切换发生在覆盖率达到阈值之后（时间戳顺序）。
- 切换前查询（旧 profile）可用；切换后新 profile 可用；回滚后旧 profile 可用且行数不变。
- `retire_at` 已写；无 `ALTER COLUMN TYPE` 语句（源码扫描）。

---

## 5. 管理后台（AC-54 / SEC-30）

### 5.1 端点

| 端点 | 关键点 |
|---|---|
| `GET /admin/users` | `q/status/limit/cursor`；`workspace_count`、`last_login_at`；**无正文** |
| `GET /admin/users/{id}` | 同上单条；不存在 → `ADMIN_USER_NOT_FOUND` |
| `POST /admin/users/{id}/disable` | `{reason}`；撤销该用户**全部会话**；禁用自己 → `CONFLICT`；禁用最后一个 platform_admin → `CONFLICT`；已禁用 → `ADMIN_USER_DISABLED` |
| `POST /admin/users/{id}/enable` | 清 `disabled_reason/disabled_at` |
| `GET /admin/workspaces` | `q/limit/cursor`；`counts.documents/reviews/tokens_month` |
| `GET /admin/workspaces/{id}` | 含 `deletion_state` |
| `GET /admin/audit-logs` | `workspace_id?/action?/from/to/limit/cursor`；只返回元数据，`detail` 已脱敏 |
| `GET /admin/system-config` | 逐键 `{key,value,locked,source}`；`source ∈ env/db/default` |
| `PUT /admin/system-config` | 锁定键 → `ADMIN_SYSTEM_CONFIG_LOCKED`；否则写 `platform_settings` |
| `GET /admin/usage` | 跨 workspace 汇总 + 分 workspace 明细（只聚合） |
| `DELETE /admin/invite-codes/{code}` | 已使用 → `CONFLICT` |

### 5.2 不变量

- 双重校验：`require_platform_admin`（已存在）。
- 限流：`rl:admin:{user_id}` 100/60s（D27）。
- 审计：每次操作写 `audit_logs`，`workspace_id=NULL`，`user_id=admin`，`detail` 不含正文。
- **SEC-30**：`/admin/*` 无任何返回 `documents.content`、`reviews.content`、`messages.content` 的端点；新增负向测试断言响应字段集合。
- 禁用用户后：登录 → 401 `AUTH_ACCOUNT_DISABLED`；已有会话 → 401（`revoked_at`）。

---

## 6. 导出 ZIP（AC-55 导出半部 / AC-64）

### 6.1 端点

| 端点 | 语义 |
|---|---|
| `POST /exports` | owner；`Idempotency-Key` 必填；`{scope, include[], format:"zip"}`；同 scope 活跃任务唯一 → `EXPORT_LIMIT`；3 次/天 → `EXPORT_LIMIT`；202 `{export_id,status:"queued",expires_at}` |
| `GET /exports/{id}` | `status ∈ queued/running/ready/failed/expired`、`progress{files,bytes}`、`artifact_bytes`、`created_at`、`expires_at`、`error` |
| `GET /exports/{id}/download` | 200 `application/zip`，`filename="mylife-{slug}-{date}.zip"`；未就绪 → `EXPORT_NOT_READY`；过期 → `EXPORT_EXPIRED` |

### 6.2 包结构

```text
documents/          原 Markdown（按 path 还原目录）+ 可用附件
reviews/            按 reviews_root 结构（每日/每周/年度 + 专题）
conversations/*.md  每个对话一个文件（标题 + 角色: 内容）
settings.json       脱敏（无凭据、无 key_ciphertext）
usage.csv           from/to 全量 usage_events（不含正文）
audit.jsonl         include 含 audit 时（脱敏 detail）
manifest.json       {workspace_id, exported_at, include[], files:[{path,sha256,bytes}], counts:{...}}
```

### 6.3 对账（AC-55 断言）

- `manifest.counts.documents == SELECT count(*) FROM documents WHERE workspace_id=:ws`
- `manifest.counts.reviews == SELECT count(*) FROM reviews WHERE workspace_id=:ws`
- `manifest.files` 中 `documents/` 条目数 == 文档数；`reviews/` 条目数 == 复盘数（专题/索引另计并单列）
- ZIP 内每个文件 sha256 == manifest 记录；条目数差 **0**。
- 证据：`docs/evidence/phase5/export-reconciliation.json`。

### 6.4 执行与清理

- 同步执行（MVP：文档量有限）或 `asyncio` 后台任务；状态机 `queued→running→ready/failed`。
- 过期：`expires_at = now()+7d`；dispatcher 每 tick 把过期行置 `expired` 并删除 ZIP。
- 路径安全：复用 `mylife_runtime.paths.safe_join(..., vault=True)`，禁止 `..`/绝对路径/符号链接；跳过 `.git` 与隐藏文件。
- 审计：`export.requested`、`export.completed`、`export.downloaded`。

---

## 7. 删除链路收口（AC-55 删除半部 / SEC-27 回归）

| 项 | 实现 |
|---|---|
| `cancellable_until` | 响应补 `= purge_at` |
| `DELETE_IN_COOLING` | 重复请求且已有 cooling → 该码（替代 `DELETE_ALREADY_REQUESTED`，后者用于 requested 态） |
| 写冻结 | middleware：cooling 期间除 `GET`/`/deletions/*`/`/auth/*` 外全部 403 `DELETE_IN_COOLING`（D14） |
| 导出前置 | 无 7 天内 `ready` 导出 → 409 `CONFLICT`，`details.reason="EXPORT_REQUIRED"`；`skip_export=true` 需二次确认并审计（D15） |
| 账户级 | 每个 owned workspace 各一行；purge 逐 workspace；最后 `users.status='deleted'` + 撤销全部会话（D16） |
| purge 0 命中 | 每表删除后 `count(*)=0` 校验；非 0 → 记 `deletion.purge_residual` 审计 + 告警日志，**不静默** |
| 非表项 | 删除 `workspaces/{ws}` 目录、`exports/`、Redis 前缀 `rl:/quota:/schedule:{ws}`；`vectors` 随 `document_chunks` 表删除（D23 同源） |
| Git 历史 | 正向断言保留且可读（沿用 `test_deletion.py`） |
| 删除页 | 展示 `will_delete/will_retain/notice` 全文 + account scope 入口 |

---

## 8. 用量与审计收口（AC-53 / AC-07 / SEC-24）

### 8.1 AC-53

- `review_runs` 改由 `agent_runs` 派生（D10）。
- 失败/部分 chat 也写 `usage_events`（D11）。
- `cost` 用 `usage_events.cost`；未配置单价时 `cost_source="unconfigured"`（D12）。
- 日桶按 workspace 时区（D13）。
- 成员只见本人用量，owner 见全 workspace（API §3.9.10）；实现按 `ctx.role` 过滤 `user_id`。
- 测试：3 次对话 + 1 次成功复盘 → token 总数 == `SUM(usage_events)`、`calls == count(*)`、`review_runs == 成功 run 数`；跨 workspace 汇总为 0。

### 8.2 AC-07（五类审计）

- 族：`user.login`、`ingest.started`、`deletion.requested`、`settings.*.updated`、`review.created`（D18）。
- 字段：`workspace_id/user_id/action/target/ip/created_at` 齐全；**所有写审计的端点必须传 `ip`**（现状多处 `ip=None`，补齐）。
- 脱敏：`detail` 不含明文 API Key；不含 >200 字符笔记正文（`insert_audit` 已截断，另加 key 白名单）。
- 测试：依次登录 → 导入 → 删除 → 配置变更 → 复盘生成，断言五族各 ≥1 行且字段非空；正则扫描 `detail` 无 `sk-`/长正文。

---

## 9. 备份、恢复与供应链（AC-61 / AC-59 / SEC-28 / SEC-29）

| 项 | 实现 |
|---|---|
| `scripts/backup_pg.sh` | `pg_dump -Fc` → `backups/pg/{date}.dump` + `sha256`；目录 0700；`age`/`openssl` 加密（D22）；保留 7 天与 30 天两档 |
| `scripts/backup_data.sh` | `tar` `MYLIFE_DATA_ROOT/workspaces` → `backups/data/`；MinIO 不适用时报告 `not_applicable`（D23） |
| `scripts/restore_drill.py` | 恢复到新库 → 对比 `documents/reviews` 计数 + 抽样 10 篇 `content_hash` → 写 `restore-drill.json`（含耗时、RTO/RPO 判定） |
| `make backup` / `make restore-drill` | Makefile 目标 |
| `scripts/e2e_ac59.py` | 容器级：kill -9 worker → 重启 → 断言 5 分钟内续跑/`retry_wait`；同 period 无重复产物；`job_runs/agent_runs` 可见；`docker compose config` 断言 `restart: unless-stopped` |
| `job_runs` 僵死回收 | dispatcher 回收 30 分钟未完成的 `running`（D25） |
| SEC-29 | CI 增 `pip-audit`、`npm audit --audit-level=high`、`cyclonedx-py` SBOM；trivy 可选（D24） |
| `docs/evidence/phase5/ac59-container.json`、`restore-drill.json` | 演练记录 |

---

## 10. 前端（AC-54 / AC-62 / AC-63 / AC-64）

| 路由 | 内容 |
|---|---|
| `/settings` | 增 LLM 卡片（provider/base_url/model/key/测试连接）、Embedding 卡片（active/building/coverage/换维/回滚）、用量卡片（totals/series）、导出卡片（请求/进度/下载） |
| `/admin`、`/admin/users`、`/admin/workspaces`、`/admin/invites`、`/admin/audit`、`/admin/system` | 仅 platform_admin 可见（`capabilities` 含 `admin`）；表格分页；禁用/启用二次确认 |
| `/privacy`、`/terms` | 根级路由，匿名 200；含数据出境供应商说明与 Git 历史不抹除告知 |
| `lib/copy/index.ts` + `LocaleProvider` | locale 注册表；`useCopy()`；`<html lang>` 随 locale |
| `public/sw.js` + `app/manifest.ts` | PWA 可安装；离线壳；**不缓存 `/api/*`** |
| 响应式 | 390px 无横向滚动：header `flex-wrap`、长路径 `break-all`、markdown 表格滚动容器、触控目标 ≥44px |

约束：文案进 `copy`；所有数据区有 loading/empty/error；`tsc + lint + build` 通过；`scripts/check_i18n.py` 扫描硬编码中文（白名单：注释、fixture、动态 `copy.<domain>[expr]`）。

---

## 11. 安全门禁（SEC 全集）

| SEC | Phase 5 动作 |
|---|---|
| SEC-01/08 | 参数化路由矩阵：所有业务端点未登录 401；query/path 伪造 `workspace_id` 被忽略 |
| SEC-13 | 默认压缩比/文件数/解压上限断言（补默认值用例） |
| SEC-15 | hooks/submodule/LFS 端到端执行（Linux）；Windows 仍 skip 并登记 |
| SEC-21 | SVG/图片脚本用例（`sandbox` CSP + `nosniff`） |
| SEC-22 | 搜索/对话 429 用例（驱动到限额） |
| SEC-24 | AC-07 五类全覆盖 + 字段齐全 |
| SEC-25 | `security_abort` 不可 approve 的负向测试 |
| SEC-28 | 备份加密、0700、恢复需密钥 |
| SEC-29 | CI 扫描 + SBOM |
| SEC-30 | 管理端无正文读取的字段集负向测试 |
| 其余 | 回归（`test_*` 全量） |

证据：`docs/evidence/phase5/sec-summary.json`（30 行，每行 `PASS/FAIL` + 测试名/证据路径）。

---

## 12. 文件级施工清单

### 12.1 新建

| 文件 | 内容 |
|---|---|
| `apps/api/migrations/versions/0011_phase5_admin_settings_export.py` | §2 全部 DDL/函数/授权 |
| `packages/contracts/mylife_contract/exports.py`、`legal.py` | §3.1 |
| `apps/api/mylife_api/services_admin.py`、`routers/admin.py`（扩展） | §5 |
| `apps/api/mylife_api/services_export.py`、`routers/exports.py` | §6 |
| `apps/api/mylife_api/services_embedding.py` | 蓝绿换维（§4.3） |
| `apps/api/mylife_api/services_llm.py` | LLM 配置与测试连接（§4.1） |
| `apps/api/mylife_api/routers/legal.py`（可选） | 静态法务页数据 |
| `apps/api/tests/test_admin.py`、`test_export.py`、`test_settings_llm.py`、`test_settings_embedding.py`、`test_usage.py`、`test_audit_ac07.py`、`test_backup_drill.py` | 门禁 |
| `scripts/backup_pg.sh`、`backup_data.sh`、`restore_drill.py`、`e2e_ac59.py`、`check_i18n.py` | §9/§10 |
| `apps/web/src/app/admin/**`、`app/privacy/page.tsx`、`app/terms/page.tsx`、`app/manifest.ts`、`public/sw.js`、`src/lib/copy/index.ts`、`src/lib/copy/en-US.ts`、`src/lib/i18n.tsx`、`src/components/sw-register.tsx` | §10 |

### 12.2 修改

| 文件 | 修改 |
|---|---|
| `packages/contracts/mylife_contract/{settings,admin,deletions,auth,enums,constants}.py` | §3.2 |
| `apps/api/mylife_api/main.py` | 注册 `exports`、`legal` 路由；middleware 冷却只读 |
| `apps/api/mylife_api/settings.py` | `allowed_private_provider_hosts`、`export_ttl_days`、`export_daily_limit`、`embedding_coverage_threshold`、`backup_dir` |
| `apps/api/mylife_api/services_settings.py` | LLM/Embedding/用量 |
| `apps/api/mylife_api/services_deletion.py` | §7 |
| `apps/api/mylife_api/services_search.py`、`repo_chat.py`、`repo_phase1.py`、`apps/worker/mylife_worker/indexer.py` | profile → chunk 表解析（D8） |
| `apps/api/mylife_api/chat_runner.py`、`review_runner.py` | 从 DB 构建 provider；失败也计量 |
| `apps/api/mylife_api/dispatcher.py` | 导出过期、`job_runs` 僵死回收、旧 chunk 表退役 |
| `apps/api/mylife_api/repositories.py`、`repo_review.py` | `list_entries` 补 `source_date`（D31）；审计 ip |
| `apps/api/tests/test_rls_matrix.py` | `embedding_reembed_jobs` |
| `.github/workflows/ci.yml` | SEC-29 扫描 + SBOM；新测试 |
| `Makefile` | `backup`、`restore-drill` |
| `.env.example` | 新配置项 |
| `apps/web/src/**` | §10 |

---

## 13. 施工顺序（每小节一个提交）

| 提交 | 内容 | 可独立验证 |
|---|---|---|
| C1 | 本文档 `docs/PHASE5-SPEC.md` | 文档评审 |
| C2 | Phase 4 遗留修复：`list_entries` 补 `source_date/evidence_id`、`_edited_sections` 补 `sentinel_used`（D31） | 编辑重评审用例 |
| C3 | 迁移 0011 + 契约（settings/exports/admin/enums/auth）+ RLS 矩阵 | `alembic upgrade/downgrade` + `test_contract` + RLS |
| C4 | LLM 设置（GET/PUT/test）+ provider 构建改造 | AC-49 |
| C5 | Embedding 设置 + 蓝绿换维 + FX-11 | AC-50 / FX-11 |
| C6 | 管理后台端点 + 限流 + 禁用语义 | AC-54 / SEC-30 |
| C7 | 导出 ZIP + 对账 + 过期清理 | AC-55 导出半部 / AC-64 数据类别 |
| C8 | 删除收口（导出前置、写冻结、0 命中、账户级） | AC-55 删除半部 |
| C9 | 用量与审计收口（AC-53 / AC-07 / SEC-24） | AC-53 / AC-07 |
| C10 | 备份/恢复 + SEC-28/29 + AC-59 容器演练 | AC-61 / AC-59 / SEC-28/29 |
| C11 | 前端：设置页扩展、admin、法务页、i18n、PWA、响应式 | `tsc/lint/build` + AC-62/63/64 |
| C12 | 证据：FX-11、导出对账、MA-08、恢复演练、SEC 汇总 | 证据齐全 |
| C13 | 文档：`PHASE5-STATUS.md`、`IMPLEMENTATION-NOTES §2E`、`HANDOFF.md`、`README` | 状态签收 |

---

## 14. 待拍板问题

| # | 问题 | 选项 | 推荐 |
|---|---|---|---|
| Q1 | 导出下载方式 | (a) 直传 FileResponse；(b) 新增 MinIO 依赖 + 302 | (a)，登记偏差 |
| Q2 | 导出是否异步 | (a) 同步（MVP）；(b) 后台任务 + 轮询 | (b) 契约已是异步；实现用进程内任务 |
| Q3 | 删除是否强制先导出 | (a) 软前置 + `skip_export`；(b) 硬前置 | (a) |
| Q4 | 换维覆盖阈值 | (a) 0.995 自动；(b) 1.0 才切换 | 自动阈值 0.995，AC-50 证据用 1.0 |
| Q5 | i18n 英文覆盖范围 | (a) 公共壳 + 3 页；(b) 全量 | (a) |
| Q6 | 备份加密工具 | (a) age；(b) openssl | 取本机可用者，脚本自动探测 |
| Q7 | 管理端跨租户实现 | (a) SECURITY DEFINER；(b) BYPASSRLS 角色直连 | (a) |
| Q8 | 系统配置锁定粒度 | (a) 逐键 env 锁定；(b) 全局布尔 | (a) |
