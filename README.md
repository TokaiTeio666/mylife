# mylife

多用户、多端访问的个人知识库 SaaS。当前进度：**Phase 0 / M1（Auth + Workspace + RunContext + RLS 骨架）**。

> 需求与设计基线见 [`docs/`](./docs/README.md)。实现与文档的偏差、证据与剩余工作见
> [`docs/IMPLEMENTATION-NOTES.md`](./docs/IMPLEMENTATION-NOTES.md) 与
> [`docs/PHASE0-STATUS.md`](./docs/PHASE0-STATUS.md)。

## 技术栈

- **web**：Next.js 15 App Router + TypeScript + Tailwind + TanStack Query
- **api**：FastAPI + SQLAlchemy 2（async）+ psycopg 3 + Alembic
- **db**：PostgreSQL 16 + pgvector（RLS ENABLE + FORCE）
- **queue/cache**：Redis（Phase 1 起接入 Celery）
- **object storage**：MinIO（Phase 1 起使用）
- **edge**：Caddy（TLS、SSE 不缓冲）

## 目录

```text
apps/api            FastAPI：路由域 + service + repositories + migrations
apps/web            Next.js：登录/注册/布局壳/仪表盘/文档树
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
