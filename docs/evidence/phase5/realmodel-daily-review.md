---
mylife: true
kind: daily
period_key: 2026-11-09
review_id: 01a1bf3f-dc4e-738f-ae50-c8f775128c91
version: 1
snapshot_id: 01a1bf3f-d3d5-7c51-8782-ede16a8dbef6
generated_at: "2026-11-09T07:39:55Z"
generator: mylife/0.4
---

## 今日待办

### 已完成

- 修复向量检索超时

### 未完成

- 补写 HNSW 压测

### 以往顺延

- 对比 BGE-M3 与 nomic-embed-text 的召回（自 2026-11-08）
- 检查 delivery manifest（自 2026-11-08）

## 学习内容摘要

- 主题：向量检索
- 概念/方法：阅读并整理了 1 篇学习笔记
- 产出：学习/向量检索.md
- 待解决：待补充

## 日记事件摘要

- 11-09（日记/2026/11.09.md）

## 做错的事

1. 查询向量未缓存（日记/2026/11.09.md#L7）
   - root_cause：每次请求都重新 embedding
   - cost：3h
   - correct_action：加 256 条 LRU
   - recurrence_trigger：高频重试

## 正确方法论

1. 先量化再优化（日记/2026/11.09.md#L10）
   - source：实践
   - applicable_scenario：检索性能
   - steps：测基线、改一处、复测
   - boundaries：小数据量
   - landing_action：本周
   - verification_criteria：P95 下降

## 明日行动

明日行动：无（未找到可执行依据）

## 一句话总结

本日要点：### 已完成
