# mylife 目录与文件约束

> 版本：v0.1 ｜ 日期：2026-11-06 ｜ 关联：`ARCHITECTURE.md` v0.4 §2.1/§8/§16.3 ｜ 决策：1D
>
> 本文件是 CI 与代码评审可执行的目录/文件规范。违反本文件的写入应被 Runtime 拒绝，而不是靠人工纪律。

---

## 1. 目的与术语

| 术语 | 定义 |
|---|---|
| `workspace` | 租户边界；所有路径、文件、对象都必须带 `workspace_id` 归属 |
| `source vault` | 用户导入的 Obsidian 仓库或 ZIP，**只读**（除非显式 `vault_writeback`） |
| `managed output repo` | mylife 管理的 Git 输出仓库；默认交付目标（决策 1D） |
| `reviews_root` | 复盘在输出仓库中的根目录；默认 `wiki/复盘` |
| `auto region` | 由 mylife 生成、可被重写的 Markdown 区域，用 `<!-- mylife:auto:start -->` / `<!-- mylife:auto:end -->` 标记 |
| `human region` | auto region 之外、由用户维护的内容，mylife 不覆盖 |
| `artifact` | 渲染产物、导出包、日志附件等非知识正文对象 |

**总原则**：source vault 只读；managed output repo 可写；所有写入经 `packages/knowledge-runtime`；任何路径先校验后写入。

---

## 2. Monorepo 代码目录

```text
mylife/
├─ apps/
│  ├─ web/                    # Next.js 15 App Router + TS + Tailwind + shadcn/ui
│  ├─ api/                    # FastAPI：路由域 + service 层 + RLS 注入
│  ├─ worker/                 # Celery：ingest/embed/review/delivery 队列
│  └─ dispatcher/             # Celery Beat + 到期扫描 + outbox 重投
├─ packages/
│  ├─ contracts/              # Pydantic v2 契约 + 生成的 TS 类型（单一事实源）
│  ├─ agent/                  # Planner/Worker/Writer/Reviewer + FSM
│  ├─ knowledge-runtime/      # 路径校验、锁、原子写、来源注册、写入模式
│  ├─ retrieval/              # 分词、RRF、混合检索、snippet
│  └─ delivery/               # 渲染、Git 分支/commit/PR、webhook 回写
├─ infra/
│  ├─ compose/                # docker-compose.yml / override / env 示例
│  ├─ caddy/                  # Caddyfile、TLS、SSE flush
│  ├─ postgres/               # init、RLS 迁移、备份脚本
│  └─ minio/                  # bucket 策略、生命周期
├─ docs/                      # 需求、架构、决策、规范
├─ tests/
│  ├─ fixtures/               # golden fixtures（fx_*）
│  ├─ contract/               # OpenAPI/SSE/工具契约测试
│  └─ e2e/                    # Playwright 端到端
└─ scripts/                   # 运维/迁移/校验脚本（用户不直接使用）
```

### 2.1 依赖方向（禁止反向）

```text
apps/web ──> packages/contracts
apps/api ──> packages/contracts, packages/agent, packages/retrieval, packages/knowledge-runtime, packages/delivery
apps/worker ──> packages/agent, packages/retrieval, packages/knowledge-runtime, packages/delivery
apps/dispatcher ──> packages/contracts
packages/agent ──> packages/contracts, packages/knowledge-runtime
packages/delivery ──> packages/contracts, packages/knowledge-runtime
packages/retrieval ──> packages/contracts
packages/contracts ──> 无（只依赖 Pydantic）
```

**硬规则**：

- `apps/web` 不得 import `apps/api`、`apps/worker` 或任何数据库/文件系统包；只能通过 HTTP/SSE 与后端交互。
- `packages/agent` 不得直接 `open()`/`write()` 文件；所有写入必须调用 `packages/knowledge-runtime`。
- `packages/contracts` 不得依赖任何 app 或运行时包，避免循环依赖。
- `packages/delivery` 不得包含业务语义（不判断“这是不是错误”），只做渲染与 Git 操作。

### 2.2 边界检查

CI 使用 `import-linter`（或 `eslint-plugin-boundaries`）执行上述方向；违反即失败。

---

## 3. 运行时数据目录

数据根由 `MYLIFE_DATA_ROOT` 指定；容器内固定 `/srv/mylife/data`，挂载持久卷。

