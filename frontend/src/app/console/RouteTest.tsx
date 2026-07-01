"use client";

import { useState, useEffect, useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet } from "./api";
import TestHistory from "./TestHistory";

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"];
const DIM_CN: Record<string, string> = { industries: "分行业", segments: "分产品", regions: "分地区", by_channel: "分销售模式", 明细: "明细", rnd_detail: "研发明细" };
const yi = (n: number) => (Math.abs(n) >= 1e8 ? (n / 1e8).toFixed(2) + "亿" : n.toLocaleString());
type Item = Record<string, unknown>;
const asDims = (result: Record<string, Item[]> | Item[] | null | undefined): [string, Item[]][] => {
  if (Array.isArray(result)) return [["明细", result]];
  if (result && typeof result === "object") return Object.entries(result).filter(([, v]) => Array.isArray(v)) as [string, Item[]][];
  return [];
};
const numKeys = (it: Item) => Object.keys(it).filter((k) => k !== "name" && typeof it[k] === "number");
const fmtCell = (v: unknown, k: string) => (typeof v !== "number" ? "" : k.includes("ratio") ? v + "%" : yi(v));
type Resp = {
  code?: string; field?: string; fingerprint?: string; cache_hit?: boolean;
  status?: string; parser_key?: string | null;
  n_certified_field?: number; certified_keys?: string[]; fp_matched?: string[];
  tried?: [string, boolean][]; confidence?: string; anchored?: boolean | null; anchor?: number | null;
  result_summary?: Record<string, number>; error?: string;
  result?: Record<string, Item[]> | Item[] | null; amount_key?: string; page?: number | null;
};

type RouteProps = {
  initial?: { code: string; year: number; field: string };
  onNext?: (s: { code: string; year: number; field: string }) => void;
};

