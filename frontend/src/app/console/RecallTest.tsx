"use client";

import { useState, useEffect, useMemo } from "react";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet } from "./api";

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "top_clients", "top_suppliers", "employees"];
type Cand = { page: number; recall_score: number; anchor_rel: number | null; amount_col: number | null; dim_count: number | null; caption?: string; doc: string };
type Sel = { page: number; amount_col: number | null; anchor_rel: number | null; dim_count: number | null; via: string };
type Resp = {
  sig?: string; query?: string; total_tables?: number; has_anchor?: boolean;
  candidates?: Cand[]; selected?: Sel | null; error?: string;
};

export default function RecallTest({ initial }: { initial?: { code: string; year: number; field: string } } = {}) {
  const [code, setCode] = useState(initial?.code || "601127");
  const [year, setYear] = useState(initial?.year || 2025);
  const [field, setField] = useState(initial?.field || "revenue_breakdown");
  const [resp, setResp] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  const [ran, setRan] = useState<{ code: string; year: number } | null>(null);
  const [viewPage, setViewPage] = useState<number | null>(null);
  const [pageImg, setPageImg] = useState<{ img: string; w: number; h: number } | null>(null);
  const [zoom, setZoom] = useState(false);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = Object.entries(STOCK_NAMES);
    return (q ? all.filter(([c, n]) => c.includes(q) || (n || "").toLowerCase().includes(q)) : all).slice(0, 50);
  }, [query, resp]);

  const run = async (c = code, y = year, f = field) => {
    setLoading(true); setResp(null); setPageImg(null); setViewPage(null);
    await loadStockNames(); setTick((t) => t + 1);
    const { data, live } = await apiGet<Resp | null>(`/debug/recall?stock_code=${c}&year=${y}&field=${f}`, null);
    setLoading(false);
    if (live && data) { setResp(data); setRan({ code: c, year: y }); }
    else setResp({ error: "后端无响应（确认 :8200；BGE 首次加载~数秒；该报告需先解析过有缓存）" });
  };
  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); }, []);

  // 出结果后 → 溯源默认落到选中表所在页(无选中则第一个候选页)
  useEffect(() => {
    if (!resp || resp.error) return;
    setViewPage(resp.selected?.page ?? resp.candidates?.[0]?.page ?? null);
  }, [resp]);

  // 溯源页变化 → 拉该页 PDF 图
  useEffect(() => {
    setPageImg(null);
    if (!viewPage || !ran) return;
    apiGet<{ page_image: string; page_w_pt: number; page_h_pt: number } | null>(
      `/debug/page?stock_code=${ran.code}&year=${ran.year}&page=${viewPage}`, null)
      .then(({ data, live }) => { if (live && data?.page_image) setPageImg({ img: data.page_image, w: data.page_w_pt, h: data.page_h_pt }); });
  }, [viewPage, ran]);

  const sel = resp?.selected;
  const relCls = (r: number | null) => r == null ? "text-gray-300" : r <= 0.03 ? "text-green-600" : r <= 0.05 ? "text-orange-500" : "text-gray-400";

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">选表解耦测试台
          <span className="text-xs font-normal text-gray-400 ml-2">— ① 向量召回(去数字语义) → ② 锚精判(列和≈锚) → ③ 维度数闸 → 定表+定金额列</span>
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
            {loading ? "召回中…" : "▶ 跑选表解耦"}
          </button>
        </div>
      </div>

      {resp?.error && <div className="bg-white rounded-lg border p-6 text-center text-red-400">{resp.error}</div>}

      {resp && !resp.error && (
        <>
          <div className="bg-white rounded-lg shadow-sm border p-4 text-sm space-y-1">
            <div className="text-gray-500">意图参照：<span className="text-gray-700">{resp.query}</span></div>
            <div className="flex items-center gap-3 flex-wrap">
              <span>全表 {resp.total_tables} 张</span>
              <span className={`text-xs px-2 py-0.5 rounded ${resp.has_anchor ? "bg-green-100 text-green-700" : "bg-orange-100 text-orange-600"}`}>
                {resp.has_anchor ? "✓ 有锚（走 召回+锚+维度）" : "✗ 无锚（只走召回，较弱）"}
              </span>
              {sel && <span className="px-3 py-1 rounded bg-green-600 text-white font-medium">→ 选中 第{sel.page}页 · 金额列{sel.amount_col} · {sel.via}</span>}
            </div>
          </div>

          <div className="bg-white rounded-lg shadow-sm border overflow-hidden">
            <div className="px-3 py-2 text-xs text-gray-500 border-b">召回候选（三路信号；绿=最终选中行）</div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-gray-400 text-left text-xs">
                <tr><th className="px-3 py-1.5">页</th><th>召回分</th><th>对锚误差</th><th>维度数</th><th>金额列</th><th>表标题 + 表文档（去数字）</th></tr>
              </thead>
              <tbody>
                {(resp.candidates || []).map((c, i) => {
                  const picked = sel && c.page === sel.page && c.dim_count === sel.dim_count;
                  const viewing = c.page === viewPage;
                  return (
                    <tr key={i} onClick={() => setViewPage(c.page)}
                      className={`border-t cursor-pointer hover:bg-blue-50 ${picked ? "bg-green-50" : ""} ${viewing ? "ring-1 ring-inset ring-blue-400" : ""}`}
                      title="点看该页 PDF 原页">
                      <td className="px-3 py-1.5 text-xs">{picked ? "✓ " : ""}p{c.page}</td>
                      <td className="px-3 py-1.5 text-xs">{c.recall_score?.toFixed(3)}</td>
                      <td className={`px-3 py-1.5 text-xs ${relCls(c.anchor_rel)}`}>{c.anchor_rel == null ? "—" : (c.anchor_rel * 100).toFixed(2) + "%"}</td>
                      <td className="px-3 py-1.5 text-xs">{c.dim_count ?? "—"}</td>
                      <td className="px-3 py-1.5 text-xs">{c.amount_col ?? "—"}</td>
                      <td className="px-3 py-1.5 text-xs truncate max-w-[380px]">
                        {c.caption && <span className="text-gray-700 font-medium mr-1">「{c.caption}」</span>}
                        <span className="text-gray-400">{c.doc}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="px-3 py-2 text-xs text-gray-400">召回=语义相似(表标题「」+去数字表文字)；对锚误差=某列和≈营业收入的最小误差；维度数=覆盖分行业/产品/地区/销售模式几个。最终：对锚≤5% 里选维度最多、平票取对锚最近。</div>
          </div>

          <div className="bg-white rounded-lg shadow-sm border p-3">
            <div className="text-xs text-gray-400 mb-1">
              溯源 · PDF 第 {viewPage ?? "?"} 页
              {sel && viewPage === sel.page && <span className="text-green-600"> · 选中表出处</span>}
              <span className="text-gray-300">（点上方候选行切换页 · 点图放大对照）</span>
            </div>
            {pageImg ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pageImg.img} alt={`page${viewPage}`} onClick={() => setZoom(true)}
                className="w-full max-w-2xl mx-auto border cursor-zoom-in" />
            ) : (
              <div className="text-xs text-gray-400 p-6 text-center">{viewPage ? "渲染中…（该报告需先解析过有页缓存）" : "无溯源页"}</div>
            )}
          </div>
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