```text
/srv/mylife/data/
├─ workspaces/
│  └─ {workspace_id}/
│     ├─ snapshots/           # 不可变快照：commit SHA + 文档哈希清单
│     ├─ artifacts/           # 渲染中间产物、diff、预览
│     ├─ exports/             # 导出 ZIP（签名 URL 短期有效）
│     ├─ tmp/                 # 原子写临时文件（同分区）
│     └─ locks/               # scope lock（也可用 Redis；文件锁仅本地兜底）
├─ backups/                   # pg_dump / MinIO 备份（跨机副本）
└─ logs/                      # 仅本地调试；生产日志走 stdout
```

| 目录 | 写入者 | 保留期 | 权限 |
|---|---|---|---|
| `snapshots/` | worker-ingest | 与 review/证据同生命周期；删除 Workspace 时清除 | 0750 / 0640 |
| `artifacts/` | worker-review、delivery | 默认 30 天，可配置 | 0750 / 0640 |
| `exports/` | api/worker | 7 天；签名 URL TTL 15 分钟 | 0750 / 0640 |
| `tmp/` | 所有 worker | 进程退出/启动清理；超过 24h 清理 | 0700 |
| `locks/` | worker | TTL 10 分钟，自动过期 | 0700 |
| `backups/` | 运维 | PostgreSQL 7/30 天；MinIO 30 天 | 0700 |

**禁止**：在 `snapshots/` 内原地修改；在 `tmp/` 外写临时文件；把日志写入 `workspaces/`。

---

## 4. Git 交付仓库目录

默认 `reviews_root = wiki/复盘`，每 Workspace 可配置（决策 1D）。

```text
{reviews_root}/
├─ 每日复盘/
│  └─ 2026/
│     └─ 2026-10-27.md
├─ 每月复盘/
│  └─ 2026/
│     └─ 2026-10.md
├─ 年度复盘/
│  └─ 2026.md
├─ 专题/
│  ├─ 错误清单.md
│  ├─ 正确方法论.md
│  └─ 行动清单.md
└─ 索引.md
```

### 4.1 路径语法

| 规则 | 约束 |
|---|---|
| 编码 | UTF-8；路径做 NFC 规范化后比较 |
| 分隔符 | 只允许 `/`；禁止 `\`、`//`、尾部 `/` |
| 禁止段 | `.`、`..`、空段、以 `.` 开头的段（除显式允许的 `.mylife` 元数据） |
| 禁止 | 绝对路径、盘符、UNC、`file://`、符号链接、硬链接、设备文件 |
| 最大深度 | 从 `reviews_root` 起 ≤ 8 层 |
| 最大长度 | 单段 ≤ 120 字符；完整路径 ≤ 240 字符 |
| 允许字符 | 中文、字母、数字、空格、`- _ . ( ) （ ）`；禁止尖括号、冒号、引号、问号、星号、竖线、控制字符 |
| 大小写 | 保留原样；冲突检测按 NFC + casefold 比较，防止 macOS/Windows 大小写冲突 |
| 生成文件 | 只能落在 `每日复盘/`、`每月复盘/`、`年度复盘/`、`专题/`、`索引.md` 白名单内 |

### 4.2 文件命名

| 产物 | 文件名 | 示例 |
|---|---|---|
| 日报 | `YYYY-MM-DD.md` | `2026-10-27.md` |
| 月报 | `YYYY-MM.md`（自然月） | `2026-10.md` |
| 年报 | `YYYY.md` | `2026.md` |
| 专题 | 固定名 | `错误清单.md` |
| 索引 | `索引.md` | — |

### 4.3 Frontmatter（生成文件必须）

```yaml
---
mylife: true
kind: daily            # daily | monthly | yearly
period_key: "2026-10-27"
review_id: "uuid"
version: 2
snapshot_id: "uuid"
generated_at: "2026-11-06T01:00:00Z"
generator: "mylife/0.4"
---
```

规则：字段名固定；`mylife: true` 用于识别生成物；缺失或无法解析 → 不覆盖，转 manual。

### 4.4 自动区域标记

```markdown
<!-- mylife:auto:start -->
（生成内容）
<!-- mylife:auto:end -->
```

| 情况 | 处理 |
|---|---|
| 标记完整 | 只重写标记内；标记外保留 |
| 标记缺失 | 不覆盖；转 `manual`；记 `GIT_MARKER_BROKEN` |
| 标记嵌套/重复 | 不覆盖；转 `manual`；告警 |
| 标记内被人工修改 | 检测 `content_hash` 不一致 → 不覆盖；转 `manual` |
| 专题页 | 全文件由 DB 重渲染；标记外内容保留 |

---

## 5. 文件格式与大小

