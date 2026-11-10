# mylife 前端设计系统与组件层级

> 版本：v0.1 ｜ 日期：2026-11-06 ｜ 技术栈：Next.js 15 App Router + TypeScript + Tailwind + shadcn/ui + TanStack Query
>
> 关联：`REQUIREMENTS.md` NFR-7/NFR-8、`ARCHITECTURE.md` §2.5/§4.6、`API-CONTRACTS.md`

---

## 1. 设计原则

| 原则 | 含义 | 可检验表现 |
|---|---|---|
| 中文优先 | 排版、断行、标点、文案以中文为第一语言 | 无中文孤字、标点挤压；文案集中管理 |
| 安静的密度 | 知识工作台，信息密度高但不拥挤 | 16px 正文、1.6 行高、4px 间距体系 |
| 可信感 | 引用、证据、版本、状态始终可见 | 每条结论可点回来源；degraded 有横幅 |
| 键盘优先 | 搜索、切换、发送、确认都可键盘完成 | `Cmd/Ctrl+K`、`/`、`Enter`、`Esc` |
| 渐进降级 | 失败不白屏，降级不静默 | 每个数据区有 loading/empty/error/degraded |
| 响应式/PWA | 桌面、平板、手机同一套组件 | 断点 640/768/1024/1280；可安装 |
| 可访问 | WCAG 2.1 AA | 对比度 ≥4.5:1、焦点可见、44px 触控 |

---

## 2. Design Tokens

### 2.1 颜色（语义 token，HSL）

```css
:root {
  --background: 0 0% 100%;
  --foreground: 222 47% 11%;
  --surface: 210 20% 98%;
  --surface-2: 210 16% 95%;
  --muted: 210 16% 93%;
  --muted-foreground: 215 16% 47%;
  --border: 214 20% 88%;
  --input: 214 20% 88%;
  --ring: 221 83% 53%;

  --primary: 221 83% 53%;
  --primary-foreground: 0 0% 100%;
  --accent: 173 58% 39%;
  --accent-foreground: 0 0% 100%;

  --success: 142 71% 45%;
  --warning: 38 92% 50%;
  --danger: 0 72% 51%;
  --citation: 38 92% 50%;       /* 引用高亮 */
  --citation-bg: 38 92% 95%;

  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
}

.dark {
  --background: 222 47% 7%;
  --foreground: 210 20% 98%;
  --surface: 222 40% 10%;
  --surface-2: 222 36% 12%;
  --muted: 217 33% 17%;
  --muted-foreground: 215 20% 65%;
  --border: 217 33% 20%;
  --input: 217 33% 20%;
  --ring: 217 91% 60%;

  --primary: 217 91% 60%;
  --primary-foreground: 222 47% 7%;
  --accent: 173 58% 47%;
  --accent-foreground: 222 47% 7%;

  --success: 142 69% 58%;
  --warning: 38 92% 55%;
  --danger: 0 72% 58%;
  --citation: 38 92% 55%;
  --citation-bg: 38 40% 18%;
}
```

Tailwind 映射：`bg-background`、`text-foreground`、`bg-surface`、`border-border`、`text-muted-foreground`、`bg-primary`、`text-danger`、`bg-citation/10` 等。禁止在组件里写死 hex。

### 2.2 字体

```css
--font-sans: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
--font-mono: "JetBrains Mono", "SFMono-Regular", Consolas, monospace;
```

| 级别 | 字号/行高 | 用途 |
|---|---|---|
| `display` | 30/38 | 页面主标题 |
| `h1` | 24/32 | 区块标题 |
| `h2` | 20/28 | 卡片标题 |
| `h3` | 18/26 | 小节 |
| `body` | 16/26 | 正文 |
| `small` | 14/22 | 辅助 |
| `caption` | 12/18 | 元数据 |

中文正文不使用斜体；英文/数字用 tabular-nums（表格、用量、日期）。

### 2.3 间距、圆角、阴影、动效

```text
spacing: 0,1,2,3,4,6,8,10,12,16,20,24 (Tailwind 4px 基)
radius: sm 6 / md 10 / lg 14 / full
shadow: sm 0 1px 2px / md 0 4px 12px / lg 0 12px 32px
motion: fast 120ms / base 180ms / slow 240ms; easing ease-out
z-index: base 0 / sticky 10 / dropdown 20 / overlay 30 / modal 40 / toast 50
```

### 2.4 断点

