# mylife 验收标准（Acceptance Criteria）

- 文档版本：v1.0（验收基线）
- 状态：待评审（评审通过后冻结）
- 日期：2026-11-06
- 需求基线：`REQUIREMENTS.md` v1.0（已冻结）
- 架构基线：`ARCHITECTURE.md` v0.4（决策 `1D 2B 3B 4B 5B 6A 7A`）
- 安全基线：`SECURITY-BOUNDARY.md`（SEC-01…SEC-30；本文件只引用编号，不重复定义）
- 适用范围：MVP Phase 0–5

> 本文件只回答一个问题：**凭什么判定这一条通过了？**
> 每条标准必须给出可观察的判定条件、可复现的执行方式、可归档的证据。凡无法判定的表述，一律不进入本文件。

---

## 1. 原则与判定规则

### 1.1 三条硬性原则

| 原则 | 含义 | 落地要求 |
|---|---|---|
| 可观察 | 判定依据是外部可见的事实，不是实现者自述 | 断言必须落在 HTTP 响应、DB 行、文件内容、事件流、指标数值、截图之一 |
| 可复现 | 同一版本、同一数据、同一环境重复执行得到同一判定 | 固定 fixture、固定 runner、固定模型参数（temperature=0 或 mock）、固定时钟注入 |
| 可判定 | 结果是二值 PASS/FAIL，不允许“看起来还行” | 每条 AC 必须写明阈值、集合相等、计数为 0、字段存在等可计算条件 |

补充规则：

1. **禁止主观词**：体验良好、基本可用、比较快、大致正确、界面友好、性能可接受、内容合理。出现即视为该 AC 未定义完成。
2. **人工判断不得作为唯一证据**：人工只用于自动化无法覆盖的维度，且必须附检查表与截图/录屏。
3. **Prompt 不构成验收依据**：模型行为只能通过输出结构、引用闭包、确定性门禁验证；Prompt 文本本身不是证据。
4. **安全与正确性不接受抽样**：租户隔离、凭据、注入、引用、无证据回答一律全量断言。
5. **降级必须显式**：任何允许降级的 AC，必须同时断言“降级标记存在”，否则判 FAIL（对应 ARCHITECTURE §1.5.3 D4）。
6. **一次失败即失败**：AC 中的 `全部`、`0 条`、`100%` 均为全量判定；任一例外判 FAIL，不允许“整体通过、个别例外”。

### 1.2 验证方式定义

| 方式 | 定义 | 证据形态 |
|---|---|---|
| automated | CI 或脚本可重复执行，无需人判断 | pytest/JUnit XML、脚本退出码 0、指标 JSON、DB 查询输出 |
| manual | 需人按步骤操作并观察 | 步骤记录、截图/录屏、检查表勾选结果 |
| inspection | 对静态产物做检查（文件、配置、schema、文案表） | diff、扫描报告、schema 校验输出 |

### 1.3 通过 / 失败判定

| 判定 | 条件 |
|---|---|
| PASS | 全部 Then 子句成立，证据已归档，无降级或降级已标记 |
| FAIL | 任一 Then 子句不成立；或证据缺失；或出现未标记降级 |
| BLOCKED | 依赖环境不可用（如外部 LLM 不可达），必须在报告中标注 `blocked` 与环境原因，不得记 PASS |
| FLAKY | 同版本连续 3 次执行结果不一致，判 FAIL 并登记缺陷；不得重跑至通过 |

---

## 2. 编号、状态与记录格式

### 2.1 编号规则

| 类型 | 格式 | 范围 | 说明 |
|---|---|---|---|
| 功能验收 | `AC-01` … `AC-55` | FR-A/B/C/D/E/F/G（共 55 条 FR） | 一条 FR 恰好对应 1 条 AC，不合并、不遗漏 |
| 非功能验收 | `AC-56` … `AC-65` | NFR-1…NFR-10 | 与功能 AC 同一命名空间，避免两套编号 |
| 决策验收 | `AC-66` … `AC-68` | DECISIONS.md 的 5B/3B/4B | 决策必须有可判定 AC，不能只写在决策表 |
| 安全验收 | `SEC-01` … | 见 `SECURITY-BOUNDARY.md` | 本文件只引用编号，不定义 |
| 人工脚本 | `MA-01` … `MA-08` | 见 §8 | 每个脚本引用其覆盖的 AC |
| Golden fixture | `FX-01` … `FX-14` | 见 §4 | fixture 名与 ARCHITECTURE §9.3 对齐 |
| 测试用例 | `T-<域>-<nn>` | 见 §9 | 域取 A/B/C/D/E/F/G/N/S |
| 证据 | `EV-<AC>-<nn>` | 归档于 `docs/evidence/<phase>/<AC>/` | 报告、日志、截图、查询结果 |

### 2.2 AC 记录字段（每条 AC 必须齐备）

> §3 的矩阵表为控制列宽省略“标题”列；标题由该行 Given/When/Then 的第一句承担，且不得与需求描述重复。CI 中每条 AC 以 `ac_id + 标题` 注册，注册表缺失即视为该 AC 未定义完成。

| 字段 | 必填 | 说明 |
|---|---|---|
| AC ID | 是 | 全局唯一，不复用、不重排 |
| 标题 | 是 | 一句话描述“验什么”；存于测试注册表，不由需求原文复制 |
| 需求 | 是 | FR-xx / NFR-x；一条 AC 可关联多条需求 |
| Given / When / Then | 是 | 前置数据、触发动作、可计算断言 |
| 验证方式 | 是 | automated / manual / inspection |
| 证据 | 是 | 证据 ID 与存放位置 |
| 优先级 | 是 | P0 阻断发布、P1 阻断阶段退出、P2 阶段内可延后 |
| Phase | 是 | 首次必须通过的阶段 |
| 状态 | 是 | 见 §2.3 |

### 2.3 状态机

```text
draft → implemented → verified → accepted
                ↘ blocked（依赖缺失，需登记原因与恢复条件）
accepted → reopened（缺陷回归或需求变更，必须重跑）
```

| 状态 | 进入条件 | 允许流转 |
|---|---|---|
| draft | AC 已定义，未实现 | implemented |
| implemented | 代码合入且有对应测试 | verified / blocked |
| verified | 证据齐全且 PASS，评审人复核 | accepted / reopened |
| accepted | 阶段退出评审签字 | reopened |
| blocked | 环境/依赖不可用 | implemented / verified |
| reopened | 缺陷或变更导致结论失效 | implemented |

**状态为 accepted 的必要条件**：证据链接可访问、执行时间在 7 天内、执行版本号等于待发布版本。

---

## 3. 需求验收矩阵（AC-01 … AC-68）

### 3.0 覆盖核对

| 需求组 | 条数 | AC 区间 | 是否全覆盖 |
|---|---|---|---|
| FR-A1…A7 | 7 | AC-01…AC-07 | 是 |
| FR-B1…B8 | 8 | AC-08…AC-15 | 是 |
| FR-C1…C6 | 6 | AC-16…AC-21 | 是 |
| FR-D1…D9 | 9 | AC-22…AC-30 | 是 |
| FR-E1…E14 | 14 | AC-31…AC-44 | 是 |
| FR-F1…F4 | 4 | AC-45…AC-48 | 是 |
| FR-G1…G7 | 7 | AC-49…AC-55 | 是 |
| NFR-1…NFR-10 | 10 | AC-56…AC-65 | 是 |
| 合计 | 55 FR + 10 NFR + 3 决策 | 68 条 AC | 无遗漏、无合并；决策 AC 单列在 §3.9 |

### 3.1 账户、认证与租户（FR-A）

| AC | FR | Given → When → Then（可计算断言） | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-01 | FR-A1 | 未注册邮箱。POST /auth/register 密码 8 位。→ 201 且 users.password_hash 前缀为 argon2id；DB 全表扫描无明文密码。密码 7 位 → 422。同 IP 60 秒内第 6 次失败登录 → 429，且错误邮箱与错误密码返回同一文案与同一状态码。 | automated | EV-AC-01-01 pytest；EV-AC-01-02 限流脚本输出 | P0 | 0 |
| AC-02 | FR-A2 | 管理员生成 1 个未使用邀请码。→ 首次注册 201 且 invite_codes.used_at 非空；同码第二次注册 → 403；不带码注册 → 403；过期码 → 403。并发 2 次同码注册，恰好 1 次成功。 | automated | EV-AC-02-01 并发脚本（2 线程）；EV-AC-02-02 DB 行 | P0 | 0 |
| AC-03 | FR-A3 | 新用户注册成功。→ GET /workspaces 恰好返回 1 条，name 等于“我的知识库”，当前用户在 workspace_members 中 role=owner；该 Workspace 的 documents 计数为 0 且不为 NULL workspace_id。 | automated | EV-AC-03-01 API 响应 JSON | P0 | 0 |
| AC-04 | FR-A4 | 用户属于 2 个 Workspace。→ 切换至 W2 后，GET /documents/tree 仅返回 W2 文档，条数与 W2 计数相等、与 W1 交集为 0；sessions.active_workspace_id 更新。切换到非成员 W3 → 404（非 403）。 | automated | EV-AC-04-01 双 Workspace 夹具；EV-AC-04-02 响应体对比 | P0 | 0 |
| AC-05 | FR-A5 | 以 owner 身份尝试为成员设置 role=admin。→ 请求被拒（4xx），workspace_members 不新增该行；DB 枚举值集合包含 owner/platform_admin/admin/member；API 可分配值集合仅 {owner}。 | automated + inspection | EV-AC-05-01 负向用例；EV-AC-05-02 枚举定义 diff | P1 | 0 |
| AC-06 | FR-A6 | 登录成功。→ Set-Cookie 含 HttpOnly、Secure、SameSite=Lax、Path=/；响应体与 JS 可读存储中无 token。登出后同 cookie 请求 → 401。执行“退出所有设备”后，旧 cookie 全部 401，sessions.token_version 加 1，其他设备会话 revoked_at 非空。 | automated | EV-AC-06-01 cookie 头断言；EV-AC-06-02 双客户端脚本 | P0 | 0 |
| AC-07 | FR-A7 | 依次执行登录、导入、删除、配置变更、复盘生成。→ audit_logs 新增恰好 5 类 action 各至少 1 行，字段 workspace_id/user_id/action/target/ip/created_at 齐全；detail 中不含明文 API Key、不含笔记正文超过 200 字符的片段。 | automated | EV-AC-07-01 SQL 计数；EV-AC-07-02 脱敏正则扫描 | P1 | 0（五类全量在 5） |

