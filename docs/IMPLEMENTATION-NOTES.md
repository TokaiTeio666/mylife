# mylife 实现说明与偏差登记（Phase 0）

> 版本：v0.1 ｜ 日期：2026-11-07 ｜ 对应代码：`apps/api`、`apps/web`、`packages/contracts`、`infra/`
>
> 本文只记录「实现与设计文档的差异」与「文档内部冲突的裁决」，不重复设计本身。
> 未列入本文的内容，均按 `docs/` 基线实现。

---

## 1. 文档冲突与裁决

| # | 冲突 | 裁决 | 理由 |
|---|---|---|---|
| C1 | `DIRECTORY-CONTRACTS §2` 写 `packages/contracts`，`API-CONTRACTS §9.1` 写 `packages/contract` | 采用 `packages/contracts`，Python 包名 `mylife_contract` | `DIRECTORY-CONSTRAINTS.md` 是 CI 可执行的目录规范，优先级更高；导入名沿用 §9.1 的 `mylife_contract` |
| C2 | `API-CONTRACTS §3.10` 要求 `users` 有 `platform_admin` 角色，`DATA-MODEL §3.1` 的 `users` 无角色列，`workspace_members.role` CHECK 只允许 owner/admin/member | 新增 `users.is_platform_admin boolean NOT NULL DEFAULT false` | 最小扩展；不把平台角色混入租户成员角色（避免跨租户语义污染） |
| C3 | `SEC-06` 要求跨租户 404，`SEC-25` 要求跨租户命中 `security_abort` | 对外 404；内部写 `audit_logs.action=security_abort`、`detail.reason=TENANT_LEAK` | Phase 0 尚无 agent run，`security_abort` 以审计事件表达；审批入口 Phase 3 才出现，不存在“可 approve”路径 |
| C4 | `DATA-MODEL §6.4` 规定 `workspaces` 策略为 `USING (id = app_workspace_id())`，但 `GET /workspaces` 必须列出用户全部 workspace（AC-03/AC-04） | `workspaces` 的 SELECT 策略额外允许“我是成员的 workspace”；`workspace_members` 的 SELECT 允许本人成员行 | 否则 RLS 下只能看到 active workspace，AC-03/AC-04 无法通过；策略仍保证用户只能看到自己的 workspace |
| C5 | `REQUIREMENTS §6.3` 与 `FR-E9` 的复盘目录冲突 | 未在 Phase 0 实现；按 `DECISIONS.md` 1D（`wiki/复盘`），Phase 3/4 落地 | 已有决策，无需新增 |
| C6 | `API-CONTRACTS §1.3` 要求 7 天空闲滑动续期，`DATA-MODEL sessions` 无 `last_used_at` | Phase 0 仅实现 30 天绝对有效期 | 避免先加无来源字段；Phase 1 增加 `last_used_at` 并补滑动续期与测试 |

---

## 2. 有意偏离（Phase 0 范围裁剪）

| # | 项目 | 现状 | 后续 |
|---|---|---|---|
| D1 | 会话空闲滑动续期 | 未实现，仅绝对 30 天 | Phase 1 加 `sessions.last_used_at` |
| D2 | 审计五类全覆盖（AC-07 / SEC-24） | 已覆盖注册、登录成功/失败、登出、登出所有设备、workspace 创建/重命名、邀请码创建、security_abort；导入/删除/复盘生成随对应功能在 Phase 1–4 接入 | 对应阶段补齐 |
| D3 | 文档树之外的文档/搜索/导入 API | 仅 `GET /documents/tree`（Phase 0 读路径） | Phase 1 |
| D4 | MinIO | compose 已声明，但 Phase 0 API 不依赖；本机镜像仓库不可达，未纳入本次全栈启动 | Phase 1 接入导入与对象存储 |
| D5 | `provider_credentials` API | 仅落库 + 信封加密模块 + 集成测试；无设置页/端点 | Phase 5（FR-G1） |
| D6 | `sessions.token_version` 的实际校验语义 | `logout-all` 对全部会话 `token_version += 1` 且 `revoked_at = now()`；校验以 `revoked_at` 为失效判定 | token 为不透明随机串、服务端只存哈希，版本号用于审计与 AC-06 断言；若后续改为带版本 token，再启用版本比对 |
| D7 | CSRF token | 纯 double-submit（Cookie 与头相等），未做 HMAC 签名或会话绑定 | 与 §1.3 一致；如需强化，Phase 2 增加签名并保持兼容 |
| D8 | `GET /workspaces/{id}` 的 `If-Match` 弱校验 | 未实现（`PATCH` 接受可选头但未校验） | Phase 1 补 ETag/If-Match |

