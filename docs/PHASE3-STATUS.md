# Phase 3 / M4 交付状态与验收证据

> 日期：2026-11-07 ｜ 范围：复盘 FSM、§5.1 章节契约、Todo 台账、G0–G9 确定性门禁、预算 guard、create_review 确认协议、专题首见、webhook 验签
>
> 判定口径：`ACCEPTANCE-CRITERIA.md §5` Phase 3 行 + `SECURITY-BOUNDARY.md §11` Phase 3 门禁；
> 施工规格见 `PHASE3-SPEC.md`。

---

## 1. 结论摘要

| 项 | 状态 |
|---|---|
| 后端测试 | **202 passed, 1 skipped**（skip 为 Windows 符号链接用例） |
| 前端 `tsc` / `eslint` / `next build` | 通过（本阶段未新增页面） |
| 依赖方向检查 | 通过（新增 `packages/delivery`） |
| 迁移 | `0004_phase3_review` upgrade/downgrade 均通过 |
| Phase 3 AC | AC-25、AC-31…AC-38、AC-41、AC-42、AC-60、AC-66、AC-68 —— 见 §2 |
| Phase 3 SEC | SEC-16、SEC-17、SEC-23 —— 见 §3 |
| FX | FX-01/02/03/04/10/12 —— 见 §4 |

---

## 2. AC 对照

| AC | 结论 | 证据 |
|---|---|---|
| AC-25 create_review 确认 | PASS | `test_review_topics.py::test_preview_confirm_enqueue_protocol`（preview 零副作用、confirm 不入队、enqueue +1、token 单次消费）；`test_unconfirmed_token_rejected_on_enqueue`；`test_recursive_create_review_rejected_in_review_run`（TOOL_NOT_ALLOWED + 审计） |
| AC-31 Todo 解析 | PASS | `test_task_ledger.py::test_checkbox_variants`、`test_fenced_code_is_ignored`、`test_natural_language_produces_no_task`、`test_ingest_path_makes_zero_llm_calls`；`test_short_filename_requires_year_directory` |
| AC-32 台账字段 | PASS | `test_cross_day_merge_and_occurrence_uniqueness`（first/last/done/source/line，唯一冲突 0）；`test_occurrence_not_duplicated_on_reimport`；`test_deleting_document_removes_occurrence_and_orphan_task` |
| AC-33 顺延与回填 | PASS | `test_carried_derived_not_persisted`（carried 派生、跨期移除） |
| AC-34 日记与日期 | PASS | `test_date_conflict_warning_persisted`；`test_date_priority_frontmatter_over_path`；FX-01 的 `journal_events` 证据 |
| AC-35 学习摘要四要素 | PASS | `test_fixture_phase3.py::test_fx01_daily`（`learning_summary` 四字段 + `field_evidence`）；`test_review_gates.py` 的 G2 校验 |
| AC-36 daily 7 章 | PASS | `test_review_api.py::test_daily_review_generates_seven_sections`（7 行、ordinal 1–7、每节非空、顺延进入 today_tasks）；FX-01 |
| AC-37 weekly 4 章 | PASS | FX-02（4 节、完成率与台账逐位相等）；`test_review_rollup.py`（重复错误 ≥2、Top 排序） |
| AC-38 yearly 5 章 | PASS | FX-03 两个场景（无周报 → 全哨兵；有周报 → 读 rollup）；`test_review_rollup.py::test_learning_map_reads_weekly_rollup_only`、`test_top_mistakes_frequency_desc_then_key_asc_and_cap_ten` |
| AC-41 可追溯与复算 | PASS | `test_review_gates.py::test_g4_requires_traceability_for_errors_and_methods`、`test_g3d_quote_hash_mismatch`、`test_g3b_hash_mismatch_is_stale_not_fabrication` |
| AC-42 手动触发与版本 | PASS | `test_active_run_conflict_and_version_bump`（409 + existing_run_id；成功后 v2；旧版本可查）；`test_period_validation_and_max_revisions` |
| AC-60 日志与回放 | PASS | `test_replays.py`（7 字段 JSON、敏感字段丢弃、chat/review 事件链一致、stream_epoch）；`scripts/verify_replay.py` |
| AC-66 预算 | PASS | `test_review_budget.py`（按 kind 默认、ratio 取最大、80%/100%、角色上限）；FX-10（workspace 月配额拒绝）；`test_review_api.py::test_review_quota_audit` |
| AC-68 专题首见 | PASS | `test_review_topics.py::test_topic_pending_confirm_and_recurrence`（pending 不进默认视图、confirm→open、复发累加不新建、重复 confirm 409） |