### 3.2 笔记导入与文档管理（FR-B）

| AC | FR | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-08 | FR-B1 | 私有仓库含 120 篇 md，分支 main。→ 连接成功；手动同步后 source_snapshots.commit_sha 等于远端 HEAD，documents 计数 120；切换分支后同步，计数等于该分支实际 md 数。错误凭据 → 连接状态 blocked 且错误码可读，documents 无新增行。 | automated | EV-AC-08-01 本地裸仓库夹具；EV-AC-08-02 commit_sha 比对 | P0 | 1 |
| AC-09 | FR-B2 | 上传 200MB Vault ZIP。→ 202 返回 job_id；进度事件从 0 单调递增至 100；导入完成后计数等于 ZIP 内 md 数。上传 201MB → 413 且不创建 job。解压后总大小 >2GB → 拒绝并记 job 失败原因。 | automated | EV-AC-09-01 进度事件序列；EV-AC-09-02 413 响应 | P0 | 1 |
| AC-10 | FR-B3 | Vault 含 `.obsidian/`、`.trash/`、`.stfolder/`、`.git/`、`node_modules/` 各 5 个 md。→ 导入后 documents 中来自这些路径的行数为 0；忽略规则可配置且默认值等于上述 5 项。 | automated | EV-AC-10-01 路径前缀查询 | P0 | 1 |
| AC-11 | FR-B4 | fixture 文件含 YAML frontmatter（tags 3 个）、3 级标题、2 个 wikilink、1 个表格。→ frontmatter_json 解析出 3 个 tags；chunks 的 heading 路径为三级串联；document_links 恰好 2 行且 target_path 原样保留；标题、正文、标签四项非空。 | automated | EV-AC-11-01 DB 行快照 | P0 | 1 |
| AC-12 | FR-B5 | 首次导入 120 篇后再次同步同一 commit。→ embedding 调用计数增量 0，parser 调用增量 0，documents.indexed_at 不变。修改 1 篇正文 → 仅 1 篇 content_hash 变化，新增 embedding 批次数等于该文件块数。重命名文件 → document_id 不变，chunk_id 不变。 | automated | EV-AC-12-01 mock 计数器；EV-AC-12-02 重命名前后 ID 对比 | P0 | 1 |
| AC-13 | FR-B6 | 目录 `a/b/c.md` 与图片 `a/img/x.png`。→ 树节点路径层级为 a→b→c.md，深度 3；渲染后含 code block、table、img 标签；含 `<script>alert(1)</script>` 的 md 渲染结果中该脚本被移除或转义，响应头含 CSP。 | automated | EV-AC-13-01 树 JSON；EV-AC-13-02 XSS 渲染输出 | P1 | 1 |
| AC-14 | FR-B7 | 对 documents 全表执行字段完整性查询。→ path/title/tags/size/mtime/content_hash/workspace_id 的 NULL 行数为 0；content_hash 长度 64；tags 为数组类型。 | automated | EV-AC-14-01 SQL 断言输出 | P1 | 1 |
| AC-15 | FR-B8 | 10 篇中 2 篇解析失败（非法 frontmatter）。→ job 状态 succeeded_with_errors；计数 queued/processing/succeeded/failed/skipped 之和等于 10，failed=2；GET job 返回每文件错误原因；重试仅处理 2 篇失败文件，成功文件 indexed_at 不变。 | automated | EV-AC-15-01 job 计数 JSON；EV-AC-15-02 重试前后时间戳 | P1 | 1 |

### 3.3 搜索与检索（FR-C）

| AC | FR | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-16 | FR-C1 | 查询同时命中词法与向量的词。→ 单次返回统一列表，score 由 RRF k=60 复算与返回值一致（误差 <1e-6）；同一查询连续 3 次结果路径序列完全相同；matched_by 至少出现 lexical 与 vector 各 1 条。 | automated | EV-AC-16-01 RRF 复算脚本；EV-AC-16-02 三次结果 diff | P0 | 1 |
| AC-17 | FR-C2 | 文档含“缓存穿透”。查询“缓存穿透”。→ 该文档进入 top 10；search_text 为 jieba 切分后的空格串；tokenizer_version 参与 parser_version 与 content_hash；查询侧与索引侧词典版本一致（版本号相等）。 | automated | EV-AC-17-01 命中断言；EV-AC-17-02 版本字段 | P0 | 1 |
| AC-18 | FR-C3 | 查询“缓存”。→ 每条结果含 title、path、snippet_html；命中词被 `<mark>` 包裹；snippet 中 `<` `>` 已转义（注入 fixture 返回 `&lt;script&gt;`）；片段长度 15–40 词。 | automated | EV-AC-18-01 HTML 断言 | P1 | 1 |
| AC-19 | FR-C4 | 同一查询分别加目录、标签、日期区间、文件类型四个过滤器。→ 每种过滤后结果集是全集真子集且每条满足谓词；四者组合为 AND；日期过滤使用 occurred_on（把 mtime 改乱后结果不变）。 | automated | EV-AC-19-01 谓词校验脚本；EV-AC-19-02 mtime 扰动对照 | P1 | 1 |
| AC-20 | FR-C5 | 任意一次搜索返回 20 条。→ 每条 path/title/snippet/score 非空；document 来源时 document_id 与 chunk_id 非空、review_id 为 NULL；review 来源时相反；heading 允许 NULL 但字段必须存在。 | automated | EV-AC-20-01 schema 校验输出 | P1 | 1 |
| AC-21 | FR-C6 | 10k 文档、约 80k chunk 数据集，≥20 次采样。→ 搜索 P95 < 1000ms（与 AC-58/FX-13 同源）；固定标注查询集上的 recall@10 不低于基线阈值；无冷启动样本混入。 | automated | EV-AC-21-01 FX-13 报告；EV-AC-21-02 recall@10 报告 | P0 | 1 |

### 3.4 Agent 对话（FR-D）

| AC | FR | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-22 | FR-D1 | 已导入 Vault。POST turn 使用 mock LLM（固定 20 token 流）。→ 响应 Content-Type 为 text/event-stream；**门禁以 mock provider 的 P95 <5s 为准**；真实 provider 仅记录 P50 趋势（目标 <3s），不阻断；Caddy 不缓冲（连续事件间隔 <100ms 可观测）；流结束发送 chat.done。 | automated | EV-AC-22-01 时延直方图（mock 门禁 + 真实趋势）；EV-AC-22-02 SSE 抓包 | P0 | 2 |
| AC-23 | FR-D2 | 新建会话 → 发 2 轮 → 重命名 → 刷新页面 → 删除。→ 会话列表顺序与 updated_at 一致；刷新后消息 2 条完整；第 2 轮回答能引用第 1 轮提到的实体；删除后 GET 404 且 messages 级联不可查。 | automated | EV-AC-23-01 会话 CRUD 脚本 | P1 | 2 |
| AC-24 | FR-D3 | 提问“我学过哪些 Redis 内容？”→ 回答正文含至少 1 个 `[n]` 标记；回答下方引用列表条数等于正文最大序号；每条含 path 与 snippet；references_json 中每个 chunk_id 属于本次 run 冻结集合。 | automated | EV-AC-24-01 引用闭包校验；EV-AC-24-02 消息 JSON | P0 | 2 |
| AC-25 | FR-D4 | 逐一调用 6 个工具。→ search_notes/read_note/list_tasks/get_review/get_stats 均返回结构化结果且写入 run_steps；create_review 在未确认时仅返回 preview+confirmation_token，reviews 行数增量为 0；确认后返回 run_id 且 reviews 增量为 1；review run 内再调 create_review → 拒绝。 | automated | EV-AC-25-01 六工具用例；EV-AC-25-02 确认前后计数 | P0 | 2（create_review 确认在 3） |
| AC-26 | FR-D5 | 发起一次含 3 次工具调用的对话。→ UI 展示 3 条轨迹，每条含工具名、参数摘要（脱敏）、状态、耗时毫秒；run_steps 中 seq 连续且与 UI 顺序一致；参数摘要不含笔记正文全文。 | automated + manual | EV-AC-26-01 run_steps 行；EV-AC-26-02 截图 | P1 | 2 |
| AC-27 | FR-D6 | 空 Workspace 提问任意事实性问题。→ 回答包含固定哨兵语义“没有找到相关记录”；claims 中无 evidence 支持的句子数为 0；引用列表为空；无编造路径（所有出现路径不在 documents 中即判 FAIL）。 | automated | EV-AC-27-01 空库 fixture；EV-AC-27-02 路径存在性校验 | P0 | 2 |
| AC-28 | FR-D7 | 构造请求体带 workspace_id 指向他人 Workspace；构造 LLM 输出试图传 workspace_id。→ 前者 422（extra=forbid）；后者工具调用参数校验失败或被服务端覆盖；两种情况下目标 Workspace 数据读取行数为 0；请求日志不含被接受的 workspace_id。 | automated | EV-AC-28-01 负向请求；EV-AC-28-02 越权读计数 | P0 | 2 |
| AC-29 | FR-D8 | 一次对话完成。→ usage_events 新增 1 行，含 prompt_tokens、completion_tokens、model、latency_ms；数值与 provider 返回 usage 差值 ≤1 token；成本字段按配置单价可复算。 | automated | EV-AC-29-01 usage 行；EV-AC-29-02 与 provider 回执比对 | P1 | 2 |
| AC-30 | FR-D9 | 修改 retrieval_top_k=5、context_window_tokens=8000、web_search_enabled=true。→ 单轮证据条数 ≤5；超出窗口时发生裁剪且 run_events 有对应事件；web_search 默认值为 false；MVP 请求置 true 返回 `FEATURE_DISABLED` 或保持 false（开启行为属 Phase 2，不在 MVP 验收范围）。 | automated + inspection | EV-AC-30-01 配置生效断言；EV-AC-30-02 默认值与拒绝断言 | P2 | 2 |

