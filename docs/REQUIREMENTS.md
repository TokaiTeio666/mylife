# mylife 全栈 Agent 个人知识库 — 需求规格说明

- 文档版本：v1.0（需求基线）
- 状态：已冻结（2026-11-06）
- 产品代号：mylife
- 目标形态：多用户 SaaS，首版按邀请制、单用户 Workspace 运行
- 技术栈：Next.js 15 + FastAPI + PostgreSQL/pgvector + Redis/Celery + MinIO/S3 + Caddy
- 部署：Linux 服务器 + Docker Compose

> 本文件是后续设计、开发和验收的需求基线。任何范围变更都要更新版本号并记录变更原因。

---

## 1. 一句话定义

mylife 是一个多用户、多端访问的个人知识库 SaaS：用户把 Obsidian 笔记、Todo 和日记接入后，系统自动构建可检索的知识库，提供带引用的 AI 对话，并生成每日、每月、年度复盘，沉淀错误清单、正确方法论和行动清单。

---

## 2. 产品目标与非目标

### 2.1 目标

1. 让用户可以直接“问自己的笔记”，回答必须引用具体笔记路径。
2. 自动识别 Todo 的完成与未完成状态，生成可执行的复盘和行动清单。
3. 把日记中的事件、错误、方法论沉淀成长期可复用的知识。
4. 多设备通过浏览器访问，不依赖 Windows 任务计划。
5. 多租户隔离，具备后续扩展团队协作和收费的基础。

### 2.2 首版非目标

- 团队协作、成员邀请、角色权限管理（仅保留数据模型）
- 订阅收费、支付、发票
- 公开注册（首版仅邀请码注册）
- 原生 iOS/Android App（使用响应式 Web/PWA）
- 复杂知识图谱可视化（首版只存链接和基础关联）
- 插件市场、第三方应用商店
- PDF/Office/网页剪报的深度解析（首版以 Markdown 为主）

---

## 3. 用户与租户模型

### 3.1 角色

| 角色 | 说明 |
|---|---|
| 普通用户 | 拥有自己的个人 Workspace，导入笔记、对话、查看复盘 |
| 平台管理员 | 管理用户、Workspace、邀请码、用量和系统配置 |
| 团队成员（后续） | 被邀请进入某个 Workspace，拥有 owner/admin/member 权限 |

### 3.2 租户规则

- 一个 Workspace 是一个租户，所有业务数据必须带 `workspace_id`。
- 首版每个用户注册后自动创建一个个人 Workspace。
- 一个用户可以拥有多个 Workspace（数据模型支持），但首版 UI 只展示个人 Workspace 和“创建 Workspace”入口。
- 所有查询、文件、向量检索、后台任务必须校验 `workspace_id`。
- 数据库使用 PostgreSQL Row-Level Security（RLS）作为最后防线，应用层校验作为第一道防线。

---

## 4. 核心用户故事

### 4.1 笔记与检索

- 作为用户，我可以连接一个 Git 仓库或上传 Obsidian Vault ZIP，系统自动导入 Markdown 笔记。
- 作为用户，我可以在网页上浏览笔记目录、查看 Markdown 渲染结果。
- 作为用户，我可以搜索“缓存穿透”，得到相关笔记、路径和高亮片段。
- 作为用户，我可以按目录、标签、日期筛选搜索结果。

### 4.2 Agent 对话

- 作为用户，我可以问“我最近一次学习 Redis 缓存是什么时候，学了什么？”Agent 会检索笔记并给出带路径引用的回答。
- 作为用户，我可以看到 Agent 调用了哪些检索工具、命中了哪些笔记。
- 作为用户，我可以继续追问，系统保留多轮对话上下文。
- 如果没有找到依据，Agent 必须明确说“没有找到相关记录”，不能编造。

### 4.3 复盘

- 作为用户，我每天/每月/每年会自动得到一份复盘，内容包括学习摘要、日记事件、做错的事、正确方法论和行动清单。
- 作为用户，我可以看到今天完成了哪些 Todo、哪些未完成、哪些是从以前顺延的。
- 作为用户，我可以查看“错误清单”“正确方法论”“行动清单”三个长期专题页。
- 作为用户，我可以手动触发某一天的复盘，也可以让服务器定时生成。

