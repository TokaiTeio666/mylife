# mylife

多用户、多端访问的个人知识库 SaaS。当前进度：**Phase 4 / M4.5–M5（Git 交付、专题页、调度、仪表盘）**。

> 需求与设计基线见 [`docs/`](./docs/README.md)。实现与文档的偏差、证据与剩余工作见
> [`docs/IMPLEMENTATION-NOTES.md`](./docs/IMPLEMENTATION-NOTES.md) 与
> [`docs/PHASE4-STATUS.md`](./docs/PHASE4-STATUS.md)。

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
apps/web            Next.js：登录/注册/布局壳/仪表盘/文档/搜索/对话/复盘/专题/设置
apps/worker         Celery：导入与索引
packages/contracts  单一事实源：Pydantic v2 契约、枚举、错误码、常量
infra/compose       docker-compose.yml + 本地 dev override
infra/postgres     角色初始化脚本
infra/caddy         Caddyfile
tests               契约 / 数据库集成测试位于 apps/api/tests
scripts             边界检查等运维脚本
docs                需求、架构、决策、规范
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

## 测试

```bash
cd apps/api
uv run pytest -q            # 契约 + 数据库集成（需 PostgreSQL）
uv run pytest -q -m "not db"  # 仅契约测试
```

数据库测试默认连接 `localhost:25432/mylife_test`（可用
`MYLIFE_TEST_DB_HOST` / `MYLIFE_TEST_DB_PORT` / `MYLIFE_TEST_ADMIN_URL` 覆盖）。
无 PostgreSQL 时数据库用例自动跳过。

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
