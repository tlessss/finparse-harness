"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet } from "./api";
import TestHistory from "./TestHistory";

type Comp = { label: string; delta: number; note: string };
type Cand = {
  page: number; total: number; selected: boolean; reject: string | null;
  caption: string; section: string; rows: number; components: Comp[]; preview: string[][];
  table_bbox?: number[] | null;
};
type PageImg = { img: string; w: number; h: number };
type Resp = {
  code?: string; field?: string; sig?: string; anchor?: number | null;
  total_tables?: number; candidates?: Cand[]; error?: string;
};

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"];
const SECTION_CN: Record<string, string> = { management: "管理层讨论", gov: "公司治理", fuzhu: "财务附注", other: "其它" };

type SelectProps = {
  initial?: { code: string; year: number; field: string };
  onConfirm?: (s: { code: string; year: number; field: string }) => void;
};

export default function SelectTest({ initial, onConfirm }: SelectProps = {}) {
  const [code, setCode] = useState(initial?.code || "000333");
  const [year, setYear] = useState(initial?.year || 2025);
  const [field, setField] = useState(initial?.field || "revenue_breakdown");
  const [resp, setResp] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [pageImg, setPageImg] = useState<Record<number, PageImg>>({});
  const [shown, setShown] = useState<Set<number>>(new Set());
  const [zoom, setZoom] = useState<{ img: string; w: number; h: number; bbox?: number[] | null; page: number } | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [namesTick, setNamesTick] = useState(0);
  const [runCount, setRunCount] = useState(0);
  const router = useRouter();
  const pick = (c: string, y: number, f: string) => { setCode(c); setYear(y); setField(f); run(c, y, f); };
  useEffect(() => { loadStockNames().then(() => setNamesTick((t) => t + 1)); }, []);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = Object.entries(STOCK_NAMES);                 // [code, name]
    const f = q ? all.filter(([c, n]) => c.includes(q) || (n || "").toLowerCase().includes(q)) : all;
    return f.slice(0, 50);
  }, [query, namesTick]);

  const togglePage = async (page: number) => {
    setShown((s) => { const n = new Set(s); if (n.has(page)) n.delete(page); else n.add(page); return n; });
    const want = [page - 1, page, page + 1].filter((p) => p >= 1 && !pageImg[p]);   // 上/本/下页
    const results = await Promise.all(want.map((p) =>
      apiGet<{ page_image: string; page_w_pt: number; page_h_pt: number } | null>(
        `/debug/page?stock_code=${code}&year=${year}&page=${p}`, null).then((r) => ({ p, r }))));
    const upd: Record<number, PageImg> = {};
    for (const { p, r } of results) if (r.live && r.data?.page_image) upd[p] = { img: r.data.page_image, w: r.data.page_w_pt, h: r.data.page_h_pt };
    if (Object.keys(upd).length) setPageImg((prev) => ({ ...prev, ...upd }));
  };

  const run = async (c = code, y = year, f = field) => {
    setLoading(true); setResp(null); setShown(new Set()); setPageImg({});
    await loadStockNames();
    const { data, live } = await apiGet<Resp | null>(
      `/debug/select?stock_code=${c}&year=${y}&field=${f}`, null);
    setLoading(false);
    setResp(live && data ? data : { error: "后端无响应（确认 http://localhost:8200 已启动；该报告需先解析过一次有缓存）" });
    setRunCount((n) => n + 1);
  };

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">选表调试台
          <span className="text-xs font-normal text-gray-400 ml-2">— 测某报告某字段，filter_by_signature 选得准不准、为什么</span>
        </h2>
        <div className="flex items-center gap-2 flex-wrap text-sm mt-2">
          <div className="relative">
            <input
              value={open ? query : codeLabel(code)}
              onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
              onFocus={() => { setQuery(""); setOpen(true); }}
              onBlur={() => setTimeout(() => setOpen(false), 150)}
              placeholder="选公司（搜名称/代码）" className="border rounded px-2 py-1 w-56" />
            {open && (
              <div className="absolute z-20 mt-1 w-72 max-h-72 overflow-auto bg-white border rounded shadow text-sm">
                {matches.length === 0 && <div className="px-2 py-1.5 text-xs text-gray-400">无匹配（公司名加载中或拼写不符）</div>}
                {matches.map(([c, n]) => (
                  <div key={c} onMouseDown={() => { setCode(c); setOpen(false); }}
                    className="px-2 py-1.5 hover:bg-blue-50 cursor-pointer">
                    {n} <span className="text-gray-400 text-xs">({c})</span>
                  </div>
                ))}
                {matches.length >= 50 && <div className="px-2 py-1 text-xs text-gray-400">…仅显示前50，继续输入缩小</div>}
              </div>
            )}
          </div>
          <input value={year} onChange={(e) => setYear(Number(e.target.value))} className="border rounded px-2 py-1 w-20" />
          <select value={field} onChange={(e) => setField(e.target.value)} className="border rounded px-2 py-1">
            {FIELDS.map((f) => <option key={f} value={f}>{FIELD_LABEL[f] || f}</option>)}
          </select>
          <button onClick={() => run()} disabled={loading || !code}
            className="px-4 py-1.5 rounded bg-blue-600 text-white disabled:opacity-40 hover:bg-blue-700">
            {loading ? "测试中…" : "▶ 测试选表"}
          </button>
          <button onClick={() => onConfirm
            ? onConfirm({ code, year, field })
            : router.push(`/console/route-test?code=${code}&year=${year}&field=${field}`)}
            disabled={!resp || !!resp.error}
            title="选表没问题 → 带着这份报告+字段进入下一步：路由测试"
            className="px-4 py-1.5 rounded bg-green-600 text-white disabled:opacity-40 hover:bg-green-700">
            ✓ 确认，进入路由测试 →
          </button>
        </div>
        {resp && !resp.error && (
          <div className="text-xs text-gray-500 mt-2">
            {codeLabel(resp.code || code)} · {FIELD_LABEL[resp.field || field]} ·
            锚={resp.anchor != null ? (resp.anchor / 1e8).toFixed(2) + "亿" : "无（该字段无DB锚）"} ·
            全表 {resp.total_tables} 张 · 相关候选 {resp.candidates?.length || 0} 张
            <span className="ml-2 text-gray-400">（top 候选即解析器会用的表）</span>
          </div>
        )}
      </div>

      <TestHistory stage="select" onPick={pick} refreshKey={runCount} />

      {resp?.error && <div className="bg-white rounded-lg border p-6 text-center text-red-400">{resp.error}</div>}
      {resp && !resp.error && !resp.candidates?.length && (
        <div className="bg-white rounded-lg border p-6 text-center text-gray-400">无相关候选表（可能抽取层就没抽到目标表）</div>
      )}

      {resp?.candidates?.map((c, i) => (
        <div key={i} className={`bg-white rounded-lg shadow-sm border p-4 ${c.selected && i === 0 ? "ring-2 ring-green-400" : c.selected ? "" : "opacity-70"}`}>
          <div className="flex items-center gap-3 mb-2 flex-wrap">
            <span className="text-2xl font-bold tabular-nums">{c.total}<span className="text-xs text-gray-400 font-normal ml-0.5">分</span></span>
            <span className="text-sm text-gray-600">第 {c.page} 页 · {c.rows} 行 · {SECTION_CN[c.section] || c.section}</span>
            {c.selected
              ? (i === 0
                ? <span className="px-2 py-0.5 rounded text-xs bg-green-100 text-green-700 font-medium">✓ 选中（top，会被解析器用）</span>
                : <span className="px-2 py-0.5 rounded text-xs bg-blue-50 text-blue-600">入选</span>)
              : <span className="px-2 py-0.5 rounded text-xs bg-red-100 text-red-600">✗ 淘汰：{c.reject}</span>}
          </div>
          <div className="text-xs text-gray-500 mb-2">标题(caption)：<span className="text-gray-800">{c.caption || "（无）"}</span></div>
          <div className="flex flex-wrap gap-1 mb-2">
            {c.components.map((x, j) => (
              <span key={j} className={`text-xs px-1.5 py-0.5 rounded ${x.delta >= 0 ? "bg-green-50 text-green-700" : "bg-red-50 text-red-600"}`}>
                {x.label} {x.delta >= 0 ? "+" : ""}{x.delta}{x.note ? ` · ${x.note}` : ""}
              </span>
            ))}
          </div>
          <div className="overflow-auto max-h-44 border rounded bg-gray-50">
            <table className="text-[10px] border-collapse">
              <tbody>
                {c.preview.map((row, r) => (
                  <tr key={r}>{row.map((cell, cc) => (
                    <td key={cc} className="border px-1 py-0.5 text-gray-600 whitespace-nowrap">{cell}</td>
                  ))}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <button onClick={() => togglePage(c.page)} className="mt-2 text-xs text-blue-600 hover:underline">
            {shown.has(c.page) ? "▲ 收起 PDF 原页" : `📄 看 PDF 原页（第 ${c.page} 页）`}
          </button>
          {shown.has(c.page) && (
            <div className="mt-2 overflow-x-auto">
              <div className="flex gap-2 items-start w-max">
                {[c.page - 1, c.page, c.page + 1].filter((p) => p >= 1).map((p) => {
                  const pi = pageImg[p]; const isCur = p === c.page;
                  return (
                    <div key={p} className="shrink-0">
                      <div className={`text-[10px] mb-0.5 ${isCur ? "text-red-600 font-medium" : "text-gray-400"}`}>
                        第 {p} 页{isCur ? "（选中表）" : p < c.page ? "（上一页）" : "（下一页）"}
                      </div>
                      {pi ? (
                        <div className="relative border rounded cursor-zoom-in" title="点击放大"
                          onClick={() => setZoom({ img: pi.img, w: pi.w, h: pi.h, bbox: isCur ? c.table_bbox : null, page: p })}
                          style={{ width: 240, aspectRatio: `${pi.w} / ${pi.h}` }}>
                          {/* eslint-disable-next-line @next/next/no-img-element */}
                          <img src={pi.img} alt={`page${p}`} className="absolute inset-0 w-full h-full object-contain" />
                          {isCur && c.table_bbox && c.table_bbox.length === 4 && (
                            <div className="absolute ring-2 ring-red-500 bg-red-500/10" style={{
                              left: `${(c.table_bbox[0] / pi.w) * 100}%`, top: `${(c.table_bbox[1] / pi.h) * 100}%`,
                              width: `${((c.table_bbox[2] - c.table_bbox[0]) / pi.w) * 100}%`,
                              height: `${((c.table_bbox[3] - c.table_bbox[1]) / pi.h) * 100}%`,
                            }} />
                          )}
                        </div>
                      ) : <div className="text-[10px] text-gray-300 flex items-center justify-center border rounded" style={{ width: 240, height: 320 }}>渲染中…</div>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      ))}

      {zoom && (
        <div onClick={() => setZoom(null)}
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center cursor-zoom-out">
          <div className="relative" style={{ height: "92vh", aspectRatio: `${zoom.w} / ${zoom.h}` }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={zoom.img} alt={`page${zoom.page}`} className="absolute inset-0 w-full h-full object-contain" />
            {zoom.bbox && zoom.bbox.length === 4 && (
              <div className="absolute ring-2 ring-red-500 bg-red-500/10 pointer-events-none" style={{
                left: `${(zoom.bbox[0] / zoom.w) * 100}%`, top: `${(zoom.bbox[1] / zoom.h) * 100}%`,
                width: `${((zoom.bbox[2] - zoom.bbox[0]) / zoom.w) * 100}%`,
                height: `${((zoom.bbox[3] - zoom.bbox[1]) / zoom.h) * 100}%`,
              }} />
            )}
          </div>
          <div className="absolute top-3 right-4 text-white text-sm">第 {zoom.page} 页 · 点击任意处关闭 ✕</div>
        </div>
      )}
    </div>
  );
}
