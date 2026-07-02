"use client";

// 流水线成功率（暂时只看营收）+ 整条链路 + 失败原因。
// 数据：GET /pipeline/result、GET /pipeline/progress、GET /pipeline/chain。

import { useEffect, useState } from "react";
import { apiGet } from "./api";
import { FIELD_LABEL, codeLabel, loadStockNames } from "./consoleData";

const REV = "revenue_breakdown";
const DIM_CN: Record<string, string> = {
  segments: "分产品", industries: "分行业", regions: "分地区", by_channel: "分销售模式", 明细: "明细",
};

type Rate = {
  green: number; committed: number; verify_hold: number;
  non_green: number; no_data: number; no_anchor: number; no_input: number;
  success_rate: number | null; anchored_denominator: number;
};
type FieldOut = { field: string; outcome: string };
type Report = { code: string; fields: FieldOut[] };
type Result = {
  n_reports: number; year: number; overall: Rate;
  by_field: Record<string, Rate>; reports: Report[]; error?: string;
};
type Stage = { name: string; ok: boolean; detail: unknown };
type Provenance = { pages: number[]; items: { path: string; page: number }[]; n: number };
type Chain = {
  code: string; field: string; stages: Stage[]; outcome: string; reason: string;
  provenance?: Provenance; source_preview?: string;
  verify_cached?: { verdict?: string; summary?: string; handed_to_human?: boolean;
    suspects?: { field?: string; issue?: string; reason?: string }[] };
  _from_db?: boolean;
};
type Progress = {
  phase: string; total: number; i: number; current: string | null;
  done: { code: string; outcomes?: Record<string, string>; verdict?: string; outcome?: string }[];
};
type Llm = {
  outcome?: string; llm_kind?: string; verdict?: string; decision?: string;
  root_cause?: string; next_action?: string; handed_to_human?: boolean;
  suspects?: { field?: string; issue?: string; reason?: string }[];
  evidence?: string[] | string; summary?: string; committed?: string; llm_error?: string;
};

const OUTCOME: Record<string, { cls: string; label: string }> = {
  committed: { cls: "bg-green-100 text-green-700 border-green-300", label: "复核过·入库" },
  green: { cls: "bg-lime-100 text-lime-700 border-lime-300", label: "过锚·待复核" },
  verify_hold: { cls: "bg-rose-100 text-rose-700 border-rose-300", label: "复核否决·人工" },
  non_green: { cls: "bg-orange-100 text-orange-700 border-orange-300", label: "非绿灯·诊断" },
  no_data: { cls: "bg-red-100 text-red-700 border-red-300", label: "无数据" },
  no_anchor: { cls: "bg-slate-100 text-slate-500 border-slate-300", label: "无锚" },
  no_input: { cls: "bg-gray-100 text-gray-400 border-gray-300", label: "无输入" },
};
const PHASE_CN: Record<string, string> = { scan: "🔍 扫表中", analyze: "⚙ 分析中", verify: "🔬 复核中" };
const pct = (r: number | null) => (r == null ? "—" : `${(r * 100).toFixed(1)}%`);
const yi = (n: number | null | undefined) =>
  typeof n === "number" ? `${(n / 1e8).toFixed(2)}亿` : "—";

