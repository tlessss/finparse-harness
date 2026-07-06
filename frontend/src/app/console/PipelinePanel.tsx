"use client";

// 流水线成功率（暂时只看营收）+ 整条链路 + 失败原因。
// 数据：GET /pipeline/result、GET /pipeline/progress、GET /pipeline/chain。

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "./api";
import { FIELD_LABEL, codeLabel, loadStockNames } from "./consoleData";

const REV = "revenue_breakdown";
const DIM_CN: Record<string, string> = {
  segments: "分产品", industries: "分行业", regions: "分地区", by_channel: "分销售模式", 明细: "明细",
};

type Rate = {
  green: number; committed: number; verify_hold: number;
  non_green: number; no_data: number; no_anchor: number; no_input: number; no_such_table: number;
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
    suspects?: Suspect[]; heal?: Heal; healed_select?: boolean;
    reverify?: string; reverify_detail?: VerifyDetail;
    value?: unknown; chat?: Chat; reverify_chat?: Chat; diag_chat?: Chat; heal_probe?: Heal };
  value?: unknown; _from_db?: boolean;
};
type Progress = {
  phase: string; total: number; i: number; current: string | null;
  done: { code: string; outcomes?: Record<string, string>; verdict?: string; outcome?: string }[];
};
type Suspect = { field?: string; issue?: string; reason?: string };
type VerifyDetail = { verdict?: string; suspects?: Suspect[]; summary?: string };
type Chat = { system?: string; prompt?: string; reply?: string };
type Heal = {
  outcome?: string; chosen_page?: number; chosen_caption?: string;
  caliber_gap?: boolean; select_reason?: string; confidence?: string;
  source_preview?: string; value?: Record<string, { name?: string; revenue_yuan?: number }[]>;
  select_chat?: Chat;
};
type Llm = {
  outcome?: string; llm_kind?: string; verdict?: string; decision?: string;
  root_cause?: string; next_action?: string; handed_to_human?: boolean;
  suspects?: Suspect[];
  evidence?: string[] | string; summary?: string; committed?: string; llm_error?: string;
  heal?: Heal; healed_select?: boolean; reverify?: string; reverify_detail?: VerifyDetail;
  value?: unknown; chat?: Chat; reverify_chat?: Chat; diag_chat?: Chat; heal_probe?: Heal;
};

const OUTCOME: Record<string, { cls: string; label: string }> = {
  committed: { cls: "bg-green-100 text-green-700 border-green-300", label: "复核过·入库" },
  green: { cls: "bg-lime-100 text-lime-700 border-lime-300", label: "过锚·待复核" },
  verify_hold: { cls: "bg-rose-100 text-rose-700 border-rose-300", label: "复核否决·人工" },
  non_green: { cls: "bg-orange-100 text-orange-700 border-orange-300", label: "非绿灯·诊断" },
  no_such_table: { cls: "bg-slate-100 text-slate-600 border-slate-400", label: "无此表·人工" },
  no_data: { cls: "bg-red-100 text-red-700 border-red-300", label: "无数据" },
  no_anchor: { cls: "bg-slate-100 text-slate-500 border-slate-300", label: "无锚" },
  no_input: { cls: "bg-gray-100 text-gray-400 border-gray-300", label: "无输入" },
};
const PHASE_CN: Record<string, string> = { scan: "🔍 扫表中", analyze: "⚙ 分析中", verify: "🔬 复核中" };
const pct = (r: number | null) => (r == null ? "—" : `${(r * 100).toFixed(1)}%`);
const yi = (n: number | null | undefined) =>
  typeof n === "number" ? `${(n / 1e8).toFixed(2)}亿` : "—";