### 4.4 SaaS 与多端

- 作为用户，我可以用邮箱和密码登录，在任何设备的浏览器访问。
- 作为用户，我的数据与其他用户完全隔离。
- 作为管理员，我可以生成邀请码、查看用户和 Workspace 使用量、禁用账号。

---

## 5. 功能需求（MVP Must Have）

### 5.1 账户、认证与租户

| ID | 需求 | 验收标准 |
|---|---|---|
| FR-A1 | 邮箱 + 密码注册/登录 | 密码至少 8 位；使用 Argon2 哈希；登录失败有频率限制 |
| FR-A2 | 邀请码注册 | 管理员生成一次性邀请码；未带邀请码不能注册 |
| FR-A3 | 自动创建个人 Workspace | 注册成功后自动创建，名称默认为“我的知识库” |
| FR-A4 | Workspace 切换 | 顶部可切换；切换后所有数据请求带当前 workspace |
| FR-A5 | 角色 | 首版 owner + platform_admin；数据模型预留 admin/member |
| FR-A6 | JWT / Session | HttpOnly Cookie；支持退出；支持“退出所有设备” |
| FR-A7 | 审计日志 | 记录登录、导入、删除、配置变更、复盘生成 |

### 5.2 笔记导入与文档管理

| ID | 需求 | 验收标准 |
|---|---|---|
| FR-B1 | Git 仓库连接 | 支持 HTTPS/SSH、分支选择、手动同步、定时拉取 |
| FR-B2 | ZIP 上传导入 | 支持 Obsidian Vault ZIP；默认上限 200MB；显示导入进度 |
| FR-B3 | 忽略规则 | 默认忽略 `.obsidian/`、`.trash/`、`.stfolder/`、`.git/`、`node_modules/` |
| FR-B4 | Markdown 解析 | 解析 YAML frontmatter、标题、正文、标签、`[[wikilinks]]` |
| FR-B5 | 增量索引 | 使用内容哈希；未变化文件不重复解析、不重复 embedding |
| FR-B6 | 文档树与预览 | 保留原始目录结构；支持 Markdown 渲染、代码块、表格、图片 |
| FR-B7 | 文档元数据 | 路径、标题、标签、大小、更新时间、内容哈希、所属 Workspace |
| FR-B8 | 导入任务状态 | 显示排队、处理中、成功、失败、跳过数量；失败可重试 |

### 5.3 搜索与检索

| ID | 需求 | 验收标准 |
|---|---|---|
| FR-C1 | 混合检索 | 关键词检索 + 向量检索合并；返回统一排序结果 |
| FR-C2 | 中文分词 | 使用 jieba 分词；中文查询能命中相关笔记 |
| FR-C3 | 关键词高亮 | 结果展示标题、路径、命中片段和高亮 |
| FR-C4 | 过滤器 | 支持目录、标签、日期范围、文件类型过滤 |
| FR-C5 | 引用信息 | 每个结果返回 `path`、`title`、`heading`、`snippet`、`score` |
| FR-C6 | 性能 | 1 万篇笔记规模下，搜索 P95 < 1 秒（不含 LLM） |

### 5.4 Agent 对话

| ID | 需求 | 验收标准 |
|---|---|---|
| FR-D1 | 流式对话 | 使用 SSE 流式输出，首 token 尽量 < 3 秒（取决于 LLM） |
| FR-D2 | 多轮会话 | 对话持久化；支持新建、重命名、删除会话 |
| FR-D3 | 引用 | 回答下方列出引用笔记路径和片段；正文标注引用序号 |
| FR-D4 | 工具调用 | Agent 可调用 `search_notes`、`read_note`、`list_tasks`、`get_review`、`create_review`、`get_stats` |
| FR-D5 | 工具轨迹 | UI 显示工具名称、参数摘要、状态、耗时 |
| FR-D6 | 无依据回答 | 没有检索证据时明确说明，不得编造事实 |
| FR-D7 | 租户隔离 | 工具调用的 `workspace_id` 必须来自会话，不能由 LLM 指定 |
| FR-D8 | 用量计量 | 记录每次对话的 prompt tokens、completion tokens、模型、耗时 |
| FR-D9 | 上下文控制 | 支持配置检索条数、上下文窗口、是否启用 Web 搜索（后续） |

