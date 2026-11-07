# 会话交接（Handoff）

> 用途：**在新会话里恢复工作**。本文件只记录“从零开始需要知道什么”，
> 细节以各阶段 STATUS 与代码为准。

---

## 1. 当前进度

| 阶段 | 提交 | 状态文档 |
|---|---|---|
| Phase 0 / M1（Auth、Workspace、RLS、Compose） | `dc355fb` | `PHASE0-STATUS.md` |
| Phase 1 / M2（导入、混合检索、文档） | `4cf9f6f` | `PHASE1-STATUS.md` |
| Phase 2 / M3（ToolGateway、chat、SSE、引用） | `f1c0025` | `PHASE2-STATUS.md` |
| Phase 3 / M4（复盘 FSM、章节契约、Todo 台账、G0–G9） | 未开始 | — |

测试基线：`cd apps/api && uv run pytest -q` → **104 passed, 1 skipped**。
前端：`cd apps/web && npx tsc --noEmit && npm run lint && npm run build`。

---

## 2. 恢复会话的提示词（复制即用）

```text
继续 mylife 项目。先读 docs/HANDOFF.md，再读 docs/PHASE2-STATUS.md §8
与 docs/IMPLEMENTATION-NOTES.md §2B。当前要做 Phase 3：
复盘 FSM、§5.1 章节契约、Todo 台账、G0–G9 确定性门禁、预算 guard。
门禁：AC-25(create_review 确认)、AC-31…AC-38、AC-41、AC-42、AC-60、
AC-66、AC-68；SEC-16/17/23；FX-01/02/03/04/10/12。
```

按阶段推进时，把“当前要做 Phase 3”替换成目标阶段即可。

---

## 3. 为什么可以清空上下文

每个阶段结束时，仓库里都有四份可独立阅读的状态：

1. **`docs/PHASE{n}-STATUS.md`**：AC/SEC 逐条结论 + 证据 + 未完成项 + 下一阶段入口；
2. **`docs/IMPLEMENTATION-NOTES.md`**：与设计文档的**偏差裁决**（每阶段新增 §2A/§2B…）；
3. **`git log`**：每次提交的信息即变更摘要；
4. **`.env` + 运行中的容器**：本机环境现状。

因此清空上下文**不会丢失工作成果**；丢失的只是“这轮调试的具体过程”，
而其中真正重要的平台坑已写入 `IMPLEMENTATION-NOTES.md §3`。

---

## 4. 本机环境坑（复现时最容易踩）

| 现象 | 处理 |
|---|---|
| `Psycopg cannot use ProactorEventLoop` | 本机跑 API 用 `uv run python -m mylife_api.devserver`；测试已在 conftest 切换事件循环 |
| 5432 端口被 Windows 保留段占用 | dev override 映射到 `25432`；`.env` 里 `MYLIFE_DB_PORT=25432` |
| Redis 6379 被占用 | dev override 映射到 `26379` |
| 仅接 internal 网络的容器不发布端口 | dev override 里 `networks.internal.internal: false` |
| `alembic.ini` 出现中文即报 GBK 解码错误 | 该文件必须保持纯 ASCII |
| pypi.org 本机不可达 | `.env` 的 `MYLIFE_PYPI_INDEX=https://mirrors.aliyun.com/pypi/simple` |
| MinIO 镜像拉不到 | 只启动核心服务；Phase 1 用本地数据根 `MYLIFE_DATA_ROOT` |
| Git 本地裸仓库浅克隆失败 | `file://` 源已自动关闭 `--depth 1` |
| Windows MAX_PATH | 导入快照目录用 `snapshots/{job_id}` 短路径 |

---

## 5. 建议的清空时机

- **在阶段边界清空**（Phase 2 已提交 → 清空 → 开 Phase 3）。
- **不要在一轮调试中途清空**（会丢掉正在验证的假设）。
- 长会话即便不清空也会被自动摘要，摘要是有损的；主动清空 + 完整状态文档
  比被动摘要更可靠。
