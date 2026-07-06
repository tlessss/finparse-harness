---
name: update-pipeline-flow
description: >-
  以 src/pipeline.py 为准，同步更新控制台解析流程图
  frontend/src/app/console/PipelineFlow.tsx（/console/flow）。当用户说更新流程图、
  同步 pipeline 图、流程和代码不一致、refresh console flow、或以代码为准改流程图时使用。
---

# 更新控制台解析流程图（/console/flow）

## 目标页面

| URL | 组件 | 说明 |
|-----|------|------|
| `http://localhost:3000/console/flow` | `frontend/src/app/console/PipelineFlow.tsx` | **本 skill 维护** — 单字段 `run_field` 决策链 |
| `/console/workflow` | `frontend/src/app/console/WorkflowGraph.tsx` | 全量批处理 + 自愈注册表轨（另图，用户明确要求时才改） |

用户说「整个系统流程图」且指批处理/1289 份 PDF → 改 `WorkflowGraph.tsx`；默认指 `/console/flow`。

## 真源（必须读代码，禁止凭记忆）

按顺序读：

1. **`src/pipeline.py`** — `run_field`、`_green_llm`、`_nongreen_llm`、`_heal_and_verify`、`_rule_heal_and_verify`、`heal_select`
2. **`src/console_service.py`** — `judge_chat` 确定性闸门（`_NO_AUTOHEAL_ROOT`）
3. **Agent 实现**（节点 `agent` 字段）：`llm_judge.verify_field`、`select_table_agent`、`judge_diagnose_agent`、`rule_heal`（若有）
4. **`docs/Agent深度化-执行计划.md`** — 仅作「现状 vs 目标」备注，**不以文档覆盖代码**

详细「函数 → 节点 id」映射见 [reference.md](reference.md)。

## 更新流程

复制此清单并逐项勾选：

```
- [ ] 1. 运行 drift 检查（可选 baseline）
- [ ] 2. 从 pipeline.py 列出分支与 outcome
- [ ] 3. 对比 PipelineFlow.tsx 的 NODES / LINKS
- [ ] 4. 改节点 label / sub / detail / agent（布局坐标尽量不动）
- [ ] 5. 增删 LINKS（分支标签与 tone 一致）
- [ ] 6. 更新页眉 subtitle（一行概括当前链）
- [ ] 7. 再跑 drift 检查
- [ ] 8. 浏览器打开 /console/flow 目测
```

### Step 1：Drift 检查

```bash
cd /Users/admin/formal/FinParseAI
python3 .cursor/skills/update-pipeline-flow/scripts/check_flow_drift.py
```

输出 `MISSING_IN_FLOW` / `EXTRA_IN_FLOW` / `PIPELINE_FUNCS` 供对照。

### Step 2：从代码提取决策树

以 `run_field(use_llm=True)` 为准，当前主干（2026-07）：

```text
no_input → route_field(routed?) → [routed 绿灯 | cold _parse_versioned]
  → no_data / no_anchor / green / non_green
green + use_llm → verify_field → pass→commit | wrong_table→heal | hold→human
non_green + use_llm → _nongreen_llm:
  → heal_select 先试 → no_pick→no_such_table | 过锚→re-verify→commit/hold
  → still_bad→rule_heal→gate→re-verify | 修不了→judge_diagnose→human/L3
```

**易漏分支**（每次更新必核对）：

- 非绿灯 **先选表再诊断**（`_nongreen_llm` 开头即 `_heal_and_verify`）
- `no_such_table` 与 `verify_hold` 区分
- L2 `rule_heal` 与 `d_gate`（过锚闸）仅在 `still_bad` 路径
- `rule_code_diagnose` **未接入** `run_field` 时不要画进主链（可写在 diag 节点 detail 里）

### Step 3：改 PipelineFlow.tsx

文件结构（只改数据区，除非新增节点才调坐标）：

```typescript
// 类型：Kind, Node, Link — 勿改除非新增 kind
const NODES: Node[] = [ ... ];  // id, label, sub, kind, x, y, w, h, detail, agent?
const LINKS: Link[] = [ ... ];  // s, ss, t, ts, label?, tone?
```

**节点字段规范**

| 字段 | 要求 |
|------|------|
| `label` | 用户可见短标题（含步骤号可选） |
| `sub` | 函数名 / agent 名 |
| `detail` | 1–3 句，与代码行为一致；写清 outcome |
| `agent` | 仅 LLM 节点：`verify` / `select_table` / `judge_diagnose` / `rule_heal`（与 `/agents/{id}` API 一致） |
| `kind` | `stage` / `decision` / `verify` / `heal` / `commit` / `human` / `exit` |

**连线规范**

- `tone: "yes" | "commit"` — 绿色通过
- `tone: "no" | "human"` — 红色/人工
- 分支 `label` 与 pipeline outcome 字符串一致（如 `wrong_table`、`still_bad`、`no_pick`）

**布局**：已有网格 `XDEAD/XMAIN/XHEAL/XMID/XTERM` 与 `Y[]`。新增节点时复制相邻节点坐标微调；避免大改以免 diff 难 review。

### Step 4：页眉与侧栏文案

同步更新：

- `<h2>` 标题下 `<p className="text-xs">` 一行摘要
- 侧栏默认说明（无选中节点时的 `<aside>` 段落）

### Step 5：验收

1. `check_flow_drift.py` 无关键 MISSING
2. 本地 `cd frontend && npm run dev`，打开 `/console/flow`
3. 点击各 LLM 节点，确认右侧能拉 `/agents/{agent}` prompt（需 API `:8200`）
4. 与 `PipelinePanel` 上展示的链路不矛盾

## 不要做的事

- 不要把 `workflow.py`（LangGraph 整 PDF 图）混进 `PipelineFlow`
- 不要编造 batch 统计数字进 `detail`（WorkflowGraph 才放 metric）
- 不要改 `PipelineFlow.tsx` 的 SVG 渲染逻辑，除非修复 bug
- 未接主链的能力（如 `rule_code_diagnose` 自动编排）标在 detail 或保持不出现在主链

## 关联 skill

- 画**独立 HTML** 全架构图 → `.agents/skills/flow-visualizer`
- 校验解析准确度 → `.cursor/skills/validate-parse-report`

## 脚本

| 脚本 | 用途 |
|------|------|
| [scripts/check_flow_drift.py](scripts/check_flow_drift.py) | 对比 pipeline 函数与流程图节点 id |
