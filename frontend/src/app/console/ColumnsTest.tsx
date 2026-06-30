"use client";

import { useState, useEffect, useMemo } from "react";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet } from "./api";

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"];
type Cols = { name?: number | null; amount?: number | null; ratio?: number | null };
type ColStat = { col: number; text: number; number: number; ratio: number };
type Resp = {
  code?: string; field?: string; page?: number; n_cols?: number; table?: string[][];
  content_method?: Cols; header_method?: Cols | null; final?: Cols; has_yaml_rule?: boolean;
  col_stats?: ColStat[]; steps?: string[]; trace?: string[]; warn?: string; error?: string;
};

// 列角色 → 颜色
const colCls = (ci: number, f?: Cols) =>
  !f ? "" : ci === f.name ? "bg-blue-100" : ci === f.amount ? "bg-green-100" : ci === f.ratio ? "bg-orange-100" : "";
const roleOf = (ci: number, f?: Cols) =>
  !f ? "" : ci === f.name ? "名称" : ci === f.amount ? "金额" : ci === f.ratio ? "占比" : "";

export default function ColumnsTest({ initial }: { initial?: { code: string; year: number; field: string } } = {}) {
  const [code, setCode] = useState(initial?.code || "601127");
  const [year, setYear] = useState(initial?.year || 2025);
  const [field, setField] = useState(initial?.field || "revenue_breakdown");
  const [resp, setResp] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  const [pageImg, setPageImg] = useState<{ img: string; w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState(false);

  // 认列出结果后 → 拉该表所在页的 PDF 图(溯源)
  useEffect(() => {
    setPageImg(null);
    const pg = resp?.page;
    if (!pg || resp?.error) return;
    apiGet<{ page_image: string; page_w_pt: number; page_h_pt: number } | null>(
      `/debug/page?stock_code=${resp?.code || code}&year=${year}&page=${pg}`, null)
      .then(({ data, live }) => { if (live && data?.page_image) setPageImg({ img: data.page_image, w: data.page_w_pt, h: data.page_h_pt }); });
  }, [resp?.page, resp?.code]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = Object.entries(STOCK_NAMES);
    return (q ? all.filter(([c, n]) => c.includes(q) || (n || "").toLowerCase().includes(q)) : all).slice(0, 50);
  }, [query, resp]);

  const run = async (c = code, y = year, f = field) => {
    setLoading(true); setResp(null);
    await loadStockNames(); setTick((t) => t + 1);
    const { data, live } = await apiGet<Resp | null>(`/debug/columns?stock_code=${c}&year=${y}&field=${f}`, null);
    setLoading(false);
    setResp(live && data ? data : { error: "后端无响应（确认 :8200；该报告需先解析过有缓存）" });
  };
  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); }, []);

  const f = resp?.final;
  const cols = resp?.n_cols || 0;

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">认列测试台
          <span className="text-xs font-normal text-gray-400 ml-2">— 解析器怎么判 哪列是名称/金额/占比（内容法 + 表头法 + 最终）</span>
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
            {FIELDS.map((ff) => <option key={ff} value={ff}>{FIELD_LABEL[ff] || ff}</option>)}
          </select>
          <button onClick={() => run()} disabled={loading || !code}
            className="px-4 py-1.5 rounded bg-blue-600 text-white disabled:opacity-40 hover:bg-blue-700">
            {loading ? "认列中…" : "▶ 测试认列"}
          </button>
        </div>
      </div>

      {resp?.error && <div className="bg-white rounded-lg border p-6 text-center text-red-400">{resp.error}</div>}

      {resp && !resp.error && (
        <>
          <div className="bg-white rounded-lg shadow-sm border p-4 text-sm space-y-3">
            <div className="font-medium text-gray-700">这份财报实际是怎么认列的（{codeLabel(resp.code || code)}）</div>
            <ul className="text-xs text-gray-700 space-y-1">
              {(resp.trace || resp.steps || []).map((s, i) => (
                <li key={i} className={s.startsWith("✅") ? "font-medium text-green-700" : s.includes("空") || s.includes("没找到") || s.includes("没有任何") ? "text-orange-600" : ""}>{s}</li>
              ))}
            </ul>
            <div className="font-medium text-gray-700 pt-1">逐列数格子（认列的证据）</div>
            <table className="text-xs border-collapse">
              <thead><tr className="text-gray-400 text-left"><th className="pr-3">列</th><th className="px-3">文字</th><th className="px-3">数字(钱)</th><th className="px-3">百分比</th><th className="pl-3">→ 判成</th></tr></thead>
              <tbody>
                {(resp.col_stats || []).map((s) => (
                  <tr key={s.col} className={colCls(s.col, f)}>
                    <td className="pr-3">列{s.col}</td>
                    <td className="px-3 text-center">{s.text || ""}</td>
                    <td className="px-3 text-center">{s.number || ""}</td>
                    <td className="px-3 text-center">{s.ratio || ""}</td>
                    <td className="pl-3 font-medium">{roleOf(s.col, f) || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-xs text-gray-400">规则：百分比 ≥3 且 0~100 → 占比列；像钱的数 ≥3 → 金额列；剩下文字最多的 → 名称列。表头法(读YAML)若命中会覆盖。</div>
          </div>

          <div className="bg-white rounded-lg shadow-sm border p-4 text-sm space-y-2">
            <div className="flex items-center gap-3 flex-wrap">
              <span>{codeLabel(resp.code || code)} · {FIELD_LABEL[resp.field || field]} · 第{resp.page}页 · {cols}列</span>
              <span className={`text-xs px-2 py-0.5 rounded ${resp.has_yaml_rule ? "bg-green-100 text-green-700" : "bg-gray-100 text-gray-500"}`}>
                {resp.has_yaml_rule ? "✓ 读到 revenue.yaml 表头规则" : "✗ 没读到 YAML（走统计法）"}
              </span>
              <span className="ml-auto flex gap-2 text-xs">
                <span className="px-1.5 py-0.5 rounded bg-blue-100">名称</span>
                <span className="px-1.5 py-0.5 rounded bg-green-100">金额</span>
                <span className="px-1.5 py-0.5 rounded bg-orange-100">占比</span>
              </span>
            </div>
            <table className="text-xs border-collapse">
              <thead><tr className="text-gray-400 text-left"><th className="pr-4">方法</th>{Array.from({ length: cols }).map((_, ci) => <th key={ci} className="px-2">列{ci}</th>)}</tr></thead>
              <tbody>
                {([["内容法", resp.content_method], ["表头法", resp.header_method], ["最终采用", resp.final]] as [string, Cols | null | undefined][]).map(([label, m], r) => (
                  <tr key={r} className={r === 2 ? "font-medium" : ""}>
                    <td className="pr-4 text-gray-500">{label}{!m && r === 1 ? "（无YAML）" : ""}</td>
                    {Array.from({ length: cols }).map((_, ci) => (
                      <td key={ci} className={`px-2 text-center ${colCls(ci, m || undefined)}`}>{roleOf(ci, m || undefined)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {f && (f.ratio === null || f.ratio === undefined) && <div className="text-xs text-orange-500">⚠ 没认出占比列——若该表确有占比列，解析出的 ratio 会是 null（赛力斯就是这情况）。</div>}
          </div>

          <div className="bg-white rounded-lg shadow-sm border p-3 overflow-auto">
            <div className="text-xs text-gray-400 mb-1">选中表（按最终认列上色，前 30 行）</div>
            <table className="text-xs border-collapse">
              <tbody>
                {(resp.table || []).map((row, ri) => (
                  <tr key={ri} className="border-t">
                    <td className="px-1 text-gray-300 text-right pr-2">{ri}</td>
                    {Array.from({ length: cols }).map((_, ci) => (
                      <td key={ci} className={`px-2 py-0.5 border-l ${colCls(ci, f)}`}>{row[ci] || ""}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {pageImg && (
            <div className="bg-white rounded-lg shadow-sm border p-3">
              <div className="text-xs text-gray-400 mb-1">溯源 · PDF 第 {resp.page} 页（原表出处，点图放大对照）</div>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={pageImg.img} alt={`page${resp.page}`} onClick={() => setZoom(true)}
                className="w-full max-w-2xl mx-auto border cursor-zoom-in" />
            </div>
          )}
        </>
      )}

      {zoom && pageImg && (
        <div onClick={() => setZoom(false)} className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 cursor-zoom-out">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={pageImg.img} alt="zoom" className="max-h-full max-w-full object-contain" />
        </div>
      )}
    </div>
  );
}
