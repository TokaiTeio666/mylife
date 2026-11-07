# Phase 1 / M2 交付状态与验收证据

> 日期：2026-11-07 ｜ 范围：L1 接入层、documents/chunks/links、混合检索、文档树/详情、导入 UI、Celery worker
>
> 判定口径：`ACCEPTANCE-CRITERIA.md §5` Phase 1 行 + `SECURITY-BOUNDARY.md §11` Phase 1 门禁。

---

## 1. 结论摘要

| 项 | 状态 |
|---|---|
| 后端契约 + 数据库集成测试 | **90 passed, 1 skipped**（`uv run pytest -q`；skip 为 Windows 符号链接用例） |
| 前端 `tsc` / `eslint` / `next build` | 通过 |
| 依赖方向检查 | 通过 |
| AC-08…AC-21、AC-58 | 见 §2；FX-14 结果见 §6 |
| SEC-11/12/13/14/15 | 见 §3 |
| FX-13 搜索 P95 | **P95 104ms / P50 76ms / 0 降级**（10k docs / 80k chunks / 45 样本） |
| FX-14 200MB 导入 | **203.8s，5,000/5,000 成功**（门禁 <600s） |

---

## 2. AC 对照

| AC | 结论 | 证据（测试） |
|---|---|---|
| AC-08 Git 导入 / 分支切换 / 错误凭据 | PASS | `test_git_ingest.py::test_git_import_and_branch_switch`、`test_git_invalid_credential_leaves_documents_untouched` |
| AC-09 ZIP 导入 / 413 / 进度 | PASS | `test_ingest.py::test_zip_import_end_to_end`、`test_zip_upload_size_limit` |
| AC-10 忽略规则 | PASS | `test_ignore_rules`（5 类目录各 5 篇 → 0 行） |
| AC-11 frontmatter/标题/wikilink/分块 | PASS | `test_zip_import_end_to_end`、`test_parser_frontmatter_headings_links` |
| AC-12 增量与重命名 | PASS | `test_incremental_hash_and_rename`（二次导入 embedding 调用增量 0；重命名保留 document_id 与 chunk_id） |
| AC-13 文档树/渲染/XSS/CSP | PASS | `test_ingest.py::test_html_render_sanitizes_scripts`、`test_zip_import_end_to_end`（树节点） |
| AC-14 字段完整性 | PASS | `test_document_fields_not_null`（NULL=0，hash 长度 64，tags 为数组） |
| AC-15 部分失败与重试 | PASS | `test_partial_failure_and_retry`（partial、failed=2、失败原因、重试后成功文件 indexed_at 不变） |
| AC-16 RRF 复算与确定性 | PASS | `test_rrf_scores_and_determinism`（score 与 1/(60+rank) 复算一致；三次路径序列相同） |
| AC-17 中文分词与版本 | PASS | `test_chinese_tokenization_and_top10`（search_text 分词、tokenizer_version 一致） |
| AC-18 snippet 高亮与转义 | PASS | `test_snippet_highlight_and_escaping`（`<mark>`、`&lt;script&gt;`、无原始 `<script>`） |
| AC-19 四类过滤器 + mtime 扰动 | PASS | `test_filters_and_mtime_independence` |
| AC-20 结果字段与来源约束 | PASS | `test_result_shape` |
| AC-21 10k/80k P95<1s | PASS | FX-13：P95 104ms（见 §5） |
| AC-58 性能（搜索 + 200MB 导入） | PASS | FX-13 P95 104ms；FX-14 203.8s |

---

## 3. SEC 对照（Phase 1）

| SEC | 结论 | 证据 |
|---|---|---|
| SEC-11 写入路径 `../`、绝对路径、符号链接 | PASS | `mylife_runtime` 路径校验（T01–T12 规则）+ `test_zip_traversal_and_bomb_rejected`、`test_clone_rejects_symlink`（Linux）/ Windows skip |
| SEC-12 Git URL SSRF | PASS | `test_ssrf_rejected`（127.0.0.1 / 169.254 / localhost / file:// / ext::） |
| SEC-13 ZIP 压缩比/解压/文件数 | PASS | `test_zip_traversal_and_bomb_rejected`（ratio）、`inspect_zip` 上限；413 上传测试 |
| SEC-14 ZIP 路径穿越 | PASS | `test_zip_traversal_and_bomb_rejected`（`../../evil.md` 拒绝且无文件写出） |
| SEC-15 hooks/submodule/LFS/filter | PASS（参数级） | `test_git_common_args_hardened`（`core.hooksPath=/dev/null`、`protocol.file.allow=never`、`submodule.recurse=false`、LFS filter 清空）；克隆后符号链接拒绝 |

---

## 4. 关键实现说明