### 5.5 复盘系统

| ID | 需求 | 验收标准 |
|---|---|---|
| FR-E1 | Todo 解析 | 识别 `- [x]` / `- [ ]`；按文件名和目录解析日期；支持 `10.27.md` 等现有格式 |
| FR-E2 | 任务台账 | 记录首次出现日期、最后出现日期、完成日期、来源文件、行号 |
| FR-E3 | 跨天跟踪 | 未完成事项自动进入“顺延/逾期”列表；后续完成可回填 |
| FR-E4 | 日记解析 | 按日期匹配日记目录中的 Markdown 文件，提取正文 |
| FR-E5 | 学习摘要 | 从 Todo、日记和当天变更的学习笔记中提炼主题、概念、产出、待解决问题 |
| FR-E6 | 每日复盘 | 必须包含：今日待办、学习摘要、日记事件、做错的事、正确方法论、明日行动 |
| FR-E7 | 每月复盘 | 汇总 7 天日报；统计任务完成率、重复错误、方法论落地情况、下月行动 |
| FR-E8 | 年度复盘 | 汇总月报/月报；输出年度学习地图、事件线、错误 Top 10、方法论体系、下一年方向 |
| FR-E9 | 中文目录 | 输出固定存放在 `wiki/复盘/每日复盘`、`每月复盘`、`年度复盘`、`专题` 等中文目录 |
| FR-E10 | 长期专题 | 自动维护 `错误清单.md`、`正确方法论.md`、`行动清单.md` |
| FR-E11 | 可追溯 | 错误与方法论条目必须带日期和来源笔记路径；无证据不写入 |
| FR-E12 | 手动触发 | 支持指定日期/周/年手动生成 |
| FR-E13 | 定时触发 | 支持每个 Workspace 配置时区和生成时间；默认每日 01:00、每月一 01:30、每年 1 月 1 日 02:00 |
| FR-E14 | 幂等 | 同一天重复生成覆盖旧版本，不产生重复文件；记录版本历史 |

### 5.6 仪表盘与任务面板

| ID | 需求 | 验收标准 |
|---|---|---|
| FR-F1 | 首页仪表盘 | 最新日报、月报、年报、任务完成率、未完成事项数量 |
| FR-F2 | 任务面板 | 列出未完成 Todo，显示首次出现日期和已持续天数 |
| FR-F3 | 复盘日历 | 按日期浏览日报；按周/年浏览月报和年报 |
| FR-F4 | 搜索页 | 全局搜索笔记和复盘内容 |

### 5.7 设置与平台管理

| ID | 需求 | 验收标准 |
|---|---|---|
| FR-G1 | LLM 设置 | 支持 DeepSeek/OpenAI 兼容接口；API Key 加密存储；可测试连接 |
| FR-G2 | Embedding 设置 | 支持 OpenAI 兼容 Embedding 接口；可配置模型与维度 |
| FR-G3 | 定时设置 | 可修改每日/每月/年度生成时间和时区 |
| FR-G4 | Git 设置 | 仓库地址、分支、SSH Key/Token、同步频率 |
| FR-G5 | 用量统计 | 按 Workspace 统计 token、调用次数、复盘生成次数 |
| FR-G6 | 管理后台 | 用户列表、Workspace 列表、邀请码、禁用账号、系统配置 |
| FR-G7 | 导出与删除 | 支持导出整个 Workspace 为 ZIP；删除账号/Workspace 有二次确认和冷却期 |

---

## 6. 复盘系统的业务规则

### 6.1 Todo 状态

- **唯一状态来源**：Markdown 复选框。
  - `- [x]` = 已完成
  - `- [ ]` = 未完成
- 不从自然语言语气推测完成状态。
- 任务文本归一化后作为 key；同一文本跨天出现视为同一任务。
- 任务状态：
  - `open`：未完成
  - `done`：已完成
  - `carried`：从之前日期顺延
  - `dropped`：超过配置天数未再出现且未完成（可选）

