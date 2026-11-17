## spec书写规范

## **Task: [一句话目标]**

## 1. 目标做什么、解决什么（1-2 句）
## 2. Acceptance Criteria
- [ ] 用户能 ...
- [ ] 当 ... 时，系统 ...
- [ ] 边界场景：...（必须可枚举可测试）
## 3. 涉及文件

- frontend/src/modules/pim/products/List.tsx (新建)
- backend/app/modules/pim/products/router.py (修改)（明确列出，不是"相关文件"）

## 4. API / 数据契约请求：

POST /api/pim/products

body: { name: string, code: string, ... }
响应：{ id, name, ... }（精确字段名 + 类型）

## 5. 参照已有代码

- 类似 CRUD：backend/app/modules/org/users/router.py
- 列表页：frontend/src/modules/org/UserList.tsx

## 6. 约束（不要做的）

- 不修改 shared/core 目录
- 不引入新依赖- 不修改其他模块文件

## 7. Out of Scope

- 批量导入（下个任务）
- 审核流接入（下个任务）- 国际化（暂时只中文）

## 8. 测试要求

- 后端：service 单测 + router 集成测试（testcontainer PG）
- 前端：list/form 组件 snapshot + 关键交互测试
- 至少覆盖 §2 中所有"边界 / 错误"场景

估时1 天（spec 评审 + Claude 实现 + 自测 + PR review + 修改）

**Dev 不需要从零写 spec**：可以让 Claude 基于 DATA-MODEL + 任务名生成 spec 草稿，dev 跟业务对齐补全 acceptance criteria，再交 Tech Lead 评审。
