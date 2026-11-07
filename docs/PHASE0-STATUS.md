# Phase 0 / M1 交付状态与验收证据

> 日期：2026-11-07 ｜ 范围：Monorepo、契约包、数据模型与 RLS、Compose、Auth/Workspace、session→RunContext 注入、基础前端布局
>
> 判定口径：`ACCEPTANCE-CRITERIA.md §5` Phase 0 行 + `SECURITY-BOUNDARY.md §11` Phase 0 门禁。

---

## 1. 结论摘要

| 项 | 状态 |
|---|---|
| 后端契约 + 数据库集成测试 | **70 passed**（`uv run pytest -q`） |
| 前端 `tsc` / `eslint` / `next build` | **通过** |
| 依赖方向检查 | **通过**（`scripts/check_boundaries.py`） |
| Compose 配置校验 | **通过**（base 与 dev override 均 `docker compose config -q`） |
| Phase 0 AC | AC-01…AC-06、AC-56 **通过**；AC-05（P1）、AC-07（P1，部分）见下 |
| Phase 0 SEC | SEC-01/02/03/05/06/07/08/18/19/25/30 **通过**；SEC-24 **部分**（仅已实现动作） |
| AC-65 一键部署 | **PARTIAL**：postgres + redis + migrate + api + web + caddy 已实际启动并通过 Caddy 验证（HTTPS、登录页、API、HTTP→HTTPS 跳转）；MinIO 镜像仓库不可达，未纳入本次启动 |
| FX-06 | **通过**（`tests/test_fixture_fx06.py`） |

> 说明：本文只声明“已被自动测试或命令验证”的项。未验证项一律标为未验证，不用“应当可用”代替证据。

---

## 2. 复现命令

```bash
# 依赖
docker compose -f infra/compose/docker-compose.yml \
               -f infra/compose/docker-compose.dev.yml \
               --env-file .env up -d postgres redis

# 后端
cd apps/api
uv run ruff check .
uv run pytest -q

# 前端
cd ../web
npm ci
npx tsc --noEmit
npm run lint
npm run build

# 依赖方向
cd ../..
python scripts/check_boundaries.py
```

测试环境：Windows 11 / Python 3.14.7 / Node 26.12.1 / PostgreSQL 16（pgvector/pgvector:pg16）。
数据库测试使用独立库 `mylife_test`，每次会话重建 schema 并执行 Alembic 迁移。

---

## 3. AC 对照

| AC | 优先级 | 结论 | 证据（测试/命令） |
|---|---|---|---|
| AC-01 | P0 | PASS | `test_auth.py::test_register_success_creates_personal_workspace`、`test_weak_password_and_csrf`、`test_login_failure_rate_limit` |
| AC-02 | P0 | PASS | `test_register_invalid_expired_used_invite`、`test_concurrent_register_with_same_code_only_one_succeeds`、`test_admin_invite_codes_require_platform_admin` |
| AC-03 | P0 | PASS | `test_register_success_creates_personal_workspace`（workspace 名称、owner、documents=0） |
| AC-04 | P0 | PASS | `test_two_workspaces_are_isolated`（切换后文档树隔离、会话 active_workspace_id 更新、非成员 404） |
| AC-05 | P1 | PASS | `test_role_assignment_is_restricted`（无成员添加端点、DB 仅 owner；`users.is_platform_admin` 另列） |
| AC-06 | P0 | PASS | `test_register_success_creates_personal_workspace`（Set-Cookie 属性）、`test_logout_all_revokes_every_device`（token_version+1、revoked_at 非空、双设备 401） |
| AC-07 | P1 | **PARTIAL** | 已覆盖：登录、注册、登出、workspace 创建/重命名、邀请码创建、security_abort；导入/删除/复盘生成随 Phase 1–4 接入 |
| AC-56 | P0 | PASS | `test_rls_matrix.py`（14 张租户表参数化矩阵、ENABLE+FORCE、NOBYPASSRLS、复合外键、跨租户 404） |
| AC-65 | P0 | **PARTIAL** | Compose 配置校验通过；核心服务实际启动并 healthy（postgres/redis/migrate/api/web/caddy），通过 Caddy HTTPS 完成注册/登录/邀请码端到端；MinIO 镜像仓库在本机不可达，未完成“全服务”证据。见 §6、§9 |

补充验证（非 Phase 0 必需但已覆盖）：

- `test_document_tree_filters`：文档树 `root`/`depth` 过滤与 404。
- `test_workspace_quota`：每用户 5 个 Workspace 上限。
- `test_contract.py`：错误码表、`extra=forbid`、`workspace_id` 拒绝、UTC `Z` 序列化、枚举/哨兵冻结。

---

## 4. SEC 对照（Phase 0 门禁）

| SEC | 结论 | 证据 |
|---|---|---|
| SEC-01 未登录访问业务端点 → 401 | PASS | `test_unauthenticated_business_endpoints_401` |
| SEC-02 退出所有设备后旧 Cookie → 401，token_version 递增 | PASS | `test_logout_all_revokes_every_device` |
| SEC-03 无 CSRF 的写请求 → 403 且无副作用 | PASS | `test_weak_password_and_csrf` |
| SEC-05 已用邀请码再注册 → 拒绝，used_at 非空 | PASS | `test_register_used_code_second_time_rejected`、并发用例 |
| SEC-06 跨租户访问 → 404 | PASS | `test_switch_to_non_member_returns_404_and_security_audit`、`test_fixture_fx06` |
| SEC-07 A 会话查询 B 租户表 → 0 行 | PASS | `test_rls_matrix.py` |
| SEC-08 伪造 workspace_id → 被拒绝/覆盖 | PASS | `test_workspace_id_in_body_rejected`、`test_fixture_fx06` |
| SEC-18 DB 泄露但无 KEK → 凭据不可解 | PASS | `test_security.py`（信封加密往返、错误 KEK、AAD 绑定、篡改拒绝、落库仅密文） |
| SEC-19 错误/调试日志无正文/token/密钥 | PASS（默认路径） | `test_unhandled_logs_do_not_contain_exception_details`；服务端日志扫描无明文 |
| SEC-24 登录/导入/删除/配置/复盘生成审计 | **PARTIAL** | 已实现动作均有审计；未实现功能无审计（见 AC-07） |
| SEC-25 人为跨租户命中 → security_abort、不可 approve | PASS | `security_abort` 审计行 + 404；Phase 0 无 approve 端点 |
| SEC-30 platform_admin 不可读复盘正文 | PASS（结构性） | admin 路由仅含邀请码，无内容读取端点；Phase 3 复盘接口不在 admin 域 |

