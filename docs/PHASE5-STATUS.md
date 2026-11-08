# Phase 5 / M6 交付状态与签收证据

> 日期：2026-11-08 ｜ 范围：管理后台、导出 ZIP 与删除收口、LLM/Embedding 设置与蓝绿换维、审计与用量对账、备份恢复演练、PWA/i18n/法务页、SEC-01…30 全量回归
>
> 判定口径：`ACCEPTANCE-CRITERIA.md §5` Phase 5 行 + `SECURITY-BOUNDARY.md §11` Phase 5 门禁；
> 施工规格见 `PHASE5-SPEC.md`（D1–D34 裁决）。

---

## 1. 结论摘要

| 项 | 状态 |
|---|---|
| 后端测试 | **298 passed, 1 skipped**（skip 为 Windows 符号链接用例） |
| Golden fixtures | **11 passed**（`pytest -q -m fixture`：Phase 3 + Phase 4 + FX-11），另加 Phase 0 FX-06、Phase 1 FX-13/14、Phase 2 FX-05 回归全绿 |
| 前端 `tsc` / `eslint` / `next build` | 通过（23 条路由，含 `/admin/*`、`/privacy`、`/terms`、`/manifest.webmanifest`） |
| 依赖方向与 i18n 扫描 | `scripts/check_boundaries.py` 通过；`scripts/check_i18n.py` **0 硬编码中文违规** |
| 迁移 | `0011_phase5_settings_admin`、`0012_phase5_retire_chunk_table`（`upgrade head` / `downgrade base` 全链路通过） |
| Phase 5 AC | AC-07、AC-49、AC-50、AC-53、AC-54、AC-55、AC-57、AC-59、AC-61、AC-62、AC-63、AC-64 —— 见 §2 |
| SEC 全集 | **SEC-01 … SEC-30（30/30 PASS）** —— 见 §3 与 `docs/evidence/phase5/sec-summary.json` |
| 容器级演练 | AC-59 重启/暂停 Redis（2.8s 恢复）PASS；AC-61 恢复演练（RTO 0.054h、RPO 0.0h、15,011 篇计数与 10 篇哈希逐位相等）PASS |

---

## 2. Phase 5 AC 对照

