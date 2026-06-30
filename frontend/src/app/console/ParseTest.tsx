"use client";

import { useState, useEffect, useMemo } from "react";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet } from "./api";
import TestHistory from "./TestHistory";

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"];
const DIM_CN: Record<string, string> = { industries: "分行业", segments: "分产品", regions: "分地区", by_channel: "分销售模式", 明细: "明细", rnd_detail: "研发明细" };
const yi = (n: number) => (Math.abs(n) >= 1e8 ? (n / 1e8).toFixed(2) + "亿" : n.toLocaleString());
const asDims = (result: Record<string, unknown[]> | unknown[] | null | undefined): [string, Record<string, unknown>[]][] => {
  if (Array.isArray(result)) return [["明细", result as Record<string, unknown>[]]];
  if (result && typeof result === "object") return Object.entries(result).filter(([, v]) => Array.isArray(v)) as [string, Record<string, unknown>[]][];
  return [];
};
const numKeys = (it: Record<string, unknown>) => Object.keys(it).filter((k) => k !== "name" && typeof it[k] === "number");
const fmtCell = (v: unknown, k: string) => typeof v !== "number" ? "" : (k.includes("ratio") ? v + "%" : yi(v));

type Dim = { dim: string; n: number; sum: number; match: boolean };
type Prov = { page: number; bbox: [number, number, number, number] };
type Item = Record<string, unknown>;
type Resp = {
  code?: string; field?: string; parser?: string; status?: string;
  anchor?: number | null; confidence?: string; anchored?: boolean | null; dims?: Dim[]; error?: string;
  result?: Record<string, Item[]> | Item[] | null; amount_key?: string;
  page?: number | null; provenance?: Record<string, Prov> | null;
};
type PageImg = { img: string; w: number; h: number };

