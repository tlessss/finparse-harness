# LangGraph 自愈级联 demo（面试用）

> 用 **LangGraph** 把 FinParseAI 的生产自愈级联复刻成一张 `StateGraph`，**节点里调的是生产真实 agent**
> （不是 mock）：`verify_field`(DeepSeek 复核)、`_routed_reuse`(按选中表骨架复用认证解析器)、
> `steward_adjudicate`(通义 qwen 二次裁决)、`_parse_versioned`(冷启动解析)。
>
> 目的：证明我能用 LangGraph（JD 首选），**并且**能讲清「框架什么时候该用、什么时候手写状态机更好」。

---

## 跑起来

```bash
# 正常跑一份：冷启动→复核→(hold则)复用/选表自愈/管家 自然流动
PYTHONPATH=. python3 demos/langgraph_min/run.py 000785

# 演示 HITL + 状态机持久化：跑到【人审】前暂停(状态落 SQLite)→ 注入人工 approve → 从断点恢复入库
PYTHONPATH=. python3 demos/langgraph_min/run.py 300014 --force-human --approve
```

实测输出（300014，全程真实 agent）：
```
1. [parse_cold ] 冷启动解析 7 行 · 锚判=low
2. [heal0_reuse] 没命中认证解析器 → 交下游 healer
3. [heal_select] 选表自愈没选到更好的表 → 交管家
4. [steward    ] 通义 qwen 判 real_hold: table:wrong_table      ← 强模型真跑了
⏸  图在【人审】前暂停 —— 状态已落 SQLite checkpointer(进程杀掉也能恢复)
5. [human      ] 人工批准 → 入库
6. [commit     ] 入库 ✓ via=人工批准
✅ 终态 outcome=committed
```

---

## 图结构（节点 + 条件边 + 环）

```
        START
          │
     ┌─ parse_cold ─┐            冷启动解析 + 金额锚判(确定性)
   value?           conf=high ───────────────► verify (DeepSeek 复核)
   否→done                                        │ pass → commit
   conf=low ─────────────► heal0_reuse ◄──────────┘ hold
                              │ 命中 → commit
                              │ miss
                              ▼
                          heal_select (选表自愈)
                              │ 好 → commit
                              │ still_bad
                              ▼
                          steward (通义 qwen 裁决)
                              │ 假hold → commit
                              │ 真hold
                              ▼
                    ⏸ human  (interrupt_before + checkpointer)
                       approve → commit / reject → done
```

- **条件边**（`add_conditional_edges`）= 生产里 `_green_llm`/`_nongreen_llm` 那堆嵌套 if/else，现在是显式路由表。
- **checkpointer**（`SqliteSaver`）= 每步落盘，9 个 checkpoint/一条链，进程重启可从断点恢复。
- **interrupt_before=['human']** = 真人审闭环：执行到人审**之前**暂停 → 人处置 → `update_state` 注入决定 → `invoke(None, config)` 从 checkpoint 续跑。

---

## 生产 ↔ demo 对照（面试直接讲这张表）

| 生产 `src/pipeline.py` | 本 demo（LangGraph） |
|---|---|
| `run_field` 手写状态级联 | `StateGraph` + 条件边 |
| 到处 threading 的 `rec` dict | `HealState`(TypedDict) + `events` reducer |
| `_green_llm`/`_nongreen_llm` 的 if/else | `route_after_*` 条件边函数 |
| `enqueue` 分诊队列 + 人工再跑 | `interrupt_before` + `update_state` 恢复（真暂停-恢复） |
| `run_full_pass(resume=)` 报告级跳过 | checkpointer 节点级续跑 |
| `emit_event` | `events` reducer / `graph.stream()` |
| `_parse_versioned`/`verify_field`/`_routed_reuse`/`steward_adjudicate` | **原样复用**（节点直接 import 调用） |

---

## JD 关键词命中（欧凯斯 AI Agent）

| demo 里的东西 | JD 命中 |
|---|---|
| `StateGraph` + 条件边 + 环 | **LangGraph（首选硬性）** |
| `interrupt_before=['human']` | HITL 工作流（加分） |
| `SqliteSaver` checkpointer | 状态机持久化（加分） |
| `verify_field`=DeepSeek / `steward`=通义 qwen | 国产模型 API + 弱/强双模型路由 |
| verify agent 结构化 JSON 裁决 | Function Calling / Tool Use |
| `HealState` TypedDict + reducer | Pydantic/类型化状态 |
| `events` 事件流 | 可观测（对齐 Redis Streams 事件流设计） |

---

## 面试杀招：**「我两种都做了，以及框架何时该用」**

> 生产链我用**手写状态级联 + 双闸 enforce**（金额锚 + LLM 复核两把尺都过才入库），因为这条链的
> **主体是确定性解析**（抽表/选表/版本池/跨页拼），LLM 只是兜底，套 agent 编排框架的开销划不来、
> 还挡在我和「正确性闸」中间。
>
> 但我**也用 LangGraph 把同一条自愈级联复刻了一遍**，吃它三样我手写要额外造的东西：`interrupt` 做
> 人审闭环、`checkpointer` 做节点级断点续跑、条件边把级联路由显式化。
>
> 所以我既能用这个业界标准，也知道**它什么时候值、什么时候纯 Python 更干净**——
> 关键看「LLM 编排是不是主体」。欧凯斯给 200 人运营团队做 Agent，多 Agent 协作 + 人审 + 记忆分层
> 是主体，**那正是 LangGraph 该上的场景**，我直接迁移这套。

> **为什么这段能加分**：大多数候选人只会「我用了 LangGraph」。「我知道何时**不**用框架」是中高级和初级的
> 分水岭，命中 JD 软技能「需求拆解为技术方案（不只写代码）」。

---

## 可能被追问 + 答法

- **Q：状态很大时 checkpointer 会不会爆？** A：State 只存指针/摘要（code/outcome/verdict），大对象
  (表网格/PDF)放外部缓存，节点按需取——demo 的 `HealState` 就没塞原始表。
- **Q：条件边里有环，会不会死循环？** A：生产靠 `healers_tried` 去重 + 双闸单调收敛；LangGraph 侧可
  设 `recursion_limit`。demo 每个 healer 只走一次，天然无环。
- **Q：为什么不把整条 `run_field` 都搬 LangGraph？** A：确定性解析段搬进去只加仪式感、不加能力，还把
  双闸挡在框架后面；我只把**真正图形化的自愈级联**用它，解析/锚闸留普通 Python。
- **Q：多 Agent 怎么扩？** A：每个 healer/裁判是一个节点(子图)，新增 agent = 加节点 + 加条件边，
  State 加字段；这正是我留 A（图形化自己代码）当地基的原因。

---

## 文件

- `graph.py` — `HealState` + 节点(调真实 agent) + 条件边 + `build_graph(checkpointer, force_human)`
- `run.py` — CLI：正常跑 / `--force-human --approve` 演 HITL；SqliteSaver 持久化
- `checkpoints.sqlite` — 运行后生成的状态机持久化 DB（可删，跑一次自动重建）

> ⚠️ 依赖：`pip install langgraph langgraph-checkpoint-sqlite`。commit 节点默认 dry-run(不写库)。