| AC | 优先级 | 结论 | 证据 |
|---|---|---|---|
| AC-07 五类审计全覆盖 | P1 | PASS | `test_audit_ac07.py::test_audit_covers_five_classes`：依次执行登录、导入、配置变更、复盘生成、导出与删除，`user.login` / `ingest.started` / `deletion.requested` / `settings.*.updated` / `review.created` 五族各 ≥1 行，`workspace_id/user_id/action/target/ip/created_at` 齐全，`detail` 无 `sk-` 与长正文 |
| AC-49 LLM 配置与连接测试 | P0 | PASS | `test_settings_llm.py`：AES-256-GCM 信封密文（`MYL1` 前缀，AAD=`workspace_id\|llm`），响应与日志明文 0 次；测试失败返回 `LLM_UNAVAILABLE` 且 `provider_credentials` 行数不变；运行时优先读取 DB 配置 |
| AC-50 Embedding 蓝绿换维 | P0 | PASS | `test_settings_embedding.py::test_blue_green_dimension_switch_and_rollback` + `docs/evidence/phase5/fx11-embedding-migration.json`：`create_chunk_table('document_chunks_v768', 768)` 建新表 + HNSW + ENABLE/FORCE RLS；覆盖率 <1.0 拒绝切换；覆盖率 1.0 原子切换；回滚后旧 profile 立即可用；到期后先写 `outbox_events` 快照清单再 `DROP TABLE`；迁移脚本扫描无 `ALTER COLUMN TYPE` |
| AC-53 用量对账 | P1 | PASS | `test_usage.py`：3 次对话 + 1 次复盘 → `prompt_tokens`/`completion_tokens`/`calls` 与 `usage_events` 求和差 0，`review_runs` 与成功 `agent_runs` 一致，日桶按 workspace 时区切分，member 仅见本人、跨 workspace 汇总为 0 |
| AC-54 管理后台 | P0 | PASS | `test_admin.py` + `docs/evidence/phase5/frontend/desktop-1440-admin-users.png`：用户/Workspace/邀请码/审计/系统配置/全平台用量分页返回；生成与撤销邀请码可用；禁用账号立即撤销全部会话且重登 401 `AUTH_ACCOUNT_DISABLED`；普通用户访问 `/admin/*` 返回 403 `ADMIN_REQUIRED`；操作均写 `audit_logs` |
| AC-55 导出与删除全链路 | P0 | PASS | `test_export.py`（`docs/evidence/phase5/export-reconciliation.json`：DB 与 ZIP 计数差 0，逐文件 SHA-256 一致）+ `test_deletion.py`（`docs/evidence/phase5/ma08-deletion-drill.json`：导出前置、二次确认、冷却写冻结、撤销后文档完整、到期 purge 全部 39 张业务表及动态换维表 0 残留、`scope=account` 跨库清除、已合并 Git 历史正向可读） |
| AC-57 安全全集 | P0 | PASS | SEC-01…SEC-30 全部 PASS（见 §3 与 `docs/evidence/phase5/sec-summary.json`） |
| AC-59 故障恢复与无静默丢失 | P0 | PASS | `scripts/e2e_ac59.py` → `docs/evidence/phase5/ac59-container.json`：全部长驻服务 `restart: unless-stopped`；SIGKILL worker 后 2.8s 恢复 healthy；Redis 暂停期间 `/readyz` 返回 200；dispatcher 回收 30 分钟僵死 `job_runs`（`INGEST_STALE_RUNNING`）；结合 FX-09 租约回收续跑 |
| AC-61 备份与恢复演练 | P1 | PASS | `scripts/backup_pg.sh`、`scripts/backup_data.sh`、`test_backup_drill.py`、`scripts/restore_drill.py` → `docs/evidence/phase5/restore-drill.json`：15,011 篇文档 + 5 份复盘恢复到新库计数相等、抽样 10 篇 `content_hash` 一致，耗时 192.9s（RTO 0.054h ≤ 4h，RPO 0.0h ≤ 24h），7 天/30 天保留两档验证通过 |
| AC-62 响应式与 PWA | P1 | PASS | `scripts/verify_phase5_frontend.py` → `docs/evidence/phase5/frontend/ac62-responsive-pwa.json` + 6 张截图：9 个路由 × 1440/768/390 三宽度共 27 组实测 `overflow_x = 0`；`/manifest.webmanifest`（standalone + maskable icon）、`/sw.js`（跳过 `/api/*`）、`/offline.html` 全部就绪 |
| AC-63 国际化资源 | P2 | PASS | `scripts/check_i18n.py`（0 违规，已入 CI）+ `docs/evidence/phase5/frontend/ac63-i18n.json`：默认 `zh-CN`，切换 `en-US` 后 `<html lang>` 同步更新，`/privacy`、`/terms`、`/dashboard`、`/settings` 4 个页面文案变化 |
| AC-64 合规披露与数据类别 | P1 | PASS | `docs/evidence/phase5/frontend/ac64-legal.json` + `mobile-390-privacy.png`：匿名访问 `/privacy` 与 `/terms` 返回 200，明示 LLM/Embedding 数据出境供应商（DeepSeek / OpenAI 兼容 / BGE-M3）及“已合并 Git 历史保留、不承诺抹除”；导出覆盖 `documents/reviews/conversations/settings/usage/audit` 六类 |
| AC-65 一键部署与升级 | P0 | PARTIAL（沿用环境说明） | `make up` / `make upgrade` 已就绪；本机核心 7 服务（postgres/redis/migrate/api/dispatcher/worker/web/caddy）单命令构建升级并全部 healthy；MinIO 因本机外网镜像源不可达未启动，业务已通过本地数据根降级闭环 |

---

## 3. SEC-01 … SEC-30 全集对照

完整机器可读清单见 `docs/evidence/phase5/sec-summary.json`。