---

## 3. SEC 对照（Phase 3）

| SEC | 结论 | 证据 |
|---|---|---|
| SEC-16 webhook 签名 | PASS | `test_webhooks.py::test_signature_invalid_no_side_effect`（401 + 仅 `audit_logs(webhook.rejected)`，`delivery_events` 0 行）、`test_missing_signature_rejected`、`test_unsupported_provider`；实现用 SECURITY DEFINER 函数定位连接，不放开 `git_connections` RLS |
| SEC-17 重放幂等 | PASS | `test_replay_is_idempotent`（第二次 200 + handled=false，`delivery_events` 仍 1 行）、`test_merged_pr_is_ignored_in_phase3`（Phase 3 记 ignored） |
| SEC-23 create_review 配额 | PASS | `test_review_api.py::test_review_quota_audit`（429 QUOTA_EXCEEDED + `review.create_rejected` 审计）；preview 与 enqueue 共享计数 |

补充（Phase 3 相关但非本阶段门禁）：

- `test_rls_matrix.py` 已覆盖全部 12 张新表（含 `review_*`、`topic_*`、`delivery_*`）。
- `test_review_gates.py::test_g0_flags_cross_tenant_evidence`：跨租户证据 → `TENANT_LEAK`（SECURITY_ABORT）。

---

## 4. Golden fixtures

| FX | 结论 | 证据 |
|---|---|---|
| FX-01 fx_daily_2026-10-27 | PASS | `docs/evidence/phase3/fx01-daily.json`：7 节、ordinal 1–7、顺延 1 条、引用 1、证据 15、重跑 diff 0.0 |
| FX-02 fx_weekly_W43 | PASS | `fx02-weekly.json`：4 节、`completion_rate` 与台账逐位相等 |
| FX-03 fx_yearly_2026 | PASS | `fx03-yearly.json`：5 节；无周报时全哨兵（只读 rollup、不编造）；有周报场景见 `test_fx03_yearly_reads_weekly_rollup` |
| FX-04 fx_empty_day | PASS | `fx04-empty-day.json`：7 节逐字哨兵、引用 0、`succeeded` |
| FX-10 fx_cost_budget | PASS | `fx10-budget.json`：80% warn / 100% stop、workspace 月配额拒绝 |
| FX-12 fx_concurrent_runs | PASS | `fx12-concurrent.json`：每 workspace 1 个 run + 9 个 409（同一 `existing_run_id`），跨 workspace 互不阻塞 |

复现：`cd apps/api && uv run pytest -q -m fixture`。

---

## 5. 关键实现说明

1. **复盘 FSM**：`created → snapshotting → planning → retrieving → drafting → reviewing`；门禁通过 → `delivering → succeeded`；blocking → `revising`（确定性 Writer 无法改进时立即 `ESCALATE → human_gate`）；`yearly` 默认人工。
2. **CAS 与租约**：每次迁移 `UPDATE ... WHERE state=:from AND (lease_owner IS NULL OR lease_owner=:owner) RETURNING id`，0 行即放弃；`run.state` 与 CAS 同事务；`needs_human` 已从 `is_active_state` 移除（API §7.1）。
3. **章节契约**：`SECTION_SCHEMAS`/`ITEM_SCHEMAS` 冻结在 contracts；`review-profile.yml` 启动时逐项校验（`validate_review_profile`）。
4. **门禁**：G0–G9 纯函数；`EVIDENCE_STALE` 明确不升级为 `FABRICATION`；`TENANT_LEAK` 直接 `SECURITY_ABORT`。
5. **预算**：run 级用量由 `usage_events(run_id)` 聚合，跨 attempt/阶段累计；80% 写 `degraded(budget_warn)`，100% 硬停且不再调用 provider。
6. **Todo 台账**：只认复选框；`tasks` 完全由 occurrence 重建；`carried` 派生不落库。
7. **确认协议**：HMAC token（TTL 600s，绑定 ws/user/kind/period），`review_confirmations` 单次消费；`POST /reviews` 带 token 时校验已消费。
8. **webhook**：原始字节验签；`delivery_events` 幂等；Phase 3 只记 `ignored`，交付回写留 Phase 4。