// 把 field_chain 每个阶段渲染成清晰的一行（不是原始 JSON）
function StageRow({ s }: { s: Stage }) {
  const d = s.detail as Record<string, unknown>;
  let body: React.ReactNode = null;

  if (s.name === "选表" && d && typeof d === "object") {
    body = <>p{String(d.page)} · 《{String(d.caption || "")}》 · {String(d.rows)}行 · {String(d.via)}</>;
  } else if (s.name === "解析" && d && typeof d === "object") {
    const dims = (d.dims as string[]) || [];
    body = <>{String(d.via)} · {dims.length} 维度：{dims.map((x) => DIM_CN[x] || x).join(" / ")}</>;
  } else if (s.name === "锚判" && d && typeof d === "object") {
    const anchor = d.anchor as number;
    const per = (d.per_dim as { dim: string; sum: number; match: boolean }[]) || [];
    const missing = (d.missing_dims as string[]) || [];
    body = (
      <div className="space-y-0.5">
        <div>锚 <b>{yi(anchor)}</b> · 置信 <b className={d.confidence === "high" ? "text-green-600" : "text-orange-600"}>{String(d.confidence)}</b></div>
        {per.map((p) => {
          const dev = anchor ? ((p.sum - anchor) / anchor) * 100 : 0;
          return (
            <div key={p.dim} className="flex gap-2 tabular-nums">
              <span className="w-16 text-gray-500">{DIM_CN[p.dim] || p.dim}</span>
              <span className="w-16 text-right">{yi(p.sum)}</span>
              <span className={p.match ? "text-green-600" : "text-orange-600"}>
                {p.match ? "✓过锚" : `✗差${dev.toFixed(0)}%`}
              </span>
            </div>
          );
        })}
        {!!missing.length && <div className="text-red-500">缺失维度：{missing.map((m) => DIM_CN[m] || m).join("、")}</div>}
      </div>
    );
  } else {
    body = <span className="text-gray-500">{typeof s.detail === "string" ? s.detail : JSON.stringify(s.detail)}</span>;
  }

  return (
    <li className="flex gap-2">
      <span className={`mt-0.5 shrink-0 ${s.ok ? "text-green-600" : "text-red-500"}`}>{s.ok ? "✓" : "✗"}</span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold">{s.name}</div>
        <div className="text-[11px] text-gray-600">{body}</div>
      </div>
    </li>
  );
}

