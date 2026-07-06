# Agent 深度化执行计划

> 基于 Prompt Registry + 两阶段 agent（`judge_diagnose` / `rule_code_diagnose`）的后续迭代。  
> 目标：**加深 LLM 使用深度 = 案件快照 + 统一解析 + 表层证据加长 + 跨 agent 结构化交接**，而非单纯堆 token。

---

## 一、背景

### 已完成（commit `2731c5c` 及后续）

| 模块 | 路径 |
|------|------|
| Prompt Registry | `src/prompts/`（7 套 YAML + Context Pack） |
| 第一阶段 agent | `judge_diagnose` — 口径差 / 选表 / 跨页 |
| 第二阶段 agent | `rule_code_diagnose` — 规则 + 代码（骨架） |
| 选表 agent | `select_table_agent` — 复核判 wrong_table 时自动重选 |
| 生产向编排 | `src/pipeline.py` — `run_field(use_llm=True)` 自动分支 |
| 持久化 | `pipeline_runs`（`test_store.py`）— chain + verify 结论 |
| 批处理 UI | `PipelinePanel` — 成功率、链路展示、选表自愈二次流程 |
| 调试台 | `JudgeTest` — meta、decision/root_cause、手动进第二阶段 |

### 当前编排现状（代码，2026-07）

系统已有**两条链路**，不要混为一谈：

#### A. Pipeline / 批处理（已是动态编排，非 LangGraph）

`run_field(code, year, field, use_llm=True)` 在 [`src/pipeline.py`](src/pipeline.py) 内**自动**路由，无需人点按钮：

```text
route_field 命中 → green
     ↓ use_llm
  verify_field（复核 agent）
     ├─ pass → _auto_commit 入库
     ├─ suspects 含 wrong_table → heal_select（select_table_agent）
     │       → forced 重解析 → verify 第二次 → pass 则入库，否则 verify_hold + enqueue
     └─ 其它 hold → verify_hold + enqueue 分诊队列

冷启动 parse → field_plausibility
     ├─ high → green →（同上 verify 链）
     └─ non_green → _nongreen_llm()
            → 自动 prepare_judge_diagnose + judge_chat
            → judge_chat 内 _NO_AUTOHEAL_ROOT 确定性改判 handoff_human
```

要点：

- **verify → 选表自愈 → re-verify** 已在 pipeline 内闭环，PipelinePanel 展示「① 第一次流程 / ② 选表自愈第二次流程」。
- **non_green → judge_diagnose** 已自动调用；结论写入 outcome / triage_queue，不是纯调试台行为。
- **rule_code_diagnose 尚未接入** `run_field` 自动链；`next_action=rule_code_diagnose` 时仍靠 JudgeTest 手动「进入第二阶段」。
- 编排是 **Python 分支 + agent 函数调用**，不是 LangGraph；[`workflow.py`](src/agents/workflow.py) 仍是独立 parse 图，未接到 judge/verify 主链。

#### B. JudgeTest / 各 Debug Tab（偏手动调试）

- 人点「准备对话 / 发送」；可编辑 messages 后再调 LLM。
- 第二阶段 `rule_code_prepare` 通过 query 传 stage1 片段，无完整 `stage1_report` JSON body。
- 各 Tab **可能各自重 parse**，尚无统一 `run_id` 快照（Phase 1 目标）。

#### 与目标架构的差距（深度化要补的）

| 能力 | Pipeline 路径 | 调试台路径 | 目标 |
|------|---------------|------------|------|
| 动态路由 | ✅ `run_field` 分支 | ❌ 人驱动 | 统一 Steward |
| 跨步状态 | ✅ `pipeline_runs` | ❌ 分散 | + `parse_runs` + `run_id` |
| verify↔选表自愈 | ✅ 自动 | — | 保持 |
| non_green→judge | ✅ 自动 | 可手动 | 加深上下文（Phase 2） |
| judge→rule_code | ❌ 未自动 | ⚠️ 手动按钮 | Phase 3 + pipeline 接线 |
| 事件流 | ❌ | ❌ | Phase 4 |

### 核心问题

1. **单 agent 喂料偏薄**：候选只有 meta 行，邻近页只有摘要，LLM 窗口利用率低。
2. **案件不串联**：各 Tab 可能各自重 parse；无 `run_id` 快照。
3. **跨 agent 交接薄**：第二阶段只收 4 个 query 参数，无完整 stage1 报告。
4. **无事件流**：prepare/chat 结论无法按时间线回溯。

