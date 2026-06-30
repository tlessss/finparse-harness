"use client";

import { useState, useEffect } from "react";
import { codeLabel, FIELD_LABEL } from "./consoleData";
import { apiGet, apiPost } from "./api";

type Rec = {
  id: number; created_at: string; stock_code: string; year: number; field: string;
  status?: string; confidence?: string; verdict?: string | null; summary?: Record<string, unknown> | null;
};

const confCls = (c?: string) => c === "high" ? "text-green-600" : c === "low" ? "text-orange-500" : "text-gray-400";

export default function TestHistory({ stage, onPick, refreshKey }: {
  stage: string; onPick: (code: string, year: number, field: string) => void; refreshKey?: number;
}) {
  const [rows, setRows] = useState<Rec[]>([]);
  const load = () => apiGet<{ records: Rec[] } | null>(`/test/list?stage=${stage}`, null).then(({ data }) => setRows(data?.records || []));
  useEffect(() => { load(); }, [stage, refreshKey]);

  const mark = async (id: number, v: string, e: React.MouseEvent) => {
    e.stopPropagation();
    await apiPost("/test/verdict", { id, verdict: v, note: "" }, null);
    load();
  };
  const sumText = (r: Rec) => {
    const s = r.summary || {};
    if (stage === "select") return `top第${s.top_page ?? "?"}页 ${s.top_score ?? "?"}分 · 候选${s.n_candidates ?? "?"}`;
    if (stage === "route") return `${s.parser_key || "—"} · 命中${(s.fp_matched as unknown[])?.length ?? 0}`;
    if (stage === "parse") return Object.entries((s.dims as Record<string, number>) || {}).map(([k, v]) => `${k}:${v}`).join(" ");
    if (stage === "judge") return `${s.verdict || ""} · 问题${s.issues ?? 0}`;
    return "";
  };

  return (
    <div className="bg-white rounded-lg shadow-sm border overflow-hidden">
      <div className="px-3 py-2 text-xs text-gray-500 border-b flex items-center justify-between">
        <span>已测记录（{rows.length}）· 点任一行重看</span>
        <button onClick={load} className="text-gray-400 hover:text-gray-600">↻ 刷新</button>
      </div>
      <div className="max-h-80 overflow-auto">
        <table className="w-full text-sm">
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t hover:bg-blue-50 cursor-pointer" onClick={() => onPick(r.stock_code, r.year, r.field)}>
                <td className="px-3 py-1.5 text-xs w-40 truncate">{codeLabel(r.stock_code)}</td>
                <td className="px-3 py-1.5 text-xs">{FIELD_LABEL[r.field] || r.field}</td>
                <td className="px-3 py-1.5 text-xs text-gray-500">{r.status || ""}</td>
                <td className={`px-3 py-1.5 text-xs ${confCls(r.confidence)}`}>{r.confidence || ""}</td>
                <td className="px-3 py-1.5 text-xs text-gray-500 truncate max-w-[200px]">{sumText(r)}</td>
                <td className="px-3 py-1.5 text-xs">{r.verdict ? <span className={r.verdict === "ok" ? "text-green-600" : "text-red-500"}>{r.verdict === "ok" ? "✓对" : "✗错"}</span> : <span className="text-gray-300">未标</span>}</td>
                <td className="px-3 py-1.5 text-xs text-gray-400">{r.created_at?.slice(5, 16)}</td>
                <td className="px-3 py-1.5 whitespace-nowrap">
                  <button onClick={(e) => mark(r.id, "ok", e)} className="text-green-600 hover:underline text-xs mr-1">对</button>
                  <button onClick={(e) => mark(r.id, "wrong", e)} className="text-red-500 hover:underline text-xs">错</button>
                </td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={8} className="px-3 py-5 text-center text-gray-400 text-xs">还没测过这个阶段（跑一个就有了）</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
