"use client";

import { useState, useEffect } from "react";
import { codeLabel, FIELD_LABEL, fmtTime } from "./consoleData";
import { apiGet, apiPost } from "./api";

type Commit = {
  id: number; created_at: string; reviewed_at?: string; stock_code: string; year: number; field: string;
  result?: unknown; confidence?: string; source?: string; status: string; note?: string;
};

const STATUSES = [
  { key: "pending", label: "待审", cn: "等人通过" },
  { key: "approved", label: "已入库", cn: "已写库" },
  { key: "rejected", label: "已驳回", cn: "未入库" },
];

const summarize = (field: string, result: unknown): string => {
  if (result && typeof result === "object" && !Array.isArray(result)) {
    return Object.entries(result as Record<string, unknown>)
      .filter(([, v]) => Array.isArray(v)).map(([k, v]) => `${k}:${(v as unknown[]).length}`).join("  ");
  }
  if (Array.isArray(result)) return `${result.length} 项`;
  return "";
};

export default function CommitReview() {
  const [status, setStatus] = useState("pending");
  const [rows, setRows] = useState<Commit[]>([]);
  const [busy, setBusy] = useState<number | null>(null);
  const [msg, setMsg] = useState("");

  const load = (s = status) => apiGet<{ commits: Commit[] } | null>(`/commit/list?status=${s}`, null).then(({ data }) => setRows(data?.commits || []));
  useEffect(() => { load(); }, [status]);

  const act = async (id: number, action: "approve" | "reject") => {
    setBusy(id); setMsg("");
    const { data } = await apiPost<{ ok?: boolean; rows_updated?: number; error?: string } | null>(`/commit/${action}`, { id }, null);
    setBusy(null);
    if (data?.error) { setMsg("❌ " + data.error); return; }
    if (action === "approve") setMsg(`✅ 已入库（更新 ${data?.rows_updated ?? 0} 行）`);
    load();
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">入库审核
          <span className="text-xs font-normal text-gray-400 ml-2">— 待人审的项进这里（浅绿），通过后写入当前库（见顶部角标）。复核 agent 已 pass 的会自动入库，不进这里</span>
        </h2>
        <div className="flex items-center gap-2 text-sm mt-2">
          {STATUSES.map((s) => (
            <button key={s.key} onClick={() => setStatus(s.key)}
              className={`px-3 py-1 rounded ${status === s.key ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-600 hover:bg-gray-200"}`}>
              {s.label} <span className="text-xs opacity-70">{s.cn}</span>
            </button>
          ))}
          <button onClick={() => load()} className="text-gray-400 hover:text-gray-600 text-xs ml-1">↻ 刷新</button>
          {msg && <span className="ml-2 text-xs">{msg}</span>}
        </div>
      </div>

      {rows.map((c) => (
        <div key={c.id} className={`rounded-lg border p-3 ${c.status === "pending" ? "bg-green-50 border-green-300" : "bg-white"}`}>
          <div className="flex items-center gap-3 text-sm flex-wrap">
            <span className="font-medium">{codeLabel(c.stock_code)}</span>
            <span>{FIELD_LABEL[c.field] || c.field}</span>
            <span className="text-xs text-gray-500">{summarize(c.field, c.result)}</span>
            <span className="text-xs text-gray-400">置信 {c.confidence} · 来源 {c.source} · {fmtTime(c.created_at, true)}</span>
            {c.status !== "pending" && <span className={`text-xs ${c.status === "approved" ? "text-green-600" : "text-red-500"}`}>{c.status === "approved" ? "✅ 已入库" : "✗ 已驳回"} {fmtTime(c.reviewed_at, true)}</span>}
            {c.status === "pending" && (
              <span className="ml-auto flex gap-2">
                <button onClick={() => act(c.id, "approve")} disabled={busy === c.id}
                  className="px-3 py-1 rounded bg-green-600 text-white text-xs hover:bg-green-700 disabled:opacity-40">
                  {busy === c.id ? "入库中…" : "✅ 通过，入库"}
                </button>
                <button onClick={() => act(c.id, "reject")} disabled={busy === c.id}
                  className="px-3 py-1 rounded bg-gray-200 text-gray-600 text-xs hover:bg-gray-300 disabled:opacity-40">✗ 驳回</button>
              </span>
            )}
          </div>
          <details className="mt-2">
            <summary className="text-xs text-blue-600 cursor-pointer">查看将入库的完整数据</summary>
            <pre className="text-xs bg-gray-50 border rounded p-2 mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(c.result, null, 2)}</pre>
          </details>
        </div>
      ))}
      {!rows.length && <div className="bg-white rounded-lg border p-8 text-center text-gray-400 text-sm">该状态下没有记录</div>}
    </div>
  );
}