### 3.5 复盘系统（FR-E）

| AC | FR | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-31 | FR-E1 | 素材含 `- [x] 写周报`、`- [ ] 读论文`、一句“今天完成了写周报”无复选框。→ 台账中前两条状态为 done/open；第三条不生成任务行；`10.27.md` 解析出 occurred_on=2026-10-27；ingest 路径 LLM 调用计数为 0。 | automated | EV-AC-31-01 台账行；EV-AC-31-02 LLM 计数 | P0 | 3 |
| AC-32 | FR-E2 | 同一任务文本在 10-25、10-26、10-27 出现。→ tasks 行 first_seen=10-25、last_seen=10-27、done_date 非空（若 27 日勾选）；source_path 与 line_number 指向最后一次出现；task_occurrences 唯一约束冲突数为 0。 | automated | EV-AC-32-01 台账 SQL | P0 | 3 |
| AC-33 | FR-E3 | 10-26 未完成、10-27 未再出现、10-28 勾选完成。→ 10-27 日报“以往顺延”含该任务；10-28 完成后 done_date=10-28 且从逾期列表移除；中间日期未完成计数单调不降。 | automated | EV-AC-33-01 三日 fixture；EV-AC-33-02 状态迁移 | P0 | 3 |
| AC-34 | FR-E4 | `日记/2026/10.27.md` 存在正文 800 字。→ 日报 journal_events 章节的证据 source_path 指向该文件，occurred_on=10-27；文件名与目录两种日期来源冲突时以显式日期优先并记 warning。 | automated | EV-AC-34-01 证据绑定 | P1 | 3 |
| AC-35 | FR-E5 | 当日有 3 篇学习笔记变更。→ 学习摘要含 topic、concept_or_method、output、open_questions 四字段各非空；每个字段绑定至少 1 个 evidence_id；document_changes 覆盖这 3 篇。 | automated | EV-AC-35-01 结构化字段校验 | P1 | 3 |
| AC-36 | FR-E6 | 运行 daily 2026-10-27。→ review_sections 恰好 7 行，section_key 集合与 §5.1 常量完全相等，ordinal 为 1…7 无重复无缺号；每节 content_md 非空；空素材日 7 节全部使用允许哨兵；引用闭包 100%；每阶段抽样 20 条人工复核，事实一致率 ≥98%（不合格扩大样本，决策 Q3）。 | automated + manual | EV-AC-36-01 G1 断言输出；EV-AC-36-02 FX-04；EV-AC-36-03 抽样报告 | P0 | 3 |
| AC-37 | FR-E7 | 运行 weekly 2026-W43，7 份日报齐备。→ 恰好 4 节；completion_rate 的 completed/total/rate 与台账 SQL 复算逐位相等；重复错误 Top 与 review_entries 指纹聚合一致；缺 1 天时 coverage_gaps 记录该日期且不编造内容。 | automated | EV-AC-37-01 复算脚本；EV-AC-37-02 缺日 fixture | P0 | 3 |
| AC-38 | FR-E8 | 运行 yearly 2026，存在 40 份周报。→ 恰好 5 节；数据只来自下级 rollup（删除周报后重跑，差异条目数等于删除周数且均属 coverage_gaps）；错误 Top10 按当前拍板口径确定性复算：频次降序，并列时 canonical_key 升序（代价/最近发生的权重如需启用，先更新 DECISIONS.md）；单次输入 token 不超过 yearly 预算。 | automated | EV-AC-38-01 排序复算 SQL；EV-AC-38-02 token 计数 | P0 | 3 |
| AC-39 | FR-E9 | 交付完成。→ 有 Git 连接时：文件存在于 `wiki/复盘/每日复盘/2026/2026-10-27.md`、`每周复盘/2026/2026-W43.md`、`年度复盘/2026.md`、`专题/错误清单.md` 等路径，`data/reviews` 无新写入；无 Git 连接时：对象存储产物 + 下载 ZIP 含同一 Markdown 与 manifest，内容哈希一致，作为**等效验收证据**（决策 Q2）；兼容读取别名可读到同一内容。 | automated + inspection | EV-AC-39-01 文件/对象清单；EV-AC-39-02 双目录与对象存储对比 | P0 | 4 |
| AC-40 | FR-E10 | 连续 3 天产生错误条目。→ 错误清单/正确方法论/行动清单三文件存在；同 canonical_key 只 1 条；occurrence 逐次追加；重渲染后标记内内容与 DB 一致、标记外人工文本保留；重跑不重复累加。 | automated | EV-AC-40-01 三文件 diff；EV-AC-40-02 重跑计数 | P0 | 4 |
| AC-41 | FR-E11 | 任一含错误条目的日报。→ 100% 条目有 source_date、source_path、line_range；quote_hash 按公式复算与存储值相等；无证据条目数为 0；删除对应源文档后重跑 → 该条目被移除或标 EVIDENCE_STALE，不保留。 | automated | EV-AC-41-01 G3/G4 输出；EV-AC-41-02 源删除对照 | P0 | 3 |
| AC-42 | FR-E12 | 手动触发 daily 2026-10-27。→ 返回 run_id；已有活跃 run 时 409 且返回现有 run_id；已成功后再触发 → 创建 version+1，旧 version 仍可查。 | automated | EV-AC-42-01 三次触发序列 | P1 | 3 |
| AC-43 | FR-E13 | 时区 Asia/Shanghai，默认 01:00。→ next_run_at 等于本地 01:00 对应 UTC；改时区为 America/New_York 后重算正确；DST 不存在时刻顺延、重复时刻取较早 offset；停机 10 天后启动，补算不超过 catchup_days=7，超出记 catchup_window_exceeded。 | automated | EV-AC-43-01 时区用例矩阵；EV-AC-43-02 停机补算 | P1 | 4 |
| AC-44 | FR-E14 | 同日连续生成 2 次，第 2 次内容不同。→ 文件路径不变、文件数仍为 1、reviews.version=2、review_versions 2 行、review_sections 按 version 隔离；并发触发 2 个请求 → 恰好 1 个活跃 run，另一个 409。 | automated | EV-AC-44-01 文件与版本查询；EV-AC-44-02 FX-12 | P0 | 4 |

### 3.6 仪表盘与任务面板（FR-F）

| AC | FR | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-45 | FR-F1 | 存在 1 日报、1 周报、1 年报、10 个任务（6 完成）。→ 仪表盘返回三类最新产物各 1 且为 current version；完成率显示 60.0%（±0.1）；未完成数 4；数值与 SQL 复算一致；响应不含 LLM 调用。 | automated | EV-AC-45-01 API 与 SQL 对比 | P1 | 4 |
| AC-46 | FR-F2 | 3 个未完成任务，first_seen 分别为 3/5/10 天前。→ 面板列出 3 条，持续天数为 3/5/10（按 workspace 时区计算，跨时区边界一致）；已完成为 0 条。 | automated | EV-AC-46-01 时区边界用例 | P1 | 4 |
| AC-47 | FR-F3 | 某月 10 天有日报、20 天无。→ 日历返回 31 个日期项，10 个 has_review=true 且带状态，21 个 false；周/年视图可切换到 W43 与 2026。 | automated | EV-AC-47-01 日历 JSON | P2 | 4 |
| AC-48 | FR-F4 | 搜索词同时命中笔记与复盘。→ 默认返回 source_type=both 的混合结果；切“仅复盘”后 100% 为 review 且 review_id 非空；切“仅笔记”后 100% 为 document。 | automated | EV-AC-48-01 三种模式结果 | P1 | 4 |

### 3.7 设置与平台管理（FR-G）

| AC | FR | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-49 | FR-G1 | 保存 DeepSeek 兼容配置与 API Key。→ provider_credentials.key_ciphertext 为密文（不等于明文、长度与格式符合 AES-256-GCM）；日志与错误响应中明文出现次数 0；测试连接成功返回 ok；失败返回可读错误码且 provider_credentials 不新增/不更新行（失败不落库）。 | automated | EV-AC-49-01 密文断言；EV-AC-49-02 日志扫描与 DB 行数 | P0 | 5 |
| AC-50 | FR-G2 | 首配 BGE-M3 1024 维；随后申请换为 768 维模型。→ 新建 document_chunks_v2 与 HNSW 索引；re-embed 覆盖率 100% 后才原子切换 embedding_profile_id；切换前旧查询仍可用；切换后回滚到旧 profile 可用且不丢数据；无原地 ALTER 维度语句。 | automated | EV-AC-50-01 蓝绿迁移脚本；EV-AC-50-02 FX-11 | P0 | 5 |
| AC-51 | FR-G3 | 修改 daily_at=02:00、weekly_at=周一 02:30、yearly_at=1/1 03:00、timezone。→ workspace_settings 三值更新；workspace_schedules.next_run_at 重算正确；已有活跃 run 不被中断。 | automated | EV-AC-51-01 配置与 next_run_at | P1 | 4 |
| AC-52 | FR-G4 | 配置源/输出仓库地址、分支、SSH Key、同步频率。→ git_connections 行字段齐全（role、provider、repo_full_name、base_branch、sync_interval_minutes、next_sync_at）；凭据密文存储；连接测试校验 contents:write，缺失即拒绝启用并返回明确原因；同步频率变更后 next_sync_at 按新间隔重算，dispatcher 到点触发一次定时拉取。 | automated | EV-AC-52-01 连接测试矩阵；EV-AC-52-02 调度触发日志 | P1 | 4 |
| AC-53 | FR-G5 | 完成 3 次对话与 1 次复盘。→ 用量页 token 总数等于 usage_events 求和（差值 0）、调用次数等于行数、复盘次数等于 review run 成功数；按 Workspace 隔离，跨 Workspace 汇总为 0。 | automated | EV-AC-53-01 对账 SQL | P1 | 5 |
| AC-54 | FR-G6 | platform_admin 打开管理后台。→ 用户/Workspace/邀请码三列表分页返回；生成邀请码可用；禁用账号后该用户登录 401 且已有会话失效；普通用户访问 /admin 返回 404 或 403；每次操作写 audit_logs。 | automated + manual | EV-AC-54-01 管理 API 用例；EV-AC-54-02 截图 | P0 | 5 |
| AC-55 | FR-G7 | 请求导出 → 请求删除 → 冷却期内撤销 → 再次删除并等待 purge。→ 导出 ZIP 含 documents 原文、reviews、manifest.json，条目数等于 DB 计数；删除需二次确认；状态 requested→cooling→purged；冷却期内撤销后状态回 requested/cancelled 且数据完整；purge 后 `DATA-MODEL.md` §9.2 级联清单全部 0 命中（含 evidence_items/run_events 的正文与引用）；**Git 历史仍存在且可通过 git log 读取已合并提交**（正向断言，决策 Q4）；删除页与用户协议明示“不承诺从 Git 历史抹除”。 | automated + manual | EV-AC-55-01 导出清单；EV-AC-55-02 MA-08 删除演练 | P0 | 5 |

