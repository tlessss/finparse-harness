"use client";

import { useState, useEffect, useCallback } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Legend,
} from "recharts";
import FlowDiagram from "./FlowDiagram";

const API_BASE = "http://localhost:8200";

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899"];

// ── 类型 ──
type StatusData = {
  status: string;
  tasks_running: number;
  db_pdf_records: number;
  db_parsed_fields: number;
  config: { pdf_cache_dir: string; rag_data_dir: string; port: number };
};

type RecordItem = {
  id: number;
  stock_code: string;
  company_name: string;
  report_year: number;
  data_source: string;
  has_revenue_breakdown: boolean;
  has_rnd_info: boolean;
  has_employees: boolean;
  has_cost_breakdown: boolean;
  has_top_clients: boolean;
  has_top_suppliers: boolean;
  pdf_parsed_at: string;
  quality_score: number | null;
};

type ParseResult = {
  stock_code: string;
  company_name: string;
  report_year: number;
  parse_duration_sec: number;
  field_count: number;
  parse_flags: Record<string, string>;
  revenue_breakdown?: any;
  rnd_info?: any;
  employees?: any;
  cost_breakdown?: any;
  top_clients?: any;
  top_suppliers?: any;
  db_write?: string;
};

export default function Home() {
  // ── 状态 ──
  const [status, setStatus] = useState<StatusData | null>(null);
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [activeTab, setActiveTab] = useState<"dashboard" | "records" | "flow" | "parse">("dashboard");
  const [parseCode, setParseCode] = useState("002407");
  const [parseYear, setParseYear] = useState(2025);
  const [parseResult, setParseResult] = useState<ParseResult | null>(null);
  const [parsing, setParsing] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState<RecordItem | null>(null);

  // ── 数据加载 ──
  const loadStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/status`);
      if (r.ok) setStatus(await r.json());
    } catch {}
  }, []);

  const loadRecords = useCallback(async () => {
    try {
      const r = await fetch(`${API_BASE}/results?limit=100`);
      if (r.ok) {
        const data = await r.json();
        setRecords(data.records || []);
      }
    } catch {}
  }, []);

  useEffect(() => {
    loadStatus();
    loadRecords();
  }, [loadStatus, loadRecords]);

  // ── 解析 ──
  const handleParse = async () => {
    setParsing(true);
    setParseResult(null);
    try {
      const r = await fetch(`${API_BASE}/parse/by-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stock_code: parseCode, year: parseYear, db_write: true }),
      });
      if (r.ok) {
        setParseResult(await r.json());
        loadRecords();
        loadStatus();
      } else {
        const err = await r.json();
        alert(`解析失败: ${err.detail}`);
      }
    } catch (e: any) {
      alert(`请求失败: ${e.message}`);
    }
    setParsing(false);
  };

  // ── 统计 ──
  const parsedCount = records.filter((r) => r.has_revenue_breakdown).length;
  const fieldDist = [
    { name: "营收结构", value: records.filter((r) => r.has_revenue_breakdown).length },
    { name: "研发费用", value: records.filter((r) => r.has_rnd_info).length },
    { name: "员工数据", value: records.filter((r) => r.has_employees).length },
    { name: "成本构成", value: records.filter((r) => r.has_cost_breakdown).length },
    { name: "前5客户", value: records.filter((r) => r.has_top_clients).length },
    { name: "前5供应商", value: records.filter((r) => r.has_top_suppliers).length },
  ];
  const maxField = Math.max(...fieldDist.map((f) => f.value), 1);

  return (
    <div className="min-h-screen bg-gray-50">
      {/* 顶部导航 */}
      <header className="bg-white border-b shadow-sm">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <h1 className="text-xl font-bold text-gray-800">FinParseAI</h1>
          <nav className="flex gap-2">
            {(["dashboard", "records", "flow", "parse"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-1.5 rounded text-sm font-medium transition ${
                  activeTab === tab ? "bg-blue-600 text-white" : "text-gray-600 hover:bg-gray-100"
                }`}
              >
                {tab === "dashboard" ? "仪表盘" : tab === "records" ? "解析记录" : tab === "flow" ? "流程图" : "解析"}
              </button>
            ))}
          </nav>
          <div className="text-xs text-gray-400">
            {status ? `${status.db_pdf_records} 条记录` : "加载中..."}
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 py-6">
        {activeTab === "dashboard" && (
          <div className="space-y-6">
            {/* 概览卡片 */}
            <div className="grid grid-cols-4 gap-4">
              {[
                { label: "PDF 记录", value: status?.db_pdf_records ?? "—", color: "text-blue-600" },
                { label: "已解析字段", value: status?.db_parsed_fields ?? "—", color: "text-green-600" },
                { label: "含营收结构", value: parsedCount, color: "text-purple-600" },
                { label: "API 状态", value: status?.status ?? "—", color: status?.status === "idle" ? "text-green-600" : "text-yellow-600" },
              ].map((card) => (
                <div key={card.label} className="bg-white rounded-xl p-4 shadow-sm border">
                  <div className="text-sm text-gray-500">{card.label}</div>
                  <div className={`text-2xl font-bold mt-1 ${card.color}`}>{card.value}</div>
                </div>
              ))}
            </div>

            {/* 字段覆盖图 */}
            <div className="bg-white rounded-xl p-4 shadow-sm border">
              <h2 className="text-sm font-semibold text-gray-700 mb-3">字段解析覆盖</h2>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={fieldDist}>
                  <XAxis dataKey="name" tick={{ fontSize: 11 }} />
                  <YAxis domain={[0, maxField + 5]} />
                  <Tooltip />
                  <Bar dataKey="value" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>

            {/* 字段分布饼图 */}
            <div className="grid grid-cols-2 gap-4">
              <div className="bg-white rounded-xl p-4 shadow-sm border">
                <h2 className="text-sm font-semibold text-gray-700 mb-3">记录数据源分布</h2>
                <ResponsiveContainer width="100%" height={200}>
                  <PieChart>
                    <Pie
                      data={[
                        { name: "PDF/hybrid", value: parsedCount },
                        { name: "未解析", value: records.length - parsedCount },
                      ]}
                      dataKey="value"
                      cx="50%"
                      cy="50%"
                      outerRadius={70}
                    >
                      <Cell fill="#3b82f6" />
                      <Cell fill="#e5e7eb" />
                    </Pie>
                    <Legend />
                  </PieChart>
                </ResponsiveContainer>
              </div>

              <div className="bg-white rounded-xl p-4 shadow-sm border">
                <h2 className="text-sm font-semibold text-gray-700 mb-3">系统配置</h2>
                {status?.config && (
                  <div className="text-xs text-gray-500 space-y-1.5">
                    <div><span className="text-gray-400">API 端口:</span> {status.config.port}</div>
                    <div><span className="text-gray-400">PDF 缓存:</span> <code className="text-blue-600">{status.config.pdf_cache_dir}</code></div>
                    <div><span className="text-gray-400">向量库:</span> <code className="text-blue-600">{status.config.rag_data_dir}</code></div>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {activeTab === "records" && (
          <div className="bg-white rounded-xl shadow-sm border overflow-hidden">
            <div className="px-4 py-3 border-b bg-gray-50">
              <h2 className="text-sm font-semibold text-gray-700">解析结果列表</h2>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 text-gray-500">
                  <tr>
                    <th className="text-left px-3 py-2 font-medium">代码</th>
                    <th className="text-left px-3 py-2 font-medium">名称</th>
                    <th className="text-center px-3 py-2 font-medium">年份</th>
                    <th className="text-center px-3 py-2 font-medium">营收</th>
                    <th className="text-center px-3 py-2 font-medium">研发</th>
                    <th className="text-center px-3 py-2 font-medium">员工</th>
                    <th className="text-center px-3 py-2 font-medium">成本</th>
                    <th className="text-center px-3 py-2 font-medium">客户</th>
                    <th className="text-center px-3 py-2 font-medium">供应商</th>
                    <th className="text-center px-3 py-2 font-medium">质量</th>
                    <th className="text-center px-3 py-2 font-medium">时间</th>
                  </tr>
                </thead>
                <tbody>
                  {records.slice(0, 50).map((r) => (
                    <tr
                      key={r.id}
                      className={`border-t hover:bg-blue-50 cursor-pointer transition ${
                        selectedRecord?.id === r.id ? "bg-blue-50" : ""
                      }`}
                      onClick={() => setSelectedRecord(r)}
                    >
                      <td className="px-3 py-2 font-mono text-xs">{r.stock_code}</td>
                      <td className="px-3 py-2">{r.company_name.trim()}</td>
                      <td className="text-center">{r.report_year}</td>
                      <td className="text-center">{r.has_revenue_breakdown ? "✅" : "—"}</td>
                      <td className="text-center">{r.has_rnd_info ? "✅" : "—"}</td>
                      <td className="text-center">{r.has_employees ? "✅" : "—"}</td>
                      <td className="text-center">{r.has_cost_breakdown ? "✅" : "—"}</td>
                      <td className="text-center">{r.has_top_clients ? "✅" : "—"}</td>
                      <td className="text-center">{r.has_top_suppliers ? "✅" : "—"}</td>
                      <td className="text-center">
                        <span
                          className={`inline-block px-1.5 py-0.5 rounded text-xs font-medium ${
                            (r.quality_score ?? 0) >= 0.85
                              ? "bg-green-100 text-green-700"
                              : (r.quality_score ?? 0) >= 0.6
                              ? "bg-yellow-100 text-yellow-700"
                              : "bg-red-100 text-red-700"
                          }`}
                        >
                          {r.quality_score != null ? r.quality_score.toFixed(2) : "—"}
                        </span>
                      </td>
                      <td className="text-center text-xs text-gray-400">
                        {r.pdf_parsed_at ? r.pdf_parsed_at.slice(0, 10) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {activeTab === "parse" && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* 左侧：解析输入 */}
            <div className="bg-white rounded-xl p-6 shadow-sm border">
              <h2 className="text-lg font-semibold text-gray-800 mb-4">按股票代码解析</h2>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm text-gray-500 mb-1">股票代码</label>
                  <input
                    value={parseCode}
                    onChange={(e) => setParseCode(e.target.value)}
                    className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none"
                    placeholder="如 002407"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-500 mb-1">年份</label>
                  <select
                    value={parseYear}
                    onChange={(e) => setParseYear(Number(e.target.value))}
                    className="w-full px-3 py-2 border rounded-lg text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                  >
                    {[2025, 2024, 2023, 2022, 2021].map((y) => (
                      <option key={y} value={y}>{y}</option>
                    ))}
                  </select>
                </div>
                <button
                  onClick={handleParse}
                  disabled={parsing}
                  className={`w-full py-2.5 rounded-lg text-sm font-medium text-white transition ${
                    parsing ? "bg-blue-400 cursor-not-allowed" : "bg-blue-600 hover:bg-blue-700"
                  }`}
                >
                  {parsing ? "解析中..." : "开始解析"}
                </button>
              </div>

              {parseResult && (
                <div className="mt-6 space-y-3">
                  <h3 className="text-sm font-semibold text-gray-700">解析结果摘要</h3>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div className="bg-gray-50 p-2 rounded">
                      <div className="text-gray-400 text-xs">耗时</div>
                      <div className="font-medium">{parseResult.parse_duration_sec}s</div>
                    </div>
                    <div className="bg-gray-50 p-2 rounded">
                      <div className="text-gray-400 text-xs">成功字段</div>
                      <div className="font-medium">{parseResult.field_count}/6</div>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(parseResult.parse_flags || {}).map(([k, v]) => (
                      <span
                        key={k}
                        className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                          v === "ok" ? "bg-green-100 text-green-700" : "bg-red-100 text-red-700"
                        }`}
                      >
                        {k === "rev" && "营收"}
                        {k === "rnd" && "研发"}
                        {k === "emp" && "员工"}
                        {k === "cost" && "成本"}
                        {k === "client" && "客户"}
                        {k === "supplier" && "供应商"}
                        : {v === "ok" ? "✅" : "❌"}
                      </span>
                    ))}
                  </div>
                  <div className="text-xs text-gray-400">
                    DB 写入: {parseResult.db_write || "—"}
                  </div>
                </div>
              )}
            </div>

            {/* 右侧：JSON 结果预览 */}
            <div className="bg-white rounded-xl p-6 shadow-sm border">
              <h2 className="text-lg font-semibold text-gray-800 mb-4">JSON 结果</h2>
              {parseResult ? (
                <pre className="bg-gray-900 text-green-300 text-xs p-4 rounded-lg overflow-auto max-h-[500px]">
                  {JSON.stringify(parseResult, null, 2).slice(0, 4000)}
                  {JSON.stringify(parseResult, null, 2).length > 4000 && "\n\n...（截断）"}
                </pre>
              ) : (
                <div className="text-gray-400 text-sm py-12 text-center">
                  执行解析后此处显示完整 JSON 结果
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === "flow" && (
          <div className="bg-white rounded-xl p-6 shadow-sm border">
            <h2 className="text-lg font-semibold text-gray-800 mb-4">全流程架构图</h2>
            <div className="text-sm text-gray-500 mb-4">
              交互式流程图（React Flow）· 状态以代码为准：实线 = 已接入主流程，虚线 = 代码已写但未接入。点节点看细节。
            </div>

            <FlowDiagram />
          </div>
        )}
      </main>
    </div>
  );
}
