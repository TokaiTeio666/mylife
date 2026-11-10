# mylife

多用户、多端访问的个人知识库 SaaS。当前进度：**MVP Phase 0–5 全量交付签收（M1–M6）**。

> 需求与设计基线见 [`docs/`](./docs/README.md)。实现与文档的偏差、验收证据见
> [`docs/IMPLEMENTATION-NOTES.md`](./docs/IMPLEMENTATION-NOTES.md) 与
> [`docs/PHASE5-STATUS.md`](./docs/PHASE5-STATUS.md)。

## 技术栈

- **web**：Next.js 15 App Router + TypeScript + Tailwind + TanStack Query
- **api**：FastAPI + SQLAlchemy 2（async）+ psycopg 3 + Alembic
- **db**：PostgreSQL 16 + pgvector（RLS ENABLE + FORCE）
- **queue/cache**：Redis（Phase 1 起接入 Celery）
- **object storage**：MinIO（Phase 1 起使用）
- **edge**：Caddy（TLS、SSE 不缓冲）

## 目录

```text
apps/api            FastAPI：路由域 + service + repositories + migrations + dispatcher
apps/web            Next.js：登录/注册/仪表盘/文档/搜索/对话/复盘/专题/设置/管理后台/法务页/PWA
apps/worker         Celery：导入与索引
packages/contracts  单一事实源：Pydantic v2 契约、枚举、错误码、常量
packages/knowledge-runtime 路径白名单、哈希与知识库运行时原语
packages/retrieval  分词、Embedding 提供方与混合检索组件
packages/agent      复盘门禁（G0–G9）、章节装配与提示注入检测
packages/delivery   Git 分支/PR/trailer 交付与专题页确定性合并
infra/compose       docker-compose.yml + 本地 dev override
infra/postgres      角色初始化脚本
infra/caddy         Caddyfile
scripts             边界/i18n 检查、备份恢复演练、容器故障演练与性能基准脚本
docs                需求、架构、决策、规范与各阶段验收证据
```

## 快速开始（本地）

```bash
# 1. 环境变量
cp .env.example .env

# 2. 启动依赖（Windows 上 5432 可能落在保留端口段，dev override 使用 25432）
docker compose -f infra/compose/docker-compose.yml \
               -f infra/compose/docker-compose.dev.yml \
               --env-file .env up -d postgres redis minio

# 3. 安装后端依赖并迁移
uv sync --all-packages
cd apps/api && uv run alembic upgrade head && cd ../..

# 4. 创建首个平台管理员与邀请码
cd apps/api
uv run python -m mylife_api.cli create-admin \
  --email admin@example.com --password 'change-me-now' --display-name 管理员
uv run python -m mylife_api.cli create-invite --count 1 --days 30
cd ../..

# 5. 启动 API 与 Web
make api    # http://localhost:8000
make web    # http://localhost:3000
```

生产部署使用 `docker compose -f infra/compose/docker-compose.yml --env-file .env up -d --build`，
Caddy 自动签发 HTTPS。

> **MinIO 镜像**：Phase 0 不使用对象存储，但 base compose 已声明 `minio` 与 `createbuckets`。
> 若部署环境无法拉取 `minio/minio`，可先只启动核心服务：
>
> ```bash
> docker compose -f infra/compose/docker-compose.yml \
>                -f infra/compose/docker-compose.dev.yml \
>                --env-file .env up -d postgres redis api web caddy
> ```
>
> Phase 1 接入导入前需确定可达的 MinIO 镜像与版本，并锁定 digest。

> Windows 注意：psycopg 的异步模式不支持 ProactorEventLoop。本机直接运行 API 时请使用
> `PYTHONASYNCIODEBUG=0` + Selector 事件循环，或直接在 Docker 中运行 API（推荐）。

## 测试与运维演练

```bash
cd apps/api
uv run pytest -q              # 全量契约 + 数据库集成（306 passed, 1 skipped）
uv run pytest -q -m fixture   # Golden fixtures（FX-01..FX-12）
uv run pytest -q -m "not db"  # 仅无数据库单元/契约测试
cd ../..

make backup                   # PostgreSQL + 数据根加密备份（AC-61 / SEC-28）
make restore-drill            # 备份恢复到临时库并核对计数与哈希（AC-61）
make ac59                     # 容器 kill -9 / Redis 暂停恢复演练（AC-59）
```

数据库测试默认连接 `localhost:25432/mylife_test`（可用
`MYLIFE_TEST_DB_HOST` / `MYLIFE_TEST_DB_PORT` / `MYLIFE_TEST_ADMIN_URL` 覆盖）。
无 PostgreSQL 时数据库用例自动跳过。

## 已实现的 Phase 5 能力（M6）