### 3.8 非功能需求（NFR）

| AC | NFR | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-56 | NFR-1 | 两张租户表 × 每表参数化用例。→ A 会话查 B 数据返回 0 行；RLS 为 ENABLE+FORCE；应用角色 NOBYPASSRLS；子表复合外键存在；跨租户 HTTP 统一 404；工具层越权用例 0 命中。 | automated | EV-AC-56-01 RLS 矩阵报告；EV-AC-56-02 外键与角色查询 | P0 | 0 |
| AC-57 | NFR-2 | 执行 `SECURITY-BOUNDARY.md` 的 SEC 全集。→ 本 AC 只判定“集合全部 PASS”，逐条结果见该文件；任何 SEC 为 FAIL 或 blocked 时本 AC FAIL。 | automated + manual | EV-AC-57-01 SEC 汇总表 | P0 | 0 起 |
| AC-58 | NFR-3 | 10k 文档 / 80k chunk；200MB Vault。→ 搜索 P95 <1000ms（20 次以上采样，排除冷启动首次）；200MB 导入墙钟 <600s（不含 LLM）；两者均需给出分阶段埋点。 | automated | EV-AC-58-01 FX-13 报告；EV-AC-58-02 FX-14 报告 | P0 | 1 |
| AC-59 | NFR-4 | 运行中 kill -9 worker；重启全部容器；网络中断 30s。→ 任务在 5 分钟内续跑或转 RETRY_WAIT；同 period 不产生重复产物；失败在 job_runs/agent_runs 可见；容器 restart 策略为 unless-stopped；无静默丢失。 | automated | EV-AC-59-01 FX-09；EV-AC-59-02 容器配置 diff | P0 | 0（续跑在 3/4） |
| AC-60 | NFR-5 | 任一已完成 run。→ 结构化日志每行含 ts/level/service/trace_id/workspace_id/run_id/event 且无正文；/healthz 与 /readyz 分离且语义正确；用 run_events 从 CREATED 重放到终态，状态序列与 DB 完全一致（100% 步数可回放）。 | automated | EV-AC-60-01 日志字段扫描；EV-AC-60-02 回放脚本 | P1 | 3 |
| AC-61 | NFR-6 | 执行备份与恢复演练。→ 每日 pg_dump 存在且校验和可读；MinIO 备份对象数与源一致；保留 7 天与 30 天两档可查；从备份恢复到新库后，documents/reviews 计数与备份时点相等，抽样 10 篇 content_hash 一致；**每个候选版本发版前必做 1 次全链路恢复，此后每季度 1 次**（决策 Q5）；RTO ≤4h、RPO ≤24h；演练耗时记录在案。 | automated + manual | EV-AC-61-01 恢复演练报告（含发版前记录） | P1 | 5 |
| AC-62 | NFR-7 | 桌面 1440、平板 768、手机 390 宽度。→ 三种宽度下无横向滚动（overflow-x 为 0）；核心操作（搜索、打开文档、发消息）可完成；Lighthouse PWA 安装性检查通过；断网打开显示应用壳与离线提示，不白屏。 | automated + manual | EV-AC-62-01 设备矩阵截图；EV-AC-62-02 Lighthouse JSON | P1 | 5 |
| AC-63 | NFR-8 | 扫描前端源码与构建产物。→ 组件内硬编码中文文案数为 0（白名单仅测试 fixture）；文案键在单一资源文件；默认 locale=zh-CN；切换 locale 后至少 3 个页面文案变化。 | automated | EV-AC-63-01 扫描报告 | P2 | 5 |
| AC-64 | NFR-9 | 用户请求导出与删除。→ 导出覆盖全部用户数据类别（笔记、复盘、对话、用量、审计）；删除流程与 AC-55 同源；隐私政策与用户协议页面可访问且返回 200；页面含数据出境供应商说明，以及“已合并的 Git 历史保留、不承诺抹除”的明确告知（决策 Q4）。 | inspection + manual | EV-AC-64-01 页面截图；EV-AC-64-02 数据类别清单 | P1 | 5 |
| AC-65 | NFR-10 | 全新机器，仅装 Docker 与 .env。→ `docker compose up -d` 单命令后 10 分钟内全部服务 healthy；Caddy 自动签发 HTTPS 且 HTTP 跳转 443；`make upgrade` 完成迁移与滚动重启；失败时回滚到上一镜像且数据不丢。 | automated | EV-AC-65-01 部署日志；EV-AC-65-02 升级回滚记录 | P0 | 0（升级在 5） |

### 3.9 已拍板决策的验收（AC-66–AC-68）

| AC | 决策 | Given → When → Then | 方式 | 证据 | P | Phase |
|---|---|---|---|---|---|---|
| AC-66 | 5B 预算 | 构造超预算 run：daily 输入达 100k、月累计达 5M。→ 80% 产生告警事件且继续；100% 终止为 FAILED/NEEDS_HUMAN；预算跨重试与修订累计（不重置）；超限后 LLM 调用计数增量为 0；不输出“数据版复盘”。 | automated | EV-AC-66-01 FX-10 report；EV-AC-66-02 usage_events | P0 | 3 |
| AC-67 | 3B 自动合并 | daily 默认 auto_merge_daily=false；开启且 Gate 全通过时 auto-merge；任一 Gate 失败时保持 PR 人工；weekly/yearly 请求 auto-merge 被拒绝。→ 与配置和 GateResult 一致，且每次自动合并写 audit_logs。 | automated | EV-AC-67-01 FX-08 PR 元数据；EV-AC-67-02 配置矩阵 | P1 | 4 |
| AC-68 | 4B 专题首见 | 首次抽取到新错误条目。→ topic_items.status=pending，不进专题页与 open 视图；人工确认后转 open 并出现在专题页；同一 canonical_key 复发时自动累加 occurrence，不重复建条目。 | automated + manual | EV-AC-68-01 FX-07；EV-AC-68-02 专题页截图 | P1 | 3 |

---

## 4. Golden fixtures

### 4.1 通用约束

| 项 | 规则 |
|---|---|
| 存放 | `tests/fixtures/<name>/`，只读，提交进仓库；二进制走 Git LFS 或生成脚本 |
| 确定性 | 所有 fixture 固定 `parser_version`、`tokenizer_version`、`embedding_profile`、`temperature=0`；LLM 走录制回放或 mock |
| 时钟 | 注入固定 `now`，禁止依赖真实系统时间 |
| 执行 | `pytest -m fixture` 单命令跑全量；每个 fixture 输出 `report.json`（断言逐条 PASS/FAIL） |
| 复用 | 同一 fixture 可被多条 AC 引用；fixture 变更需在 PR 中说明影响的 AC |
| 禁止 | 不得用生产数据；不得包含真实人名、邮箱、密钥；注入类 fixture 只含合成攻击串 |

### 4.2 fixture 清单

