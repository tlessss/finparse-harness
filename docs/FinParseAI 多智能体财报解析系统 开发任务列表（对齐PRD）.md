# FinParseAI 多智能体财报解析系统 开发任务列表（对齐PRD）

**适配项目状态**：已有成熟向量数据库、已有大量财报解析存量样本、无需从零搭建底座

**开发环境**：Cursor IDE 全流程开发

**任务阶段划分**：MVP 最小可用阶段 → 核心闭环阶段 → 优化量产阶段

**优先级标识**：P0（必做核心）、P1（重要功能）、P2（优化迭代）

**参考项目**：`../agent-platform`（**仅参考**其数据库连接、数据字段格式、向量库路径等配置信息）

**重要原则**：**不迁移 agent-platform 的任何业务能力代码**（解析器、Agent、任务调度、前端等全部在 FinParseAI 中从零实现）。

---

## 基础设施与配置（仅参考 agent-platform 配置）

### 数据库（MySQL / PolarDB — 复用 caibaoxia 库）

| 配置项 | 值 |
|--------|-----|
| `DATABASE_URL` | `mysql+pymysql://tless:***@main-tless.mysql.polardb.rds.aliyuncs.com:3306/caibaoxia` |
| 驱动 | `pymysql` |
| 核心表 | `financial_reports`（财报结构化数据）、`stocks`（股票基础信息） |
| PDF 缓存 | `PDF_CACHE_DIR=../book-agent/output/pdf_cache`（命名规则 `{stock_code}_{year}.pdf`，共 1,289 份缓存） |

**`financial_reports` 核心 JSON 字段**（FinParseAI 仅解析这 6 个字段）：

| 字段名 | 中文 | 结构要点 |
|--------|------|----------|
| `revenue_breakdown` | 营收结构 | `segments` / `industries` / `regions`，每项含 `name`、`revenue_wan`、`ratio_pct` |
| `cost_breakdown` | 成本构成 | `[{item, industry, ratio_pct, amount_wan}]` |
| `rnd_info` | 研发数据 | `{rnd_detail:[{name, amount_this, amount_last}], total_this, total_last}` |
| `employees` | 员工数据 | `{total, parent, composition[{type, count}], education[{type, count}]}` |
| `top_suppliers` | 前五大供应商 | `{total_amount, total_ratio_pct, items:[{rank, name, amount, ratio_pct}]}` |
| `top_clients` | 前五大客户 | 同上结构 |

### 向量数据库（复用 quantification 存量资产）

| 配置项 | 值 |
|--------|-----|
| 存储方式 | numpy 文件持久化（`{name}_meta.json` + `{name}_vectors.npy`） |
| 数据目录 | `RAG_DATA_DIR=../quantification/rag_data`（约 5.3GB，全市场已索引） |
| 嵌入模型 | `BAAI/bge-small-zh-v1.5`（512 维） |
| Collection 命名 | `{stock_code}_{year}_annual` |
| 检索方式 | `cosine_similarity` 余弦相似度 Top-K |

**PRD 向量校验阈值**：版式相似度≥80%（同类文档）、语义相似度<60%（触发纠错）、版式<60%（新建解析器）

### LLM 与端口

| 配置项 | 值 |
|--------|-----|
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | `deepseek-chat` |
| 后端端口 | `8200`（`PORT`） |
| 前端端口 | `5281`（`FRONTEND_PORT`） |
| 迭代上限 | `MAX_ITERATE=3` |

### 数据范围边界

| 数据来源 | 覆盖范围 |
|----------|---------|
| **akshare（已有，FinParseAI 不处理）** | 三张主表下的所有独立 DECIMAL 字段（revenue、cost、rnd_expense、total_assets 等 30+ 字段） |
| **FinParseAI 从 PDF 解析（核心产出）** | 附注明细——仅 **6 个 JSON 字段** |
| **下期规划（本版本不处理）** | `mda_summary`（管理层讨论与分析） |

---

## 项目当前进度（截至 2026-06-10 14:17）

### ✅ MVP 阶段核心已完成

#### 6 个核心解析器（全部开发完成，多氟多 2025 验证通过）