---

## 5. Golden fixture

| fixture | 结论 | 证据 |
|---|---|---|
| FX-06 fx_tenant_leak | PASS | `tests/test_fixture_fx06.py`：两租户各 50 篇同名文档；跨租户 404；注入 `workspace_id` 422；A 读 B 行数 0；B 行数确为 50；security_abort 审计存在 |
| FX-13 / FX-14（Phase 1） | 未开始 | 依赖导入与检索 |
| FX-01/02/03/04/10/12（Phase 3） | 未开始 | 依赖复盘 FSM |
| FX-07/08/09（Phase 4） | 未开始 | 依赖交付与调度 |
| FX-11（Phase 5） | 未开始 | 依赖 embedding profiles |

---

## 6. 未完成与阻塞

| 项 | 状态 | 原因 / 下一步 |
|---|---|---|
| AC-65 全栈一键部署端到端 | PARTIAL | postgres/redis/migrate/api/web/caddy 已实际启动并通过 Caddy 验证；MinIO 在 Phase 0 不被使用，但本机无法从 Docker Hub / Quay 拉取镜像（`minio/minio` denied；Quay unauthorized），因此“全服务 healthy”缺一项。部署环境需提供可达的 MinIO 镜像并锁定 digest |
| AC-59 容器重启续跑 | PARTIAL | compose 已设 `restart: unless-stopped`；kill -9 与 5 分钟续跑属 Phase 3/4 |
| AC-60 run 回放 | PARTIAL | `/healthz`、`/readyz` 已分离；结构化日志字段齐备；`run_events` 回放属 Phase 3 |
| 会话 7 天滑动续期 | 未实现 | 见 `IMPLEMENTATION-NOTES.md` D1 |
| MinIO / worker / dispatcher 服务进程 | 未实现 | Phase 1/4 |

---

## 7. E2E 冒烟记录（本机，非自动化）

在 Windows 上以 `uv run python -m mylife_api.devserver` 启动真实服务，使用真实 HTTP + Cookie：

| 步骤 | 结果 |
|---|---|
| `GET /healthz` | `{"status":"ok"}` |
| `GET /api/v1/auth/csrf` | 200，写入 `mylife_csrf` |
| `POST /auth/register`（邀请码） | 201，workspace=我的知识库，role=owner |
| `GET /auth/me` | 200，capabilities=`chat,review_manual,git_delivery,export,settings` |
| `GET /documents/tree` | 200，`total_documents=0` |
| 管理员登录 + `POST /admin/invite-codes` | 201，返回邀请码 |
| `POST /auth/logout-all` | 204；随后 `GET /auth/me` → 401 |
| 响应头 | `X-Request-Id: req_...`、`X-API-Version: 1` |
| 日志扫描 | 无密码/令牌/密钥明文 |

---

## 8. 容器化 E2E（本机，2026-11-07）

启动命令（去掉 MinIO，原因见 §6）：

```bash
docker compose -f infra/compose/docker-compose.yml \
               -f infra/compose/docker-compose.dev.yml \
               --env-file .env up -d postgres redis api web caddy
```

容器状态：

| 服务 | 状态 |
|---|---|
| postgres | Up (healthy)，宿主 25432 → 5432 |
| redis | Up (healthy)，宿主 26379 → 6379 |
| migrate | Exited(0)（Alembic 迁移成功） |
| api | Up (healthy)，宿主 8000 → 8000 |
| web | Up，宿主 3000 → 3000 |
| caddy | Up，宿主 80/443 |

通过 Caddy 的验证：

| 请求 | 结果 |
|---|---|
| `GET https://localhost/healthz` | 200 `{"status":"ok"}` |
| `GET https://localhost/login` | 200（Next.js 登录页） |
| `GET https://localhost/api/v1/auth/csrf` | 200，返回 CSRF token |
| `GET http://localhost/healthz` | 308（跳转 HTTPS） |
| 管理员登录 → 创建邀请码 → 新用户注册 → `/auth/me` | 全部成功；workspace=我的知识库，role=owner |

> 说明：Caddy 在 `localhost` 使用内部 CA，客户端需信任该 CA；生产域名下使用公开证书。

---

## 9. 下一阶段入口（Phase 1）

进入 Phase 1 前需要：

1. 解决 MinIO 镜像来源并锁定 digest，完成 AC-65 的全链路证据。
2. 实现 L1 接入层：Git/ZIP 导入、忽略规则、Markdown 解析、分块、内容哈希增量索引。
3. 混合检索（jieba + tsvector + pgvector + RRF）与性能门禁 FX-13/FX-14。
4. 补齐文档树之外的文档详情/资源代理 API 与前端。
5. 补齐 `sessions.last_used_at` 与滑动续期（D1）。

Phase 1 门禁：AC-08…AC-21、AC-58；SEC-11/12/13/14/15；FX-13、FX-14。