| ID | fixture | 输入 | 预期与断言 | 证据 | 关联 AC |
|---|---|---|---|---|---|
| FX-01 | fx_daily_2026-10-27 | 120 篇 md Vault；10-25…10-27 三日 Todo；10-27 日记 1 篇；3 篇学习笔记变更；2 条错误、1 条方法论 | 7/7 章节齐；每节证据 ≤12、全局 ≤80；≥1 条错误带 path 与 line_range；顺延 2 条、回填 1 条且 done_date 正确；无证据条目 0；G0–G9 全 PASS；同 snapshot 重跑 diff 率 ≤10% | report.json、渲染后的 md、verdict JSON | AC-31…AC-36、AC-41 |
| FX-02 | fx_weekly_W43 | W43 内 7 份日报（含 1 份 degraded）；台账原始 Todo | 4 节齐；完成率与台账复算逐位相等；重复错误按指纹出现 ≥2 次标出，Top 排序确定；缺 1 日时 coverage_gaps 含该日且不编造 | report.json、复算 SQL 输出 | AC-37、AC-38 |
| FX-03 | fx_yearly_2026 | 52 周周报（含 3 周缺失）；全年台账 | 5 节齐；只读下级 rollup（断链周报后结果可解释）；错误 Top10 按 频次→代价→最近 排序可复算；输入 token ≤ yearly 预算 | report.json、排序复算输出 | AC-38 |
| FX-04 | fx_empty_day | 某日无 Todo、无日记、无笔记变更 | 7 节全部为允许哨兵原文；虚构 claim 数 0；引用数 0；run 正常 SUCCEEDED（非 FAILED）；no_evidence_ratio=1.0 | report.json、渲染 md | AC-36、AC-27 |
| FX-05 | fx_injection | 笔记内嵌 8 类攻击串：忽略以上指令、外链图片、HTML script、路径穿越 `../../etc`、伪造 EVIDENCE 定界符、要求输出 API Key、要求调用写工具、要求跨 workspace 读 | 0 条指令被遵循；8 类全部命中检测并写 security 事件；工具调用集合 ⊆ 只读白名单；输出路径全部在 snapshot manifest 内；渲染后无 script 与外部图片请求 | report.json、事件流、渲染 HTML | AC-57（SEC 集合） |
| FX-06 | fx_tenant_leak | 两个 Workspace 各 50 篇，同名文件、相似内容；请求体与工具参数均注入他人 workspace_id | 跨租户引用 0；读取他人行数 0；HTTP 统一 404；LLM 输出被服务端覆盖后仍为 0；run 状态无 SECURITY_ABORT 以外的异常 | report.json、DB 计数 | AC-56、AC-28 |
| FX-07 | fx_topic_merge | 三日错误清单：同一 canonical_key 出现 2 次、冲突字段 1 处、人工编辑段落 1 段 | 无重复条目；冲突显式标记（不静默择一）；重跑 v(n+1) 后 occurrence 不重复累加（计数等于实际出现次数）；标记外人工文本保留；标记丢失时转 manual 不覆盖 | report.json、三文件 diff | AC-40、AC-41 |
| FX-08 | fx_pr_delivery | 已配置 GitHub 测试仓；daily 与 weekly 各 1 份 | 分支名 `review/daily/2026-10-27`；commit trailer 含 Run-Id/Review-Id/Version/Workspace；PR 标题与正文六段齐；正文首行无 degraded 时省略标记；同 period 重跑复用同一 open PR（PR 数不增）；无 force push；weekly 不自动合并 | report.json、PR 元数据 JSON | AC-39、AC-44 |
| FX-09 | fx_restart | 运行中 kill worker、重启全部容器、断 Redis 30s | run 5 分钟内续跑或转 RETRY_WAIT；无重复产物；租约 sweeper 回收僵尸 run 并记 retry_from_state；dispatcher 启动补算 next_run_at；事件流 epoch 变化后客户端被要求重建 | report.json、run 状态序列 | AC-59、AC-43 |
| FX-10 | fx_cost_budget | 构造超预算 run：daily 输入达 100k、月累计达 5M | 80% 时产生告警事件且继续；100% 时终止为 FAILED/NEEDS_HUMAN；跨重试与修订累计（不重置）；超限后不再发起 LLM 调用（计数 0 增量）；不输出“数据版复盘” | report.json、usage_events、告警事件 | AC-66 |
| FX-11 | fx_embedding_migration | 1024 维 active profile；换 768 维 | 新表与新索引创建；re-embed 覆盖率 100% 前不切换；切换原子；切换后旧 profile 可回滚且查询可用；无 ALTER COLUMN TYPE；旧表延迟删除且删除前有快照 | report.json、profile 状态序列 | AC-50 |
| FX-12 | fx_concurrent_runs | 同一 (workspace, daily, 2026-10-27) 并发 10 次触发；另一 workspace 同时 10 次 | 每 workspace 恰好 1 个活跃 run，其余 409 并返回同一 run_id；跨 workspace 互不阻塞；产物文件每 workspace 1 份；无死锁（10 分钟内全部结束） | report.json、run 行计数 | AC-44、AC-42 |
| FX-13 | fx_search_p95 | 10k 文档 / 80k chunk；50 条查询（中英混合、含过滤器） | P95 <1000ms；P99 <1500ms；embedding 超 250ms 时降级 lexical-only 且标 degraded=vector_timeout；分阶段埋点齐全；连续 3 次 top10 完全一致 | report.json、直方图、埋点表 | AC-21、AC-58 |
| FX-14 | fx_import_200mb | 200MB ZIP，含 5k md、图片、2 个超大单文件、1 个压缩炸弹样本 | 正常包导入 <600s；解压 >2GB 拒绝；单文件 >20MB 拒绝；压缩比 >100:1 拒绝；路径穿越与符号链接拒绝；失败不产生半成品 documents 行 | report.json、job 错误码 | AC-09、AC-58 |

### 4.3 fixture 与 Phase 的绑定

| Phase | 必须全绿的 fixture |
|---|---|
| 0 | FX-06 |
| 1 | FX-13、FX-14 |
| 2 | FX-05、FX-06 |
| 3 | FX-01、FX-02、FX-03、FX-04、FX-10、FX-12 |
| 4 | FX-07、FX-09；FX-08 仅在配置了 Git 连接时必跑（否则以对象存储交付 FX-09 替代） |
| 5 | FX-11（蓝绿换维）+ 全量重跑（回归） |

---

## 5. Phase 0–5 退出标准

规则：**阶段门禁是硬门**。任一 P0 AC 未 accepted，或任一 SEC 未 PASS，不得进入下一阶段；例外只能按 §5.1 的书面风险接受流程处理，且不得用于 SEC。

| Phase | 必须通过的 AC | 必须通过的 SEC | 性能/质量门禁 | 退出判定证据 |
|---|---|---|---|---|
| Phase 0 | AC-01、AC-02、AC-03、AC-04、AC-05、AC-06、AC-56、AC-65 | SEC-01、SEC-02、SEC-03、SEC-05、SEC-06、SEC-07、SEC-08、SEC-18、SEC-19、SEC-24、SEC-25、SEC-30 | 跨租户测试矩阵 100% PASS；session→RunContext→RLS 集成测试 PASS；`docker compose up -d` 10 分钟 healthy | 阶段评审纪要 + 全量 report.json |
| Phase 1 | AC-08…AC-21、AC-58 | Phase 0 + SEC-11、SEC-12、SEC-13、SEC-14、SEC-15 | FX-13 P95<1s；FX-14 200MB<10min；忽略规则 5/5；增量索引 embedding 增量为 0 | 性能报告 + 导入报告 |
| Phase 2 | AC-22…AC-30 | Phase 1 + SEC-09、SEC-10、SEC-20、SEC-21、SEC-22、SEC-26 | mock provider 首 token P95<5s（门禁）；真实 provider P50<3s 仅趋势；FX-05 指令遵循 0；FX-06 跨租户 0 | 对话录屏 + 事件流导出 |
| Phase 3 | AC-25（create_review 确认协议部分）、AC-31…AC-38、AC-41、AC-42、AC-60、AC-66、AC-68 | Phase 2 + SEC-16、SEC-17、SEC-23 | FX-01/02/03/04/10/12 全绿；引用覆盖率 100%；每阶段抽样 20 条人工复核，事实一致率 ≥98%（抽样不合格扩大样本）；章节完整率 100%；重跑 diff ≤10% | fixtures report + 质量看板快照 + 抽样报告 |
| Phase 4 | AC-39、AC-40、AC-43…AC-48、AC-51、AC-52、AC-67 | Phase 3 + SEC-27 | FX-07/09 全绿；FX-08 在配置 Git 时全绿；同日重跑 1 文件 version+1；专题不重复累加；定时按 workspace 时区触发误差 <60s | PR 清单 + 调度日志 |
| Phase 5 | AC-07、AC-49、AC-50、AC-53、AC-54、AC-55、AC-57、AC-59、AC-61…AC-64 | SEC-01…SEC-30 全集 | FX-11 蓝绿换维全绿；审计五类全覆盖；导出 ZIP 可下载且条目数对账 0 差；删除冷却可撤销、purge 全链路 0 命中；恢复演练 RTO ≤4h、RPO ≤24h | 安全评审报告 + 恢复演练记录 + 签收清单 |

### 5.1 阶段门禁判定表

补充：AC-57（NFR-2）与 SEC 全集为**连续门禁**，从 Phase 0 起每阶段都要复跑，不因首次通过而移出；上表仅在 Phase 5 列出其“全量收口”位置。

| 条件 | 判定 |
|---|---|
| 全部 P0 AC 为 accepted 且证据 ≤7 天 | 允许进入下一阶段 |
| 存在 P0 AC 为 blocked | 不允许；需登记阻塞项与恢复日期 |
| 存在 P1 AC 未 accepted | 需产品与架构双签“可延后”，写入本节下方风险接受记录，并注明补偿控制与复核日期 |
| 存在 P2 AC 未 accepted | 允许进入，但必须带入下一阶段 backlog 并注明 |
| 任一 SEC 为 FAIL | 一律不允许；SEC 不接受延后，不接受风险接受 |
| 性能门禁超标 >10% | 不允许；超标 ≤10% 需架构师书面接受并给出修复期限 |

### 5.2 风险接受记录模板

| 字段 | 要求 |
|---|---|
| 被接受的 AC | 必须为 P1/P2；P0 与 SEC 不可填 |
| 接受人 | 产品负责人 + 架构负责人双签 |
| 理由与影响 | 说明为什么可以延后、影响哪个用户故事 |
| 补偿控制 | 至少 1 条临时监控或限制 |
| 复核日期 | 不超过 30 天，到期未修则升级为阻断 |
| 审计落点 | 写入 `DECISIONS.md` 与本文件附录，不接受口头或聊天记录 |

---

## 6. 非功能验收细则

### 6.1 性能

| 指标 | 口径 | 阈值 | 测量方法 | 证据 |
|---|---|---|---|---|
| 搜索 P95 | 10k 文档 / 80k chunk；50 条查询；排除冷启动首次；含鉴权与序列化 | <1000ms | FX-13 脚本，20 次以上采样 | 直方图 + 分阶段埋点 |
| 搜索 P99 | 同上 | <1500ms | 同上 | 同上 |
| 200MB 导入 | 从 202 响应到 job succeeded；不含 LLM | <600s | FX-14 墙钟 | 开始/结束时间戳 |
| 首 token | 从 POST turn 到首个 SSE token | 门禁：mock provider P95 <5s；真实 provider P50 <3s 仅趋势（SLO 非硬保证，按 ARCHITECTURE §4.6） | mock 直方图 + 真实趋势图 | 时延直方图 |
| 单 turn 总墙钟 | 含工具循环 | ≤60s；流式上限 120s | 事件流时间差 | run_events 时间戳 |
| 复盘 run 总时长 | daily / weekly / yearly | daily ≤10min、weekly ≤30min、yearly ≤90min | run 开始到 SUCCEEDED | run 时间戳 |