| 项 | 约束 |
|---|---|
| 编码 | UTF-8，无 BOM |
| 换行 | LF |
| 单篇复盘 | ≤ 2 MB；超过 → 拆分或失败 |
| frontmatter | ≤ 64 KB |
| 图片 | 单张 ≤ 20 MB；仅 `png/jpg/jpeg/gif/webp/svg`；SVG 渲染前 sanitize |
| Markdown | 禁止 `<script>`、`<iframe>`、`on*` 事件属性；渲染时 sanitize + CSP |
| 代码块 | 保留语言标签；不执行 |
| 链接 | `[[wikilink]]` 保留；外链图片走代理，不直接加载 |

---

## 6. 导入约束（source vault / ZIP）

### 6.1 忽略规则

```text
.obsidian/
.trash/
.stfolder/
.git/
node_modules/
.DS_Store
Thumbs.db
*.tmp
*.swp
```

可配置额外 ignore；默认规则不可删除，只能追加。

### 6.2 允许扩展名

- 正文：`.md`（唯一进入知识库的正文类型）
- 图片：`.png .jpg .jpeg .gif .webp .svg`（仅预览/引用，不做 OCR）
- 其他：首版忽略并计入 `skipped`（PDF/Office 深度解析属非目标）

### 6.3 上传限制

| 项 | 上限 |
|---|---|
| ZIP 压缩包 | 200 MB |
| 解压后总大小 | 2 GB |
| 单文件 | 20 MB |
| 文件数 | 50,000 |
| 压缩比 | > 100:1 拒绝（ZIP 炸弹） |
| 路径穿越 | 拒绝任何解压后越出目标目录的条目 |
| 符号链接 | 拒绝；不跟随 |
| 嵌套 ZIP | 不递归解压 |

### 6.4 编码异常

- 非 UTF-8：尝试 `utf-8-sig` → `gb18030`；仍失败则记 `ENCODING_ERROR`，跳过该文件，不阻断整批。
- BOM：读取时剥离；写回统一无 BOM。
- 非法字符：替换为 `U+FFFD` 并记 warning。

---

## 7. 写入约束

### 7.1 原子写

1. 在目标目录同分区的 `tmp/` 创建临时文件；
2. 写入并 `fsync`；
3. `rename` 到目标路径（同分区原子）；
4. 更新来源注册/索引（同一事务或 outbox）。

禁止：直接 `open(target, 'w')`；跨分区 rename；写一半后失败留下半文件。

### 7.2 锁

- 粒度：`workspace_id + kind + period_key`（复盘）或 `workspace_id + document_id`（导入）。
- 实现：Redis `SET NX PX` + fencing token；本地文件锁仅兜底。
- TTL：10 分钟；持有者定期续租；过期由 sweeper 回收。
- 冲突：返回 `409 ACTIVE_RUN_EXISTS` + 现有 `run_id`。

### 7.3 写入模式

| 模式 | 适用 | 语义 |
|---|---|---|
| `create_only` | `source_snapshots`、首次生成 | 已存在则失败/跳过，不覆盖 |
| `update_allowed` | 日/月/年复盘 | 同 period 覆盖同一路径；version 递增 |
| `append_only` | `run_events`、`delivery_events`、`audit_logs` | 只追加，不修改/删除 |

### 7.4 路径白名单

写入前必须同时满足：
1. 路径在 `reviews_root` 或 `artifacts_root` 下；
2. 规范化后不以 `..` 越界；
3. 目标文件在 snapshot manifest 或交付白名单内；
4. 不含符号链接；
5. 大小/类型符合 §5。

任一不满足 → `PATH_REJECTED`，写审计，不重试。

### 7.5 幂等

- 内容哈希：`sha256(parser_version + normalized_body + canonical_frontmatter)`；路径不参与哈希。
- 复盘：同 `(workspace_id, kind, period_key)` 覆盖同一路径；`content_hash` 未变 → `delivery=noop`。
- 导入：同 `(workspace_id, document_id, content_hash)` 跳过重复解析与 embedding。
- 专题：同 `(workspace_id, topic, canonical_key)` 去重；occurrence 按日期/路径/行号唯一。

---

## 8. 生成物与人工内容边界

| 路径/区域 | 归属 | 规则 |
|---|---|---|
| `每日复盘/`、`每月复盘/`、`年度复盘/` | mylife | 整个文件为生成物；auto 标记内可重写，标记外保留 |
| `专题/` | mylife + 用户 | DB 全量重渲染；auto 标记外人工内容保留 |
| `索引.md` | mylife | 生成；不手改 |
| source vault 其他文件 | 用户 | 只读；不写回（除非 `vault_writeback` opt-in） |
| `documents.source_type=generated` | mylife | 排除普通证据池、Todo 抽取；不参与 PRIOR 原始证据 |