| 断点 | 宽度 | 布局 |
|---|---|---|
| mobile | < 640 | 单列；侧栏抽屉；底部导航 |
| sm | ≥ 640 | 单列加宽 |
| md | ≥ 768 | 侧栏可收起；右侧面板抽屉 |
| lg | ≥ 1024 | 侧栏 240 + 主内容 |
| xl | ≥ 1280 | 侧栏 240 + 主内容 + 右侧 320 |

---

## 3. 布局壳

```text
┌──────────────────────────────────────────────────────────────┐
│ Topbar: Logo | WorkspaceSwitcher | Search | Cmd+K | User     │
├──────────┬───────────────────────────────────┬───────────────┤
│ Sidebar  │ Main                              │ RightPanel    │
│ 240px    │ 文档/搜索/对话/复盘/仪表盘         │ 证据/工具轨迹  │
│ 导航     │                                   │ 320px 可折叠   │
│ 最近     │                                   │               │
└──────────┴───────────────────────────────────┴───────────────┘
```

- **Sidebar**：Workspace 切换、主导航（文档、搜索、对话、复盘、专题、仪表盘）、最近复盘、设置入口。
- **Topbar**：全局搜索、命令面板、同步状态、用户菜单。
- **RightPanel**：对话时显示引用与工具轨迹；复盘时显示章节导航与 GateResult；可折叠。
- **Mobile**：Topbar + 抽屉导航；RightPanel 变为底部 Sheet；触控目标 ≥44px。

---

## 4. 组件层级

### 4.1 第一层：Primitives（`components/ui`，shadcn/ui）

Button、Input、Textarea、Select、Checkbox、RadioGroup、Switch、Badge、Tag、Tooltip、Popover、Dialog、Sheet/Drawer、Toast、Tabs、Table、Pagination、Skeleton、Progress、Avatar、DropdownMenu、Command、Separator、ScrollArea。

约定：

- 全部 `forwardRef`；`className` 可覆盖；`data-state` 暴露状态。
- 变体用 `class-variance-authority`；不新增一次性颜色。
- 所有交互元素有 `focus-visible:ring-2 ring-ring`。

### 4.2 第二层：Composites（`components/`）

| 组件 | 职责 | 关键 props | 状态 |
|---|---|---|---|
| `AppShell` | 布局壳、导航、右侧面板 | `workspace`, `nav`, `rightPanel`, `children` | — |
| `WorkspaceSwitcher` | 切换/创建 Workspace | `workspaces`, `activeId`, `onSwitch`, `onCreate` | loading/empty |
| `CommandPalette` | 全局命令与搜索 | `open`, `onOpenChange`, `actions` | — |
| `DocumentTree` | 目录树 | `nodes`, `selectedPath`, `expanded`, `onSelect`, `onToggle`, `filter` | loading/empty/error |
| `MarkdownRenderer` | Markdown 渲染 | `content`, `citations`, `onCitationClick`, `autoMarkers`, `sanitize` | — |
| `SearchBar` | 查询输入 | `value`, `onChange`, `onSubmit`, `filters` | — |
| `SearchFilters` | 目录/标签/日期/类型 | `filters`, `onChange`, `facets` | — |
| `SearchResultList` | 结果列表 | `results`, `query`, `onOpen`, `onCite` | loading/empty/error |
| `SnippetHighlight` | 命中片段 | `html`, `query` | — |
| `ChatThread` | 消息流 | `messages`, `streaming`, `onRetry` | loading/empty/error |
| `ChatMessage` | 单条消息 | `role`, `content`, `citations`, `tools`, `status` | streaming/partial |
| `ChatComposer` | 输入与发送 | `onSend`, `disabled`, `modelProfile`, `contextConfig` | — |
| `CitationList` | 引用列表 | `citations`, `onOpen` | empty |
| `CitationCard` | 单条引用 | `index`, `path`, `heading`, `snippet`, `score` | — |
| `ToolTrace` | 工具轨迹 | `steps`, `collapsed`, `onOpenStep` | running/done/failed |
| `ReviewCalendar` | 复盘日历 | `month`, `days`, `onSelect` | loading/empty |
| `ReviewViewer` | 复盘正文 | `kind`, `periodKey`, `sections`, `citations`, `version`, `status` | degraded/manual |
| `ReviewSection` | 单章节 | `section`, `citations`, `onCite` | — |
| `ReviewStatusBadge` | 状态/版本 | `status`, `version`, `degraded` | — |
| `TaskBoard` | 任务面板 | `tasks`, `filter`, `onOpenSource` | loading/empty |
| `TaskItem` | 单任务 | `task`, `onToggle`, `onOpenSource` | — |
| `TopicPage` | 专题页 | `topic`, `items`, `onConfirm`, `onResolve` | loading/empty |
| `DeliveryStatus` | 交付状态 | `delivery`, `onOpenPR`, `onRetry` | pending/merged/conflict |
| `SettingsForm` | 设置表单 | `schema`, `values`, `onSubmit` | loading/error |
| `ProviderTestCard` | 测试连接 | `provider`, `onTest`, `status` | testing/ok/fail |
| `UsageChart` | 用量图 | `series`, `period` | loading/empty |
| `EmptyState` | 空态 | `title`, `description`, `action` | — |
| `ErrorState` | 错误态 | `code`, `message`, `retry` | — |
| `DegradedBanner` | 降级横幅 | `reason`, `details` | — |