| 解析器 | 文件 | 验证结果 |
|--------|------|----------|
| RevenueParser | `src/parsers/revenue_parser.py` | 分产品5项、分行业2项、分地区2项，占比和99.99% |
| RndParser | `src/parsers/rnd_parser.py` | 9项明细，合计4.87亿（本期）/3.95亿（上期） |
| EmployeeParser | `src/parsers/employee_parser.py` | 总数8,040人，5类专业+4类学历 |
| CostParser | `src/parsers/cost_parser.py` | 氟基新材料→直接材料88.95% |
| TopSupplierParser | `src/parsers/top_supplier_parser.py` | 前5客户33.49%（零关联），前2供应商22.38% |

#### 编排管线与数据库写入

- [x] 集成管线 `src/engine_orchestrator.py` — 一次调用跑全部 6 个解析器
- [x] 2.4 秒完成 264 页年报全部分析
- [x] 结果自动写入 `financial_reports`（`data_source=hybrid`、`pdf_parsed_at` 自动记录）
- [x] 数据库回读验证：6 个 JSON 字段全部非空

#### API 服务（FastAPI）

- [x] `GET /health` — 健康检查
- [x] `GET /status` — 运行状态（记录数、字段覆盖数、配置）
- [x] `POST /parse/by-code` — 按股票代码+年份从 PDF 缓存解析
- [x] `POST /parse` — 上传 PDF 文件解析
- [x] `GET /results` — 解析结果列表
- [x] 服务运行在 `http://localhost:8200`
- [x] API 文档 `http://localhost:8200/docs`

#### LangGraph Agent 调度

- [x] LangGraph 状态机流程：`parse_pdf → validate → db_write → report`
- [x] 4 个节点：解析 PDF / LLM 校验 / 写入 DB / 状态报告
- [x] 集成 DeepSeek LLM 校验（默认跳过，第二阶段启用）

#### 批量脚本

- [x] `scripts/batch_parse.py` — 扫描 PDF 缓存，筛选未解析记录，批量执行
- [x] 支持 `--limit` / `--stock` / `--year` / `--dry-run`
- [x] 已验证：dry-run 能正确扫描出待解析记录

#### 前端可视化

- [x] Next.js 项目初始化（`frontend/`）
- [x] 仪表盘：总览卡片、字段覆盖柱状图、数据源饼图、系统配置
- [x] 解析记录表格：显示 6 个字段状态 + 质量评分
- [x] 解析页面：输入股票代码+年份，调后端 API，展示摘要和完整 JSON
- [x] 前端运行在 `http://localhost:5281`

---

## 第一阶段：MVP 最小可用版本

### 1.1 项目架构初始化（P0）

- [x] 项目目录结构搭建
- [x] 数据库配置与连接层（`src/config.py` + `src/database.py`）
- [x] PDF 解析引擎框架（`src/parsers/` 6 个解析器）
- [x] YAML 规则配置（`src/parser_rules/industry_default.yaml`）
- [x] 规则热加载器（`src/parser_engine/rule_loader.py`）
- [x] FastAPI 接口服务（`src/api.py`，运行在端口 8200）
- [x] LangGraph Agent 调度流水线（`src/agents/workflow.py`）
- [x] 批量解析脚本（`scripts/batch_parse.py`）

### 1.2 六个核心 JSON 字段解析（P0）

- [x] RevenueParser（营收结构）
- [x] RndParser（研发费用明细）
- [x] EmployeeParser（员工构成）
- [x] CostParser（成本构成）
- [x] TopSupplierParser（前五大供应商+客户）
- [x] 集成管线 `src/engine_orchestrator.py`（一次调出 6 个字段）
- [x] 解析结果写入 `financial_reports` 对应 JSON 字段
- [ ] **重要缺口：当前解析器页码硬编码适配多氟多，其他公司年报版式不同（如澜起科技科创板的营收结构在第几页不确定），需要改为自动搜索定位**

### 1.3 多智能体基础调度流程（P0）

- [x] LangGraph 框架安装
- [x] LangGraph 状态机构建：`parse_pdf → validate → db_write → report`
- [x] LLM 校验节点（DeepSeek，默认跳过，第二阶段启用）
- [ ] 任务队列模块（`src/task_manager.py`）
- [ ] 任务 ID 生成、文档预处理、状态管控

### 1.4 简易前端可视化（P1）

