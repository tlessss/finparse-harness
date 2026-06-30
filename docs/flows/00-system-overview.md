# 鸟瞰：运行期 vs 构建期

系统本质是**两条线、一个闭环**：

- **运行期**：零 LLM，跑冻结的确定性 Python
- **构建期**：LLM 写专用解析器，`exact` 闸门验收后入库
- **闭环**：认证后写回路由缓存，下次同版式自动 `routed`

```mermaid
flowchart TB
    subgraph RUN["🟦 运行期（零 LLM，生产批处理）"]
        PDF[PDF 年报] --> SCAN[scan_pdf 抽表一次]
        SCAN --> CACHE[(tables_cache)]
        CACHE --> ENGINE[FinParseAI.run<br/>6 字段解析]
        ENGINE --> OUT[JSON 输出 + 可选写库]
        OUT --> TRIAGE[triage_queue 分诊台账]
    end

    subgraph BUILD["🟧 构建期（LLM 写代码，离线/自愈）"]
        PROBLEM[红/橙：解不干净] --> GOLDEN[golden 标准答案]
        GOLDEN --> HEAL[heal_field / auto_heal]
        HEAL --> REPAIR[repair 三岔]
        REPAIR --> GEN[generate_parser<br/>LLM 写码循环]
        GEN --> CERTIFY[certify 认证入目录]
    end

    TRIAGE -->|needs_write / low_conf| PROBLEM
    CERTIFY -.->|版式指纹写回| ROUTE[route_field 路由缓存]
    ROUTE -.-> ENGINE

    style RUN fill:#0d2137,stroke:#0891b2,color:#e5e7eb
    style BUILD fill:#2a1f0d,stroke:#b45309,color:#e5e7eb
```

**相关代码**：`src/batch_runner.py` · `src/engine_orchestrator.py` · `src/agents/heal_pipeline.py`
