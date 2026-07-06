# pipeline.py → PipelineFlow 节点映射

> 更新流程图时对照此表。`id` 为 `PipelineFlow.tsx` 中 `NODES[].id`。

## 主链

| 节点 id | pipeline / 模块 | 说明 |
|---------|-----------------|------|
| `scan` | `get_tables` + `_pdf` | `run_field` 入口 |
| `no_input` | `outcome: no_input` | 无表或无 PDF |
| `d_route` | `route_field` | `status == routed` |
| `cold` | `_parse_versioned` | base 优先 + 规则版本池 |
| `no_data` | `outcome: no_data` | 冷启动无解 |
| `d_anchor` | `field_plausibility` | `confidence == high` |
| `no_anchor` | `outcome: no_anchor` | 无 `anchor_key` |

## 绿灯 + LLM（`_green_llm`）

| 节点 id | 函数 / agent | 说明 |
|---------|--------------|------|
| `verify` | `verify_field` | agent: `verify` |
| `d_verify` | verdict 分支 | pass / wrong_table / hold |
| `t_commit` | `_auto_commit` | outcome: committed |
| `t_human` | `enqueue` | outcome: verify_hold |

## 选表自愈（`_heal_and_verify` / `heal_select`）

| 节点 id | 函数 / agent | 说明 |
|---------|--------------|------|
| `heal` | `heal_select` → `select_table_llm` | agent: `select_table` |
| `d_heal` | heal outcome | green/caliber_gap/still_bad/no_pick |
| `t_nosuch` | `no_such_table` | `_nongreen_llm` 或 heal no_pick |
| `verifyT` | `verify_field` + `_TRUST_NOTE` | 自愈后二次复核 |
| `d_verifyT` | re-verify verdict | |
| `t_commitH` | committed after heal | |
| `t_humanT` | verify_hold after heal | |

## L2 改规则（`_rule_heal_and_verify`）

| 节点 id | 函数 / agent | 说明 |
|---------|--------------|------|
| `rule` | `_rule_heal_and_verify` | agent: `rule_heal`（若 API 有） |
| `d_gate` | 合并 delta 后过锚 | 不过锚不采纳 |

## 诊断（`_nongreen_llm` 末尾）

| 节点 id | 函数 / agent | 说明 |
|---------|--------------|------|
| `diag` | `prepare_judge_diagnose` + `judge_chat` | agent: `judge_diagnose` |
| `t_diag` | handoff / rule_code 手动 | **rule_code 未进 pipeline 自动链** |

## 非绿灯入口（易漏）

代码路径：`d_anchor` 否 → `outcome: non_green` → `_nongreen_llm`：

1. 先 `_heal_and_verify`（与 verify wrong_table 共用 heal 子图）
2. `no_pick` → `t_nosuch`
3. `still_bad` → `rule` → `d_gate`
4. 其余 → `diag`

流程图应从 `d_anchor` 的「否」边进入 `heal`，且 heal 失败且非 still_bad/no_pick 时应有到 `diag` 的边（若代码有该分支）。

## Outcome 枚举（detail 里写准）

| outcome | 含义 |
|---------|------|
| `no_input` / `no_data` / `no_anchor` | 确定性出口 |
| `green` | 过锚（use_llm 前） |
| `committed` | 复核 pass 入库 |
| `verify_hold` | 复核 hold |
| `non_green` | 未过锚（use_llm 前） |
| `no_such_table` | 全表无构成表 |

## 另一张图：WorkflowGraph.tsx

批处理轨：`autonomous_run` / `engine.run` / hard_rules / deadletter  
自愈轨：`heal_pipeline` / registry / review  

仅当用户明确要更新 `/console/workflow` 时修改；字段为 `status: live|partial|frontend|design` 与 `mock` 指标。

## 文件路径

```
src/pipeline.py                          # 真源
frontend/src/app/console/PipelineFlow.tsx  # /console/flow
frontend/src/app/console/flow/page.tsx     # 入口（通常不改）
frontend/src/app/console/WorkflowGraph.tsx # /console/workflow
docs/交接-工作流图修正.md                   # WorkflowGraph 历史修正单
docs/Agent深度化-执行计划.md                # 编排现状说明
```
