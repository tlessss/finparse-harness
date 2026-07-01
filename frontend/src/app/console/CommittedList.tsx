"use client";

import { useState, useEffect, useMemo, Fragment } from "react";
import { codeLabel, FIELD_LABEL } from "./consoleData";
import { apiGet } from "./api";

type Commit = {
  id: number; created_at: string; reviewed_at?: string; stock_code: string; year: number; field: string;
  result?: unknown; confidence?: string; source?: string; status: string; note?: string;
};

// 入库来源 → 人话标签
const SRC = (s?: string): { label: string; cls: string } => {
  if (s === "verify_agent") return { label: "🤖 复核自动", cls: "bg-green-100 text-green-700" };
  if (s === "llm_ok") return { label: "④ 诊断确认", cls: "bg-blue-100 text-blue-700" };
  return { label: s || "人审", cls: "bg-gray-100 text-gray-600" };
};

const summarize = (result: unknown): string => {
  if (result && typeof result === "object" && !Array.isArray(result)) {
    return Object.entries(result as Record<string, unknown>)
      .filter(([, v]) => Array.isArray(v)).map(([k, v]) => `${k}:${(v as unknown[]).length}`).join("  ");
  }
  if (Array.isArray(result)) return `${result.length} 项`;
  return "";
};

export default function CommittedList() {
  const [rows, setRows] = useState<Commit[]>([]);
  const [db, setDb] = useState<{ reports_table?: string; is_test?: boolean } | null>(null);
  const [fField, setFField] = useState("");
  const [open, setOpen] = useState<number | null>(null);
  const [state, setState] = useState<"loading" | "error" | "ok">("loading");

  const load = () => {
    setState("loading");
    apiGet<{ reports_table?: string; is_test?: boolean } | null>("/health", null).then(({ data }) => setDb(data));
    apiGet<{ commits: Commit[] } | null>("/commit/list?status=approved&limit=500", null).then(({ data, live }) => {
      if (live && data) { setRows(data.commits || []); setState("ok"); } else setState("error");
    });
  };
  useEffect(() => { load(); }, []);

  const filtered = fField ? rows.filter((r) => r.field === fField) : rows;
  const byVerify = useMemo(() => rows.filter((r) => r.source === "verify_agent").length, [rows]);
  const fields = useMemo(() => Array.from(new Set(rows.map((r) => r.field))), [rows]);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">📥 已入库数据
          <span className="text-xs font-normal text-gray-400 ml-2">— 复核 agent 通过(或人审)后写入的记录</span>
        </h2>
        <div className="flex items-center gap-3 text-sm mt-2 flex-wrap">
          <span className="text-gray-500">目标库：
            <b className={db?.is_test ? "text-green-600" : "text-red-600"}>{db?.reports_table || "?"}</b>
            {db && <span className="text-xs text-gray-400 ml-1">{db.is_test ? "(测试库)" : "(生产库)"}</span>}
          </span>
          <span className="text-gray-400 text-xs">共 <b className="text-gray-700">{rows.length}</b> 条 · 其中复核自动入库 <b className="text-green-700">{byVerify}</b> 条</span>
          <select value={fField} onChange={(e) => setFField(e.target.value)} className="border rounded px-2 py-1 text-xs ml-auto">
            <option value="">全部字段</option>
            {fields.map((f) => <option key={f} value={f}>{FIELD_LABEL[f] || f}</option>)}
          </select>
          <button onClick={load} className="text-gray-400 hover:text-gray-600 text-xs">↻ 刷新</button>
        </div>
      </div>

      {state === "error" && <div className="bg-white rounded-lg border p-8 text-center text-red-400">无法连接后端（确认 :8200）</div>}
      {state !== "error" && (
        <div className="bg-white rounded-lg shadow-sm border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 text-gray-500 text-left text-xs">
              <tr>{["公司", "年份", "字段", "数据摘要", "来源", "入库时间", ""].map((h) =>
                <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
            </thead>
            <tbody>
              {filtered.map((c) => {
                const src = SRC(c.source);
                return (
                  <Fragment key={c.id}>
                    <tr className="border-t hover:bg-gray-50">
                      <td className="px-3 py-1.5">{codeLabel(c.stock_code)}</td>
                      <td className="px-3 py-1.5 text-xs text-gray-500">{c.year}</td>
                      <td className="px-3 py-1.5">{FIELD_LABEL[c.field] || c.field}</td>
                      <td className="px-3 py-1.5 text-xs text-gray-500">{summarize(c.result)}</td>
                      <td className="px-3 py-1.5"><span className={`px-2 py-0.5 rounded text-xs ${src.cls}`}>{src.label}</span></td>
                      <td className="px-3 py-1.5 text-xs text-gray-400">{(c.reviewed_at || c.created_at)?.slice(0, 16)}</td>
                      <td className="px-3 py-1.5 text-right">
                        <button onClick={() => setOpen(open === c.id ? null : c.id)} className="text-blue-600 hover:underline text-xs">
                          {open === c.id ? "收起" : "详情"}
                        </button>
                      </td>
                    </tr>
                    {open === c.id && (
                      <tr className="bg-gray-50"><td colSpan={7} className="px-3 py-2">
                        <pre className="text-xs bg-white border rounded p-2 max-h-96 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(c.result, null, 2)}</pre>
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
              {state === "ok" && !filtered.length && <tr><td colSpan={7} className="px-3 py-8 text-center text-gray-400">
                还没有已入库记录（去 ✅ 复核agent 跑一份、pass 就会自动入库）
              </td></tr>}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