// 单个阶段的详情（选表/解析/锚判 结构化，其它兜底）
function stageDetail(s: Stage): React.ReactNode {
  const d = s.detail as Record<string, unknown>;
  if (s.name === "选表" && d && typeof d === "object")
    return <>p{String(d.page)} · 《{String(d.caption || "")}》 · {String(d.rows)}行 · {String(d.via)}</>;
  if (s.name === "解析" && d && typeof d === "object") {
    const dims = (d.dims as string[]) || [];
    return <>{String(d.via)} · {dims.map((x) => DIM_CN[x] || x).join(" / ")}</>;
  }
  if (s.name === "锚判" && d && typeof d === "object") {
    const anchor = d.anchor as number;
    const per = (d.per_dim as { dim: string; sum: number; match: boolean }[]) || [];
    const missing = (d.missing_dims as string[]) || [];
    return (
      <div className="space-y-0.5">
        <div>锚 <b>{yi(anchor)}</b> · 置信 <b className={d.confidence === "high" ? "text-green-600" : "text-orange-600"}>{String(d.confidence)}</b></div>
        {per.map((p) => {
          const dev = anchor ? ((p.sum - anchor) / anchor) * 100 : 0;
          return (
            <div key={p.dim} className="flex gap-2 tabular-nums">
              <span className="w-16 text-gray-500">{DIM_CN[p.dim] || p.dim}</span>
              <span className="w-16 text-right">{yi(p.sum)}</span>
              <span className={p.match ? "text-green-600" : "text-orange-600"}>{p.match ? "✓过锚" : `✗差${dev.toFixed(0)}%`}</span>
            </div>
          );
        })}
        {!!missing.length && <div className="text-red-500">缺失维度：{missing.map((m) => DIM_CN[m] || m).join("、")}</div>}
      </div>
    );
  }
  return <span className="text-gray-500">{typeof s.detail === "string" ? s.detail : JSON.stringify(s.detail)}</span>;
}

type StepT = { name: string; status: "ok" | "fail" | "neutral"; detail?: React.ReactNode };
const STEP_CLS: Record<string, string> = {
  ok: "bg-green-100 text-green-700 border-green-300",
  fail: "bg-rose-100 text-rose-700 border-rose-300",
  neutral: "bg-gray-100 text-gray-500 border-gray-300",
};

// 横向 step 组件：点某步展开它的详情
function Stepper({ steps }: { steps: StepT[] }) {
  const [sel, setSel] = useState<number | null>(null);
  return (
    <div>
      <div className="flex items-center flex-wrap gap-y-1">
        {steps.map((st, i) => (
          <div key={i} className="flex items-center">
            <button onClick={() => setSel(sel === i ? null : i)}
              className={`px-2 py-1 rounded border text-[11px] ${STEP_CLS[st.status]} ${sel === i ? "ring-2 ring-blue-400" : ""}`}>
              <span className="mr-1">{st.status === "ok" ? "✓" : st.status === "fail" ? "✗" : i + 1}</span>{st.name}
            </button>
            {i < steps.length - 1 && <span className="text-gray-300 mx-0.5">→</span>}
          </div>
        ))}
      </div>
      {sel !== null && steps[sel].detail && (
        <div className="mt-1.5 text-[11px] text-gray-700 bg-gray-50 border rounded p-2">{steps[sel].detail}</div>
      )}
    </div>
  );
}

