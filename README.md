# FinParse Harness

A 股年报 PDF **结构化解析引擎**：构建期用 LLM 生成专用解析器，运行期 **零 LLM**、确定性执行；解析失败可自动修复并认证入库，供下游勾稽与解读。

仓库：[github.com/tlessss/finparse-harness](https://github.com/tlessss/finparse-harness)

---

## 它解决什么问题

上市公司年报附注表版式千差万别，一套通用规则很难全覆盖。本项目的思路是：

| 阶段 | 做什么 | 是否用 LLM |
|------|--------|-----------|
| **构建期** | 针对某版式生成/修复专用 Python 解析器，与标准答案 **exact** 对齐后认证入库 | ✅ |
| **运行期** | 扫 PDF → 路由已认证解析器 → 硬性勾稽校验 → 结构化 JSON | ❌ |

核心机制：**选择即验证**——不猜测该用哪个解析器，而是让候选解析器都跑一遍，用客观规则（如占比之和≈100%）判定谁解对了。

当前支持 **6 个字段**：营收构成、营业成本、研发费用、员工情况、前五客户、前五供应商。

---

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+（前端控制台，可选）
- macOS / Linux（Camelot 依赖 Ghostscript 等系统库）

### 1. 克隆与依赖

```bash
git clone https://github.com/tlessss/finparse-harness.git
cd finparse-harness

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env：至少配置 PDF_CACHE_DIR；构建期自愈需 LLM_API_KEY
```

### 2. 准备 PDF

仓库**不包含**真实财报 PDF。请自备年报 PDF，或指向本地缓存目录：

```bash
# .env 示例
PDF_CACHE_DIR=../book-agent/output/pdf_cache
```

目录结构示例：`{PDF_CACHE_DIR}/{股票代码}/{年份}.pdf`

### 3. 启动 API

```bash
python3 -m src.api
# 默认 http://localhost:8200
# 交互文档 http://localhost:8200/docs
```

按代码解析（需 PDF 已在缓存中）：

```bash
curl -X POST http://localhost:8200/parse/by-code \
  -H "Content-Type: application/json" \
  -d '{"stock_code": "000425", "year": 2025}'
```

### 4. 启动前端控制台（可选）

```bash
cd frontend
npm install
npm run dev
# 默认 http://localhost:3000/console
```

前端连 `localhost:8200`；后端未启动时会回退到 mock 数据。

### 5. 跑测试

```bash
pytest tests/ -q
```

---

## 本地私密数据（不进 git）

以下目录/文件在 `.gitignore` 中，需自行准备，**不影响本地开发**：

| 路径 | 用途 |
|------|------|
| `.env` | 数据库、LLM、PDF 路径等密钥 |
| `goldset/` | 标准答案、表缓存、分诊队列 |
| `PDF_CACHE_DIR` | 年报 PDF 文件 |
| `test_results/` | 批量测试与校验报告 |

`goldset/` 可从团队内部获取，或按 `scripts/seed_revenue_golden.py` 自行标注少量样本起步。

---

## 项目结构

```
src/
  api.py                  # FastAPI 入口 (:8200)
  engine_orchestrator.py  # 解析主编排
  batch_runner.py         # 批量跑 + 可选自动修复
  parsers/
    infra/                # 扫表、认列、版式指纹
    revenue/ cost/ ...    # 各字段默认解析器
    versions/             # 认证通过的版式专用解析器
    revenue_router.py     # 选择即验证路由
  eval/                   # 打分、分诊队列、认证目录
  agents/                 # 构建期 LLM 修复管线
frontend/                 # Next.js 控制台（分诊、测试台、批处理）
docs/flows/               # 系统流程图与导读
tests/                    # pytest
```

---

## 文档

| 文档 | 内容 |
|------|------|
| [docs/flows/README.md](docs/flows/README.md) | 六张分层流程图（建议从这里读） |
| [docs/系统原理与面试导读.md](docs/系统原理与面试导读.md) | 设计哲学与面试陈述 |
| [docs/批处理到解析完成-全流程详解.md](docs/批处理到解析完成-全流程详解.md) | 批处理端到端 |
| [docs/system-flow.html](docs/system-flow.html) | 可点击的交互流程图 |
| [docs/控制台测试指南.md](docs/控制台测试指南.md) | 前端各测试页说明 |

---

## 架构一览

```
PDF → scan_pdf（全表池）→ route_field（选择即验证）
     → plausibility（勾稽）→ triage（分诊台账）
     → [构建期] LLM 修复 → exact 认证 → 下次自动 routed
```

**三个核心名词**：`routed` = 已有认证解析器直接解对；`golden` = 人工确认的标准答案；`exact` = 与 golden 完全一致才允许认证入库。

---

## 与兄弟项目的关系（可选集成）

- **book-agent**（私有/兄弟仓）：公告下载与 PDF 缓存
- **caibaoxia**：MySQL 财报库 + RAG 解读层

FinParseAI 负责 **可勾稽的结构化抽取**；语义解读由上层产品完成。数据库 `DATABASE_URL` 可选，仅入库/锚点校验时需要。

---

## 贡献

欢迎 Issue 与 PR。开发前请阅读 `docs/flows/`，新字段优先扩展 `src/eval/field_spec.py` 中的 `FieldSpec` 插件，而非改机器层。

---

## License

暂未指定开源协议；使用前请联系维护者。