**判定规则**：任一指标超标即 FAIL；不得用“平均值”替代 P95；采样不足 20 次的结果无效。

### 6.2 可靠性

| 项 | 判定条件 | 证据 |
|---|---|---|
| 重启恢复 | kill 全部容器后 5 分钟内服务 healthy；未完成 run 续跑或转 RETRY_WAIT；无重复产物 | FX-09 |
| 任务续跑 | worker 被杀后任务从 `retry_from_state` 重入，不重放历史状态；attempt 计数正确 | FX-09、run 状态序列 |
| 幂等 | 同 period 重跑文件数不增、version+1；同 client_turn_id 重发不产生第 2 条回答 | AC-44、AC-23 |
| 降级可见 | 任何降级都有 `degraded` 事件 + UI 横幅 + PR 首行标记 + usage_events.degraded=true | FX-13、FX-01 |
| 队列隔离 | ingest 队列阻塞不影响 review 队列（反之亦然） | 故障注入脚本 |

### 6.3 可观测性

| 项 | 判定条件 |
|---|---|
| 日志字段 | 每行含 ts/level/service/trace_id/workspace_id/run_id/event；正文与密钥出现次数 0 |
| 健康检查 | `/healthz` 只反映进程存活；`/readyz` 反映 DB/Redis/MinIO 就绪；依赖断开时 readyz 非 200、healthz 仍 200 |
| run 可回放 | 用 run_events 从 CREATED 重放到终态，状态序列与 DB 100% 一致；回放不产生副作用 |
| 指标 | review_job_total、git_pr_duration_seconds、queue_depth、search_latency_p95、llm_tokens_total 五项存在且有数据 |
| 事件保留 | 已完成 run 事件保留 30 天；超期归档到对象存储；cursor 早于窗口时明确报“事件已丢失”，不静默续接 |

### 6.4 备份与恢复

| 项 | 判定条件 |
|---|---|
| 每日备份 | pg_dump 每日 1 份，校验和可读；MinIO 对象数与源一致 |
| 保留策略 | 7 天档与 30 天档各可列出；删除任务按策略执行且不误删 30 天档 |
| 恢复演练 | 发版前 1 次全链路 + 此后每季度 1 次；恢复到新库后 documents/reviews 计数与备份时点相等；抽样 10 篇 content_hash 一致；RTO ≤4h、RPO ≤24h |
| Git 第二副本 | 已交付复盘在 Git 仓库可读；Git 不可用不影响备份可用性判定 |

### 6.5 可访问性与多端

| 项 | 判定条件 |
|---|---|
| WCAG 2.1 AA | axe 扫描 0 个 critical/serious；键盘可完成搜索、打开文档、发消息；对比度 ≥4.5:1（正文）；焦点可见 |
| 响应式 | 1440/768/390 三宽度无横向滚动；触控目标 ≥44px；表格在窄屏可横向滚动而非破版 |
| PWA | 可安装；manifest 字段齐全；断网显示应用壳与离线提示，不白屏；不承诺离线生成/检索 |
| 国际化 | 组件内硬编码中文 0（白名单仅 fixture）；文案键集中；默认 zh-CN；切换 locale 至少 3 页变化 |

---

## 7. 安全验收引用

本文件**不定义安全标准**，只规定“哪些 SEC 必须在哪个阶段通过”。SEC 的判定条件、攻击用例与证据形态以 `SECURITY-BOUNDARY.md` 为唯一权威。

### 7.1 必须通过的集合

| 阶段 | 必须 PASS 的 SEC 集合 | 阻断规则 |
|---|---|---|
| Phase 0 | SEC-01、SEC-02、SEC-03、SEC-05、SEC-06、SEC-07、SEC-08、SEC-18、SEC-19、SEC-24、SEC-25、SEC-30 | 任一 FAIL 不得进入 Phase 1 |
| Phase 1 | Phase 0 + SEC-11、SEC-12、SEC-13、SEC-14、SEC-15 | 任一 FAIL 不得进入 Phase 2 |
| Phase 2 | Phase 1 + SEC-09、SEC-10、SEC-20、SEC-21、SEC-22、SEC-26 | 任一 FAIL 不得进入 Phase 3 |
| Phase 3 | Phase 2 + SEC-16、SEC-17、SEC-23 | 任一 FAIL 不得进入 Phase 4 |
| Phase 4 | Phase 3 + SEC-27 | 任一 FAIL 不得进入 Phase 5 |
| Phase 5 | SEC-01…SEC-30 全集 + 渗透测试报告 | 任一 FAIL 不得发布 |

### 7.2 不可豁免项

| 项 | 规则 |
|---|---|
| SEC 不得延后 | 与 P1/P2 AC 不同，SEC 无“可延后”路径 |
| 不可人工放行的安全事件 | `TENANT_LEAK` / `FABRICATION` 命中即 `SECURITY_ABORT`，任何人工 approve 均判 FAIL |
| 例外流程 | 仅允许“书面风险接受 + 补偿控制 + 期限 + 审计记录”，且需安全负责人签字；口头豁免无效 |
| 回归 | 任一 SEC 曾 FAIL，必须新增对应自动化回归用例，并纳入 §11 CI 集 |

### 7.3 编号对齐说明

`SECURITY-BOUNDARY.md` v0.1 已落地，`SEC-01`–`SEC-30` 为实际编号；本节已按该文件的 §11 发布门禁逐项对齐。任何 SEC 编号变更必须同时更新两份文件，并在两份文件的变更记录中各记一行。

---

## 8. 人工验收脚本

通用要求：每脚本在预发布环境执行；执行人记录版本号、时间、账号；证据存 `docs/evidence/manual/<MA>/`；任一预期不满足即 FAIL。

| ID | 场景 | 覆盖 AC |
|---|---|---|
| MA-01 | 新用户邀请注册 | AC-01、AC-02、AC-03、AC-06 |
| MA-02 | 导入 Vault | AC-08、AC-09、AC-13、AC-15 |
| MA-03 | 搜索 | AC-17、AC-18、AC-19、AC-48 |
| MA-04 | 带引用对话 | AC-22、AC-24、AC-26、AC-27 |
| MA-05 | 生成 2026-10-27 日报 | AC-36、AC-41、AC-39 |
| MA-06 | 周报 / 年报 | AC-37、AC-38、AC-47 |
| MA-07 | Git PR 交付 | AC-39、AC-52 |
| MA-08 | 删除冷却 | AC-55、AC-64 |

### MA-01 新用户邀请注册

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 管理员在后台生成邀请码 | 列表出现 1 个未使用码，可复制 | 截图 |
| 2 | 打开注册页，输入邮箱、8 位密码、邀请码 | 注册成功，跳转首页 | 截图 |
| 3 | 首页检查 Workspace 名称 | 显示“我的知识库”，角色 owner | 截图 |
| 4 | 退出后重新登录 | 成功；DevTools 中 Cookie 标 HttpOnly，JS 读不到 token | Cookie 截图 |
| 5 | 用同一邀请码再注册 | 明确提示邀请码已使用，注册失败 | 截图 |
| 6 | 另一浏览器执行“退出所有设备” | 两处会话均失效，需重新登录 | 双浏览器截图 |

### MA-02 导入 Vault

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 上传含 120 篇 md 的 ZIP（≤200MB） | 显示进度，从 0 到 100 | 进度截图 |
| 2 | 查看任务状态 | 成功数 = 120，失败 0，跳过 = 忽略目录内文件数 | 截图 |
| 3 | 打开文档树 | 层级与源目录一致，深度正确 | 截图 |
| 4 | 打开含代码块、表格、图片的笔记 | 三类均正确渲染，图片可显示 | 截图 |
| 5 | 再点“同步” | 任务成功且显示“未变化”计数 120 | 截图 |
| 6 | 修改 1 篇后同步 | 仅 1 篇更新 | 截图 |

### MA-03 搜索

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 搜索“缓存” | 返回相关笔记，含标题、路径、高亮片段 | 截图 |
| 2 | 打开任一结果 | 跳转到正确路径与标题位置 | 截图 |
| 3 | 加目录过滤 | 结果全部在该目录下 | 截图 |
| 4 | 加标签过滤 | 结果全部含该标签 | 截图 |
| 5 | 加日期范围 | 结果 occurred_on 在区间内 | 截图 |
| 6 | 切“复盘”来源 | 结果为复盘内容且带 review_id | 截图 |
| 7 | 搜索不存在词 | 返回空状态，不报错 | 截图 |

### MA-04 带引用对话

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 新建会话，提问“我学过哪些 Redis 内容？” | 首 token 3 秒内出现 | 录屏 |
| 2 | 检查回答正文 | 含 `[1]` 等引用序号 | 截图 |
| 3 | 检查引用列表 | 条数等于最大序号，每条有真实路径与片段 | 截图 |
| 4 | 打开引用路径 | 文件存在且片段可定位 | 截图 |
| 5 | 追问“那缓存穿透呢？” | 回答承接上文，不重复自我介绍 | 截图 |
| 6 | 展开工具轨迹 | 显示工具名、参数摘要、状态、耗时 | 截图 |
| 7 | 新 Workspace 提问事实性问题 | 明确回答“没有找到相关记录”，无引用 | 截图 |

### MA-05 生成 2026-10-27 日报

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 在复盘页选择日期 2026-10-27，手动生成 | 返回 run_id，进度可见 | 截图 |
| 2 | 查看生成结果 | 7 个章节全部存在，标题与顺序固定 | 截图 |
| 3 | 检查“今日待办” | 已完成/未完成/以往顺延三组分别列出 | 截图 |
| 4 | 检查“做错的事” | 每条有日期与来源路径，可点击打开 | 截图 |
| 5 | 检查“明日行动” | 每项为 `- [ ]` 且可执行 | 截图 |
| 6 | 在输出仓库查看文件 | `wiki/复盘/每日复盘/2026/2026-10-27.md` 存在 | 截图 |
| 7 | 再次生成同日 | 文件仍 1 份，版本号 +1，旧版本可查 | 截图 |