### 6.2 复盘输出结构

每日复盘必须包含以下章节：

1. 今日待办（已完成 / 未完成 / 以往顺延）
2. 学习内容摘要（主题、概念/方法、产出、待解决问题）
3. 日记事件摘要（事件、人物、决策、情绪、结果）
4. 做错的事（事件、根本原因、代价、正确做法、防止再犯的触发条件）
5. 正确方法论（方法名、来源、适用场景、步骤、边界/反例、落地动作、验证标准）
6. 明日行动（`- [ ]` 可执行项）
7. 一句话总结

月报/年报必须复用每日复盘的字段，只做聚合、对比和趋势分析，不重新定义口径。

### 6.3 中文存储目录

```text
data/reviews/
├─ 每日复盘/
│  └─ 2026/2026-10-27.md
├─ 每月复盘/
│  └─ 2026/2026-10.md
├─ 年度复盘/
│  └─ 2026.md
├─ 专题/
│  ├─ 错误清单.md
│  ├─ 正确方法论.md
│  └─ 行动清单.md
└─ 索引.md
```

### 6.4 生成原则

- 只依据素材，不允许编造。
- 每条错误、方法论必须能追溯到具体日期和来源文件。
- 没有证据时写“无记录”或“本日无明显错误记录”，不得凑内容。
- 学习摘要必须具体，禁止“学习了相关知识”这类空话。
- 行动项必须可执行，尽量带时间、触发条件和验证标准。

---

## 7. 数据模型（核心表）

| 表 | 关键字段 |
|---|---|
| `users` | id, email, password_hash, display_name, status, created_at |
| `workspaces` | id, name, slug, owner_id, plan, created_at |
| `workspace_members` | workspace_id, user_id, role, joined_at |
| `documents` | id, workspace_id, path, title, content, frontmatter_json, content_hash, size, mtime, indexed_at |
| `document_chunks` | id, workspace_id, document_id, heading, content, token_count, embedding vector |
| `document_links` | id, workspace_id, source_document_id, target_path, link_text |
| `tasks` | id, workspace_id, key, text, status, first_seen, last_seen, done_date, source_path, line_number |
| `reviews` | id, workspace_id, kind, period_key, title, content, model, generated_at, version |
| `conversations` | id, workspace_id, user_id, title, created_at, updated_at |
| `messages` | id, conversation_id, role, content, references_json, tool_events_json, created_at |
| `usage_events` | id, workspace_id, user_id, kind, model, prompt_tokens, completion_tokens, cost, created_at |
| `audit_logs` | id, workspace_id, user_id, action, target, detail, ip, created_at |
| `invite_codes` | id, code, created_by, used_by, expires_at, used_at |
| `job_runs` | id, workspace_id, kind, period_key, status, error, started_at, finished_at |

所有业务表必须包含 `workspace_id`，并建立 RLS 策略。

---

## 8. 技术架构

### 8.1 服务

- `web`：Next.js 15 App Router + TypeScript + Tailwind + shadcn/ui + TanStack Query
- `api`：FastAPI + SQLAlchemy + Alembic + Pydantic
- `worker`：Celery + Redis，处理导入、embedding、复盘生成
- `scheduler`：Celery Beat，按 Workspace 时区触发复盘
- `postgres`：PostgreSQL 16 + pgvector
- `redis`：队列与缓存
- `minio`：原始文件/附件对象存储
- `caddy`：HTTPS 反向代理

### 8.2 检索方案

- 文档分块：按 Markdown 标题层级分块，保留标题路径。
- 关键词检索：PostgreSQL `tsvector` + jieba 预分词；中文查询使用相同分词器。
- 向量检索：pgvector + HNSW 索引；embedding 维度按模型配置。
- 混合排序：关键词分数 + 向量相似度 + 标题/路径加权，使用 RRF 或加权融合。
- 检索结果必须带 `document_id`、`path`、`title`、`heading`、`snippet`。

### 8.3 Agent

- 推荐 PydanticAI；如需复杂多步编排再升级 LangGraph。
- 工具调用必须带 workspace 上下文，服务端强制覆盖 `workspace_id`。
- 流式输出使用 SSE。
- 记录完整工具轨迹和引用。