### 设计原则

- **该多的阶段多，该少的阶段少**：表层 agent 加长表证据；规则/代码只在第二阶段。
- **LLM 不能单干**：确定性筛子 + Steward 闸门不变。
- **不用 Redis**：SQLite（`test_store.db`）存快照与事件。

---

## 二、目标架构

**现状（已有）** — Pipeline 自动链：

```text
run_field(use_llm=True)
  → routed/cold → verify | judge_diagnose
  → [wrong_table] heal_select → reparse → re-verify → commit | human
  → save pipeline_runs
```

**目标（深度化后）** — 调试台与 Pipeline 共用案件快照 + 完整交接：

```text
③ parse_debug / resolve_field_result
        ↓ save run_id
   parse_runs (SQLite)  ← 与 pipeline_runs 并存
        ↓
   judge_diagnose (表层，加长网格)
        ↓ stage1_report JSON
   rule_code_diagnose (规则/代码)  ← 接入 pipeline 自动链（可配置）
        ↓
   process_events (append-only)
        ↓
   Console / PipelinePanel（统一时间线）
```

---

## Phase 1 — 统一解析 + 案件快照（P0，1–2 天）

### 1.1 新建 `resolve_field_result()`

- **文件**：`src/console/field_resolver.py`
- **逻辑**：从 `parse_debug` 抽出，**路由优先 → 冷启动兜底**（与生产一致）
- **返回**：
  ```python
  {
    "value", "provenance", "source": "routed"|"cold_start",
    "parser_key", "routed", "sig", "anchor", "dims", "page", "error?"
  }
  ```

### 1.2 SQLite：`parse_runs` 表

- **文件**：`src/eval/test_store.py`
- 与 `pipeline_runs` 并存：后者记 outcome；前者记**解析快照**
- **API**：`save_parse_run(...) -> run_id`，`get_parse_run(run_id)`

### 1.3 改造 prepare 读 `run_id`

| 函数 | 改动 |
|------|------|
| `parse_debug` | 返回 `run_id` |
| `prepare_judge_diagnose` | 有 run_id 不重 parse |
| `prepare_rule_code_diagnose` | 同上 |
| `heal_debug` / `gather_diagnose_context` | 消除二次 parse |

### 1.4 前端传 `run_id`

- `Testing.tsx` / `ParseTest.tsx` / `JudgeTest.tsx`
- URL：`?run_id=42`；无 run_id 时黄色提示

**验收**

- 300009：③ parse → ④ judge，messages 内 JSON 与 parse 一致
- `tests/test_field_resolver.py`

---

## Phase 2 — 加深第一阶段上下文（P1，1 天）

**只加深表层证据，不塞 YAML/代码。**

### 2.1 扩展 Context Pack（`src/prompts/context/table.py`）

| 变量 | 现状 | 目标 |
|------|------|------|
| `table_preview` | 35×10 | 保持或略增 |
| `neighbor_tables` | 摘要行 | + 邻近页关键表各 **15 行网格** |
| `candidates` | 一行 meta | **top2 各 10 行预览** + meta |
| `next_page_table` | 部分已有 | 选中页**下一页**同主题表网格 |

### 2.2 更新 `judge_diagnose.yaml`

- 增加占位符段落
- 硬约束：**不得推断规则/代码问题**

### 2.3 Token 预算

- 第一阶段 user：**8k–15k 字符**（约 4k–8k token）
- 超长：候选只保留 top2 网格

**验收**

- 300009 prompt 含 p22/p23 网格
- `tests/test_prompts_judge_diagnose.py`

---

## Phase 3 — 跨 Agent 结构化交接（P1，1 天）

### 3.1 `stage1_report` 契约

```python
{
  "run_id": 123,
  "decision", "root_cause", "next_action", "confidence",
  "summary", "evidence": [...],
  "pick_page", "cross_page_suspect", "missing_dims"
}
```

### 3.2 后端

- `judge_chat` 返回完整 `stage1_report`
- `rule_code_prepare` 接受 JSON body（非 4 个 query）
- `rule_code_diagnose.yaml` 渲染 `{{stage1_report_json}}`

