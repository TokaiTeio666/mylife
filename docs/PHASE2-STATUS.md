# Phase 2 / M3 交付状态与验收证据

> 日期：2026-11-07 ｜ 范围：ToolGateway 六工具、chat 精简 FSM、SSE、引用闭包、工具轨迹、会话 CRUD、注入检测、前端对话
>
> 判定口径：`ACCEPTANCE-CRITERIA.md §5` Phase 2 行 + `SECURITY-BOUNDARY.md §11` Phase 2 门禁。

---

## 1. 结论摘要

| 项 | 状态 |
|---|---|
| 后端契约 + 数据库集成测试 | **104 passed, 1 skipped**（skip 为 Windows 符号链接用例） |
| 前端 `tsc` / `eslint` / `next build` | 通过（新增 `/chat`、`/chat/[id]`） |
| 依赖方向检查 | 通过（新增 `packages/agent`） |
| AC-22…AC-30 | 见 §2 |
| SEC-09/10/20/21/22/26 | 见 §3 |
| FX-05 / FX-06 | FX-05 PASS（8 类注入）；FX-06 见 §4 |
| 容器 E2E（Caddy + 真实 SSE） | PASS（见 §5） |

---

## 2. AC 对照

| AC | 结论 | 证据（测试） |
|---|---|---|
| AC-22 SSE / 首 token / chat.done | PASS | `test_full_chat_with_citations`（`text/event-stream`、`X-Stream-Epoch`、`retry: 3000`、`chat.done`）；`test_mock_first_token_latency_budget`（3 次端到端 <5s，mock 门禁） |
| AC-23 会话 CRUD 与刷新 | PASS | `test_conversation_crud_and_idempotency`（列表顺序、重命名、删除后 404、messages 级联不可查） |
| AC-24 引用闭包 | PASS | `test_full_chat_with_citations`（正文含 `[1]`；引用条数 = 最大序号；每条含 path/snippet；chunk 属于本 workspace 文档） |
| AC-25 六工具 | PASS | `test_six_tools_return_structured_results`（search/read/list_tasks/get_review/get_stats 结构化；`create_review` 仅 preview + token，reviews 增量 0）；`test_three_tool_calls_are_traced`（写入 run_steps） |
| AC-26 工具轨迹 | PASS | `test_three_tool_calls_are_traced`（seq 连续、与 tool_events 一致、摘要不含正文） |
| AC-27 无依据哨兵 | PASS | `test_empty_workspace_returns_sentinel`（content 精确等于“没有找到相关记录”，引用为空，无路径） |
| AC-28 workspace_id 注入 | PASS | `test_request_workspace_id_rejected_and_web_search_disabled`（422 + `WORKSPACE_ID_NOT_ALLOWED`）；`test_tool_args_reject_workspace_id` + 六个入参模型逐一断言 |
| AC-29 用量计量 | PASS | `test_full_chat_with_citations`（usage_events 一行，prompt/completion/model/latency 齐备） |
| AC-30 配置生效 | PASS | `test_retrieval_top_k_setting_limits_evidence`（retrieval_top_k=1 → 引用 ≤1）；web_search 请求返回 `FEATURE_DISABLED` |

补充（Phase 2 相关但非本阶段门禁）：

- `test_cross_tenant_chat_isolated`：另一租户的文档不会进入引用。
- `test_cancel_run`：`POST /runs/{id}/cancel` → 202，写 `chat.cancelled`。

---

## 3. SEC 对照（Phase 2）

| SEC | 结论 | 证据 |
|---|---|---|
| SEC-09 工具参数含 `workspace_id` | PASS | `test_tool_args_reject_workspace_id`（执行层拒绝 + 六个模型 `extra=forbid` 断言） |
| SEC-10 笔记内嵌指令 | PASS | `test_prompt_injection_detected_and_not_followed`：8 类攻击全部命中并写 `security.prompt_injection`；回答为哨兵；工具集合 ⊆ 只读白名单 |
| SEC-20 Markdown `<script>`/外链图片 | PASS | 注入的 `<script>`/`onerror`/外链图片文档被隔离，不进入 LLM 上下文；渲染侧 Phase 1 已测 |
| SEC-21 SVG/图片脚本 | 沿用 Phase 1 | 资产代理只按扩展名白名单返回，`Content-Security-Policy: sandbox` |
| SEC-22 对话超限 | PASS | `services_chat.create_turn`：workspace 20/min → `RATE_LIMITED`；user 200/day → `QUOTA_EXCEEDED` |
| SEC-26 发往 LLM 的请求 | PASS | `test_prompt_contains_only_minimal_evidence`：prompt 不含邮箱、workspace_id；只含 `<<<EVIDENCE>>>` 定界片段 |