export default function RouteTest({ initial, onNext }: RouteProps = {}) {
  const sp = useSearchParams();
  const [code, setCode] = useState(initial?.code || sp.get("code") || "000425");
  const [year, setYear] = useState(initial?.year || Number(sp.get("year")) || 2025);
  const [field, setField] = useState(initial?.field || sp.get("field") || "revenue_breakdown");
  const [resp, setResp] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [namesTick, setNamesTick] = useState(0);
  const [runCount, setRunCount] = useState(0);
  const [pageImg, setPageImg] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);
  const pick = (c: string, y: number, f: string) => { setCode(c); setYear(y); setField(f); run(c, y, f); };

  // 结果出来后 → 拉溯源页的 PDF 图
  useEffect(() => {
    setPageImg(null);
    const pg = resp?.page;
    if (!pg || resp?.error) return;
    apiGet<{ page_image: string } | null>(`/debug/page?stock_code=${resp?.code || code}&year=${year}&page=${pg}`, null)
      .then(({ data, live }) => { if (live && data?.page_image) setPageImg(data.page_image); });
  }, [resp?.page, resp?.code]);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = Object.entries(STOCK_NAMES);
    return (q ? all.filter(([c, n]) => c.includes(q) || (n || "").toLowerCase().includes(q)) : all).slice(0, 50);
  }, [query, namesTick]);

  const run = async (c = code, y = year, f = field) => {
    setLoading(true); setResp(null);
    await loadStockNames(); setNamesTick((t) => t + 1);
    const { data, live } = await apiGet<Resp | null>(`/debug/route?stock_code=${c}&year=${y}&field=${f}`, null);
    setLoading(false);
    setResp(live && data ? data : { error: "后端无响应（确认 :8200 已启动；该报告需先解析过有缓存）" });
    setRunCount((n) => n + 1);
  };
  // 带参数进来(从选表台确认跳转)→自动跑一次
  useEffect(() => { loadStockNames().then(() => setNamesTick((t) => t + 1)); if (initial?.code || sp.get("code")) run(); }, []);

  const routed = resp?.status === "routed";

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">路由测试台
          <span className="text-xs font-normal text-gray-400 ml-2">— 这份报告的版式指纹命中哪个认证解析器、路由到谁、过锚没</span>
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
            {loading ? "测试中…" : "▶ 测试路由"}
          </button>
          {onNext && (
            <button onClick={() => onNext({ code, year, field })} disabled={!resp || !!resp.error}
              title="下一步：未命中时测冷启动解析"
              className="px-4 py-1.5 rounded bg-green-600 text-white disabled:opacity-40 hover:bg-green-700">
              下一步：冷启动解析 →
            </button>
          )}
        </div>
      </div>

      <TestHistory stage="route" onPick={pick} refreshKey={runCount} />

      {resp?.error && <div className="bg-white rounded-lg border p-6 text-center text-red-400">{resp.error}</div>}

      {resp && !resp.error && (
        <>
          <div className="bg-white rounded-lg shadow-sm border p-4 space-y-3">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-sm">{codeLabel(resp.code || code)} · {FIELD_LABEL[resp.field || field]}</span>
              <span className={`px-3 py-1 rounded text-sm font-medium ${routed ? "bg-green-100 text-green-700" : "bg-red-100 text-red-600"}`}>
                {routed ? "✓ 路由命中（routed）" : "✗ 未命中（needs_repair → 走冷启动/需自愈）"}
              </span>
              {resp.cache_hit && <span className="text-xs text-gray-400">缓存路由</span>}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              <Box label="版式指纹" value={resp.fingerprint || "—"} mono />
              <Box label="路由到解析器" value={resp.parser_key || "—"} />
              <Box label="锚验证" value={resp.confidence || "—"} cls={resp.confidence === "high" ? "text-green-600" : resp.confidence === "low" ? "text-orange-500" : "text-gray-400"} />
              <Box label="该字段认证器" value={`${resp.n_certified_field ?? 0} 个 · 指纹命中 ${resp.fp_matched?.length ?? 0}`} />
            </div>
            {resp.result_summary && Object.keys(resp.result_summary).length > 0 && (
              <div className="text-xs text-gray-500">结果概览：{Object.entries(resp.result_summary).map(([k, v]) => `${FIELD_LABEL[k] || k} ${v}项`).join(" · ")}</div>
            )}
          </div>

          <div className="bg-white rounded-lg shadow-sm border p-4">
            <h3 className="text-sm font-medium mb-2">选择即验证：试过的认证解析器候选（跑它、按硬规则判过没）</h3>
            {(!resp.tried || resp.tried.length === 0) ? (
              <div className="text-xs text-gray-400">{resp.cache_hit ? "走了缓存路由（上次已确定该指纹用哪个），未重试候选" : "无候选（该字段还没有认证解析器）"}</div>
            ) : (
              <ul className="text-sm space-y-1">
                {resp.tried.map(([key, ok], i) => (
                  <li key={i} className="flex items-center gap-2">
                    <span className={`px-1.5 py-0.5 rounded text-xs ${ok ? "bg-green-100 text-green-700" : "bg-red-50 text-red-500"}`}>{ok ? "✓ 过" : "✗ 没过"}</span>
                    <span className="text-gray-700">{key}</span>
                  </li>
                ))}
              </ul>
            )}
            {resp.certified_keys && resp.certified_keys.length > 0 && (
              <div className="mt-2 text-xs text-gray-400">该字段全部认证器：{resp.certified_keys.join(" / ")}</div>
            )}
          </div>

          {/* 解析结果明细 */}
          {asDims(resp.result).length > 0 && (
            <div className="bg-white rounded-lg shadow-sm border p-4 space-y-3">
              <h3 className="text-sm font-medium text-gray-600">解析结果（{routed ? `认证解析器「${resp.parser_key}」直出` : "冷启动"}）</h3>
              {asDims(resp.result).map(([dim, rows]) => (
                <div key={dim}>
                  <div className="text-xs text-gray-500 mb-1">{DIM_CN[dim] || dim} · {rows.length} 项</div>
                  <table className="w-full text-sm">
                    <tbody>
                      {rows.map((it, i) => (
                        <tr key={i} className="border-t">
                          <td className="px-2 py-1 text-gray-700">{String(it.name ?? "")}</td>
                          {numKeys(it).map((k) => (
                            <td key={k} className="px-2 py-1 text-right text-gray-600 tabular-nums whitespace-nowrap">{fmtCell(it[k], k)}</td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ))}
            </div>
          )}

          {/* 溯源 PDF 原页 */}
          <div className="bg-white rounded-lg shadow-sm border p-3">
            <div className="text-xs text-gray-400 mb-1">
              溯源 · PDF 第 {resp.page ?? "?"} 页（该字段所在表出处，点图放大对照）
              {routed && <span className="text-gray-300"> · 认证解析器不产单元格坐标，展示整页对照</span>}
            </div>
            {pageImg ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={pageImg} alt={`page${resp.page}`} onClick={() => setZoom(true)}
                className="w-full max-w-2xl mx-auto border cursor-zoom-in" />
            ) : (
              <div className="text-xs text-gray-400 p-6 text-center">{resp.page ? "渲染中…（该报告需先解析过有页缓存）" : "无溯源页"}</div>
            )}
          </div>
        </>
      )}

      {zoom && pageImg && (
        <div onClick={() => setZoom(false)} className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-4 cursor-zoom-out">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={pageImg} alt="zoom" className="max-h-full max-w-full object-contain" />
        </div>
      )}
    </div>
  );
}

function Box({ label, value, cls, mono }: { label: string; value: string; cls?: string; mono?: boolean }) {
  return (
    <div className="bg-gray-50 rounded px-3 py-2">
      <div className={`text-sm font-medium ${cls || "text-gray-800"} ${mono ? "font-mono" : ""} break-all`}>{value}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}