### 3.3 独立第二阶段 chat

- `POST /debug/rule_code/chat` + `rule_code_chat()`
- 历史 tag：`field|rule_code`

### 3.4 前端

- 进入第二阶段 POST `stage1_report`
- 展示 `root_cause_layer` / `minimal_fix` / `fix_json`

**验收**

- 第二阶段 user 含完整 stage1 证据

---

## Phase 4 — 事件流 + Steward + UI（P2，1–2 天）

### 4.1 `process_events` 表

- 字段：`run_id, ts, agent_id, event_type, payload_json`
- 事件：`parse_saved | stage1_prepare | stage1_chat | stage2_prepare | stage2_chat | apply_fix`

### 4.2 薄 Steward（确定性）

- **文件**：`src/case_steward.py` 或扩展 `pipeline.py`
- 读 run_id + events → `next_step`
- 例：`human_review` 不入修复队列；`rule_code_diagnose` 才开放 stage2

### 4.3 PipelinePanel

- case 时间线 + agent 结论卡片
- 与 AgentsPanel 联动（template 版本）

**验收**

- 一次完整流程 UI 可见 4+ 条 event

---

## Phase 5 — Pipeline 与案件快照对齐（P2）

> **注意**：`run_field(use_llm=True)` 的 verify / judge_diagnose / heal_select **已实现**，本节不是「首次接 LLM」，而是把 Phase 1–4 成果并进生产链。

- `run_field()` / `_green_llm` / `_nongreen_llm` 改用 `resolve_field_result()`，消除与调试台 parse 不一致
- `_nongreen_llm`：当 `next_action=rule_code_diagnose` 且配置允许时，自动 `prepare_rule_code_diagnose`（仍**不**自动 apply_fix）
- `field_chain()` / `save_run` 写入 `run_id`，与 `parse_runs` 关联
- Steward（Phase 4）作为 pipeline 与调试台的统一 `next_step` 出口
- **不做**：批处理自动 apply_fix；LLM 改规则写库

---

## 三、排期

| 阶段 | 内容 | 优先级 | 预估 |
|------|------|--------|------|
| Phase 1 | 统一解析 + run_id | **P0** | 1–2d |
| Phase 2 | 加深 judge_diagnose | P1 | 1d |
| Phase 3 | stage1_report + rule_code chat | P1 | 1d |
| Phase 4 | events + Steward + UI | P2 | 1–2d |
| Phase 5 | pipeline 与 run_id/Steward 对齐 | P2 | 1d |

**推荐顺序**：1 → 2 → 3 → 4 → 5（Phase 5 依赖 1、4；可与 2、3 部分并行）

---

## 四、刻意不做

- Redis
- 单 agent 同时判表 + 改规则 + fix
- LLM 结论自动写生产库（除 decision=ok 且配置允许）
- 整本 PDF 塞进一个 prompt

---

## 五、面试一句话

> 生产侧已有 Python 动态编排（verify→选表自愈→re-verify、non_green→judge）；深度化是在此之上补**案件快照（run_id）**、**加长表层证据**、**stage1→stage2 结构化交接**与 **event 时间线**——让调试台与 Pipeline 共用同一套 State，而不是再堆 token 或另起一套 LangGraph。

---

## 六、关键文件

| 操作 | 路径 |
|------|------|
| 新建 | `src/console/field_resolver.py` |
| 新建 | `src/case_steward.py` |
| 改 | `src/eval/test_store.py` |
| 改 | `src/agents/judge_diagnose_agent.py` |
| 改 | `src/agents/rule_code_diagnose_agent.py` |
| 改 | `src/console_service.py`, `src/api.py` |
| 改 | `src/prompts/context/table.py`, `templates/judge_diagnose.yaml` |
| 改 | `src/pipeline.py`（Phase 5：run_id + rule_code 可选自动链） |
| 改 | `frontend/.../ParseTest.tsx`, `JudgeTest.tsx`, `PipelinePanel.tsx` |
| 已有 | `src/agents/select_table_agent.py`, `src/pipeline.py`（verify/heal_select/_nongreen_llm） |
| 未接主链 | `src/agents/workflow.py`（LangGraph parse 图，独立） |
| 新建 | `tests/test_field_resolver.py`, `tests/test_prompts_judge_diagnose.py` |
