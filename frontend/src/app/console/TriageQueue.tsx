"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  REASON_META, OPEN_REASONS, FIELD_LABEL, codeLabel, loadStockNames,
  type TriageRecord, type Confidence, type Summary,
} from "./consoleData";
import { apiGet, apiPost } from "./api";

const CONF_DOT: Record<Confidence, string> = { high: "bg-green-500", low: "bg-orange-500", unknown: "bg-amber-400" };
type LoadState = "loading" | "error" | "ok";

export default function TriageQueue() {
  const router = useRouter();
  const goReview = (code: string, year: number, field: string) => {
    const q = new URLSearchParams({ code, year: String(year), field });
    router.push(`/console/review?${q}`);
  };
  const [records, setRecords] = useState<TriageRecord[]>([]);
  const [srv, setSrv] = useState<Summary | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [fReason, setFReason] = useState<string>("");
  const [fField, setFField] = useState<string>("");
  const [fStatus, setFStatus] = useState<string>("");
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 20;

  const load = () => {
    setState("loading");
    apiGet<Summary | null>("/triage/summary", null).then(({ data }) => { if (data) setSrv(data); });
    apiGet<{ records: TriageRecord[] } | null>("/triage/queue?status=all", null).then(({ data, live }) => {
      if (live && data) { setRecords(data.records || []); setState("ok"); }
      else setState("error");
    });
  };
  useEffect(() => { loadStockNames().then(load); }, []);
  useEffect(() => { setPage(0); }, [fReason, fField, fStatus]);

  // 默认列表 = 待办(不含可信绿/已解决)；选 fStatus="ok" 看可信绿清单
  const byField = useMemo(() => {
    const m: Record<string, number> = {};
    for (const r of records) if (r.status !== "resolved" && r.status !== "ok") m[r.field] = (m[r.field] || 0) + 1;
    return m;
  }, [records]);
  const filtered = records.filter((r) => {
    const statusOk = fStatus ? r.status === fStatus : (r.status !== "resolved" && r.status !== "ok");
    return statusOk && (!fReason || r.reason === fReason) && (!fField || r.field === fField);
  });
  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const cur = Math.min(page, pageCount - 1);
  const rows = filtered.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);

  const claim = (r: TriageRecord) =>
    setRecords((prev) => prev.map((x) => x === r ? { ...x, status: "in_progress" } : x));
  const SEED = ["000425", "300005", "000333", "000088", "000563", "000601"];
  const scan = async () => {
    const codes = records.length ? Array.from(new Set(records.map((r) => r.code))) : SEED;
    await apiPost("/triage/scan", { codes, year: 2025 }, null);
    load();
  };
  const reviewLow = async () => { await apiPost("/triage/review", { reason: "low_confidence", limit: 20 }, null); load(); };
  const recheck = async (r: TriageRecord) => { await apiPost("/triage/review", { reason: r.reason, limit: 1 }, null); load(); };

  if (state === "loading") return <Empty text="加载中…" />;
  if (state === "error") return <Empty text="无法连接后端 /triage/queue（确认 http://localhost:8200 已启动）" retry={load} />;

  return (
    <div className="space-y-4">
      {/* 安全感大数字（后端 /triage/summary）*/}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Card title="🟢 已核验（可信）" value={srv?.verified ?? 0} sub={`${srv?.verified_pct ?? 0}% · 锚过+复核 agent 通过`} accent="text-green-600" />
        <Card title="📊 解析出数据" value={srv?.parsed ?? 0} sub={`${srv?.parsed_pct ?? 0}% · 含待核验(黄)`} accent="text-blue-600" />
        <Card title="🔴 待办" value={srv?.open ?? 0} sub={`累计字段 ${srv?.total ?? 0}`} accent="text-red-600" />
        <Card title="净通过率" value={`${srv?.verified_pct ?? 0}%`} sub="已核验 / 总字段" accent="text-green-700" />
      </div>

      {/* 待办分级 + 可信清单入口 */}
      <div className="bg-white rounded-lg shadow-sm border p-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-gray-500 mr-1">待办分级：</span>
        {OPEN_REASONS.map((rs) => (
          <button key={rs} onClick={() => { setFStatus(""); setFReason(fReason === rs ? "" : rs); }}
            className={`px-2 py-1 rounded text-xs ${REASON_META[rs].cls} ${fReason === rs && fStatus !== "ok" ? "ring-2 ring-offset-1" : ""}`} title={REASON_META[rs].todo}>
            {REASON_META[rs].label} <b>{srv?.by_reason[rs] || 0}</b>
          </button>
        ))}
        <button onClick={() => { setFReason(""); setFStatus(fStatus === "ok" ? "" : "ok"); }}
          className={`px-2 py-1 rounded text-xs bg-green-100 text-green-700 ${fStatus === "ok" ? "ring-2 ring-offset-1" : ""}`}>
          🟢 可信清单 <b>{srv?.verified ?? 0}</b>
        </button>
      </div>

      {/* 待办按字段分布 */}
      <div className="bg-white rounded-lg shadow-sm border p-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="text-gray-500 mr-1">待办按字段：</span>
        {Object.entries(byField).length === 0 && <span className="text-gray-300 text-xs">无</span>}
        {Object.entries(byField).map(([f, n]) => (
          <button key={f} onClick={() => setFField(fField === f ? "" : f)}
            className={`px-2 py-1 rounded text-xs border ${fField === f ? "bg-blue-600 text-white border-blue-600" : "bg-gray-50 text-gray-600 border-gray-200"}`}>
            {FIELD_LABEL[f] || f} <b>{n}</b>
          </button>
        ))}
      </div>

      {/* 筛选 + 扫描/复核 */}
      <div className="bg-white rounded-lg shadow-sm border p-3 flex items-center gap-3 text-sm flex-wrap">
        <span className="text-gray-500">筛选：</span>
        <select value={fReason} onChange={(e) => setFReason(e.target.value)} className="border rounded px-2 py-1">
          <option value="">全部 reason</option>
          {OPEN_REASONS.map((rs) => <option key={rs} value={rs}>{REASON_META[rs].label}</option>)}
        </select>
        <select value={fField} onChange={(e) => setFField(e.target.value)} className="border rounded px-2 py-1">
          <option value="">全部字段</option>
          {Object.entries(FIELD_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={fStatus} onChange={(e) => setFStatus(e.target.value)} className="border rounded px-2 py-1">
          <option value="">待办(默认)</option>
          <option value="ok">🟢 可信(绿)</option>
          <option value="open">待办</option>
          <option value="in_progress">处理中</option>
          <option value="resolved">已解决</option>
        </select>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={scan} className="px-2.5 py-1 rounded text-xs bg-gray-100 text-gray-600 hover:bg-gray-200">↻ 手动扫描</button>
          <button onClick={reviewLow} className="px-2.5 py-1 rounded text-xs bg-orange-100 text-orange-700 hover:bg-orange-200">批量诊断 low</button>
          <span className="text-xs text-gray-400">{filtered.length} 条</span>
        </div>
      </div>

      {/* 队列表格 */}
      <div className="bg-white rounded-lg shadow-sm border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 text-gray-500 text-left">
            <tr>{["字段", "报告", "原因", "置信度", "差异", "状态", "更新时间", "操作"].map((h) =>
              <th key={h} className="px-4 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t hover:bg-gray-50">
                <td className="px-4 py-2">{FIELD_LABEL[r.field]}</td>
                <td className="px-4 py-2 text-xs">{codeLabel(r.code)} <span className="text-gray-400">/ {r.year}</span></td>
                <td className="px-4 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs ${REASON_META[r.reason].cls}`} title={REASON_META[r.reason].todo}>
                    {REASON_META[r.reason].label}
                  </span>
                </td>
                <td className="px-4 py-2">
                  <span className="flex items-center gap-1 text-xs">
                    <span className={`inline-block w-2 h-2 rounded-full ${CONF_DOT[r.signal.confidence]}`} />
                    {r.signal.confidence}
                  </span>
                </td>
                <td className="px-4 py-2 text-xs text-gray-500">{r.signal.diff_pct != null ? `${r.signal.diff_pct}%` : "—"}</td>
                <td className="px-4 py-2">
                  <span className={`text-xs ${r.status === "ok" ? "text-green-600" : r.status === "in_progress" ? "text-blue-600" : "text-gray-500"}`}>
                    {r.status === "ok" ? "🟢可信" : r.status === "in_progress" ? "处理中" : "待办"}
                  </span>
                </td>
                <td className="px-4 py-2 text-xs text-gray-400">{r.updated_at}</td>
                <td className="px-4 py-2 space-x-2 whitespace-nowrap">
                  <button onClick={() => goReview(r.code, r.year, r.field)} className="text-blue-600 hover:underline text-xs">审核</button>
                  {r.status === "open" && <button onClick={() => claim(r)} className="text-gray-500 hover:underline text-xs">认领</button>}
                  {r.reason === "low_confidence" && <button onClick={() => recheck(r)} className="text-orange-600 hover:underline text-xs">诊断</button>}
                </td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={8} className="px-4 py-8 text-center text-gray-400">
              {fStatus === "ok" ? "暂无可信记录（先扫描/认证解析器）" : "无待办 🎉（可点\"手动扫描\"分诊一批）"}
            </td></tr>}
          </tbody>
        </table>
        {filtered.length > PAGE_SIZE && (
          <div className="flex items-center justify-end gap-3 px-4 py-2 border-t text-sm">
            <span className="text-xs text-gray-400">
              第 {cur * PAGE_SIZE + 1}-{Math.min((cur + 1) * PAGE_SIZE, filtered.length)} / 共 {filtered.length} 条
            </span>
            <button disabled={cur === 0} onClick={() => setPage(cur - 1)} className="px-2 py-1 rounded border disabled:opacity-40">上一页</button>
            <span className="text-xs">{cur + 1} / {pageCount}</span>
            <button disabled={cur >= pageCount - 1} onClick={() => setPage(cur + 1)} className="px-2 py-1 rounded border disabled:opacity-40">下一页</button>
          </div>
        )}
      </div>
    </div>
  );
}

function Card({ title, value, sub, accent, chip }: { title: string; value: number | string; sub?: string; accent?: string; chip?: string }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm text-gray-500">{title}</span>
        {chip && <span className={`w-3 h-3 rounded ${chip}`} />}
      </div>
      <div className={`text-2xl font-bold mt-1 ${accent || "text-gray-800"}`}>{value}</div>
      {sub && <div className="text-xs text-gray-400 mt-0.5">{sub}</div>}
    </div>
  );
}

function Empty({ text, retry }: { text: string; retry?: () => void }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border p-10 text-center text-gray-400">
      {text}
      {retry && <button onClick={retry} className="ml-3 text-blue-600 hover:underline text-sm">重试</button>}
    </div>
  );
}