1. **增量索引**：`raw_hash` 未变 → 不解析、不 embedding；`content_hash` 未变 → 不动 `indexed_at`；重命名按 `content_hash` 迁移 `document_id` 并保留 chunk/embedding。
2. **检索**：三路候选（tsv top50、HNSW top50、标题/路径 top20）→ RRF k=60 → 按 `(score desc, path asc, ordinal asc)` 稳定排序。
3. **向量超时**：Postgres `statement_timeout=250ms` + `force_custom_plan`，避免取消 in-flight 查询破坏事务；超时返回 `degraded=vector_timeout`。
4. **导入执行**：Celery `q.ingest`；API 只投递任务。测试通过直接调用 `run_job` 验证真实管线。
5. **进度可见**：每 10 个文件提交一次进度与计数，API 轮询能实时看到 `processed_files` 增长。

---

## 5. FX-13 搜索性能

数据：10,000 文档 / 80,000 chunk（1024 维向量，HNSW m=16, ef_construction=64）；
50 条查询（中英混合，含目录/标签/日期/类型过滤、lexical/vector 单独模式），排除前 5 条预热。

| 指标 | 结果 |
|---|---|
| P50 | 76.23 ms |
| P95 | **104.15 ms**（门禁 <1000ms） |
| P99 | 107.75 ms |
| 降级响应 | 0 |

原始报告：`docs/evidence/fx13-search.json`；复现：`make bench-search`。

> 数据集为合成数据（每篇含相同术语），用于测量延迟而非召回率；recall@10 基线需真实标注集，Phase 2 与评测集一起建立。

---

## 6. FX-14 200MB 导入

结果文件：`docs/evidence/fx14-import.json`。

| 指标 | 结果 |
|---|---|
| ZIP 大小 | 191.6 MiB（200,902,802 字节，ZIP_STORED） |
| Markdown 文件数 | 5,000 |
| 索引文档数 | 5,000（failed=0） |
| chunk 数 | 5,000（每篇一个 H1 下无子标题） |
| 任务状态 | `succeeded` |
| 墙钟耗时 | **203.8 s**（门禁 <600s） |
| Embedding | 关闭（按 NFR-3 “不含 LLM”口径） |

复现：`make bench-import`。原始报告：`docs/evidence/fx14-import.json`。

> 说明：该数据集用不可压缩随机内容把 ZIP 撑到 200MB，且每篇仅一个标题；
> 真实 Vault 的 chunk 数会更多（分块更细），但条目级写入已使用 executemany。

---

## 7. 未完成 / 阻塞

| 项 | 状态 | 说明 |
|---|---|---|
| MinIO 对象存储 | 未接入 | 本机镜像不可达；Phase 1 使用本地数据根（`MYLIFE_DATA_ROOT`），见 `IMPLEMENTATION-NOTES.md` P1-1 |
| 复盘检索 `source_type=review` | 返回空集合 | `review_search` 在 Phase 3 落地 |
| 真实 Embedding（BGE-M3） | 默认 mock | 配置 `MYLIFE_EMBEDDING_PROVIDER=openai` + base_url/key 即切换；未在 CI 调用外部 API |
| recall@10 | 未建立 | 需要带标注的查询集；Phase 2 随评测集补充 |

---

## 8. 容器化 E2E（本机，2026-11-07）

服务：postgres / redis / migrate / api / worker（Celery q.ingest）/ web / caddy 全部启动；
api 与 worker 共享 `mylife_data` 卷。

| 步骤 | 结果 |
|---|---|
| `POST /api/v1/ingest/zip`（经 Caddy HTTPS） | 202，返回 job_id |
| worker 执行 | `succeeded`；counters `created=3, failed=0`；progress `3/3` |
| `GET /api/v1/search?q=缓存穿透` | 3 条结果；首条 `notes/redis.md`；`matched_by=both` |
| `GET /api/v1/documents/by-path?path=notes/redis.md` | 200，`content_html` 含渲染后的 `<h1>` |

> 说明：本次容器 E2E 的 worker 镜像构建于批处理优化提交之前，但已验证 Celery 队列、
> Redis、共享数据卷与 API 状态轮询链路。镜像构建脚本与 compose 配置已在仓库中。

---

## 9. 下一阶段入口（Phase 2）

Phase 2 门禁：AC-22…AC-30；SEC-09/10/20/21/22/26；FX-05、FX-06。

进入前需要：
1. ToolGateway 六工具与 `SearchResult` 契约对齐；
2. chat 精简 FSM（created → retrieving → drafting → verifying → succeeded）与 SSE 事件；
3. 引用闭包校验与无依据哨兵；
4. Prompt injection 检测与 `fx_injection`；
5. 首 token 门禁（mock provider P95<5s）。