**冲突处理**：检测到人工修改、标记破坏、路径冲突 → `manual` 状态；不覆盖、不删除；在 UI/PR 中标 `conflict`。

---

## 9. 交付物、快照、导出、备份

| 对象 | 路径 | 保留期 | 删除策略 |
|---|---|---|---|
| review 版本 | DB `review_versions` + Git 提交 | 长期 | Workspace 删除时按 7A 冷却后清除 DB/MinIO/向量/凭据；**Git 历史保留且可读**（补充决策 11），不承诺抹除 |
| 证据快照 | `snapshots/{workspace_id}/{snapshot_id}/` | 与 review 同生命周期 | 级联删除 |
| 渲染产物 | `artifacts/{workspace_id}/{run_id}/` | 30 天 | 定时清理 |
| 导出 ZIP | `exports/{workspace_id}/{timestamp}.zip` | 7 天 | 过期删除；签名 URL TTL 15 分钟 |
| PostgreSQL 备份 | `backups/pg/` | 7/30 天 | 轮转 |
| MinIO 备份 | `backups/minio/` | 30 天 | 轮转 |

---

## 10. `wiki/复盘` 与 `data/reviews` 兼容

- **写入**：只写 `wiki/复盘`（决策 1D）。
- **读取**：`data/reviews/**` 作为只读兼容源；一次性迁移到 `wiki/复盘`，迁移后标记 `legacy=true`，不再双写。
- **索引**：两个根都建索引；搜索结果标注 `source_root`（`wiki` / `data`）。
- **禁止**：同一 review 同时写两个根；迁移期间并发写入。

---

## 11. 路径安全与并发测试清单

| # | 用例 | 预期 |
|---|---|---|
| T01 | `../etc/passwd` | `PATH_REJECTED` |
| T02 | `/etc/passwd` | `PATH_REJECTED` |
| T03 | `wiki/复盘/../../secret` | 规范化后越界，拒绝 |
| T04 | `C:\Windows\...` | 拒绝 |
| T05 | `file:///etc/passwd` | 拒绝 |
| T06 | 符号链接指向外部 | 拒绝，不跟随 |
| T07 | 硬链接 | 拒绝 |
| T08 | NFD/NFC 同名冲突 | 归一化后判冲突，拒绝覆盖 |
| T09 | 大小写不同但 casefold 相同 | 判冲突，拒绝 |
| T10 | 路径长度 241 字符 | 拒绝 |
| T11 | 单段 121 字符 | 拒绝 |
| T12 | 含竖线、问号、星号等非法字符 | 拒绝 |
| T13 | ZIP 含 `../../evil` | 拒绝整个条目，记审计 |
| T14 | ZIP 压缩比 200:1 | 拒绝 |
| T15 | 两个 worker 同时写同一 period | 一个成功，另一个 409 + 现有 run_id |
| T16 | 写入中进程崩溃 | 目标文件不出现半成品；临时文件可清理 |
| T17 | 同内容重跑 | `content_hash` 相同 → noop，不产生新文件 |
| T18 | 人工修改 auto 标记内 | 检测 hash 不一致 → manual，不覆盖 |
| T19 | 标记缺失/嵌套 | manual + `GIT_MARKER_BROKEN` |
| T20 | `data/reviews` 迁移后仍有写入 | 拒绝双写，告警 |

---

## 12. 速查表

| 目录/文件 | 内容 | 写入者 | 保留期 | 权限 |
|---|---|---|---|---|
| `apps/*` | 代码 | 开发 | 版本库 | 0644 |
| `packages/knowledge-runtime` | 路径/锁/原子写 | 开发 | 版本库 | 0644 |
| `/srv/mylife/data/workspaces/{id}/snapshots` | 证据快照 | ingest worker | review 生命周期 | 0750/0640 |
| `/srv/mylife/data/workspaces/{id}/artifacts` | 渲染产物 | review/delivery | 30 天 | 0750/0640 |
| `/srv/mylife/data/workspaces/{id}/exports` | 导出 ZIP | api/worker | 7 天 | 0750/0640 |
| `/srv/mylife/data/tmp` | 原子写临时 | worker | 24h | 0700 |
| `{reviews_root}/每日复盘/YYYY/` | 日报 | delivery | 长期 | Git |
| `{reviews_root}/专题/` | 三个专题 | delivery | 长期 | Git |
| `backups/pg` | PostgreSQL 备份 | 运维 | 7/30 天 | 0700 |
| `backups/minio` | MinIO 备份 | 运维 | 30 天 | 0700 |