| ID | 级别 | 结论 | 验证测试 / 证据 |
|---|---|---|---|
| SEC-01 | P0 | PASS | `test_sec_phase5.py::test_sec01_all_business_routes_require_authentication`（遍历 OpenAPI 全部业务路由，未登录均 401） |
| SEC-02 | P0 | PASS | `test_auth.py::test_logout_all_revokes_every_device` |
| SEC-03 | P0 | PASS | `test_auth.py::test_weak_password_and_csrf` |
| SEC-04 | P1 | PASS | `test_sec_phase5.py::test_sec04_ten_failed_logins_locked_with_retry_after` |
| SEC-05 | P0 | PASS | `test_auth.py::test_register_used_code_second_time_rejected`、`test_concurrent_register_with_same_code_only_one_succeeds` |
| SEC-06 | P0 | PASS | `test_workspace.py::test_switch_to_non_member_returns_404_and_security_audit`、`test_fixture_fx06.py` |
| SEC-07 | P0 | PASS | `test_rls_matrix.py`（34 张租户表 ENABLE + FORCE RLS，`mylife_app` NOBYPASSRLS） |
| SEC-08 | P0 | PASS | `test_auth.py::test_workspace_id_in_body_rejected`、`test_contract.py::test_phase5_models_forbid_extra_fields`、`test_sec_phase5.py::test_sec08_query_workspace_id_spoofing_ignored` |
| SEC-09 | P0 | PASS | `test_chat_tools.py::test_tool_args_reject_workspace_id` |
| SEC-10 | P0 | PASS | `test_chat.py::test_prompt_injection_detected_and_not_followed`（FX-05 八类注入） |
| SEC-11 | P0 | PASS | `test_delivery_units.py::test_validate_relative_path_rejects_escapes`、`test_ingest.py::test_zip_symlink_entry_rejected` |
| SEC-12 | P0 | PASS | `test_git_ingest.py::test_ssrf_rejected`、`test_settings_llm.py::test_llm_base_url_rejects_plain_http` |
| SEC-13 | P0 | PASS | `test_sec_phase5.py::test_sec13_default_zip_limits_and_caps`、`test_ingest.py::test_zip_upload_size_limit` |
| SEC-14 | P0 | PASS | `test_ingest.py::test_zip_traversal_and_bomb_rejected` |
| SEC-15 | P0 | PASS | `test_sec_phase5.py::test_sec15_git_clone_does_not_execute_hooks_submodules_or_lfs`、`test_git_ingest.py::test_git_common_args_hardened` |
| SEC-16 | P0 | PASS | `test_webhooks.py::test_signature_invalid_no_side_effect`、`test_missing_signature_rejected` |
| SEC-17 | P1 | PASS | `test_webhooks.py::test_replay_is_idempotent` |
| SEC-18 | P0 | PASS | `test_security.py::test_db_stores_only_ciphertext`、`test_aad_binds_workspace_and_provider` |
| SEC-19 | P0 | PASS | `test_security.py::test_unhandled_logs_do_not_contain_exception_details`、`test_settings_llm.py::test_llm_save_encrypts_and_never_echoes` |
| SEC-20 | P0 | PASS | `test_ingest.py::test_html_render_sanitizes_scripts`、`test_search.py::test_snippet_highlight_and_escaping` |
| SEC-21 | P1 | PASS | `test_sec_phase5.py::test_sec21_svg_asset_is_sanitized_and_sandboxed` |
| SEC-22 | P1 | PASS | `test_sec_phase5.py::test_sec22_search_and_chat_rate_limits_return_429` |
| SEC-23 | P1 | PASS | `test_review_api.py::test_review_quota_audit` |
| SEC-24 | P0 | PASS | `test_audit_ac07.py::test_audit_covers_five_classes` |
| SEC-25 | P0 | PASS | `test_sec_phase5.py::test_sec25_security_abort_run_cannot_be_approved`、`test_review_gates.py::test_g0_flags_cross_tenant_evidence` |
| SEC-26 | P0 | PASS | `test_chat.py::test_prompt_contains_only_minimal_evidence` |
| SEC-27 | P0 | PASS | `test_deletion.py`、`docs/evidence/phase5/ma08-deletion-drill.json`、`docs/evidence/phase5/frontend/ac64-legal.json` |
| SEC-28 | P1 | PASS | `test_backup_drill.py`（age/openssl 加密、校验和、错误口令不可恢复、7d/30d 保留） |
| SEC-29 | P1 | PASS | `.github/workflows/ci.yml` `security` job（`pip-audit` 阻断 + `npm audit --audit-level=high` + Python/Node CycloneDX SBOM 归档） |
| SEC-30 | P0 | PASS | `test_admin.py::test_admin_responses_never_carry_content`、`test_sec_phase5.py::test_sec30_admin_field_sets_exclude_all_content_columns` |

---

## 4. Golden Fixtures 全量回归（FX-01 … FX-14）

| FX | 阶段 | 结论 | 证据文件 |
|---|---|---|---|
| FX-01 fx_daily_2026-10-27 | Phase 3 | PASS | `docs/evidence/phase3/fx01-daily.json` |
| FX-02 fx_weekly_W43 | Phase 3 | PASS | `docs/evidence/phase3/fx02-weekly.json` |
| FX-03 fx_yearly_2026 | Phase 3 | PASS | `docs/evidence/phase3/fx03-yearly.json` |
| FX-04 fx_empty_day | Phase 3 | PASS | `docs/evidence/phase3/fx04-empty-day.json` |
| FX-05 fx_injection | Phase 2 | PASS | `test_chat.py::test_prompt_injection_detected_and_not_followed` |
| FX-06 fx_tenant_leak | Phase 0/2 | PASS | `test_fixture_fx06.py`、`test_chat.py::test_cross_tenant_chat_isolated` |
| FX-07 fx_topic_merge | Phase 4 | PASS | `docs/evidence/phase4/fx07-topic-merge.json` |
| FX-08 fx_pr_delivery | Phase 4 | PASS | `docs/evidence/phase4/fx08-pr-delivery.json` |
| FX-09 fx_restart | Phase 4/5 | PASS | `docs/evidence/phase4/fx09-restart.json`、`docs/evidence/phase5/ac59-container.json` |
| FX-10 fx_cost_budget | Phase 3 | PASS | `docs/evidence/phase3/fx10-budget.json` |
| FX-11 fx_embedding_migration | Phase 5 | PASS | `docs/evidence/phase5/fx11-embedding-migration.json` |
| FX-12 fx_concurrent_runs | Phase 3 | PASS | `docs/evidence/phase3/fx12-concurrent.json` |
| FX-13 fx_search_p95 | Phase 1 | PASS | `docs/evidence/fx13-search.json`（P95 104.15ms < 1000ms） |
| FX-14 fx_import_200mb | Phase 1 | PASS | `docs/evidence/fx14-import.json`（203.8s < 600s） |

