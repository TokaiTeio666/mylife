# Phase 4 / M4.5–M5 交付状态与验收证据

> 日期：2026-11-08 ｜ 范围：Git 交付（分支/commit/PR/auto-merge）、专题页确定性重渲染、调度与 dispatcher、仪表盘/日历/搜索接入、SEC-27 删除冷却
>
> 判定口径：`ACCEPTANCE-CRITERIA.md §5` Phase 4 行 + `SECURITY-BOUNDARY.md §11` Phase 4 门禁；
> 施工规格见 `PHASE4-SPEC.md`（D1–D43 裁决）。

---

## 1. 结论摘要

| 项 | 状态 |
|---|---|
| 后端测试 | **249 passed, 1 skipped**（skip 为 Windows 符号链接用例） |
| 前端 `tsc` / `eslint` / `next build` | 通过（新增 `/reviews`、`/reviews/[kind]/[period]`、`/topics`、`/topics/[topic]`、`/settings`） |
| 依赖方向检查 | 通过（`packages/delivery` 新增模块仅依赖 contracts/runtime） |
| 迁移 | `0005_phase4_delivery` … `0009_phase4_outbox_scan` upgrade/downgrade 均通过 |
| Phase 4 AC | AC-39、AC-40、AC-43…AC-48、AC-51、AC-52、AC-67 —— 见 §2 |
| Phase 4 SEC | SEC-27（SEC-16/17/23 回归通过）—— 见 §3 |
| FX | FX-07、FX-08、FX-09 —— 见 §4 |
| 容器 E2E（Caddy） | PASS —— 见 §6 |

---

## 2. AC 对照

| AC | 结论 | 证据 |
|---|---|---|
| AC-39 交付完成 | PASS | 无 Git 连接：`test_delivery_git.py::test_object_store_fallback_bundle_and_zip`（manifest 含 5 个路径、ZIP 含 Markdown + manifest、下载 200）；`test_compat_alias_reads_review_content`（`data/reviews/...` 读到同一内容）；有 Git 连接：`test_git_delivery_branch_trailers_and_pr_body`（文件落在 `wiki/复盘/每日复盘/2026/2026-10-27.md` 等） |
| AC-40 三专题 | PASS | `test_topics_render.py::test_topic_merge_dedupe_conflict_and_human_region`（同 canonical_key 1 条、冲突显式、重跑计数不变、区外文本保留、标记丢失→manual）；`test_fixture_phase4.py::test_fx07_topic_merge` |
| AC-43 时区/DST/补算 | PASS | `test_schedule.py`（上海/纽约 01:00、周一、1 月 1 日、DST 不存在顺延、歧义取较早 offset、7 天补算上限）；`test_settings_schedule.py::test_dispatch_tick_admits_skips_and_catchup`（`period_already_succeeded`、`catchup_window_exceeded`、NULL 补算） |
| AC-44 同日重跑与并发 | PASS | `test_delivery_git.py::test_same_day_rerun_version_increment_single_path`（version+1、`review_versions` 2 行、路径唯一）；并发沿用 FX-12 |
| AC-45 仪表盘 | PASS | `test_dashboard.py::test_latest_and_completion_rate_matches_sql`（三类最新产物、完成率与 SQL 复算一致、无 LLM 调用） |
| AC-46 任务天数 | PASS | `test_dashboard.py::test_days_open_by_workspace_timezone`（3/5/10 天、已完成 0 条） |
| AC-47 日历 | PASS | `test_dashboard.py::test_calendar_month_and_year`（31 天、10 个 has_daily、W43 落在 10 月） |
| AC-48 搜索三模式 | PASS | `test_dashboard.py::test_search_three_source_modes`（both 同时含 document/review；review 模式 `review_id` 非空；document 模式全为 document） |
| AC-51 调度配置 | PASS | `test_settings_schedule.py::test_schedule_defaults_and_update`（三时间 + catchup 落库、next_run_at 重算、活跃 run 不中断） |
| AC-52 Git 配置 | PASS | `test_settings_schedule.py::test_git_settings_connection_matrix`（role/provider/repo/branch/interval/next_sync_at 齐全、凭据密文、缺 `contents:write` 拒绝且不落库、间隔变更重算） |
| AC-67 auto-merge | PASS | `test_delivery_git.py::test_auto_merge_matrix_and_audit`（daily + 开关 + Gate 通过 → merged 且写 `delivery.auto_merged`；weekly 不自动合并） |

补充：

- 回滚：`test_delivery_git.py::test_rollback_creates_new_version_and_delivers`（version+1、内容等于目标版本、旧行不删）。
- 同 period 复用 PR：`test_delivery_git.py::test_git_rerun_reuses_open_pr`。
- webhook 回写：`test_webhooks.py::test_merged_pr_writes_back_delivery_record`。

---

## 3. SEC 对照（Phase 4）

