"use client";

import { useState, useEffect, useCallback } from "react";
import { codeLabel, loadStockNames, FIELD_LABEL, fmtTime } from "./consoleData";
import { apiGet, apiPost } from "./api";

type Rec = {
  id: number; created_at: string; stage: string; stock_code: string; year: number; field: string;
  status?: string; confidence?: string; verdict?: string | null; note?: string;
  summary?: Record<string, unknown> | null;
};
type Stats = { total: number; by_stage_verdict: { stage: string; v: string; n: number }[] };

const STAGE_CN: Record<string, string> = { select: "选表", route: "路由", parse: "解析" };

export default function TestData() {
  const [records, setRecords] = useState<Rec[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [stage, setStage] = useState("");
  const [verdict, setVerdict] = useState("");
  const [, setTick] = useState(0);

  const load = useCallback(async () => {
    await loadStockNames(); setTick((t) => t + 1);
    const qs = [stage && `stage=${stage}`, verdict && `verdict=${verdict}`].filter(Boolean).join("&");
    const { data } = await apiGet<{ records: Rec[] } | null>(`/test/list${qs ? "?" + qs : ""}`, null);
    setRecords(data?.records || []);
    const s = await apiGet<Stats | null>("/test/stats", null);
    setStats(s.data || null);
  }, [stage, verdict]);
  useEffect(() => { load(); }, [load]);

  const mark = async (id: number, v: string) => { await apiPost("/test/verdict", { id, verdict: v, note: "" }, null); load(); };

  const summaryText = (r: Rec) => {
    const s = r.summary || {};
    if (r.stage === "select") return `top第${s.top_page ?? "?"}页 ${s.top_score ?? "?"}分 · 候选${s.n_candidates ?? "?"}`;
    if (r.stage === "route") return `${s.parser_key || "—"} · 命中${(s.fp_matched as unknown[])?.length ?? 0} · 试${s.tried ?? 0}`;
    return JSON.stringify(s).slice(0, 50);
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-2">测试阶段数据 <span className="text-xs font-normal text-gray-400 ml-1">— 每次选表/路由测试自动入库(SQLite)，可回看 + 标对错</span></h2>
        <div className="flex items-center gap-2 flex-wrap text-sm">
          <span className="text-gray-500">阶段：</span>
          <select value={stage} onChange={(e) => setStage(e.target.value)} className="border rounded px-2 py-1">
            <option value="">全部</option><option value="select">选表</option><option value="route">路由</option><option value="parse">解析</option>
          </select>
          <span className="text-gray-500 ml-2">判定：</span>
          <select value={verdict} onChange={(e) => setVerdict(e.target.value)} className="border rounded px-2 py-1">
            <option value="">全部</option><option value="ok">✓对</option><option value="wrong">✗错</option>
          </select>
          <button onClick={load} className="px-2.5 py-1 rounded text-xs bg-gray-100 text-gray-600 hover:bg-gray-200 ml-2">↻ 刷新</button>
          {stats && <span className="text-xs text-gray-400 ml-2">共 {stats.total} 条 · {stats.by_stage_verdict.map((x) => `${STAGE_CN[x.stage] || x.stage}/${x.v} ${x.n}`).join(" · ")}</span>}
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-sm border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-left">
            <tr>{["阶段", "公司", "字段", "状态", "置信", "摘要", "判定", "时间", "操作"].map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr key={r.id} className="border-t hover:bg-gray-50">
                <td className="px-3 py-2"><span className={`px-1.5 py-0.5 rounded text-xs ${r.stage === "route" ? "bg-purple-100 text-purple-700" : "bg-blue-100 text-blue-700"}`}>{STAGE_CN[r.stage] || r.stage}</span></td>
                <td className="px-3 py-2 text-xs">{codeLabel(r.stock_code)}</td>
                <td className="px-3 py-2 text-xs">{FIELD_LABEL[r.field] || r.field}</td>
                <td className="px-3 py-2 text-xs">{r.status || "—"}</td>
                <td className="px-3 py-2 text-xs">{r.confidence ? <span className={r.confidence === "high" ? "text-green-600" : r.confidence === "low" ? "text-orange-500" : "text-gray-400"}>{r.confidence}</span> : "—"}</td>
                <td className="px-3 py-2 text-xs text-gray-500">{summaryText(r)}</td>
                <td className="px-3 py-2">{r.verdict ? <span className={`text-xs ${r.verdict === "ok" ? "text-green-600" : "text-red-500"}`}>{r.verdict === "ok" ? "✓ 对" : "✗ 错"}</span> : <span className="text-xs text-gray-300">未标</span>}</td>
                <td className="px-3 py-2 text-xs text-gray-400">{fmtTime(r.created_at, true)}</td>
                <td className="px-3 py-2 space-x-2 whitespace-nowrap">
                  <button onClick={() => mark(r.id, "ok")} className="text-green-600 hover:underline text-xs">标对</button>
                  <button onClick={() => mark(r.id, "wrong")} className="text-red-500 hover:underline text-xs">标错</button>
                </td>
              </tr>
            ))}
            {!records.length && <tr><td colSpan={9} className="px-3 py-8 text-center text-gray-400">还没有测试记录（去选表/路由测试台跑几个）</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