### 4.3 第三层：Pages（`app/`）

| 路由 | 页面 | 主要组件 | 数据依赖 |
|---|---|---|---|
| `/login` | 登录 | AuthForm | `POST /auth/login` |
| `/register` | 邀请码注册 | AuthForm + InviteCode | `POST /auth/register` |
| `/documents` | 文档树 | DocumentTree + EmptyState | `GET /documents/tree` |
| `/documents/[...path]` | 文档预览 | MarkdownRenderer + DocumentMeta | `GET /documents/{id}` |
| `/search` | 搜索 | SearchBar/SearchFilters/SearchResultList | `GET /search` |
| `/chat` | 会话列表 | ConversationList | `GET /conversations` |
| `/chat/[id]` | 对话 | ChatThread/ChatComposer/CitationList/ToolTrace | `GET /conversations/{id}` + SSE |
| `/reviews` | 复盘日历 | ReviewCalendar + ReviewStatusBadge | `GET /reviews` |
| `/reviews/[kind]/[period]` | 复盘详情 | ReviewViewer + ReviewTOC + DeliveryStatus | `GET /reviews/{id}` |
| `/topics/[topic]` | 专题页 | TopicPage + TopicItem + TopicTimeline | `GET /topics/{topic}/items` |
| `/dashboard` | 仪表盘 | StatCards + LatestReviews + TaskBoard | `GET /dashboard/*` |
| `/settings/*` | 设置 | SettingsForm/ProviderTestCard/ScheduleForm/GitForm/UsageChart | `GET/PUT /settings/*` |
| `/admin/*` | 管理后台 | UserTable/InviteCodeTable/AuditLogTable | `/admin/*` |

每个页面必须实现：`loading`（骨架）、`empty`（EmptyState）、`error`（ErrorState + retry）、`degraded`（DegradedBanner）。

---

## 5. 关键组件契约

### 5.1 DocumentTree

```ts
interface DocumentTreeProps {
  nodes: DocumentNode[];          // { id, path, title, children, type: 'dir'|'file' }
  selectedPath?: string;
  expanded: Set<string>;
  onSelect(path: string): void;
  onToggle(path: string): void;
  filter?: { tags?: string[]; query?: string };
}
```

- 键盘：↑↓ 移动、→ 展开、← 收起、Enter 打开。
- 选中态 `aria-selected`；目录 `role=tree`，文件 `role=treeitem`。
- 生成物标注 `generated` 图标；不在普通证据池中高亮。

### 5.2 MarkdownRenderer

```ts
interface MarkdownRendererProps {
  content: string;
  citations?: CitationRef[];      // { index, evidenceId, path, heading }
  onCitationClick?(index: number): void;
  autoMarkers?: boolean;          // 显示 <!-- mylife:auto -->
  sanitize?: boolean;             // 默认 true
}
```

- 禁止原生 HTML；`react-markdown` + `remark-gfm` + `rehype-sanitize`。
- `[[wikilink]]` 解析为应用内路由；断链显示为普通文本并标记。
- 引用序号 `[1]` 渲染为 `<button>`，点击滚动到 CitationList。
- 代码块只读、带语言标签；不执行。
- 图片走代理；外链图片不直接加载。

### 5.3 ChatMessage

```ts
interface ChatMessageProps {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  tools?: ToolStep[];
  status?: 'drafting' | 'complete' | 'partial' | 'cancelled';
  onRetry?(): void;
  onOpenCitation?(c: Citation): void;
}
```

- 流式时 `aria-live="polite"`，不逐字打断读屏。
- `partial` 显示“回答未完成”标记，禁止伪装成完整答案。
- 无引用时显示“未找到相关记录”哨兵，不显示“来源”区。

### 5.4 ReviewViewer