| SEC | 结论 | 证据 |
|---|---|---|
| SEC-27 删除冷却 | PASS | `test_deletion.py::test_cooling_purge_and_git_history_retained`：`confirm_text` 精确校验（422）、`cooling` 状态、撤销、到期 purge 后正文表 0 行、审计保留、**Git 历史提交仍可读**；响应 `notice` 明示“不承诺抹除” |
| SEC-16/17 回归 | PASS | `test_webhooks.py` 全部通过（签名失败无副作用、重放幂等） |
| 路径与 SSRF | PASS | `test_delivery_units.py::test_validate_relative_path_rejects_escapes`；`services_settings._validate_remote`（拒绝内嵌凭据/非 https/私网，`file://` 仅 allow_local） |
| 凭据 | PASS | Git 凭据 AES-256-GCM 密文落库（`test_git_settings_connection_matrix` 断言密文不含明文）；日志不记录凭据 |

---

## 4. Golden fixtures

| FX | 结论 | 证据 |
|---|---|---|
| FX-07 fx_topic_merge | PASS | `docs/evidence/phase4/fx07-topic-merge.json`：1 条 canonical_key、occurrence 3 次、冲突字段 `cost` 两值并列、人工段落保留、重跑计数 3 |
| FX-08 fx_pr_delivery | PASS | `fx08-pr-delivery.json`：分支 `review/daily/2026-10-27`、四个 trailer、PR 六段、同 period 复用 PR、weekly 不自动合并 |
| FX-09 fx_restart | PASS | `fx09-restart.json`：过期租约→`retry_wait`（`retry_from_state=drafting`、`lease_expired` 事件）、dispatcher 启动补算 `next_run_at` |

复现：`cd apps/api && uv run pytest -q -m fixture`（Phase 3 + Phase 4）。

---

## 5. 关键实现说明

1. **交付编排**：`services_delivery.deliver_review()` 一次写复盘 + 三专题 + 索引；Git 连接存在时 `clone → branch → commit(trailer) → push → PR → 可选 auto-merge`；否则本地对象存储包 + ZIP（`ui_diff`）。
2. **专题重渲染**：只重写 `mylife:auto` 区；区外逐字保留；标记缺失/嵌套或区内哈希变化 → `manual` + `GIT_MARKER_BROKEN`；occurrence 按 `review_id` 重算，计数由 `COUNT` 派生。
3. **调度**：`schedule.py` 纯函数（zoneinfo），dispatcher 单副本 + 专用连接持有 advisory lock；`dispatch_due_schedules` / `dispatch_due_git_syncs` / `dispatch_resumable_review_runs` 是受限 SECURITY DEFINER 逃逸口。
4. **续跑**：API 进程内 sweeper 每 15s 回收过期租约并拉起 `created/retry_wait`；runner 从 `retry_from_state` 重入。
5. **投递顺序**：Celery 任务在事务提交后投递（`queue.enqueue_ingest(session=...)`），避免 worker 先于 commit 读到 job。
6. **删除**：`requested→cooling→purged/cancelled`；purge 按并集清单逐表 savepoint 删除，保留 `audit_logs` 与 Git 历史。

---

## 6. 容器 E2E（本机，2026-11-08）

服务：postgres / redis / migrate / api / worker / dispatcher / web / caddy（MinIO 镜像不可达，未启动；交付走对象存储降级路径）。
脚本：`scripts/e2e_phase4.py`；结果：`docs/evidence/phase4/e2e-container.json`。

| 步骤 | 结果 |
|---|---|
| 注册 + 邀请码 | 201 |
| ZIP 导入 | `succeeded`（**修复前**曾出现 worker `job not found`，见 §7） |
| `POST /reviews` daily | run `succeeded`，7 节 |
| 交付 | `object_store / committed`，manifest 5 个路径，`GET /deliveries/{id}/download` 200 |
| 专题 | 1 条 pending → confirm 200 |
| 仪表盘 | daily 2026-10-27、completion_rate 0.0、open_tasks 0 |
| 日历 | 31 个日格 |
| 搜索 | both → {document, review}；review 模式 1 条 |
| 调度 | GET 返回三条 `next_run_at`；PUT 200 |
| 删除 | 201 `cooling`，notice 含“Git 历史保留” |
| dispatcher | 到期行被准入：`dispatch.admit admitted=true`，`agent_runs.trigger_type=schedule`，随后 `succeeded` 并交付 |

---

