# 复盘能力评测（review eval）

这是一套**面向“复盘能力”而非单点函数**的测试集与评分器：同一套语料既检查
确定性部分（Todo 台账、日期、G0–G9、章节装配、月/年 rollup），也检查真实模型
的语义抽取质量（错误/方法论召回、字段完整、证据可追溯、注入安全）。

所有判定都直接调用生产代码，不复制实现：

| 能力 | 生产入口 |
|---|---|
| Todo 文法 / 归一化 | `mylife_agent.task_ledger` |
| 业务日期 | `mylife_worker.markdown.resolve_occurred_on` |
| 门禁 G0–G9 | `mylife_agent.gates` / `mylife_agent.reviewer` |
| 章节装配 | `mylife_api.review_composer` |
| 月/年 rollup | `mylife_api.review_runner` 的 `_recurring` / `_learning_map` / ... |
| 语义 Writer | `mylife_agent.review_writer`（提示词、解析、证据绑定） |
| 提示注入 | `mylife_agent.injection` |
| 渲染确定性 | `mylife_delivery.renderer` |

## 快速开始

```bash
# 根目录；离线（不需要数据库 / 模型），适合 CI
uv run python evals/review/run_eval.py --fail-under 90

# 加上真实模型语义抽取（读取 MYLIFE_LLM_*）；建议每用例 3 次采样
uv run --env-file .env python evals/review/run_eval.py --llm --repeat 3 --timeout 180
```

或使用 Makefile：

```bash
make eval-review       # 离线，总分低于 90 失败
make eval-review-llm   # 真实模型，总分低于 80 失败
```

结果写入（`--out` 可覆盖）：

- `docs/evidence/review-eval/summary.json`：逐用例、逐检查项的完整结果；
- `docs/evidence/review-eval/report.md`：人类可读报告；
- `make eval-review` 写到 `docs/evidence/review-eval-offline/`，避免覆盖真实模型报告。

## 语料构成

### 语义抽取（`corpus.SEMANTIC_CASES`，11 条）

| 用例 | 考察点 |
|---|---|
| S01 显式事故 | 有“发布/备份”事实时能否抽出错误 + 方法论 |
| S02 隐式叙述 | 没有“做错的事/正确方法论”标题时，能否按语义识别 |
| S03 标题干扰 | 正文说“没有做错的事”，不应硬凑错误 |
| S04 复述去重 | 同一事故换两种说法，只应输出一条 |
| S05 空日 | 中性内容不得产出错误/方法论 |
| S06 单条注入 | 注入与真实内容同段时整段隔离，不进入模型 |
| S07 注入隔离 | 一条笔记注入、另一条正常，正常内容仍应抽取 |
| S08 伪定界符 | `<<<END>>>` 等伪证据边界必须拦截 |
| S09 多条目 | 两个不同事故 + 两个方法论，不得合并或漏抽 |
| S10 成功经验 | 成功叙事不应被误判为错误，方法论应保留 |
| S11 长段落尾部 | 错误位于候选 800 字符截断之后，暴露当前召回边界 |

金标按**关键词集合**匹配（`match_all` 全含 / `match_any` 至少一个），
避免逐字比对造成假阴性；每个金标 item 还声明 `required_fields`，用于检查
“字段是否被真实填写而不是占位符”。

### 离线检查

| Suite | 内容 |
|---|---|
| `todo_ledger` | 复选框文法（`[x]` / `[ ]` / `[-]` 三态）、fenced code、非法标记、自然语言、行号偏移、空白折叠、标点归一化 |
| `date_resolution` | frontmatter / 目录 / 文件名优先级、短日期补年份、冲突与非法 warning |
| `gate_enforcement` | G0 租户泄漏、G1 章节/ordinal、G2 schema、G3a–G3d 快照/哈希/引用、G4 追溯、G5 引用闭包、G6 行动可执行、G7 哨兵、G8 覆盖缺口、G9 路径白名单、证据上限 |
| `action_generation` | 错误/方法论的 `correct_action` / `landing_action` 能否落地为明日行动与下月行动（FR-E6/E7） |
| `assembly` | 日/月/年章节集合、ordinal、schema、引用闭包、行动格式、空日哨兵、`today_tasks` 三态（已完成/未完成/暂停中）、ledger 一致性 |
| `rollup` | 重复错误次数与首末日期、Top mistakes 排序、方法论落地、年度学习地图/事件线/方向 |
| `determinism` | 同一输入重复装配、渲染、门禁的指纹一致 |
| `injection_scanner` | 8 类注入模式与 `<<<` 定界符中和 |