---

## 3. 运行时与平台注意事项

1. **Windows + async psycopg**：psycopg 异步模式不支持 `ProactorEventLoop`。
   - 本机启动 API 请用 `uv run python -m mylife_api.devserver`（已固定 `SelectorEventLoop`）；
   - 生产为 Linux，不受影响；
   - 测试在 `tests/conftest.py` 顶部切换事件循环策略。
2. **本地端口**：Windows 的 Hyper-V 保留端口段包含 5432。`docker-compose.dev.yml` 将 Postgres 映射到 `25432`；
   对应 `.env` 中 `MYLIFE_DB_PORT=25432`（容器内仍使用 `postgres:5432`）。
3. **Docker 端口发布与 internal 网络**：仅接入 `internal: true` 网络的容器不会发布宿主端口。
   dev override 将 `internal` 设为 `internal: false`；生产 base compose 保持隔离。
4. **Alembic 文件编码**：`alembic.ini` 必须保持纯 ASCII。Windows 中文环境读取配置默认使用 GBK，
   非 ASCII 字符会导致 `UnicodeDecodeError`。
5. **日志**：默认不记录未处理异常的堆栈（可能含 SQL 参数与密码哈希）。排查时显式设置
   `MYLIFE_LOG_EXCEPTIONS=true`，且仅在受控环境使用。

---

## 4. 事务与审计语义

- 每个 HTTP 请求一个事务；成功提交。
- 已处理的业务错误（`AppError`）也提交事务，以便同事务的 `audit_logs`（登录失败、跨租户尝试等）落库；
  未处理异常一律回滚。
- 预期内的唯一约束冲突（并发同邮箱注册）使用 `SAVEPOINT`，避免事务进入 aborted 状态。
- 该语义是本项目“审计与业务同事务”要求的实现方式；若后续引入明确的“业务失败必须回滚所有写入”，
  需把审计拆到独立 outbox 写入点。

---

## 5. 安全实现要点（Phase 0）

| 控制 | 实现位置 |
|---|---|
| Argon2id 密码哈希 | `mylife_api/security.py` |
| 会话 token 只存 SHA-256 | `mylife_api/security.py`、`sessions.token_hash` |
| HttpOnly + Secure + SameSite=Lax Cookie | `session_cookie_kwargs` |
| double-submit CSRF（含 login/register） | `middleware.CsrfMiddleware` |
| 登录失败限流与锁定 | `ratelimit.py` + `services.login` |
| workspace 只来自服务端会话 | `deps.get_run_context`、`RunContext` |
| 入参禁止 `workspace_id`（`extra=forbid` + 稳定 reason） | `common.ApiModel`、`errors._validation_details` |
| RLS ENABLE + FORCE + 默认拒绝 | 迁移 `0001_phase0` |
| 应用角色 `NOBYPASSRLS` | `infra/postgres/init/001-roles.sh`、迁移兜底 |
| 凭据信封加密（KEK→DEK，AES-256-GCM，AAD 绑定租户/用途） | `mylife_api/crypto.py` |
| 日志脱敏（不记录堆栈/SQL 参数） | `errors.install_error_handlers` |

---

## 6. 待决策 / 风险

| # | 事项 | 影响 | 建议 |
|---|---|---|---|
| O1 | `security_abort` 是否应对外返回 403（而非 404） | 与 SEC-06 冲突 | 保持 404 + 内部审计；如安全评审要求显式 403，需同步修改 SEC-06 |
| O2 | 平台管理员是否允许读取审计中的 `target`（可能含邮箱） | 隐私 | Phase 5 管理后台只展示脱敏邮箱 |
| O3 | MinIO 镜像源迁移 | 部署可用性 | 部署时确认可用的 MinIO 镜像/版本，并锁定 digest |
| O4 | Windows 本地开发是否强制 Docker | 开发体验 | README 已建议用容器运行 API；如需原生运行，保留 `devserver` 入口 |

---

## 7. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-11-07 | Phase 0 初版：记录 6 项文档冲突裁决、8 项范围裁剪、5 条运行时注意事项 |