---

## 4. FX-05 / FX-06

| fixture | 结论 | 证据 |
|---|---|---|
| FX-05 fx_injection | **PASS（8/8 类）** | `test_prompt_injection_detected_and_not_followed`：`instruction_override`、`external_image`、`html_script`、`path_traversal`、`fake_evidence_delimiter`、`secret_exfiltration`、`write_tool_request`、`cross_workspace_read` 全部命中；输出无密钥；工具调用 ⊆ 只读白名单 |
| FX-06 fx_tenant_leak | PASS（Phase 0 已建）+ Phase 2 增量 | `tests/test_fixture_fx06.py`；`test_cross_tenant_chat_isolated`（跨租户文档不进入引用，引用文档均属于本 workspace） |

---

## 5. 容器化 E2E（本机，2026-11-07）

服务：postgres / redis / migrate / api / worker / web / caddy。

| 步骤 | 结果 |
|---|---|
| 登录已有账号（经 Caddy HTTPS） | 200，active workspace 正确 |
| `POST /conversations` | 201 |
| `POST /conversations/{id}/turns` | 202，返回 run_id 与 events_url |
| 轮询 `GET /runs/{id}` | `succeeded`，`steps=2`（search_notes + read_note） |
| `GET /conversations/{id}/messages` | assistant 含 3 条引用；正文形如 `根据《Redis 缓存》，…[1]；…[2]；…[3]` |
| `GET /runs/{id}/events?after=0` | 200，含 `event: chat.tool_result` 与 `event: chat.done` |

---

## 6. 关键实现说明

1. **chat 精简分支**：`created → retrieving → drafting → verifying → succeeded/failed/cancelled`；不进入复盘门禁，不写 `reviews`。
2. **有界工具循环**：确定性首轮计划（search_notes 必选；任务/统计/复盘关键词追加工具；需要细节时追加 read_note），上限 10 次、单轮 60s。
3. **引用闭包**：`close_citations` 只保留可解析的 `[n]` 并按首次出现重排；无证据统一改写为哨兵；引用记录带 `document_id`/`chunk_id`（来自检索结果，不由 LLM 提供）。
4. **注入防护**：8 类模式命中即从证据集中剔除并写 `security.prompt_injection`；证据块定界符被中和（`<<<` → `＜＜＜`）。
5. **SSE**：持久化事件（run.state/tool/citation/done/error/cancelled）从 `run_events` 回放；`token` 仅经进程内总线推送；`retry: 3000`；epoch 不匹配返回 `degraded`。
6. **并发与幂等**：`(workspace, conversation)` 活跃唯一 + `(workspace, conversation, client_turn_id)` 唯一；新 turn 先取消旧 run；重复 turn 返回原 run 并附 `Idempotent-Replayed: true`。
7. **会话删除**：软删除 `deleted_at`；活跃 run 先 `cancelled`。

---

## 7. 未完成 / 偏差

| 项 | 状态 | 说明 |
|---|---|---|
| `create_review` 的 confirm / enqueue | 仅 preview | `POST /reviews` 与 review FSM 属 Phase 3；preview 已零副作用并返回 HMAC token |
| `get_review` 的 sections/entries/citations | 返回空数组 | 复盘结构化数据在 Phase 3 落地 |
| 真实 LLM（DeepSeek/OpenAI） | 默认 mock | 配置 `MYLIFE_LLM_PROVIDER=deepseek` + base_url/key 即切换；CI 不调用外部 API |
| 真实首 token <3s | 未测 | 按决策 8：mock P95<5s 为门禁；真实 provider 仅记录趋势 |
| 事件保留 30 天 / 归档 | 未实现 | 表与索引已建；归档任务随运维阶段补充 |
| 多进程 token 流 | 单进程内存总线 | 多副本部署时 token 不保证跨进程；最终消息与持久化事件仍完整 |

---

## 8. 下一阶段入口（Phase 3）

Phase 3 门禁：AC-25（create_review 确认）、AC-31…AC-38、AC-41、AC-42、AC-60、AC-66、AC-68；SEC-16/17/23；FX-01/02/03/04/10/12。

进入前需要：
1. 复盘 FSM（planning/retrieving/drafting/reviewing/revising/human_gate/delivering）；
2. 章节契约 `SECTION_SCHEMAS` 与 `review_sections/entries/citations`；
3. Todo 台账（`task_occurrences` → `tasks` 重建）与日期解析；
4. 确定性门禁 G0–G9 与引用校验；
5. 预算 guard（80% 告警 / 100% 硬停）。
