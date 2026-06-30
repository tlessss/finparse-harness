"use client";

import { useState, useEffect, useMemo } from "react";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet } from "./api";

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"];
const DIM_CN: Record<string, string> = { industries: "分行业", segments: "分产品", regions: "分地区", by_channel: "分销售模式", 明细: "明细" };
const yi = (n: number) => (typeof n === "number" && Math.abs(n) >= 1e8 ? (n / 1e8).toFixed(2) + "亿" : String(n));

type Dim = { dim: string; n: number; sum: number; match: boolean };
type Resp = {
  code?: string; field?: string; anchor?: number | null; dims?: Dim[];
  verdict?: string; reason?: string; need_heal?: boolean; fix_hint?: string | null;
  any_match?: boolean; dims_agree?: boolean | null; error?: string;
};

// 判定 → 颜色
const verdictStyle = (v?: string, need?: boolean) =>
  need ? "bg-red-100 text-red-700 border-red-300"
    : v?.includes("口径") ? "bg-amber-100 text-amber-700 border-amber-300"
      : v?.includes("无锚") ? "bg-gray-100 text-gray-600 border-gray-300"
        : "bg-green-100 text-green-700 border-green-300";

export default function HealTest({ initial }: { initial?: { code: string; year: number; field: string } } = {}) {
  const [code, setCode] = useState(initial?.code || "601127");
  const [year, setYear] = useState(initial?.year || 2025);
  const [field, setField] = useState(initial?.field || "revenue_breakdown");
  const [resp, setResp] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = Object.entries(STOCK_NAMES);
    return (q ? all.filter(([c, n]) => c.includes(q) || (n || "").toLowerCase().includes(q)) : all).slice(0, 50);
  }, [query, resp]);

  const run = async (c = code, y = year, f = field) => {
    setLoading(true); setResp(null);
    await loadStockNames(); setTick((t) => t + 1);
    const { data, live } = await apiGet<Resp | null>(`/debug/heal?stock_code=${c}&year=${y}&field=${f}`, null);
    setLoading(false);
    setResp(live && data ? data : { error: "后端无响应（确认 :8200；该报告需先解析过有缓存）" });
  };
  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); }, []);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">自愈测试台
          <span className="text-xs font-normal text-gray-400 ml-2">— 真失败筛子：先判要不要自愈（锚/维度一致当裁判，别修没坏的），要修才出病历</span>
        </h2>
        <div className="flex items-center gap-2 flex-wrap text-sm mt-2">
          <div className="relative">
            <input value={open ? query : codeLabel(code)}
              onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
              onFocus={() => { setQuery(""); setOpen(true); }}
              onBlur={() => setTimeout(() => setOpen(false), 150)}
              placeholder="选公司（搜名称/代码）" className="border rounded px-2 py-1 w-56" />
            {open && (
              <div className="absolute z-20 mt-1 w-72 max-h-72 overflow-auto bg-white border rounded shadow text-sm">
                {matches.map(([c, n]) => (
                  <div key={c} onMouseDown={() => { setCode(c); setOpen(false); }}
                    className="px-2 py-1.5 hover:bg-blue-50 cursor-pointer">{n} <span className="text-gray-400 text-xs">({c})</span></div>
                ))}
              </div>
            )}
          </div>
          <input value={year} onChange={(e) => setYear(Number(e.target.value))} className="border rounded px-2 py-1 w-20" />
          <select value={field} onChange={(e) => setField(e.target.value)} className="border rounded px-2 py-1">
            {FIELDS.map((f) => <option key={f} value={f}>{FIELD_LABEL[f] || f}</option>)}
          </select>
          <button onClick={() => run()} disabled={loading || !code}
            className="px-4 py-1.5 rounded bg-blue-600 text-white disabled:opacity-40 hover:bg-blue-700">
            {loading ? "诊断中…" : "▶ 诊断要不要自愈"}
          </button>
        </div>
      </div>

      {resp?.error && <div className="bg-white rounded-lg border p-6 text-center text-red-400">{resp.error}</div>}

      {resp && !resp.error && (
        <>
          <div className="bg-white rounded-lg shadow-sm border p-4 space-y-2">
            <div className="flex items-center gap-3 flex-wrap text-sm">
              <span>{codeLabel(resp.code || code)} · {FIELD_LABEL[resp.field || field]}</span>
              <span className={`px-3 py-1 rounded text-sm font-medium border ${verdictStyle(resp.verdict, resp.need_heal)}`}>
                {resp.need_heal ? "🔧 需自愈" : "✓ "}{resp.verdict}
              </span>
              <span className="text-xs text-gray-500">锚 {resp.anchor != null ? yi(resp.anchor) : "无"}</span>
            </div>
            <p className="text-sm text-gray-700">{resp.reason}</p>
            {resp.fix_hint && <p className="text-sm text-red-600">修复方向：{resp.fix_hint}</p>}
            <div className="text-xs text-gray-400">
              裁判依据：任一维度过锚={String(resp.any_match)} · 各维度互相一致={resp.dims_agree === null ? "—" : String(resp.dims_agree)}
            </div>
          </div>

          <div className="bg-white rounded-lg shadow-sm border p-4">
            <h3 className="text-sm font-medium mb-2">证据：各维度分项和 vs 锚</h3>
            <table className="w-full text-sm">
              <thead className="text-gray-400 text-left text-xs"><tr><th className="py-1">维度</th><th>条数</th><th>分项和</th><th>对锚</th></tr></thead>
              <tbody>
                {(resp.dims || []).map((d, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-1.5">{DIM_CN[d.dim] || d.dim}</td>
                    <td>{d.n}</td>
                    <td className="tabular-nums">{yi(d.sum)}</td>
                    <td>{d.match ? <span className="text-green-600 text-xs">✓ 过锚</span> : <span className="text-orange-500 text-xs">✗</span>}</td>
                  </tr>
                ))}
                {!(resp.dims || []).length && <tr><td colSpan={4} className="py-6 text-center text-gray-400">解析为空</td></tr>}
              </tbody>
            </table>
            <div className="text-xs text-gray-400 mt-2">规则：任一维度过锚 → 无需自愈；都不过但各维度互相一致 → 多是口径差(非bug)；维度互相矛盾 → 真bug，进自愈。</div>
          </div>
        </>
      )}
    </div>
  );
}