export default function PipelinePanel() {
  const [res, setRes] = useState<Result | null>(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState<string | null>(null);
  const [chain, setChain] = useState<Chain | null>(null);
  const [chainLoading, setChainLoading] = useState(false);
  const [llm, setLlm] = useState<Llm | null>(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [prog, setProg] = useState<Progress | null>(null);
  const [, setTick] = useState(0);

  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); }, []);

  const load = () => apiGet<Result | null>("/pipeline/result", null).then(({ data, live }) => {
    if (!live || !data) { setErr("后端无响应（确认 :8200）"); return; }
    if (data.error) { setErr(data.error); return; }
    setErr(""); setRes(data);
  });
  useEffect(() => { load(); }, []);

  useEffect(() => {
    let stop = false;
    const poll = async () => {
      const { data } = await apiGet<Progress | null>("/pipeline/progress", null);
      if (stop) return;
      setProg(data);
      if (data && data.phase !== "idle" && data.phase !== "done") load();
    };
    poll();
    const id = setInterval(poll, 2500);
    return () => { stop = true; clearInterval(id); };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const openChain = async (code: string, fresh = false) => {
    setSel(code); setChain(null); setLlm(null); setChainLoading(true);
    const { data } = await apiGet<Chain | null>(
      `/pipeline/chain?stock_code=${code}&year=${res?.year || 2025}&field=${REV}${fresh ? "&fresh=1" : ""}`, null);
    setChainLoading(false); setChain(data);
    if (data?.verify_cached) {  // DB 里存过复核结论 → 直接显示，不用再点跑 LLM
      setLlm({ llm_kind: "verify", outcome: data.outcome, ...data.verify_cached });
    }
  };

  const runLlm = async () => {
    if (!sel) return;
    setLlmLoading(true); setLlm(null);
    const { data } = await apiGet<Llm | null>(
      `/pipeline/llm?stock_code=${sel}&year=${res?.year || 2025}&field=${REV}`, null);
    setLlmLoading(false); setLlm(data);
  };

  const running = prog && prog.phase !== "idle" && prog.phase !== "done";
  const doneGood = (prog?.done || []).filter(
    (d) => d.outcomes?.[REV] === "green" || d.verdict === "pass" || d.outcome === "committed").length;
  const rev = res?.by_field?.[REV];

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <div className="flex items-center gap-3">
          <h2 className="font-semibold">营收解析成功率</h2>
          <span className="text-xs text-gray-400">抽表 → 选表 → 解析 → 锚判 → 出口；绿灯 = 自主过锚可入库</span>
          <button onClick={load} className="ml-auto px-3 py-1 rounded bg-blue-600 text-white text-xs hover:bg-blue-700">刷新</button>
        </div>
        {err && <div className="text-red-500 text-sm mt-2">{err}</div>}
        {running && prog && (
          <div className="mt-3 rounded border border-blue-200 bg-blue-50 p-2.5">
            <div className="flex items-center gap-2 text-sm">
              <span className="inline-block w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
              <b>{PHASE_CN[prog.phase] || prog.phase}</b>
              <span className="tabular-nums text-gray-600">[{prog.i}/{prog.total}]</span>
              <span className="font-medium">{prog.current ? codeLabel(prog.current) : "…"}</span>
              <span className="ml-auto text-xs text-gray-500">已完成 {prog.done.length} · {prog.phase === "verify" ? "复核过" : "营收绿灯"} <b className="text-green-600">{doneGood}</b></span>
            </div>
            <div className="mt-1.5 h-1.5 w-full rounded bg-blue-100 overflow-hidden">
              <div className="h-full bg-blue-500 transition-all" style={{ width: `${(prog.i / (prog.total || 1)) * 100}%` }} />
            </div>
          </div>
        )}
        {rev && (
          <div className="mt-3 flex items-center gap-6">
            <div>
              <div className="text-3xl font-bold text-green-600">{pct(rev.success_rate)}</div>
              <div className="text-xs text-gray-400">营收成功率（{res?.n_reports} 份 · {res?.year}）</div>
            </div>
            <div className="text-sm text-gray-600">
              入库 <b className="text-green-600">{rev.committed}</b> · 待复核 <b className="text-lime-600">{rev.green}</b> · 复核否决 <b className="text-rose-600">{rev.verify_hold}</b> · 非绿灯 <b className="text-orange-600">{rev.non_green}</b> · 无数据 <b className="text-red-600">{rev.no_data}</b>
            </div>
            <div className="flex-1 flex h-3 rounded overflow-hidden bg-gray-100 max-w-md">
              {[["committed", "bg-green-500"], ["green", "bg-lime-400"], ["verify_hold", "bg-rose-400"], ["non_green", "bg-orange-400"], ["no_data", "bg-red-400"]].map(([k, cls]) => {
                const n = (rev as unknown as Record<string, number>)[k] || 0;
                return n ? <div key={k} className={cls} style={{ width: `${(n / rev.anchored_denominator) * 100}%` }} /> : null;
              })}
            </div>
          </div>
        )}
      </div>

      <div className="flex gap-4 items-start">
        {/* 报告列表（营收） */}
        <div className="w-72 shrink-0 bg-white rounded-lg shadow-sm border p-2 max-h-[70vh] overflow-auto">
          <div className="text-xs text-gray-400 px-2 py-1">点公司看链路（{res?.reports?.length || 0} 份）</div>
          {(res?.reports || []).map((rep) => {
            const o = rep.fields.find((x) => x.field === REV)?.outcome || "no_input";
            return (
              <button key={rep.code} onClick={() => openChain(rep.code)}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded mb-0.5 text-left ${sel === rep.code ? "bg-blue-50 ring-1 ring-blue-300" : "hover:bg-gray-50"}`}>
                <span className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 ${OUTCOME[o]?.cls}`}>{OUTCOME[o]?.label || o}</span>
                <span className="text-sm truncate">{codeLabel(rep.code)}</span>
              </button>
            );
          })}
          {!res?.reports?.length && <div className="text-xs text-gray-400 px-2 py-4">无结果</div>}
        </div>

        {/* 链路 */}
        <div className="flex-1 min-w-0 bg-white rounded-lg shadow-sm border p-4 sticky top-20">
          {!sel && <div className="text-sm text-gray-400">← 点左边一家公司，看营收整条链路</div>}
          {sel && (
            <>
              <div className="flex items-center gap-2 mb-2">
                <div className="text-sm font-semibold">{codeLabel(sel)} · 营收链路</div>
                {chain?._from_db && <span className="text-[10px] text-gray-400">DB 存档</span>}
                <button onClick={() => openChain(sel, true)} disabled={chainLoading}
                  className="ml-auto px-2.5 py-1 rounded bg-gray-100 text-gray-600 text-xs disabled:opacity-40 hover:bg-gray-200">
                  {chainLoading ? "重跑中…" : "🔄 重跑链路"}
                </button>
              </div>
              {chainLoading && <div className="text-xs text-gray-400">重新解析中…</div>}
              {chain && (
                <>
                  <div className={`rounded p-2.5 text-sm mb-4 border ${OUTCOME[chain.outcome]?.cls || ""}`}>
                    <b>{OUTCOME[chain.outcome]?.label || chain.outcome}</b>
                    <div className="text-xs font-normal mt-0.5">{chain.reason}</div>
                  </div>
                  <ol className="space-y-3 relative border-l-2 border-gray-100 pl-4">
                    {chain.stages.map((s, i) => <StageRow key={i} s={s} />)}
                  </ol>

                  {chain.provenance && (
                    <div className="mt-4 border-t pt-3">
                      <div className="text-xs font-semibold mb-1">
                        🔎 溯源
                        <span className="ml-2 font-normal text-gray-500">
                          来源页 {chain.provenance.pages.map((p) => `p${p}`).join("、") || "—"} · 共 {chain.provenance.n} 个值
                        </span>
                      </div>
                      {!!chain.provenance.items.length && (
                        <div className="text-[11px] text-gray-500 max-h-28 overflow-auto space-y-0.5">
                          {chain.provenance.items.map((it, i) => (
                            <div key={i} className="flex gap-2">
                              <span className="text-gray-400">p{it.page}</span>
                              <span className="truncate">{it.path}</span>
                            </div>
                          ))}
                        </div>
                      )}
                      {chain.source_preview && (
                        <details className="mt-2">
                          <summary className="text-[11px] text-gray-500 cursor-pointer">溯源原表（选中表原文，前30行）</summary>
                          <pre className="mt-1 text-[10px] bg-gray-50 border rounded p-2 whitespace-pre overflow-auto max-h-64">{chain.source_preview}</pre>
                        </details>
                      )}
                    </div>
                  )}

                  {/* LLM 诊断 / 复核（按需跑，~15s） */}
                  <div className="mt-4 border-t pt-3">
                    <button onClick={runLlm} disabled={llmLoading}
                      className="px-3 py-1.5 rounded bg-purple-600 text-white text-xs disabled:opacity-40 hover:bg-purple-700">
                      {llmLoading ? "🤖 LLM 分析中…(~15s)"
                        : chain.outcome === "green" ? "🤖 跑复核 agent（选错表/跨页体检）" : "🤖 跑诊断 agent（找根因）"}
                    </button>
                    {llm && (llm.llm_error ? (
                      <div className="text-red-500 text-xs mt-2">LLM 错误：{llm.llm_error}</div>
                    ) : (
                      <div className={`mt-2 rounded border p-2.5 text-xs space-y-1 ${
                        llm.outcome === "committed" ? "bg-green-50 border-green-300"
                          : llm.handed_to_human ? "bg-rose-50 border-rose-300" : "bg-amber-50 border-amber-300"}`}>
                        {llm.llm_kind === "verify" ? (
                          <>
                            <div><b>复核 agent</b> · verdict=<b>{llm.verdict}</b>
                              {llm.outcome === "committed" && <span className="ml-2 text-green-700">✅ 已入库（测试库）</span>}
                              {llm.handed_to_human && <span className="ml-2 text-rose-600">→ 交人工（分诊队列）</span>}
                            </div>
                            {llm.summary && <div className="text-gray-700">{llm.summary}</div>}
                            {(llm.suspects || []).map((s, i) => (
                              <div key={i} className="text-rose-700">⚠ [{s.issue}] {s.reason}</div>
                            ))}
                          </>
                        ) : (
                          <>
                            <div><b>诊断 agent</b> · <b>{llm.decision}</b> / {llm.root_cause} / {llm.next_action}
                              {llm.handed_to_human && <span className="ml-2 text-rose-600">→ 交人工</span>}
                            </div>
                            {llm.summary && <div className="text-gray-700">{llm.summary}</div>}
                            {(Array.isArray(llm.evidence) ? llm.evidence : llm.evidence ? [llm.evidence] : []).map((e, i) => (
                              <div key={i} className="text-gray-600">· {e}</div>
                            ))}
                          </>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