### MA-06 周报 / 年报

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 生成 2026-W43 周报 | 4 节齐，完成率与任务面板一致 | 截图 |
| 2 | 对照任务台账复算完成率 | 数值一致 | 截图 + 复算表 |
| 3 | 检查重复错误 | 出现 ≥2 次的条目被标出 | 截图 |
| 4 | 生成 2026 年报 | 5 节齐，Top10 有排序 | 截图 |
| 5 | 打开复盘日历 | 按日/周/年可切换，缺失日期有标识 | 截图 |

### MA-07 Git PR 交付

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 配置测试仓库并测试连接 | 连接成功，提示写权限已校验 | 截图 |
| 2 | 生成一份 daily 复盘 | 产生分支 `review/daily/2026-10-27` 与 PR | 截图 |
| 3 | 检查 PR 标题与正文 | 六段齐全：结论/自检/证据前10/变更文件/风险回滚/GateResult | 截图 |
| 4 | 检查 commit trailer | 含 Run-Id/Review-Id/Version/Workspace | 截图 |
| 5 | 同日重跑 | 复用同一 open PR，PR 数不增 | 截图 |
| 6 | 检查远端历史 | 无 force push 记录 | 截图 |
| 7 | weekly 产物 | 不自动合并，等待人工 | 截图 |

### MA-08 删除冷却

| 步骤 | 操作 | 预期 | 证据 |
|---|---|---|---|
| 1 | 请求导出 Workspace | 生成 ZIP 可下载，含 manifest 与全部数据类别 | 截图 |
| 2 | 请求删除账号 | 弹出二次确认，需输入确认词 | 截图 |
| 3 | 确认后查看状态 | 状态 cooling，显示剩余天数与 purge 日期 | 截图 |
| 4 | 冷却期内撤销 | 状态回退，数据仍完整（抽查 1 篇笔记） | 截图 |
| 5 | 再次请求删除并等待 purge | purge 后登录 404，DB/MinIO/向量/凭据查询 0 命中 | 截图 + 查询输出 |
| 6 | 检查告知文案 | 明确说明“已合并的 Git 历史无法抹除” | 截图 |

---

## 9. 追溯矩阵

### 9.1 需求 → AC → 测试/证据 → Phase

| 需求 | AC | 测试用例 | 证据 | Phase |
|---|---|---|---|---|
| FR-A1 | AC-01 | T-A-01 注册校验、T-A-02 登录限流 | EV-AC-01-01/02 | 0 |
| FR-A2 | AC-02 | T-A-03 邀请码一次性、T-A-04 并发同码 | EV-AC-02-01/02 | 0 |
| FR-A3 | AC-03 | T-A-05 注册建 Workspace | EV-AC-03-01 | 0 |
| FR-A4 | AC-04 | T-A-06 切换隔离、T-A-07 非成员 404 | EV-AC-04-01/02 | 0 |
| FR-A5 | AC-05 | T-A-08 角色枚举 | EV-AC-05-01/02 | 0 |
| FR-A6 | AC-06 | T-A-09 Cookie 属性、T-A-10 退出所有设备 | EV-AC-06-01/02 | 0 |
| FR-A7 | AC-07 | T-A-11 五类审计 | EV-AC-07-01/02 | 0 / 5 |
| FR-B1 | AC-08 | T-B-01 Git 连接、T-B-02 分支切换 | EV-AC-08-01/02 | 1 |
| FR-B2 | AC-09 | T-B-03 ZIP 上限、T-B-04 进度 | EV-AC-09-01/02 | 1 |
| FR-B3 | AC-10 | T-B-05 忽略规则 | EV-AC-10-01 | 1 |
| FR-B4 | AC-11 | T-B-06 解析器 | EV-AC-11-01 | 1 |
| FR-B5 | AC-12 | T-B-07 增量哈希、T-B-08 重命名迁移 | EV-AC-12-01/02 | 1 |
| FR-B6 | AC-13 | T-B-09 树与渲染、T-B-10 XSS | EV-AC-13-01/02 | 1 |
| FR-B7 | AC-14 | T-B-11 元数据完整性 | EV-AC-14-01 | 1 |
| FR-B8 | AC-15 | T-B-12 任务计数与重试 | EV-AC-15-01/02 | 1 |
| FR-C1 | AC-16 | T-C-01 RRF 复算、T-C-02 稳定性 | EV-AC-16-01/02 | 1 |
| FR-C2 | AC-17 | T-C-03 中文分词 | EV-AC-17-01/02 | 1 |
| FR-C3 | AC-18 | T-C-04 高亮与转义 | EV-AC-18-01 | 1 |
| FR-C4 | AC-19 | T-C-05 四过滤器、T-C-06 mtime 扰动 | EV-AC-19-01/02 | 1 |
| FR-C5 | AC-20 | T-C-07 SearchResult schema | EV-AC-20-01 | 1 |
| FR-C6 | AC-21 | T-C-08 规模召回、T-C-09 P95 | EV-AC-21-01 | 1 |
| FR-D1 | AC-22 | T-D-01 SSE 时延、T-D-02 不缓冲 | EV-AC-22-01/02 | 2 |
| FR-D2 | AC-23 | T-D-03 会话 CRUD、T-D-04 多轮上下文 | EV-AC-23-01 | 2 |
| FR-D3 | AC-24 | T-D-05 引用闭包 | EV-AC-24-01/02 | 2 |
| FR-D4 | AC-25 | T-D-06 六工具、T-D-07 create_review 确认 | EV-AC-25-01/02 | 2 / 3 |
| FR-D5 | AC-26 | T-D-08 轨迹展示 | EV-AC-26-01/02 | 2 |
| FR-D6 | AC-27 | T-D-09 无依据哨兵 | EV-AC-27-01/02 | 2 |
| FR-D7 | AC-28 | T-D-10 入参越权、T-D-11 LLM 指定 workspace | EV-AC-28-01/02 | 2 |
| FR-D8 | AC-29 | T-D-12 用量计量 | EV-AC-29-01/02 | 2 |
| FR-D9 | AC-30 | T-D-13 上下文配置 | EV-AC-30-01/02 | 2 |
| FR-E1 | AC-31 | T-E-01 复选框解析、T-E-02 文件名日期 | EV-AC-31-01/02 | 3 |
| FR-E2 | AC-32 | T-E-03 台账字段 | EV-AC-32-01 | 3 |
| FR-E3 | AC-33 | T-E-04 顺延与回填 | EV-AC-33-01/02 | 3 |
| FR-E4 | AC-34 | T-E-05 日记匹配 | EV-AC-34-01 | 3 |
| FR-E5 | AC-35 | T-E-06 学习摘要四要素 | EV-AC-35-01 | 3 |
| FR-E6 | AC-36 | T-E-07 daily 7 章 | EV-AC-36-01/02 | 3 |
| FR-E7 | AC-37 | T-E-08 weekly 聚合与复算 | EV-AC-37-01/02 | 3 |
| FR-E8 | AC-38 | T-E-09 yearly 分层与 Top10 | EV-AC-38-01/02 | 3 |
| FR-E9 | AC-39 | T-E-10 中文目录 | EV-AC-39-01/02 | 4 |
| FR-E10 | AC-40 | T-E-11 三专题重渲染 | EV-AC-40-01/02 | 4 |
| FR-E11 | AC-41 | T-E-12 可追溯与复算 | EV-AC-41-01/02 | 3 |
| FR-E12 | AC-42 | T-E-13 手动触发 | EV-AC-42-01 | 3 |
| FR-E13 | AC-43 | T-E-14 时区/DST/补算 | EV-AC-43-01/02 | 4 |
| FR-E14 | AC-44 | T-E-15 幂等与版本、T-E-16 并发 | EV-AC-44-01/02 | 4 |
| FR-F1 | AC-45 | T-F-01 仪表盘对账 | EV-AC-45-01 | 4 |
| FR-F2 | AC-46 | T-F-02 任务面板天数 | EV-AC-46-01 | 4 |
| FR-F3 | AC-47 | T-F-03 日历 | EV-AC-47-01 | 4 |
| FR-F4 | AC-48 | T-F-04 全局搜索三模式 | EV-AC-48-01 | 4 |
| FR-G1 | AC-49 | T-G-01 凭据加密、T-G-02 连接测试 | EV-AC-49-01/02 | 5 |
| FR-G2 | AC-50 | T-G-03 蓝绿换维与回滚 | EV-AC-50-01/02 | 5 |
| FR-G3 | AC-51 | T-G-04 定时配置 | EV-AC-51-01 | 4 |
| FR-G4 | AC-52 | T-G-05 Git 配置与权限校验 | EV-AC-52-01 | 4 |
| FR-G5 | AC-53 | T-G-06 用量对账 | EV-AC-53-01 | 5 |
| FR-G6 | AC-54 | T-G-07 管理后台、T-G-08 禁用 | EV-AC-54-01/02 | 5 |
| FR-G7 | AC-55 | T-G-09 导出、T-G-10 冷却与撤销、T-G-11 purge | EV-AC-55-01/02 | 5 |
| NFR-1 | AC-56 | T-N-01 RLS 矩阵、T-N-02 越权 404 | EV-AC-56-01/02 | 0 |
| NFR-2 | AC-57 | SEC 全集（见 SECURITY-BOUNDARY） | EV-AC-57-01 | 0 起 |
| NFR-3 | AC-58 | T-N-03 搜索性能、T-N-04 导入性能 | EV-AC-58-01/02 | 1 |
| NFR-4 | AC-59 | T-N-05 kill 续跑、T-N-06 重启恢复 | EV-AC-59-01/02 | 0 / 3 / 4 |
| NFR-5 | AC-60 | T-N-07 日志字段、T-N-08 回放 | EV-AC-60-01/02 | 3 |
| NFR-6 | AC-61 | T-N-09 备份校验、T-N-10 恢复演练 | EV-AC-61-01 | 5 |
| NFR-7 | AC-62 | T-N-11 设备矩阵、T-N-12 PWA | EV-AC-62-01/02 | 5 |
| NFR-8 | AC-63 | T-N-13 文案扫描 | EV-AC-63-01 | 5 |
| NFR-9 | AC-64 | T-N-14 导出覆盖、T-N-15 协议页 | EV-AC-64-01/02 | 5 |
| NFR-10 | AC-65 | T-N-16 一键部署、T-N-17 升级回滚 | EV-AC-65-01/02 | 0 / 5 |
| 决策 5B | AC-66 | FX-10 预算硬停 | EV-AC-66-01/02 | 3 |
| 决策 3B | AC-67 | FX-08 自动合并矩阵 | EV-AC-67-01/02 | 4 |
| 决策 4B | AC-68 | FX-07 专题首见确认 | EV-AC-68-01/02 | 3 |