export default function ParseTest({ initial, onNext }: {
  initial?: { code: string; year: number; field: string };
  onNext?: (s: { code: string; year: number; field: string }) => void;
} = {}) {
  const [code, setCode] = useState(initial?.code || "000333");
  const [year, setYear] = useState(initial?.year || 2025);
  const [field, setField] = useState(initial?.field || "revenue_breakdown");
  const [resp, setResp] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  const [runCount, setRunCount] = useState(0);
  const [sel, setSel] = useState<string | null>(null);
  const [pageImg, setPageImg] = useState<PageImg | null>(null);
  const pick = (c: string, y: number, f: string) => { setCode(c); setYear(y); setField(f); run(c, y, f); };

  // 结果加载后 → 拉溯源页的 PDF 图
  useEffect(() => {
    setSel(null); setPageImg(null);
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
    const { data, live } = await apiGet<Resp | null>(`/debug/parse?stock_code=${c}&year=${y}&field=${f}`, null);
    setLoading(false);
    setResp(live && data ? data : { error: "后端无响应（确认 :8200 已启动；该报告需先解析过有缓存）" });
    setRunCount((n) => n + 1);
  };
  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); if (initial?.code) run(); }, []);

  const anchorMatched = resp?.dims?.some((d) => d.match);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">冷启动解析测试台
          <span className="text-xs font-normal text-gray-400 ml-2">— 路由未命中时,强制跑通用解析器,看各维度对不对锚</span>
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
            {loading ? "解析中…" : "▶ 测试冷启动解析"}
          </button>
          {onNext && (
            <button onClick={() => onNext({ code, year, field })} disabled={!resp || !!resp.error}
              title="下一步：让 LLM 对照原表判数据对不对(末道诊断)"
              className="px-4 py-1.5 rounded bg-purple-600 text-white disabled:opacity-40 hover:bg-purple-700">
              下一步：LLM 判定 →
            </button>
          )}
        </div>
      </div>

      <TestHistory stage="parse" onPick={pick} refreshKey={runCount} />

      {resp?.error && <div className="bg-white rounded-lg border p-6 text-center text-red-400">{resp.error}</div>}

      {resp && !resp.error && (
        <>
          <div className="bg-white rounded-lg shadow-sm border p-4 space-y-2">
            <div className="flex items-center gap-3 flex-wrap text-sm">
              <span>{codeLabel(resp.code || code)} · {FIELD_LABEL[resp.field || field]}</span>
              <span className="text-xs text-gray-400">解析器：{resp.parser}</span>
              <span className={`px-3 py-1 rounded text-sm font-medium ${anchorMatched ? "bg-green-100 text-green-700" : "bg-orange-100 text-orange-600"}`}>
                {anchorMatched ? "✓ 过锚（至少一维≈营业收入）" : "✗ 没过锚（无维度对得上）"}
              </span>
              <span className="text-xs text-gray-500">锚={resp.anchor != null ? yi(resp.anchor) : "无"} · 置信={resp.confidence}</span>
            </div>
          </div>

          <div className="bg-white rounded-lg shadow-sm border p-4">
            <h3 className="text-sm font-medium mb-2">各维度 vs 锚（绿=分项和≈营业收入；红=对不上，该维度抓串/漏/单位错）</h3>
            <table className="w-full text-sm">
              <thead className="text-gray-400 text-left text-xs">
                <tr><th className="py-1">维度</th><th>条数</th><th>分项和</th><th>对锚</th></tr>
              </thead>
              <tbody>
                {(resp.dims || []).map((d, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-1.5">{DIM_CN[d.dim] || d.dim}</td>
                    <td>{d.n}</td>
                    <td className="tabular-nums">{yi(d.sum)}</td>
                    <td>{d.match ? <span className="text-green-600 text-xs">✓ 过锚</span> : <span className="text-red-500 text-xs">✗ 对不上</span>}</td>
                  </tr>
                ))}
                {!(resp.dims || []).length && <tr><td colSpan={4} className="py-6 text-center text-gray-400">无维度数据（解析为空）</td></tr>}
              </tbody>
            </table>
            <div className="text-xs text-gray-400 mt-2">说明：字段只要**任一维度过锚**就算可信(high)；某维度红=那一维抓串/漏行/单位错，需修冷启动解析器或发专用解析器。</div>
          </div>

          {/* 二级明细 + PDF原页溯源 */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-white rounded-lg shadow-sm border p-4 space-y-3">
              <h3 className="text-sm font-medium text-gray-600">二级明细（点数字 → 右边 PDF 高亮出处）</h3>
              {asDims(resp.result).map(([dim, rows]) => (
                <div key={dim}>
                  <div className="text-xs text-gray-500 mb-1">{DIM_CN[dim] || dim}（{rows.length}）</div>
                  <table className="w-full text-xs">
                    <tbody>
                      {rows.map((it, i) => (
                        <tr key={i} className="border-t">
                          <td className="py-1 pr-2 text-gray-700">{String(it.name ?? "")}</td>
                          {numKeys(it).map((k) => {
                            const path = `${dim}[${i}].${k}`;
                            return (
                              <td key={k} onClick={() => setSel(path)}
                                className={`py-1 px-1 text-right cursor-pointer rounded tabular-nums ${sel === path ? "bg-red-100 text-red-700" : "hover:bg-gray-100"}`}>
                                {fmtCell(it[k], k)}
                              </td>
                            );
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>

            <div className="bg-white rounded-lg shadow-sm border p-2">
              <div className="text-xs text-gray-400 px-2 py-1">PDF 第 {resp.page ?? "?"} 页（溯源高亮；点明细/红框联动）</div>
              {pageImg ? (
                <div className="relative" style={{ aspectRatio: `${pageImg.w} / ${pageImg.h}` }}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={pageImg.img} alt={`page${resp.page}`} className="absolute inset-0 w-full h-full object-contain" />
                  {Object.entries(resp.provenance || {}).filter(([, p]) => p.page === resp.page).map(([path, p]) => {
                    const [x0, y0, x1, y1] = p.bbox; const active = sel === path;
                    return <div key={path} onClick={() => setSel(path)}
                      style={{ left: `${(x0 / pageImg.w) * 100}%`, top: `${(y0 / pageImg.h) * 100}%`, width: `${((x1 - x0) / pageImg.w) * 100}%`, height: `${((y1 - y0) / pageImg.h) * 100}%` }}
                      className={`absolute cursor-pointer transition ${active ? "ring-2 ring-red-500 bg-red-500/20" : "ring-1 ring-red-300/40 hover:bg-red-400/10"}`} />;
                  })}
                </div>
              ) : <div className="text-xs text-gray-400 p-4 text-center">{resp.page ? "渲染中…" : "无溯源（该解析器未产坐标）"}</div>}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
