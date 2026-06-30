"use client";

/* FinParseAI 全流程图（React Flow）
 *
 * 状态以【代码实情】为准（不是架构文档、也不是旧表格）：
 *   实线节点 = ✅ 已接入 workflow.py 的状态机（parse_pdf→validate→db_write→report）
 *   虚线节点 = 代码已写，但没 add_node 进 workflow（迭代/决策/复核当前没在主流程里跑）
 */
import { useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  Position,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

type Status = "done" | "unwired";
type NodeDef = {
  id: string; x: number; y: number; color: string; status: Status;
  title: string; sub?: string;
  detail: { file: string; what: string; points?: string[]; data?: string };
};

const NODES: NodeDef[] = [
  { id: "gateway", x: 220, y: 0, color: "#2563eb", status: "done",
    title: "① FastAPI 网关 :8200", sub: "/parse · /parse/by-code · /results",
    detail: { file: "src/api.py", what: "HTTP 网关，按 stock_code+report_year 从 PDF 缓存取文件解析。", data: "✅ 已实现" } },
  { id: "parse", x: 220, y: 110, color: "#0891b2", status: "done",
    title: "② parse_pdf", sub: "LangGraph 流水线起点",
    detail: { file: "src/agents/workflow.py · engine_orchestrator.py",
      what: "状态机首节点，调 FinParseAI 编排器跑 6 个解析器。",
      points: ["真实图：parse_pdf → validate → db_write → report（4 节点，线性，无 decide）"], data: "✅ 已接入" } },
  { id: "parsers", x: 220, y: 220, color: "#0ea5e9", status: "done",
    title: "③ 6 解析器（YAML 驱动）", sub: "营收/研发/员工/成本/供应商客户",
    detail: { file: "src/parsers/*.py + src/parser_rules/*.yaml",
      what: "每字段一个解析器，逻辑由 YAML 规则驱动；page_locator 用关键词定位页码。",
      points: ["pdfplumber 提表", "helper：unit_detector / page_locator / table_scanner / selector"], data: "✅" } },
  { id: "validate", x: 220, y: 330, color: "#7c3aed", status: "done",
    title: "④ validate（向量 + LLM）", sub: "vector_validator ✅ 已接入",
    detail: { file: "src/agents/workflow.py(node_validate) + src/validators/vector_validator.py(342行)",
      what: "validate 节点真的调用 VectorValidator(BGE) 做三重校验，并叠加 LLM 判断（可通过开关跳过）。",
      points: ["⚠ 架构文档标 🔜，但代码里向量校验已接入 —— 以代码为准", "跳过时直接 validation_passed=True"], data: "✅ 已接入主流程" } },
  { id: "db_write", x: 220, y: 440, color: "#16a34a", status: "done",
    title: "⑤ db_write", sub: "caibaoxia financial_reports",
    detail: { file: "src/database.py",
      what: "写回主数据（6 JSON + 30+ DECIMAL 字段），data_source=hybrid。",
      points: ["与 agent-platform 共用同一个 caibaoxia 库"], data: "✅" } },
  { id: "report", x: 220, y: 550, color: "#db2777", status: "done",
    title: "⑥ report", sub: "generate_report.py",
    detail: { file: "generate_report.py(40KB) · validate_v4_report.py(36KB)",
      what: "对结果生成解析报告。流水线终点（→ END）。", data: "✅" } },

  // ── 代码已写，但未 add_node 进 workflow（不在主流程里跑）──
  { id: "decide", x: 580, y: 330, color: "#9ca3af", status: "unwired",
    title: "decide 决策", sub: "已写设计 · 未接入",
    detail: { file: "（架构文档的设计，workflow.py 里无此节点）",
      what: "设计为三分支：全通过→归档 / 部分异常→迭代 / 严重异常→人工。",
      points: ["⚠ workflow 图里 validate 直接连 db_write，没有 decide 分支"], data: "🔜 未接入主流程" } },
  { id: "optimizer", x: 580, y: 440, color: "#9ca3af", status: "unwired",
    title: "优化 / 迭代 Agent", sub: "模块已实现 · 未接入",
    detail: { file: "src/agents/optimizer.py(296行) + iteration.py(167行) + experience_db.py",
      what: "根因诊断 → 改 YAML / 新建版式 YAML → 二次解析 → 二次校验；经验写入 experience_db。",
      points: ["模块存在且有实现，但没加入 workflow.add_node", "→ 迭代闭环当前不在主流程里跑（前端旧表格标 ✅ 是误导）"], data: "🔜 代码已写，未接入" } },
  { id: "review", x: 580, y: 550, color: "#9ca3af", status: "unwired",
    title: "人工复核", sub: "模块已实现 · 未接入",
    detail: { file: "src/review/manager.py(303行)",
      what: "异常拦截 → 人工修正 → 标注入库 → 更新向量库。",
      points: ["模块存在，未接入 workflow"], data: "🔜 未接入" } },
];

const SOLID: [string, string][] = [
  ["gateway", "parse"], ["parse", "parsers"], ["parsers", "validate"],
  ["validate", "db_write"], ["db_write", "report"],
];
const DASHED: [string, string, string?][] = [
  ["validate", "decide", "（设计）"], ["decide", "optimizer"], ["decide", "review"],
  ["optimizer", "parse", "迭代闭环（规划）"],
];

const rfNodes: Node[] = NODES.map((n) => ({
  id: n.id,
  position: { x: n.x, y: n.y },
  sourcePosition: Position.Bottom,
  targetPosition: Position.Top,
  data: {
    label: (
      <div style={{ textAlign: "center", lineHeight: 1.3 }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>{n.title}</div>
        {n.sub && <div style={{ fontSize: 10.5, color: "#6b7280", marginTop: 2 }}>{n.sub}</div>}
      </div>
    ),
  },
  style: {
    width: 210,
    borderRadius: 10,
    border: `2px ${n.status === "unwired" ? "dashed" : "solid"} ${n.color}`,
    background: n.status === "unwired" ? "#f9fafb" : "#ffffff",
    padding: 8,
    opacity: n.status === "unwired" ? 0.9 : 1,
  },
}));

const rfEdges: Edge[] = [
  ...SOLID.map(([s, t]) => ({
    id: `${s}-${t}`, source: s, target: t,
    markerEnd: { type: MarkerType.ArrowClosed },
  })),
  ...DASHED.map(([s, t, l]) => ({
    id: `${s}-${t}`, source: s, target: t, label: l,
    style: { strokeDasharray: "5 4", stroke: "#9ca3af" },
    labelStyle: { fontSize: 10, fill: "#9ca3af" },
    markerEnd: { type: MarkerType.ArrowClosed, color: "#9ca3af" },
  })),
];

export default function FlowDiagram() {
  const [sel, setSel] = useState<NodeDef | null>(null);

  return (
    <div className="flex gap-4">
      <div style={{ height: 640 }} className="flex-1 border rounded-xl bg-gray-50">
        <ReactFlow
          nodes={rfNodes}
          edges={rfEdges}
          fitView
          onNodeClick={(_, node) => setSel(NODES.find((n) => n.id === node.id) || null)}
          proOptions={{ hideAttribution: true }}
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>

      <aside className="w-80 shrink-0 border rounded-xl p-4 bg-white">
        {sel ? (
          <>
            <h3 className="font-semibold text-gray-800">{sel.title}</h3>
            <div className="font-mono text-xs text-blue-600 break-all mt-1">{sel.detail.file}</div>
            <p className="text-sm text-gray-700 mt-3 leading-relaxed">{sel.detail.what}</p>
            {sel.detail.points && sel.detail.points.length > 0 && (
              <ul className="list-disc pl-4 mt-2 text-xs text-gray-600 space-y-1">
                {sel.detail.points.map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            )}
            {sel.detail.data && (
              <div className="mt-3 bg-gray-50 rounded p-2 text-xs text-gray-500">📊 {sel.detail.data}</div>
            )}
          </>
        ) : (
          <div className="text-sm text-gray-400 leading-relaxed">
            👈 点任意节点看细节（文件 / 职责 / 状态）。<br />
            <span className="text-gray-500">实线</span> = ✅ 已接入主流程（workflow.py 的状态机）；<br />
            <span className="text-gray-500">虚线</span> = 代码已写，但未接入主流程。
          </div>
        )}
      </aside>
    </div>
  );
}