```ts
interface ReviewViewerProps {
  kind: 'daily' | 'monthly' | 'yearly';
  periodKey: string;
  reviewId: string;
  version: number;
  status: 'draft' | 'in_review' | 'published' | 'manual' | 'superseded';
  sections: ReviewSectionData[];
  citations: Citation[];
  degraded?: { reason: string };
  onApprove?(): void;
  onReject?(reason: string): void;
  onOpenPR?(): void;
}
```

- 章节顺序严格按 `SectionId` / `MonthlySectionId` / `YearlySectionId`。
- 每个错误/方法论条目显示来源日期 + 路径 + 行号；点击跳转原文。
- `manual` 状态显示“人工编辑，自动重渲染已暂停”。
- `degraded` 顶部固定横幅，不可关闭。

### 5.5 TopicPage

```ts
interface TopicPageProps {
  topic: 'errors' | 'methods' | 'actions';
  items: TopicItem[];
  onConfirm(id: string): void;    // pending → open
  onResolve(id: string): void;    // open → resolved
  onOpenSource(item: TopicItem): void;
}
```

- `pending` 条目置顶并标“待确认”；未确认不进入正式专题内容。
- `stale` 只降权排序，不隐藏。
- 每条显示 occurrence 时间线；不提供“删除历史”按钮。

### 5.6 CitationCard

```ts
interface CitationCardProps {
  index: number;
  path: string;
  heading?: string;
  snippet: string;
  score?: number;
  sourceType: 'document' | 'review';
  onOpen(path: string): void;
}
```

- 显示 `path#Lx-Ly`（有行号时）；`score` 仅内部/调试可见，不面向普通用户。
- 点击打开文档并滚动到 heading/行号。

### 5.7 ToolTrace

```ts
interface ToolStep {
  id: string;
  name: string;                   // search_notes / read_note / ...
  argsSummary: string;
  status: 'running' | 'succeeded' | 'failed' | 'skipped';
  latencyMs?: number;
  errorCode?: string;
}
```

- 折叠态显示“调用了 N 个工具”；展开显示参数摘要、状态、耗时。
- 不显示原始笔记全文；只显示参数摘要。
- 失败步骤显示错误码；可重试的提供“重试”按钮。

### 5.8 DeliveryStatus

```ts
interface DeliveryStatusProps {
  delivery: {
    target: 'git' | 'object_store' | 'db_only';
    status: 'pending' | 'blocked' | 'committing' | 'pr_open' | 'merged' | 'conflict' | 'failed' | 'noop';
    branch?: string;
    prNumber?: number;
    prUrl?: string;
    commitSha?: string;
  };
  onOpenPR?(): void;
  onRetry?(): void;
}
```

- `pr_open` 显示 PR 链接与“等待合并”；`merged` 显示合并 commit；`conflict` 显示冲突文件与“转人工”。
- 不提供 force push / 删除分支按钮。

---

## 6. 数据层

### 6.1 TanStack Query key 规范

```ts
['workspace', wsId]
['documents', wsId, path?]
['document', wsId, documentId]
['search', wsId, query, filters]
['conversations', wsId]
['conversation', wsId, conversationId]
['run', wsId, runId]
['reviews', wsId, { kind?, period? }]
['review', wsId, reviewId]
['topics', wsId, topic]
['dashboard', wsId, section]
['settings', wsId]
['admin', 'users' | 'invites' | 'audit', page]
```

- `staleTime`：文档 30s、搜索 10s、复盘 60s、设置 0。
- 变更后精确失效；不做全量 `invalidateQueries()`。
- 乐观更新仅用于：发送消息、专题 confirm/resolve、任务勾选（若 UI 支持）；失败回滚并 toast。

### 6.2 SSE Hook

```ts
useRunStream({ runId, after, epoch, onEvent })
```

- 连接 `GET /runs/{runId}/events?after=&epoch=`；浏览器 `EventSource` 或 `fetch` 流。
- 事件：`run.state`、`tool.call`、`tool.result`、`citation`、`token`、`done`、`error`、`degraded`、`heartbeat`。
- `epoch` 不匹配：关闭流，`refetch(['run', runId])`，显示“运行已重启，正在重建”。
- 断线：指数退避 250ms→2s，最多 5 次；之后显示“连接中断，点击重连”。
- `token` 只追加到本地流式缓冲；完成后用 `messages` 最终内容替换。

### 6.3 错误与降级