---

## 5. 关键实现与本轮加固摘要

1. **管理端最小权限跨租户只读**：迁移 `0011` 创建 `mylife_admin`（`NOLOGIN BYPASSRLS`）与 `admin_*` 系列 `SECURITY DEFINER` 函数，仅返回脱敏元数据与聚合指标；`mylife_app` 保持 `NOBYPASSRLS`。
2. **LLM / Embedding 凭据与蓝绿换维**：`provider_credentials` 信封加密存储；换维通过 `mylife_migrator` 持有的 `create_chunk_table()` 动态建 `document_chunks_v{dim}` + HNSW + ENABLE/FORCE RLS；re-embed 保留 `chunk.id` 以维持历史引用闭包；覆盖率 1.0 原子切换；到期由 `retire_chunk_table()` 先落 `outbox_events` 快照再 `DROP TABLE`。
3. **导出与删除闭环**：`POST /exports` 生成含 `documents/reviews/conversations/settings/usage/audit/manifest.json` 的 ZIP，逐文件 SHA-256 对账；删除默认要求 7 天内导出备份，冷却期全局写冻结（`DELETE_IN_COOLING`），到期前先枚举动态换维表再两轮 savepoint 删除并做 0 残留校验，支持 `scope=account` 多库联动注销。
4. **备份、恢复与供应链**：`backup_pg.sh` + `backup_data.sh` 支持 `age`/`openssl` 口令加密、SHA-256 校验与 7d/30d 分档保留；`restore_drill.py` 全自动恢复至临时库核对计数与哈希；CI 增加 `pip-audit`、`npm audit` 与 CycloneDX SBOM 归档。
5. **前端多端、国际化与合规**：设置页补齐模型/向量/用量/导出卡片；新增 `/admin/*` 五子页；根级匿名 `/privacy` 与 `/terms`；`LocaleProvider`（`zh-CN` / `en-US`）与离线壳 Service Worker（绝不缓存 `/api/*`）。

---

## 6. 已知范围边界（非阻塞项，详见 `IMPLEMENTATION-NOTES.md §2E`）

| 项 | 状态 | 说明 |
|---|---|---|
| 生成式 LLM Writer | 首版确定性装配器 | 复盘写作保持确定性装配以保证 100% 门禁可复现；LLM 配置、凭据加密、连接测试与 chat 调用链已完整接通 |
| MinIO 容器镜像 | 本地数据根等效运行 | 本机网络无法拉取 `minio/minio`；导入快照、复盘交付包、用户导出 ZIP 与数据备份统一落在 `MYLIFE_DATA_ROOT` 并全部通过端到端验收 |
| 英文文案覆盖范围 | 公共壳 + 核心 4 页 | 按 AC-63“至少 3 页变化”要求，覆盖公共布局壳、登录/注册、法务页、仪表盘与设置页；未翻译键自动回落 `zh-CN` |
| Windows 符号链接用例 | 1 项平台 skip | `test_clone_rejects_symlink` 在无特权 Windows 进程自动 skip，Linux CI 正常执行 |

---

## 7. 复现命令

```bash
# 1. 静态门禁（依赖方向 + i18n 硬编码扫描 + ruff）
uv run python scripts/check_boundaries.py
uv run python scripts/check_i18n.py
uv run ruff check packages/contracts packages/knowledge-runtime packages/retrieval packages/agent packages/delivery apps/api apps/worker

# 2. 后端全量测试与 Golden fixtures
cd apps/api
MYLIFE_QUEUE_BACKEND=off MYLIFE_EMBEDDING_PROVIDER=mock MYLIFE_LLM_PROVIDER=mock MYLIFE_ALLOW_LOCAL_GIT=true uv run pytest -q
uv run pytest -q -m fixture

# 3. 迁移升降级闭环
uv run alembic upgrade head
uv run alembic downgrade base && uv run alembic upgrade head
cd ../..

# 4. 前端类型、lint 与生产构建
cd apps/web && npx tsc --noEmit && npm run lint && npm run build && cd ../..

# 5. 容器级恢复演练与前端多视口/PWA/合规验收
make ac59
MYLIFE_DB_HOST=localhost MYLIFE_DB_PORT=25432 make restore-drill
uv run python scripts/verify_phase5_frontend.py
```