- 管理后台（`/admin/*`）：用户查询与禁用/启用（即时吊销会话）、Workspace 统计、邀请码生成与撤销、脱敏审计日志、系统配置与跨租户聚合用量（`mylife_admin` 最小权限函数，零正文暴露）
- LLM 与 Embedding 设置：API Key 信封加密存储、保存前连接探测、BGE-M3 等模型蓝绿换维（动态建 `document_chunks_v{dim}` + HNSW + FORCE RLS、100% 覆盖率原子切换、一键回滚、7 天快照退役）
- 复盘 Writer：每日复盘的错误/方法论由配置的 LLM 做语义抽取，证据 ID、追溯字段、canonical_key 与 G0–G9 门禁仍由确定性代码校验；未配置模型或调用失败时回退确定性装配
- 数据导出与删除收口：全类别 ZIP 导出与 `manifest.json` SHA-256 对账、删除前导出软前置、30 天冷却期全局写冻结与随时撤销、到期 39 张业务表及动态向量表 0 残留清除、账户级跨库注销、Git 历史保留边界显式告知
- 审计与用量对账：登录/导入/删除/配置/复盘五类审计全覆盖，按 Workspace 时区聚合用量与成员级隔离
- 备份、恢复与供应链安全：`backup_pg.sh` / `backup_data.sh`（`age`/`openssl` 加密、7d/30d 两档保留）、`restore_drill.py`（RTO ≤4h / RPO ≤24h）、CI `pip-audit` + `npm audit` + CycloneDX SBOM、SEC-01…SEC-30 全集通过
- 前端多端与合规：1440/768/390 无横向溢出响应式、PWA manifest 与离线壳 Service Worker、`zh-CN`/`en-US` 语言切换、匿名可访问 `/privacy` 与 `/terms`

## 已实现的 Phase 3–4 能力（M4–M5）

- 复盘状态机（daily 7 章 / monthly 4 章 / yearly 5 章）、Todo 复选框台账与顺延统计、确定性门禁 G0–G9、80% 告警 / 100% 硬停预算守卫、`create_review` 预览确认协议、人工编辑重评审与版本回滚
- Git 分支/commit trailer/PR 交付与可选 daily 自动合并、对象存储 ZIP 降级交付、错误清单/正确方法论/行动清单三专题 `mylife:auto` 区间合并（保留人工备注与冲突并列）
- 单副本 `dispatcher`（时区/DST/补算窗口调度、定时 Git 同步、过期租约回收、outbox 重投、导出过期与僵死任务清理）、仪表盘与复盘日历、笔记+复盘混合检索

## 已实现的 Phase 2 能力（M3）

- ToolGateway 六工具（`search_notes` / `read_note` / `list_tasks` / `get_review` / `get_stats` / `create_review` preview），入参 `extra=forbid` 且拒绝 `workspace_id`
- chat 精简 FSM：`created → retrieving → drafting → verifying → succeeded/failed/cancelled`
- SSE 事件流（`run.state` / 工具轨迹 / `chat.citation` / `token` / `chat.done` / `heartbeat`），`run_events` 回放 + cursor/epoch
- 引用闭包：只保留可解析的 `[n]`，无证据返回“没有找到相关记录”哨兵
- 提示注入防护（8 类模式）+ 证据定界符中和
- 会话 CRUD、软删除、多轮上下文、`client_turn_id` 幂等、会话内串行
- 用量计量（`usage_events`）与每 workspace/用户限流
- 前端：会话列表、流式对话、引用卡片、工具轨迹折叠

## 已实现的 Phase 1 能力（M2）

- Obsidian Vault ZIP 导入（200MB 上限、忽略规则、路径/符号链接/压缩比防护）
- Git 仓库导入（HTTPS/SSH、SSRF 校验、hooks/submodule/LFS 禁用、分支切换同步）
- Markdown 解析（frontmatter、标题层级、标签、wikilink、日期）、按标题分块
- 增量索引（raw_hash 短路解析、content_hash 短路 embedding、重命名保留文档/块 ID）
- 快照与变更历史（`source_snapshots` / `snapshot_documents` / `document_changes`）
- 混合检索：jieba + tsvector 词法、pgvector HNSW 向量、标题/路径三路 RRF（k=60）
- 文档详情/元数据/原文/资产代理，Markdown 服务端渲染 + nh3 清洗
- 前端：文档树、文档阅读、搜索（过滤器/高亮/降级横幅）、ZIP 导入与进度
- Celery worker（`q.ingest`）与数据卷；性能基准脚本（FX-13 / FX-14）

## 已实现的 Phase 0 能力

- 邮箱 + 密码注册（邀请码一次性核销、并发安全）、登录、登出、退出所有设备
- HttpOnly 会话 Cookie、double-submit CSRF、Argon2id、登录失败限流/锁定
- 注册自动创建“我的知识库”、Workspace 列表/创建/切换/详情/重命名/成员
- `session → RunContext → PostgreSQL RLS` 全链路注入
- 14 张租户表的 ENABLE + FORCE RLS 与跨租户矩阵
- 邀请码管理（平台管理员）、审计日志（登录/注册/登出/配置）
- 文档树 API（Phase 0 读路径，供 Phase 1 导入填充）
- 前端登录/注册/布局壳/仪表盘/文档树空态

## 许可

内部项目，未授权不得分发。