## 7. 本轮发现并修复的缺陷

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | 导入任务偶发 `job not found`（静默丢失） | Celery 投递发生在请求事务提交前 | `enqueue_ingest(session=...)` 延迟到 after_commit；回滚则丢弃 |
| 2 | 到期的调度行推进了时间但从不生成复盘 | `next_run_at` 只返回严格晚于 since 的时刻，漏掉“正好到期”的边界 | `missed_occurrences(due_at=...)` 显式计入 |
| 3 | 调度 run 停在 `created` 不续跑 | 续跑扫描函数外 JOIN `agent_runs` 重新受 RLS，返回 0 行 | `has_review` 移入 SECURITY DEFINER 函数（迁移 0007） |
| 4 | 新建 workspace 的 `next_run_at` 为空 | 默认调度行未计算 | `insert_default_schedules` 创建即计算 |
| 5 | 对象存储交付后 `delivery_status` 停在 pending | API 枚举无 `object_store` | 映射为 `ui_diff` |
| 6 | dispatcher 日志 `event` 字段嵌套 JSON | `logger.info(json.dumps(...))` | 改用 `extra` 平铺字段 |
| 7 | 删除冷却到期从不执行 purge | `deletion_requests` 是 FORCE RLS，dispatcher 普通 SELECT 恒 0 行 | 迁移 0008 增加 `dispatch_due_deletions()` |
| 8 | outbox 重投从不执行 | 同上（`outbox_events`） | 迁移 0009 增加 `dispatch_due_outbox()` |
| 9 | purge 后 owner 会话仍可登录 | `sessions` 无 `workspace_id`，通用循环静默跳过 | 按 owner 显式删除并加断言 |
| 10 | 到期点因并发/活跃 run 被永久跳过 | 无论准入结果都推进 `next_run_at` | 仅在非暂时失败时推进 |
| 11 | 编辑重评审时 G3c 被静默跳过 | `frozen_text` 传空 | 从快照加载冻结原文 |
| 12 | 任意成员可读 webhook 投递日志 | 端点无角色校验 | 限 owner/admin |
| 13 | `GET /deliveries/{id}` 对无 manifest 的历史行 500 | 直接 `model_validate` | 返回最小占位 manifest |
| 14 | Phase 4 fixture 报告每次重跑都变化 | 报告写入随机 id 与时间戳 | 只写键名/长度，去掉时间戳 |
| 15 | 交付后仪表盘仍显示旧计数 | 60s 进程内缓存未失效 | 交付完成时 `clear_dashboard_cache()` |
| 16 | 日历 `open_tasks` 恒为 0 | 硬编码占位 | 按任务区间逐日统计 |
| 17 | outbox 重投可能给已成功周期再生成一版 | `admit_scheduled_review` 未做成功检查 | 函数内先判 `period_already_succeeded` |

---

## 8. 未完成 / 偏差（详见 `IMPLEMENTATION-NOTES.md §2D`）

| 项 | 状态 | 说明 |
|---|---|---|
| 独立 worker-review 进程 | 未实现 | 复盘仍在 API 进程执行；dispatcher 独立容器负责调度。Phase 5 可迁 |
| 真实生成式 LLM Writer | 未启用 | 仍为确定性装配器；`model_profile` 为扩展点 |
| 完整导出 ZIP（AC-55）与管理后台（AC-54） | 未实现 | Phase 5；Phase 4 只建 `export_jobs` 表并为删除提示保留入口 |
| 删除撤销/MA-08 演练、隐私政策页（AC-64） | 未实现 | Phase 5；SEC-27 所需告知已在 API 响应与设置页呈现 |
| 备份/恢复演练（AC-61）、PWA/i18n（AC-62/63） | 未实现 | Phase 5 |
| `stale` 自动迁移（N/TTL） | 未实现 | 需产品定义阈值；`drop` 已改为 `dropped` tombstone |
| FX-08 真实 GitHub 仓 | 未执行 | 本机无外网；用本地裸仓库 + fake GitHub HTTP 断言同等元数据 |
| 账户级跨 workspace 删除 | 部分 | 每 owned workspace 一行；完整产品语义留 Phase 5 |

---

## 9. 复现命令

```bash
# 后端
cd apps/api
uv run pytest -q                 # 249 passed, 1 skipped
uv run pytest -q -m fixture      # Phase 3 + Phase 4 golden fixtures
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head

# 前端
cd apps/web && npx tsc --noEmit && npm run lint && npm run build

# 依赖方向
cd ../.. && uv run python scripts/check_boundaries.py

# 容器 E2E（需先插入未使用邀请码）
uv run python scripts/e2e_phase4.py <invite-code>
```

---

## 10. 下一阶段入口（Phase 5）

Phase 5 门禁：AC-07、AC-49、AC-50、AC-53、AC-54、AC-55、AC-57、AC-59、AC-61…AC-64；SEC-01…SEC-30 全集。

进入前需要：

1. 管理后台（用户/Workspace/邀请码/禁用/系统配置）与审计五类收口；
2. 导出 ZIP 与删除撤销/MA-08 全链路演练（含 `export_jobs` 实现）；
3. LLM/Embedding 设置页与蓝绿换维（FX-11）；
4. 备份/恢复演练（RTO ≤4h、RPO ≤24h）与安全评审报告；
5. AC-59 全量签收（kill -9、断网 30s、容器策略 diff）。