---

## 9. 非功能需求

| ID | 需求 |
|---|---|
| NFR-1 | 多租户隔离：任何跨 Workspace 数据访问都视为严重安全缺陷；必须有 RLS 集成测试 |
| NFR-2 | 安全：HTTPS、Argon2 密码哈希、HttpOnly Cookie、CSRF 防护、登录限流、API Key 加密存储 |
| NFR-3 | 性能：1 万文档搜索 P95 < 1s；导入 200MB Vault < 10 分钟（不含 LLM） |
| NFR-4 | 可靠性：Celery 任务重试、幂等、失败可见；容器 `restart: unless-stopped` |
| NFR-5 | 可观测性：结构化日志、健康检查、任务运行记录；可选 Langfuse 追踪 LLM |
| NFR-6 | 备份：PostgreSQL 每日备份；MinIO 数据备份；保留 7/30 天策略 |
| NFR-7 | 多端：桌面/平板/手机响应式；首屏可用的 PWA |
| NFR-8 | 国际化：首版中文界面，文案集中管理，后续可加英文 |
| NFR-9 | 合规：支持导出全部数据、删除账号；隐私政策与用户协议页面预留 |
| NFR-10 | 可部署：Docker Compose 单机部署；环境变量配置；一键升级 |

---

## 10. 里程碑

| 阶段 | 交付物 |
|---|---|
| M0 | 需求基线（本文件）、架构决策、数据模型 |
| M1 | Monorepo、Docker Compose、Auth、Workspace、基础布局 |
| M2 | Obsidian 导入、Markdown 解析、文档树、混合检索 |
| M3 | Agent 对话、SSE 流式、引用、工具调用、会话历史 |
| M4 | Todo 台账、每日/每月/年度复盘、中文目录、专题页 |
| M5 | 仪表盘、任务面板、设置、用量统计、部署上线 |
| M6 | 管理后台、邀请码、审计日志、备份与监控 |
| M7 | 团队协作、计费、公开注册（后续） |

---

## 11. 已冻结决策

| 编号 | 问题 | 冻结值 | 状态 |
|---|---|---|---|
| Q1 | 首版是否收费 | 免费邀请制，不做支付 | 已冻结 |
| Q2 | 是否支持团队共享 Workspace | 首版不支持，仅个人 Workspace；数据模型预留 | 已冻结 |
| Q3 | 部署区域 | 海外/香港 Linux 服务器，Docker Compose | 已冻结 |
| Q4 | 登录方式 | 邮箱密码 + 邀请码；后续加 OAuth | 已冻结 |
| Q5 | 笔记导入方式 | Git 为主，ZIP 上传为辅 | 已冻结 |
| Q6 | Embedding 服务 | BGE-M3（SiliconFlow API 优先，本地兜底） | 已冻结 |
| Q7 | 产品名称 | mylife（工作名） | 已冻结 |
| Q8 | 域名 | 待提供，部署时填入 | 待提供 |
| Q9 | 删除数据冷却期 | 30 天 | 已冻结 |

---

## 12. 验收基线

首版 MVP 通过条件：

1. 新用户用邀请码注册，自动获得个人 Workspace。
2. 能导入一个包含 100+ 篇 Markdown 的 Obsidian Vault，并在文档树中浏览。
3. 搜索“缓存”能返回相关笔记路径和片段。
4. 在 Chat 中提问“我学过哪些 Redis 内容？”，回答必须带至少 1 条真实笔记引用。
5. 从现有 TODO/日记生成一份 2026-10-27 的每日复盘，包含全部规定章节。
6. 生成一份月报和一份年度复盘，内容来自已有日报/月报。
7. 两个测试账号的数据互相不可见。
8. 服务器重启后容器自动恢复，定时任务继续生效。
9. `docker compose up -d` 一条命令完成部署，Caddy 自动 HTTPS。

---

## 13. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-06 | 需求草案，等待确认 |
| v1.0 | 2026-11-06 | 按默认值冻结：免费邀请制、个人 Workspace、海外/香港服务器、邮箱密码 + 邀请码、Git 为主、BGE-M3、mylife 工作名、30 天删除冷却期 |
