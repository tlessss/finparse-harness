# AI Agent 工程师 — 杭州（待投递）

> **计划投递日**：2026-07-06（周六，自记录日起 +2 天）  
> **JD 截图**：`.cursor/projects/Users-admin-formal-FinParseAI/assets/image-63f63765-29e7-4156-bee5-f23ccc9c97d1.png`  
> **记录日**：2026-07-04  
> **状态**：`待投递`

---

## 岗位概要

| 项 | 内容 |
|---|---|
| 职位 | AI Agent 工程师 / Agent 应用开发工程师 |
| 薪资 | 20–50K |
| 地点 | 杭州 · 钱塘区 |
| 经验 | 3–5 年 |
| 学历 | 本科 |
| 人数 | 1 人 |
| JD 更新 | 7 月 2 日 |

> 公司名称、招聘平台链接：投递前从原帖补全（截图未含）。

---

## 主要职责

1. 负责 **AI Agent 应用** 的设计与开发，包括任务规划、工具调用、对话管理、记忆系统、执行反馈、错误恢复等核心能力。
2. 构建 **企业级 Agent 工作流**，将业务 SOP、知识文档、系统数据抽象为可复用的「AI Skills」或自动化流程。
3. 设计并实现 **RAG 系统**，包括多源知识接入、向量检索、上下文管理，优化知识问答质量。
4. 基于 **LangChain、LangGraph**、LlamaIndex、Spring AI、OpenClaw、Hermes Agent 等框架进行二次开发或扩展。
5. **Multi-Agent 协作**：角色分工、任务分解、执行编排、**结果校验**、反思机制、跨任务状态管理。
6. 使用 **Claude Code、Codex、Cursor、GitHub Copilot** 等 AI 工具快速开发，实现从 Prompt 到应用的端到端交付。
7. 跟踪 LLM、Agent、RAG、**MCP**、Tool Use、Memory、Subagents、**Context Engineering** 等前沿技术。

---

## 任职要求

1. 本科及以上学历，计算机、软件工程、人工智能等相关专业。
2. 扎实的软件工程能力，精通 **Python、Java 或 TypeScript** 中至少一门。
3. 有 Agent 系统开发经验，熟悉 **LLM API 调用、Prompt Engineering、Function/Tool Calling、RAG、Embeddings、向量数据库**。
4. 熟悉 **LangChain、LangGraph**、LlamaIndex 等 Agent 开发框架。
5. 了解 **Chroma、Pinecone、Weaviate、Milvus、Qdrant** 等向量数据库。
6. 具备将业务流程抽象为 **可执行、可校验、可迭代** 的 Agent 工作流的能力。
7. 熟练使用 AI 开发者工具（Claude Code、Cursor、Manus 等）。
8. 具备 **Agent 工程化问题** 的解决能力，包括模型幻觉、上下文丢失、工具调用失败、任务执行不稳定、Token 成本控制等。

---

## 与 FinParseAI 的对齐点（投递时可讲）

| JD 关键词 | 项目对应 |
|---|---|
| Agent 工作流 | `run_field`：parse → verify → 选表自愈 → re-verify → judge_diagnose |
| 结果校验 | `verify_field` + Prompt Registry（verify v1.1）；源表对齐、四维度误判修复 |
| 错误恢复 | wrong_table → heal_select → 二次 verify；rule heal |
| Multi-Agent | judge_diagnose / rule_code_diagnose / select_table_agent |
| RAG | 向量库 + 证监会准则 PDF（`llm-docs/`） |
| Context Engineering | `src/prompts/` YAML + Context Pack |
| 业务抽象 | FieldSpec 插件、准则→解析映射文档 |
| 工程化踩坑 | 50 家批跑、000785 根因、verify_hold → committed |

**可主动说明的差异**：生产主链目前是 Python 编排（非 LangGraph 全图），`workflow.py` 有 parse 图；LangGraph 可迁移，Agent 深度化计划 Phase 4 含事件流。

---

## 投递前 checklist

- [ ] 补全：公司名、招聘平台、投递链接 / 内推联系人
- [ ] 更新简历：突出 Agent 流水线、verify 闭环、批跑数据（如 50 家 59.2% committed）
- [ ] 准备 1 个 STAR 案例：000785 verify 源表不对齐 → 修复 → pass
- [ ] 准备 1 个 STAR 案例：verify prompt 误导 LLM → v1.1 条款修正
- [ ] GitHub / 作品集：FinParseAI 控制台截图或 demo 视频（如有）
- [ ] 确认期望薪资区间（JD 20–50K，按 3–5 年经验锚定）

---

## 投递记录

| 日期 | 动作 | 备注 |
|---|---|---|
| 2026-07-04 | 记录 JD | 计划 07-06 投递 |
| 2026-07-06 | （待填）投递 | |
| | 反馈 / 面试 | |