- [x] Next.js + React 前端项目初始化
- [x] 仪表盘（总览卡片、字段覆盖图、系统配置）
- [x] 解析记录表格（6 个字段状态 + 质量评分）
- [x] 解析页面（输入代码+年份，调 API，展示结果）
- [x] 前端运行在 `http://localhost:5281`
- [ ] PDF 在线预览基础功能
- [ ] 多智能体协作流程图（React Flow）

### 1.5 MVP 交付产物

```
后端 API (8200) ←→ 前端 (5281)
    │
    ├── POST /parse/by-code → FinParseAI 管线 (2.4s)
    │       │
    │       ├── 6个解析器 → 统一JSON
    │       └── 写入 financial_reports
    │
    ├── GET /results → 解析记录列表
    ├── GET /status → 运行状态
    └── GET /health → 健康检查
```

---

## 第二阶段：核心闭环能力开发

### 2.1 向量多维校验智能体（P0 核心）

- [ ] 对接 quantification 存量向量库（`rag_data`）
- [ ] 三重校验逻辑：语义一致性、数据逻辑勾稽、内容完整性
- [ ] 版式相似度、指标语义相似度计算（阈值60%/80%）
- [ ] 异常识别：漏提、错提、单位错误、结构异常、逻辑错误
- [ ] 标准化异常报告输出
- [ ] 流程分支逻辑：校验通过归档、异常进入迭代

### 2.2 解析优化智能体（P0 核心）

- [ ] 异常根因智能诊断（排版变动、字段别名、版式改版）
- [ ] 双模式决策：修改现有 YAML / 新建 YAML
- [ ] 解析规则优化方案输出
- [ ] 解析器版本管理

### 2.3 迭代闭环流程（P0）

- [ ] 自动迭代重试：更新规则 → 二次解析 → 二次校验
- [ ] 迭代终止机制：3 次失败转入人工复核
- [ ] 样本自动沉淀至向量库与案例库

### 2.4 全流程可视化（P1）

- [ ] React Flow 多智能体协作流程图
- [ ] 六大可视化区域完善
- [ ] PDF 异常点位高亮、相似度展示
- [ ] 迭代日志全量溯源

### 2.5 人工复核模块（P1）

- [ ] 异常任务拦截机制
- [ ] 数据修正、异常原因标注、方案确认
- [ ] 人工标注数据自动入库

---

## 第三阶段：优化与量产落地

### 3.1 性能优化（P1）

- [ ] 单份 PDF 解析 ≤ 30s（当前 2.4s 已达标）
- [ ] 向量检索 ≤ 2s
- [ ] 多任务并行处理（10 份同时）
- [ ] 任务调度优化

### 3.2 功能完善与量产（P1）

- [ ] 数据归档、条件检索、历史查询
- [ ] 解析结果 Excel/JSON 导出
- [ ] 解析器批量管理
- [ ] 边缘场景适配（跨版式自动定位）

### 3.3 扩展性与准确率（P2）

- [ ] 智能体可插拔配置
- [ ] 自定义新增解析指标/文档类型
- [ ] 基于存量案例库迭代优化

---

## 任务优先级总览

| 优先级 | 任务 | 状态 |
|--------|------|------|
| **P0** | **项目架构与基础配置** | ✅ 全部完成 |
| **P0** | **6 个 JSON 字段解析** | ✅ 全部完成（多氟多验证通过）|
| **P0** | **编排管线 + 数据库写入** | ✅ 完成（2.4s 全字段写入）|
| **P0** | **FastAPI 接口服务** | ✅ 完成（5 个端点，运行在 8200）|
| **P0** | **LangGraph 调度流水线** | ✅ 完成（4 节点状态机）|
| **P1** | **前端可视化** | ✅ 完成（Next.js，仪表盘/记录/解析页）|
| **P1** | **跨版式自适应（当前最大缺口）** | 🔜 当前解析器页码硬编码，其他公司版式不同需自动定位 |
| **P0** | 向量校验 Agent | 🔜 待开发 |
| **P0** | 解析优化 Agent | 🔜 待开发 |
| **P0** | 迭代闭环流程 | 🔜 待开发 |
| **P1** | PDF 在线预览 | 🔜 待开发 |
| **P2** | 扩展性/准确率调优 | 🔜 待开发 |

---

> （注：本文档随项目进展持续更新）