### 9.2 反向核对

| 检查 | 方法 | 判定 |
|---|---|---|
| 无遗漏 FR | 从 REQUIREMENTS 抽取 55 个 FR ID，与 §9.1 第一列做集合差 | 差集为空 |
| 无孤立 AC | 每个 AC 至少在 §9.1 出现一次 | 0 个孤立 |
| 无孤立 fixture | 每个 FX 至少关联 1 条 AC | 0 个孤立 |
| 无孤立 SEC | SEC 全集均落在某个 Phase 集合 | 0 个未分配 |
| Phase 可退出 | 每 Phase 的 AC/SEC 集合均非空且可执行 | 6/6 成立 |

---

## 10. 签收清单

签收规则：每个角色逐项勾选；任一项未勾选，该角色不得签收；签收记录附版本号与日期。

### 10.1 产品

| # | 签收项 | 判定 |
|---|---|---|
| P1 | AC-01…AC-55 全部 accepted，且 Given/When/Then 无主观词 | 是/否 |
| P2 | 8 个人工脚本（MA-01…MA-08）全部执行通过并有截图 | 是/否 |
| P3 | 验收口径与 REQUIREMENTS §12 九条逐条对应，无新增范围 | 是/否 |
| P4 | §12 开放问题均已拍板或书面接受 | 是/否 |

### 10.2 后端

| # | 签收项 | 判定 |
|---|---|---|
| B1 | AC-31…AC-44、AC-56、AC-59、AC-60 全绿 | 是/否 |
| B2 | FX-01/02/03/04/10/12 全绿；重跑 diff ≤10% | 是/否 |
| B3 | 三把唯一索引存在且并发测试通过（FX-12） | 是/否 |
| B4 | 所有租户表 RLS ENABLE+FORCE，跨租户矩阵 100% | 是/否 |
| B5 | 预算 80%/100% 行为符合 5B（FX-10） | 是/否 |

### 10.3 前端

| # | 签收项 | 判定 |
|---|---|---|
| F1 | AC-13、AC-18、AC-26、AC-45…AC-48 全绿 | 是/否 |
| F2 | AC-62 设备矩阵与 PWA 通过；WCAG AA 扫描 0 critical/serious | 是/否 |
| F3 | AC-63 硬编码文案 0 | 是/否 |
| F4 | 降级横幅、事件丢失提示、冷却倒计时三处 UI 均存在并可截图 | 是/否 |

### 10.4 安全

| # | 签收项 | 判定 |
|---|---|---|
| S1 | SEC 全集 PASS，无 blocked | 是/否 |
| S2 | FX-05、FX-06 全绿 | 是/否 |
| S3 | 渗透测试报告无 high/critical 未修复项 | 是/否 |
| S4 | 凭据加密与日志脱敏扫描 0 命中（AC-49） | 是/否 |
| S5 | 无人工放行 TENANT_LEAK/FABRICATION 的记录 | 是/否 |

### 10.5 运维

| # | 签收项 | 判定 |
|---|---|---|
| O1 | AC-65 一键部署与升级回滚通过 | 是/否 |
| O2 | AC-61 恢复演练通过，RTO ≤4h、RPO ≤24h | 是/否 |
| O3 | AC-59 重启恢复与续跑通过 | 是/否 |
| O4 | 备份 7/30 天两档可查；监控五项指标有数据 | 是/否 |
| O5 | 删除 purge 全链路 0 命中（AC-55） | 是/否 |

---

## 11. 回归策略

### 11.1 分层

| 层 | 触发 | 范围 | 时限 | 失败处置 |
|---|---|---|---|---|
| L1 提交级（CI） | 每次 push / PR | 标注 `ci` 的 AC 自动化用例 + 单元测试 + schema 校验 | ≤10 分钟 | 阻断合并 |
| L2 合并级（CI） | 合入 main | L1 + FX-01/02/04/05/06/12 + 迁移冒烟 | ≤30 分钟 | 阻断发布分支 |
| L3 夜间（nightly） | 每日 02:00 | FX-03/07/09/10/11/13 + 性能基线与 RLS 全矩阵 | ≤2 小时 | 次日 10:00 前建缺陷，P0 阻断发布 |
| L4 发版前（人工） | 每个候选版本 | MA-01…MA-08 + FX-08 + AC-62 + 渗透回归 | 1 个工作日 | 阻断发布 |

### 11.2 AC 的 CI 归属

| 归属 | AC |
|---|---|
| 必须进 CI（L1） | AC-01、AC-02、AC-03、AC-04、AC-06、AC-10、AC-11、AC-12、AC-16、AC-17、AC-20、AC-24、AC-27、AC-28、AC-31、AC-32、AC-36、AC-41、AC-44、AC-56、AC-57（自动化部分） |
| 必须进 nightly（L3） | AC-05、AC-07、AC-08、AC-09、AC-13、AC-14、AC-15、AC-18、AC-19、AC-21、AC-22、AC-23、AC-25、AC-26、AC-29、AC-30、AC-33…AC-35、AC-37…AC-40、AC-42、AC-43、AC-45…AC-50、AC-58、AC-59、AC-60、AC-63、AC-65 |
| 发版前人工（L4） | AC-51、AC-52、AC-53、AC-54、AC-55、AC-61、AC-62、AC-64 + MA-01…MA-08 |

### 11.3 回归纪律

1. **修复必须带回归**：任一线上缺陷修复，必须新增或强化对应 AC 断言；无回归用例的修复不予合并。
2. **fixture 变更需评审**：修改 fixture 必须列出受影响 AC，并在 PR 中给出前后断言差异。
3. **不允许“重跑到绿”**：FLAKY 判 FAIL 并登记，禁止以重跑通过作为证据。
4. **性能基线只升不降**：P95 基线恶化 >10% 即视为回归，需定位或书面接受。
5. **安全用例常驻**：SEC 相关用例全部进 L2/L3，不允许移出 CI 以缩短时长。

---

## 12. 已拍板口径（2026-11-06）

| # | 问题 | 影响 | 拍板结果 | 状态 |
|---|---|---|---|---|
| Q1 | 首 token 的验收口径：mock 还是真实 provider？ | AC-22、Phase 2 退出 | **门禁用 mock（P95<5s）；真实 provider 只报 P50 趋势，不阻断** | 已拍板 |
| Q2 | 日报验收是否必须依赖 Git PR？无 Git 连接能否用对象存储 + 下载？ | AC-39、MA-05、MA-07 | **等效：先验对象存储产物；Git PR 单列 MA-07 验收** | 已拍板 |
| Q3 | 事实一致率 ≥98% 的标注主体与抽样方式？ | AC-36、AC-38、Phase 3 质量门禁 | **引用闭包 100% + 每阶段抽样 20 条人工复核；抽样不合格则扩大样本** | 已拍板 |
| Q4 | 删除后是否正向断言 Git 历史仍可读？ | AC-55、AC-64 | **正向断言“Git 历史保留且可读”，删除页与协议明示；不承诺抹除** | 已拍板 |
| Q5 | 恢复演练频率？ | AC-61、Phase 5 退出 | **发版前必做 1 次全链路恢复；此后每季度 1 次** | 已拍板 |

### 12.1 与 ARCHITECTURE 的冲突登记

| # | 冲突点 | 本文件处理 | 建议 |
|---|---|---|---|
| C1 | REQUIREMENTS §6.3 写 `data/reviews`，ARCHITECTURE §8.1 与决策 1D 定为 `wiki/复盘` | AC-39 以 `wiki/复盘` 为判定路径，`data/reviews` 仅兼容读取 | 需求 v1.1 修正 §6.3（ARCHITECTURE §14 已登记） |
| C2 | FR-E6 列 6 章，REQUIREMENTS §6.2 列 7 章 | AC-36 按 7 章判定（含“一句话总结”） | 需求 v1.1 修正 FR-E6 |
| C3 | 需求未定义“月报”，FR-E8 提及年度汇总周报/月报 | AC-38 不要求月报，缺失周用日报补齐并记 coverage_gaps | 需求 v1.1 明确不做月报 |
| C4 | FR-C6 写“1 万篇 P95<1s”，ARCHITECTURE §2.6 预算按 80k chunk 估算 | AC-58 同时约束文档数与 chunk 数，避免只测文档数 | 无冲突，仅口径补全 |

> 上表 C1–C4 不构成与 ARCHITECTURE 的实质冲突，均为需求文本与架构裁决的已知差异，已在 ARCHITECTURE §14 登记或按决策处理。

---

## 13. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-11-06 | 首版验收基线：68 条 AC（65 需求 + 3 决策）、14 个 fixture、6 阶段退出标准、8 个人工脚本、追溯矩阵与签收清单 |
| v1.1 | 2026-11-06 | 拍板 Q1–Q5：mock 首 token 门禁、日报对象存储等效、事实抽样 20 条、Git 历史正向断言、发版前恢复演练；同步更新 AC-22/36/38/39/55/61/64 与 Phase 门禁 |
