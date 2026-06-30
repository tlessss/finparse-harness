# FinParseAI 系统流程图

六张分层流程图 + 交互版总览。建议阅读顺序：

| # | 文件 | 内容 |
|---|------|------|
| 0 | [00-system-overview.md](./00-system-overview.md) | 鸟瞰：运行期 vs 构建期 + 认证闭环 |
| 1 | [01-runtime-pipeline.md](./01-runtime-pipeline.md) | 一份报告怎么跑（运行期主流程） |
| 2 | [02-field-routing.md](./02-field-routing.md) | 单字段：先路由、后冷启动 |
| 3 | [03-route-field-internals.md](./03-route-field-internals.md) | `route_field` 内部（选择即验证） |
| 4 | [04-triage-queue.md](./04-triage-queue.md) | 分诊队列（解析完记台账） |
| 5 | [05-heal-loop.md](./05-heal-loop.md) | 构建期自愈闭环（**详细导读**，含逐步拆解） |
| 6 | [06-persistence.md](./06-persistence.md) | 持久状态文件（数据存在哪） |

**产品规划**：[第二期规划](../第二期字段规划.md) — 字段扩展、公告监听与自动解读优先级。

交互版（可点击节点）：[`../system-flow.html`](../system-flow.html)

**三个核心名词**：`routed` = 已有认证解析器直接解对；`golden` = 标准答案；`exact` = 和 golden 完全一致才允许认证入库。