// 溯源：按需加载 PDF 原图；点击缩略图 → 全屏灯箱可放大/缩小
function PdfPage({ code, year, page }: { code: string; year: number; page?: number }) {
  const [img, setImg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [scale, setScale] = useState(1);
  const [loading, setLoading] = useState(false);
  if (!page) return null;
  const load = async (): Promise<string | null> => {
    if (img) return img;
    setLoading(true);
    const { data } = await apiGet<{ page_image?: string } | null>(`/debug/page?stock_code=${code}&year=${year}&page=${page}`, null);
    setLoading(false);
    if (data?.page_image) { setImg(data.page_image); return data.page_image; }
    return null;
  };
  const toggle = async () => { if (!img) await load(); setOpen((o) => !o); };
  const btn = "px-2 py-0.5 border rounded bg-white hover:bg-gray-100";
  return (
    <div>
      <button onClick={toggle} className="text-[11px] text-indigo-600 hover:underline">
        {loading ? "加载中…" : open ? `▾ 收起 PDF 第 ${page} 页` : `🖼 溯源：看 PDF 第 ${page} 页原图`}
      </button>
      {open && img && (
        <div className="mt-1">
          <div className="flex items-center gap-1.5 mb-1 text-[11px]">
            <button onClick={() => setScale((s) => Math.max(0.25, Math.round((s - 0.25) * 100) / 100))} className={btn}>− 缩小</button>
            <span className="tabular-nums w-10 text-center">{Math.round(scale * 100)}%</span>
            <button onClick={() => setScale((s) => Math.min(5, Math.round((s + 0.25) * 100) / 100))} className={btn}>＋ 放大</button>
            <button onClick={() => setScale(1)} className={btn}>复位</button>
            <button onClick={() => window.open(img, "_blank")} className={btn}>⛶ 新窗口</button>
          </div>
          <div className="overflow-auto max-h-[70vh] border rounded bg-gray-50">
            <img src={img} alt={`p${page}`} style={{ width: `${scale * 100}%`, maxWidth: "none" }} className="block" />
          </div>
        </div>
      )}
    </div>
  );
}

// 一段 LLM 对话（发过去的 prompt + LLM 回复）
function ChatView({ chat, label }: { chat?: Chat; label: string }) {
  if (!chat || (!chat.prompt && !chat.reply)) return null;
  return (
    <details>
      <summary className="text-[11px] text-gray-500 cursor-pointer">🗨 {label}</summary>
      <div className="mt-1 space-y-1 text-[10px]">
        <div className="text-gray-400">① 发给 LLM（prompt）</div>
        <pre className="bg-blue-50 border rounded p-2 whitespace-pre-wrap overflow-auto max-h-64">{chat.prompt}</pre>
        <div className="text-gray-400">② LLM 回复</div>
        <pre className="bg-purple-50 border rounded p-2 whitespace-pre-wrap overflow-auto max-h-56">{chat.reply}</pre>
      </div>
    </details>
  );
}

// 一次流程卡片：标题 + step 条 + 溯源(PDF页) + 提取表文本 + 解析JSON + 复核对话
function Pass({ title, subtitle, steps, code, year, page, sourceText, json, chat }: {
  title: string; subtitle?: React.ReactNode; steps: StepT[];
  code: string; year: number; page?: number; sourceText?: string;
  json?: unknown; chat?: Chat;
}) {
  const hasJson = json != null && (!Array.isArray(json) ? Object.keys(json as object).length : (json as unknown[]).length);
  return (
    <div className="rounded-lg border shadow-sm p-3 space-y-2 bg-white">
      <div className="font-semibold text-sm">{title}
        {subtitle && <span className="ml-2 text-xs font-normal text-gray-500">{subtitle}</span>}</div>
      <Stepper steps={steps} />
      <PdfPage code={code} year={year} page={page} />
      {sourceText && (
        <details>
          <summary className="text-[11px] text-gray-500 cursor-pointer">提取的表格文本</summary>
          <pre className="mt-1 text-[10px] bg-gray-50 border rounded p-2 whitespace-pre overflow-auto max-h-56">{sourceText}</pre>
        </details>
      )}
      {hasJson ? (
        <details>
          <summary className="text-[11px] text-gray-500 cursor-pointer">解析结果 JSON</summary>
          <pre className="mt-1 text-[10px] bg-gray-50 border rounded p-2 whitespace-pre-wrap overflow-auto max-h-72">{JSON.stringify(json, null, 2)}</pre>
        </details>
      ) : null}
      <ChatView chat={chat} label="与复核 LLM 的对话" />
    </div>
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

  // 深链:从「失败分析」页点公司过来 → /console/pipeline?code=xxx → 自动打开那份的解析链路
  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get("code");
    if (code) {
      openChain(code);
      setTimeout(() => document.getElementById("chain-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }), 300);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

  // 跑批中：从 0 起，只统计/展示本轮已完成的报告（不显示上一轮旧数据）
  const done = prog?.done || [];
  const liveCnt: Record<string, number> = {};
  done.forEach((d) => { const o = d.outcome || "no_input"; liveCnt[o] = (liveCnt[o] || 0) + 1; });
  const liveSuccess = (liveCnt.committed || 0) + (liveCnt.green || 0);
  const liveAnchored = liveSuccess + (liveCnt.verify_hold || 0) + (liveCnt.non_green || 0) + (liveCnt.no_data || 0);
  const liveRate = liveAnchored ? liveSuccess / liveAnchored : null;
  const gridReports = running
    ? done.map((d) => ({ code: d.code, fields: [{ field: REV, outcome: d.outcome || "no_input" }] }))
    : (res?.reports || []);

  // 两次流程的 step 数据
  const ok2 = (b: boolean): "ok" | "fail" => (b ? "ok" : "fail");
  const heal = llm?.heal || llm?.heal_probe;   // 自愈成功(heal) 或 只是跑过没成功(heal_probe)
  const healOk = !!llm?.healed_select;
  const pass1Steps: StepT[] = chain ? [
    ...chain.stages.map((s) => ({ name: s.name, status: ok2(s.ok), detail: stageDetail(s) })),
    ...(llm?.llm_kind === "verify" ? [{
      name: "复核", status: (llm.verdict === "pass" ? "ok" : llm.verdict === "hold" ? "fail" : "neutral") as StepT["status"],
      detail: (<div>verdict=<b>{llm.verdict}</b>
        {(llm.suspects || []).map((s, i) => <div key={i} className="text-rose-700">⚠ [{s.issue}] {s.reason}</div>)}
        {llm.summary && <div className="text-gray-500">{llm.summary}</div>}</div>),
    }] : []),
  ] : [];
  const pass1Page = chain ? ((chain.stages.find((s) => s.name === "选表")?.detail as { page?: number })?.page ?? chain.provenance?.pages?.[0]) : undefined;
  const pass2Steps: StepT[] = heal ? [
    { name: "选表自愈", status: heal.outcome === "no_pick" ? "fail" : "ok",
      detail: (<div>{heal.outcome === "no_pick" ? "未选出营收构成表（全表里没有这张表）" : `重选 p${heal.chosen_page} 「${heal.chosen_caption}」`}
        <div className="text-gray-500 mt-0.5">{heal.select_reason}</div>
        <ChatView chat={heal.select_chat} label="选表 agent 对话" /></div>) },
    ...(heal.value ? [{ name: "重解析", status: ok2(Object.values(heal.value).some((v) => v && v.length)),
      detail: (<div>{Object.entries(heal.value || {}).filter(([, v]) => v && v.length).map(([dim, items]) => `${DIM_CN[dim] || dim}[${items.map((it) => `${it.name} ${yi(it.revenue_yuan)}`).join("、")}]`).join("  ") || "（空）"}</div>) }] : []),
    ...(llm?.reverify ? [{ name: "重复核", status: (llm.reverify === "pass" ? "ok" : llm.reverify === "hold" ? "fail" : "neutral") as StepT["status"],
      detail: (<div>verdict=<b>{llm.reverify}</b>{(llm.reverify_detail?.suspects || []).map((s, i) => <div key={i} className="text-rose-700">⚠ [{s.issue}] {s.reason}</div>)}</div>) }] : []),
  ] : [];

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <div className="flex items-center gap-3">
          <h2 className="font-semibold">营收解析成功率</h2>
          <span className="text-xs text-gray-400">抽表 → 选表 → 解析 → 锚判 → 出口；绿灯 = 自主过锚可入库</span>
          <button
            onClick={async () => {
              if (running) return;
              if (!confirm("对 DB 里全部报告跑完整 LLM 流水线（复核 + 选表自愈 + L2 改规则 + 诊断）？会发 LLM，可在进度条看实时。")) return;
              const { data } = await apiPost<{ started?: boolean; error?: string } | null>("/pipeline/run_llm", { field: REV }, null);
              if (data?.error) setErr(data.error);
            }}
            disabled={!!running}
            className="ml-auto px-3 py-1 rounded bg-emerald-600 text-white text-xs hover:bg-emerald-700 disabled:opacity-50">
            {running ? "跑批中…" : "▶ 跑完整流水线"}
          </button>
          <button onClick={load} className="px-3 py-1 rounded bg-blue-600 text-white text-xs hover:bg-blue-700">刷新</button>
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
        {running ? (
          <div className="mt-3 flex items-center gap-6">
            <div>
              <div className="text-3xl font-bold text-green-600">{pct(liveRate)}</div>
              <div className="text-xs text-gray-400">跑批中 · 本轮已完成 {done.length}/{prog?.total}（从 0 累积）</div>
            </div>
            <div className="text-sm text-gray-600">
              入库 <b className="text-green-600">{liveCnt.committed || 0}</b> · 待复核 <b className="text-lime-600">{liveCnt.green || 0}</b> · 复核否决 <b className="text-rose-600">{liveCnt.verify_hold || 0}</b> · 非绿灯 <b className="text-orange-600">{liveCnt.non_green || 0}</b> · 无此表 <b className="text-slate-600">{liveCnt.no_such_table || 0}</b>
            </div>
          </div>
        ) : rev && (
          <div className="mt-3 flex items-center gap-6">
            <div>
              <div className="text-3xl font-bold text-green-600">{pct(rev.success_rate)}</div>
              <div className="text-xs text-gray-400">营收成功率（{res?.n_reports} 份 · {res?.year}）</div>
            </div>
            <div className="text-sm text-gray-600">
              入库 <b className="text-green-600">{rev.committed}</b> · 待复核 <b className="text-lime-600">{rev.green}</b> · 复核否决 <b className="text-rose-600">{rev.verify_hold}</b> · 非绿灯 <b className="text-orange-600">{rev.non_green}</b> · 无此表 <b className="text-slate-600">{rev.no_such_table}</b> · 无数据 <b className="text-red-600">{rev.no_data}</b>
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
          <div className="text-xs text-gray-400 px-2 py-1">
            {running ? `跑批中：本轮已完成 ${gridReports.length} 份` : `点公司看链路（${gridReports.length} 份）`}
          </div>
          {gridReports.map((rep) => {
            const o = rep.fields.find((x) => x.field === REV)?.outcome || "no_input";
            return (
              <button key={rep.code} onClick={() => openChain(rep.code)}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded mb-0.5 text-left ${sel === rep.code ? "bg-blue-50 ring-1 ring-blue-300" : "hover:bg-gray-50"}`}>
                <span className={`text-[10px] px-1.5 py-0.5 rounded border shrink-0 ${OUTCOME[o]?.cls}`}>{OUTCOME[o]?.label || o}</span>
                <span className="text-sm truncate">{codeLabel(rep.code)}</span>
              </button>
            );
          })}
          {!gridReports.length && <div className="text-xs text-gray-400 px-2 py-4">{running ? "刚开始，等第一家跑完…" : "无结果"}</div>}
        </div>

        {/* 链路 */}
        <div id="chain-detail" className="flex-1 min-w-0 bg-white rounded-lg shadow-sm border p-4 sticky top-20 scroll-mt-20">
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
                  <div className={`rounded p-2.5 text-sm mb-3 border ${OUTCOME[chain.outcome]?.cls || ""}`}>
                    <b>{OUTCOME[chain.outcome]?.label || chain.outcome}</b>
                    <div className="text-xs font-normal mt-0.5">{chain.reason}</div>
                  </div>

                  {/* 两次流程平级 */}
                  <div className="space-y-3">
                    <Pass title="① 第一次流程" code={sel} year={res?.year || 2025}
                      page={pass1Page} sourceText={chain.source_preview} steps={pass1Steps}
                      json={llm?.value ?? chain.value} chat={llm?.chat} />
                    {heal && (
                      <Pass title="② 第二次流程"
                        subtitle={healOk ? "选表自愈 · ✅ 成功入库/复核" : heal.outcome === "no_pick" ? "选表自愈 · 跑了，但没有营收构成表" : "选表自愈 · 跑了，但重选后仍不过锚"}
                        code={sel} year={res?.year || 2025}
                        page={heal.chosen_page} sourceText={heal.source_preview} steps={pass2Steps}
                        json={heal.value} chat={llm?.reverify_chat} />
                    )}
                  </div>

                  <div className="mt-3 flex items-center gap-2">
                    <button onClick={runLlm} disabled={llmLoading}
                      className="px-3 py-1.5 rounded bg-purple-600 text-white text-xs disabled:opacity-40 hover:bg-purple-700">
                      {llmLoading ? "🤖 LLM 分析中…(~15s)"
                        : llm ? "🤖 重跑（实时）"
                          : chain.outcome === "green" ? "🤖 跑复核 agent" : "🤖 跑诊断 agent"}
                    </button>
                    {llm?.outcome === "committed" && <span className="text-green-700 text-xs">✅ 已入库（测试库）</span>}
                    {llm?.handed_to_human && <span className="text-rose-600 text-xs">→ 交人工（分诊队列）</span>}
                  </div>
                  {llm?.llm_error && <div className="text-red-500 text-xs mt-2">LLM 错误：{llm.llm_error}</div>}
                  {llm?.llm_kind === "diagnose" && (
                    <div className="mt-2 rounded border p-2.5 text-xs bg-amber-50 border-amber-300 space-y-1">
                      <div><b>诊断 agent</b> · <b>{llm.decision}</b> / {llm.root_cause} / {llm.next_action}
                        {llm.handed_to_human && <span className="ml-2 text-rose-600">→ 交人工</span>}</div>
                      {llm.summary && <div className="text-gray-700">{llm.summary}</div>}
                      {(Array.isArray(llm.evidence) ? llm.evidence : llm.evidence ? [llm.evidence] : []).map((e, i) => (
                        <div key={i} className="text-gray-600">· {e}</div>
                      ))}
                      <ChatView chat={llm.diag_chat} label="诊断 agent 对话" />
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