---

## 6. 未完成 / 偏差（详见 `IMPLEMENTATION-NOTES.md §2C`）

| 项 | 状态 | 说明 |
|---|---|---|
| Git 分支/commit/PR 与 `delivery_records` 回写 | 未实现 | Phase 4（ARCHITECTURE §13）；Phase 3 的 `DELIVERING` 只做本地渲染 + DB 登记 |
| 专题页 md 渲染、`索引.md`、`GIT_MARKER_BROKEN` | 未实现 | Phase 4；Phase 3 只落 `topic_items/occurrences` 与确认端点 |
| 真实 LLM 写作 | 未启用 | Phase 3 的 Writer 是确定性装配器；`model_profile` 为扩展点 |
| `POST /reviews/{id}/rollback` | 未实现 | 依赖交付层（Phase 4）；当前返回 404 |
| 复盘检索 `review_search` 接入搜索页 | 未实现 | 表与版本写入已就绪；查询接入属 AC-48（Phase 4） |
| 租约 sweeper | 未实现为独立任务 | 当前只在 runner 内续租；Phase 4 迁 Celery beat |
| `edited_content_md` 重评审 | 拒绝绕过 | 当前返回 `REVIEW_GATE_FAILED`；完整编辑-重评审链留 Phase 4 |
| FX-01 的“2 条错误 + 1 条方法论” | 章节哨兵 | Phase 3 不自动从笔记抽取错误/方法论；素材已在 fixture 中，抽取规则待需求 v1.1 明确 |
| 人工抽样 20 条（≥98%） | 未执行 | 需人工复核；`docs/evidence/phase3/sampling.md` 待填 |

---

## 6A. 容器化 E2E（本机，2026-11-07）

服务：postgres / redis / migrate / api / worker / web / caddy；API 镜像已含
`packages/delivery`，迁移到 `0004_phase3`。

| 步骤 | 结果 |
|---|---|
| `POST /api/v1/reviews`（经 Caddy HTTPS，`allow_empty=true`） | 201，返回 run_id 与 review_id |
| 轮询 `GET /runs/{id}` | `succeeded`；`budget.state=ok` |
| `GET /reviews/{id}` | 7 节（ordinal 1–7）、`content_md` 非空、`versions=[1]` |
| 再次触发同 period | 200，`version=2`，`review_versions` 2 行，旧版本仍可查 |
| `GET /reviews` | 1 条 `daily 2026-10-27`，`status=delivered`，`degraded=false` |
| `GET /topics` | 三专题（错误清单 / 正确方法论 / 行动清单） |
| `GET /openapi.json` | 含 `/reviews`、`/topics/*`、`/webhooks/git/{provider}` |

> 空 workspace 生成的是合法空日复盘：7 节全哨兵、`degraded=false`（与 FX-04 一致）。

---

## 7. 复现命令

```bash
# 后端
cd apps/api
uv run pytest -q                 # 202 passed, 1 skipped
uv run pytest -q -m fixture      # golden fixtures
uv run alembic upgrade head
uv run alembic downgrade -1 && uv run alembic upgrade head

# 回放校验（AC-60）
uv run python scripts/verify_replay.py <run_id> --workspace <workspace_id>

# 前端
cd apps/web && npx tsc --noEmit && npm run lint && npm run build

# 依赖方向
cd ../.. && uv run python scripts/check_boundaries.py
```

---

## 8. 下一阶段入口（Phase 4）

Phase 4 门禁：AC-39、AC-40、AC-43…AC-48、AC-51、AC-52、AC-67；SEC-27；FX-07、FX-09（FX-08 在配置 Git 时必跑）。

进入前需要：

1. 交付层：`delivery_records` 回写、分支 `review/{kind}/{period_key}`、commit trailer、PR 六段正文、auto-merge（决策 3B）；
2. 专题页确定性重渲染 + `mylife:auto` 标记保护（`GIT_MARKER_BROKEN`）；
3. 调度：`workspace_schedules.next_run_at`、DST 规则、`catchup_days`；
4. 仪表盘/日历/全局搜索（`review_search` 接入）；
5. 备份/恢复演练与删除冷却（SEC-27）。