- 全局 `ErrorBoundary` + 路由级 `error.tsx`；错误页显示 `request_id`。
- `DEGRADED` 是 200/206，不是错误；必须显示 `DegradedBanner`。
- 401 → 跳登录；403/404 → 页面级；429 → toast + `Retry-After`；5xx → ErrorState + retry。

---

## 7. Markdown 与引用

| 元素 | 规则 |
|---|---|
| 标题 | 保留层级；页面内生成 TOC |
| 表格 | 横向滚动；表头 sticky |
| 代码 | 只读、语言标签、复制按钮；不执行 |
| 图片 | 代理 + `loading=lazy`；外链不直接加载 |
| Wikilink | 解析为 `/documents/{id}`；断链标灰 |
| 引用 | `[n]` 可点击；与 CitationList 双向高亮 |
| 自动区域 | 只读展示；不提供“编辑生成内容”入口 |
| HTML | 禁止；sanitize 后仅保留安全标签 |

---

## 8. 可访问性与响应式

- WCAG 2.1 AA：正文对比度 ≥4.5:1；大字号 ≥3:1。
- 所有交互可键盘完成；焦点环 2px `ring-ring`；`Esc` 关闭浮层。
- `aria-live` 用于流式回答与 toast；避免逐字播报。
- `prefers-reduced-motion` 时禁用位移/缩放动画，只保留透明度。
- 触控目标 ≥44×44px；移动端底部导航。
- 表格有 `<caption>`/`scope`；图标按钮有 `aria-label`。
- 语言 `<html lang="zh-CN">`；中英混排使用 `lang` 标注。

---

## 9. 文案与 i18n

- 文案集中：`apps/web/lib/copy/zh-CN.ts`；组件不硬编码中文。
- 文案 key 采用 `domain.component.action`，如 `review.viewer.approve`。
- 错误文案：用户可读 + `request_id`；不暴露堆栈/SQL/token。
- 哨兵文案与后端一致（7 条）；前端不得改写。
- 日期/时间：`Intl.DateTimeFormat('zh-CN', { timeZone })`；相对时间用“3 分钟前”。

---

## 10. 设计系统治理

| 项 | 规则 |
|---|---|
| Token 单一来源 | `apps/web/styles/tokens.css` + `tailwind.config.ts`；组件禁止 hex |
| Primitive | 只从 `components/ui` 引用；不复制 shadcn 组件 |
| Composite | `components/` 下按域分目录：`documents/`、`search/`、`chat/`、`review/`、`topics/`、`settings/`、`admin/` |
| 组件 API | `forwardRef` + `cva` + `asChild`（需要时）；props 必须导出类型 |
| Storybook | 每个 composite 至少 3 个 story：default / loading / error |
| 视觉回归 | Playwright 截图；关键页面 desktop + mobile 两套 |
| 变更 | 新增 token/组件需更新本文件与 Storybook；删除需 codemod |

---

## 11. 前端验收清单

| # | 检查 | 通过标准 |
|---|---|---|
| FE01 | 登录/注册 | 邀请码错误、限流、密码错误均有可读提示 |
| FE02 | 文档树 | 1000 节点不卡顿；键盘可导航；空态/错误态 |
| FE03 | 搜索 | 高亮正确；四类过滤器可用；降级横幅可见 |
| FE04 | 对话 | SSE 流式；引用可点击；无依据显示哨兵；partial 标记 |
| FE05 | 工具轨迹 | 折叠/展开；失败步骤有错误码；不泄露正文 |
| FE06 | 复盘详情 | 章节顺序正确；来源路径可跳转；版本/状态可见 |
| FE07 | 降级 | degraded 横幅不可关闭；PR 首行标注 |
| FE08 | 专题页 | pending 待确认；stale 降权不隐藏；无删除历史 |
| FE09 | 交付状态 | PR 链接、合并 commit、冲突转人工 |
| FE10 | 空态/错误态 | 每个数据区都有；错误含 request_id |
| FE11 | 可访问性 | axe 无 critical；键盘全流程；对比度达标 |
| FE12 | 响应式 | 360/768/1280 三档无横向滚动 |
| FE13 | PWA | 可安装；离线壳可打开；不承诺离线生成 |
| FE14 | 文案 | 无硬编码中文；哨兵文案与后端一致 |
| FE15 | 主题 | 明暗切换无闪烁；token 全覆盖 |
| FE16 | 性能 | 首屏 LCP < 2.5s（本地/边缘）；路由切换 < 300ms |
| FE17 | 安全 | 无 `dangerouslySetInnerHTML` 未净化；外链图片走代理 |
| FE18 | 视觉回归 | 关键页面截图无意外 diff |