## 评分

维度权重（总分 100）：

| 维度 | 权重 |
|---|---:|
| 语义抽取 F1 | 25% |
| G0–G9 门禁 | 15% |
| Todo 台账 | 10% |
| 月/年 rollup | 10% |
| 提示注入安全 | 10% |
| 行动清单生成 | 5% |
| 章节装配与哨兵 | 5% |
| 字段完整率 | 5% |
| 证据可追溯率 | 5% |
| 日期解析 | 5% |
| 确定性 | 5% |

- 语义 F1 = 错误 F1 与方法论 F1 的平均；P/R 按用例 micro 汇总。
- 字段完整率的分母包含**所有金标 item** 的必填字段，漏抽会同时拉低该项。
- 可追溯率 = 模型条目被证据绑定接受的比例（同 `canonical_key` 的主动去重不计入分母），
  与 `source_path + line_range` 落在原文范围内的比例之平均。
- 未启用 `--llm` 时，语义/字段/可追溯不参与总分，权重按剩余维度归一化。
- `--repeat N` 对每个语义用例采样 N 次，P/R/F1 用 micro 汇总；默认 1，建议基准用 3。

## 当前基线（2026-11-10，`auto/minimax-m3.1-flash`，每用例 3 次采样）

| 指标 | 结果 |
|---|---|
| 总体 | **93.14 / 100** |
| 语义抽取 F1 | 88.77% |
| 错误 P / R | 94.12% / 76.19% |
| 方法论 P / R | 100% / 87.50% |
| 字段完整率 | 94.03% |
| 证据可追溯率 | 100% |
| 行动清单生成 | 25%（1/4） |
| 注入安全 | 3/3 |
| 其余离线 suites | 全部 100% |
| 用量 | 21,573 prompt / 10,985 completion tokens，约 117s |

相同配置重复运行总分约在 **93 分上下 1 分**波动，主要来自 S09 的合并与 S11 的截断/偶发假阳性。

失分按影响排序：

1. **行动清单生成（FR-E6/E7）**：这是最大的能力缺口。错误条目的 `correct_action`
   和方法条目的 `landing_action` 已经是可执行依据，但流水线没有把它们转成
   `tomorrow_actions` / `next_month_actions` 条目；`_action_section` 只接受
   `item_type="action"`，而生产代码从未生成 action 条目，因此两处稳定落到
   “无（未找到可执行依据）”哨兵。正向对照（显式 action 条目）可以正常渲染，
   说明是“生成”缺失而不是“渲染”缺陷。
2. **S09**：模型把两次都与“测试”有关的独立事故合并成一条（共享同一根因），
   导致错误召回 1/2。这是“不同事件不得因共享根因而合并”的提示词边界；
   现有 Writer 系统提示只写了“同一条错误只输出一次”，没有反向约束。
3. **S11**：单段 800+ 字的笔记里，错误位于候选 `content[:800]` 截断之后，
   模型只看到前置背景：三次采样中两次返回空、一次返回一条无金标支撑的条目，
   召回 0 且出现一个假阳性。这是当前分块/截断策略的真实边界。
4. **字段完整率 63/67**：个别采样对已匹配条目留下了占位符字段，
   说明“字段必须来自原文或明确标注未提及”的约束仍会被模型忽略。

## 已知限制

- 语义评测依赖外部模型，结果会随模型与采样变化；报告记录模型名、token 与延迟。
- 金标是合成语料，覆盖典型能力边界，不替代 `docs/evidence/phase3/sampling.md`
  要求的真实用户语料人工复核。
- 规则类金标（如“两个事故必须两条”）反映产品期望，不是对当前实现的背书；
  分数用于回归比较，发现下降应定位到具体用例与检查项。
- 新增用例时，请同时给出 `match_all` / `match_any` / `required_fields`，
  否则会把表述差异误判成能力缺陷。
