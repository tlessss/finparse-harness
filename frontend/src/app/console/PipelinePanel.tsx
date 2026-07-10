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
    value?: unknown; chat?: Chat; reverify_chat?: Chat; diag_chat?: Chat; heal_probe?: Heal;
    rule_heal?: Llm["rule_heal"]; healed_rule?: boolean; extract_heal?: Llm["extract_heal"];
    healed_extract?: boolean; codegen?: Llm["codegen"]; certified_parser?: string; steward?: Llm["steward"];
    reused_parser?: string; reused_after_hold?: boolean; via?: string; cert?: Llm["cert"] };
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
  // 各自愈层（链上平级显示）
  rule_heal?: { outcome?: string; reason?: string; rounds_used?: number };
  healed_rule?: boolean;
  extract_heal?: { outcome?: string; profile?: string; reason?: string; tries?: unknown[] };
  healed_extract?: boolean;
  codegen?: { outcome?: string; rounds?: number; escalate?: string };
  certified_parser?: string;
  cert?: { certified?: boolean; reason?: string };
  reused_parser?: string; reused_after_hold?: boolean; via?: string;   // heal-step-0 复用认证解析器
  steward?: { decision?: string; strong_verdict?: string; cause?: string; strong_summary?: string };
};

type StewardDiag = { failure_layer?: string; root_cause?: string; evidence?: string; why_no_heal?: string; prescription?: string; fixable_now?: boolean; error?: string; diagnosed_at?: string;
  adjudication?: { decision?: string; strong_verdict?: string; cause?: string; strong_summary?: string; reason?: string } };

const OUTCOME: Record<string, { cls: string; label: string; dot: string; bar: string }> = {
  committed:     { cls: "bg-emerald-50 text-emerald-700 border-emerald-200", label: "入库", dot: "bg-emerald-500", bar: "bg-emerald-500" },
  green:         { cls: "bg-lime-50 text-lime-700 border-lime-200", label: "待复核", dot: "bg-lime-400", bar: "bg-lime-400" },
  verify_hold:   { cls: "bg-amber-50 text-amber-700 border-amber-200", label: "复核否决", dot: "bg-amber-500", bar: "bg-amber-400" },
  non_green:     { cls: "bg-orange-50 text-orange-700 border-orange-200", label: "非绿灯", dot: "bg-orange-500", bar: "bg-orange-400" },
  no_such_table: { cls: "bg-slate-50 text-slate-600 border-slate-200", label: "无此表", dot: "bg-slate-400", bar: "bg-slate-400" },
  no_data:       { cls: "bg-red-50 text-red-600 border-red-200", label: "无数据", dot: "bg-red-400", bar: "bg-red-400" },
  no_anchor:     { cls: "bg-slate-50 text-slate-500 border-slate-200", label: "无锚", dot: "bg-slate-300", bar: "bg-slate-300" },
  no_input:      { cls: "bg-gray-50 text-gray-400 border-gray-200", label: "无输入", dot: "bg-gray-300", bar: "bg-gray-300" },
  out_of_scope:  { cls: "bg-slate-50 text-slate-500 border-slate-200", label: "金融域外", dot: "bg-slate-400", bar: "bg-slate-400" },
};
const PHASE_CN: Record<string, string> = { scan: "🔍 扫表中", analyze: "⚙ 分析中", verify: "🔬 复核中" };
const pct = (r: number | null) => (r == null ? "—" : `${(r * 100).toFixed(1)}%`);
const yi = (n: number | null | undefined) =>
  typeof n === "number" ? `${(n / 1e8).toFixed(2)}亿` : "—";

// 单个阶段的详情（选表/解析/锚判 结构化，其它兜底）
function stageDetail(s: Stage): React.ReactNode {
  const d = s.detail as Record<string, unknown>;
  if (s.name === "抽表" && d && typeof d === "object")
    return <>共 {String(d.n_tables)} 张表（PDF 抽出的全部候选）</>;
  if (s.name === "选表" && d && typeof d === "object")
    return <>p{String(d.page)} · 《{String(d.caption || "")}》 · {String(d.rows)}行 · {String(d.via)}</>;
  if (s.name === "解析" && d && typeof d === "object") {
    const rpd = (d.rows_per_dim as Record<string, number>) || {};
    const dims = (d.dims as string[]) || [];
    return (
      <div>
        <div>{String(d.via)}</div>
        <div className="mt-0.5">{Object.keys(rpd).length
          ? Object.entries(rpd).map(([k, n]) => <span key={k} className={n ? "text-gray-700" : "text-red-500"}>{DIM_CN[k] || k}:{n}行　</span>)
          : dims.map((x) => DIM_CN[x] || x).join(" / ")}</div>
      </div>
    );
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

type IO = { in: string; out: string };
type StepT = { name: string; status: "ok" | "fail" | "neutral"; detail?: React.ReactNode; io?: IO };
const STEP_CLS: Record<string, string> = {
  ok: "bg-green-100 text-green-700 border-green-300",
  fail: "bg-rose-100 text-rose-700 border-rose-300",
  neutral: "bg-gray-100 text-gray-500 border-gray-300",
};
const STEP_BAR: Record<string, string> = { ok: "bg-emerald-500", fail: "bg-rose-500", neutral: "bg-slate-300" };

// 竖排 step 流程：每步**始终显示 输入 → 输出**，点开看完整详情。
function Stepper({ steps }: { steps: StepT[] }) {
  const [sel, setSel] = useState<number | null>(null);
  return (
    <div className="space-y-1">
      {steps.map((st, i) => (
        <div key={i}>
          <button onClick={() => setSel(sel === i ? null : i)}
            className={`w-full flex items-stretch text-left rounded border overflow-hidden ${sel === i ? "ring-1 ring-blue-400" : ""}`}>
            <span className={`w-1 shrink-0 ${STEP_BAR[st.status]}`} />
            <span className="flex-1 px-2 py-1 bg-white">
              <span className="flex items-center gap-1.5 flex-wrap">
                <span className={`text-[11px] px-1.5 py-0.5 rounded border ${STEP_CLS[st.status]}`}>
                  {st.status === "ok" ? "✓" : st.status === "fail" ? "✗" : i + 1} {st.name}
                </span>
                {st.io && (
                  <span className="text-[11px] text-gray-600">
                    <span className="text-gray-400">输入</span> {st.io.in}
                    <span className="text-gray-300 mx-1">→</span>
                    <span className="text-gray-400">输出</span> <b className="text-gray-800">{st.io.out}</b>
                  </span>
                )}
                {st.detail && <span className="ml-auto text-[10px] text-gray-300">{sel === i ? "▲" : "▼ 详情"}</span>}
              </span>
            </span>
          </button>
          {sel === i && st.detail && (
            <div className="mt-0.5 ml-1 text-[11px] text-gray-700 bg-gray-50 border rounded p-2">{st.detail}</div>
          )}
        </div>
      ))}
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
  const [steward, setSteward] = useState<StewardDiag | null>(null);
  const [stewardLoading, setStewardLoading] = useState(false);
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
    setSel(code); setChain(null); setLlm(null); setSteward(null); setChainLoading(true);
    const { data } = await apiGet<Chain | null>(
      `/pipeline/chain?stock_code=${code}&year=${res?.year || 2025}&field=${REV}${fresh ? "&fresh=1" : ""}`, null);
    setChainLoading(false); setChain(data);
    if (data?.verify_cached) {  // DB 里存过复核结论 → 直接显示，不用再点跑 LLM
      setLlm({ llm_kind: "verify", outcome: data.outcome, ...data.verify_cached });
    }
    // 管家诊断存档:开页先显示历史诊断(不重跑 LLM)
    apiGet<StewardDiag>(`/steward/diagnosis/${code}?year=${res?.year || 2025}&field=${REV}`, {})
      .then(({ data: sd }) => { if (sd?.failure_layer) setSteward(sd); });
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
  const runSteward = async () => {
    if (!sel) return;
    setStewardLoading(true); setSteward(null);
    const { data } = await apiPost<StewardDiag>(`/steward/diagnose/${sel}?year=${res?.year || 2025}&field=${REV}`, {}, {});
    setStewardLoading(false); setSteward(data);
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
    ...chain.stages.map((s) => ({ name: s.name, status: ok2(s.ok), detail: stageDetail(s),
      io: (s.detail as { io?: IO })?.io })),
    ...(llm?.llm_kind === "verify" ? [{
      name: "复核", status: (llm.verdict === "pass" ? "ok" : llm.verdict === "hold" ? "fail" : "neutral") as StepT["status"],
      io: { in: "解析JSON + 源文表", out: `verdict=${llm.verdict}${llm.suspects?.length ? ` · ${llm.suspects.length}处疑点` : ""}` } as IO,
      detail: (<div>verdict=<b>{llm.verdict}</b>
        {(llm.suspects || []).map((s, i) => <div key={i} className="text-rose-700">⚠ [{s.issue}] {s.reason}</div>)}
        {llm.summary && <div className="text-gray-500">{llm.summary}</div>}</div>),
    }] : []),
  ] : [];
  const pass1Page = chain ? ((chain.stages.find((s) => s.name === "选表")?.detail as { page?: number })?.page ?? chain.provenance?.pages?.[0]) : undefined;
  // 自愈循环里各层（谁触发就显示谁，全部平级接在复核之后）
  const rh = llm?.rule_heal, eh = llm?.extract_heal, cg = llm?.codegen, sw = llm?.steward;
  const reused = !!llm?.reused_parser || llm?.via === "routed(自愈复用)";
  const pass2Steps: StepT[] = [
    // heal-step-0：按选中表骨架复用已认证解析器（codegen/人修好的成果）—— 自愈级联最便宜的第一步
    ...(reused ? [{ name: "heal-0 复用认证解析器", status: "ok" as StepT["status"],
      io: { in: "选中表骨架 → 匹认证库", out: `命中 ${(llm?.reused_parser || "").split("/").pop()} · 过双闸入库` } as IO,
      detail: (<div>冷启动没过锚 → 按选中表骨架命中已认证解析器 <code className="text-[10px] bg-gray-100 px-1 rounded">{(llm?.reused_parser || "").split("/").pop()}</code>，跑它过金额锚+复核双闸 → 入库。
        <div className="text-gray-500 mt-0.5">{llm?.reused_after_hold ? "触发点：绿灯但复核 hold（如 300014 矩阵表）" : "触发点：冷启动无结果/不过锚"} · 复用而非重造，最便宜的 healer</div></div>) }] : []),
    // 选表自愈（+ 重解析 + 重复核）
    ...(heal ? [{ name: "选表自愈", status: (heal.outcome === "no_pick" ? "fail" : "ok") as StepT["status"],
      io: { in: "全部表 + 复核疑点", out: heal.outcome === "no_pick" ? "全表没有营收构成表" : `重选 p${heal.chosen_page}` } as IO,
      detail: (<div>{heal.outcome === "no_pick" ? "未选出营收构成表（全表里没有这张表）" : `重选 p${heal.chosen_page} 「${heal.chosen_caption}」`}
        <div className="text-gray-500 mt-0.5">{heal.select_reason}</div>
        <ChatView chat={heal.select_chat} label="选表 agent 对话" /></div>) }] : []),
    ...(heal?.value ? [{ name: "重解析", status: ok2(Object.values(heal.value).some((v) => v && v.length)),
      io: { in: "重选的表", out: Object.entries(heal.value || {}).filter(([, v]) => v && v.length).map(([dim, items]) => `${DIM_CN[dim] || dim}:${items.length}行`).join("、") || "全空" } as IO,
      detail: (<div>{Object.entries(heal.value || {}).filter(([, v]) => v && v.length).map(([dim, items]) => `${DIM_CN[dim] || dim}[${items.map((it) => `${it.name} ${yi(it.revenue_yuan)}`).join("、")}]`).join("  ") || "（空）"}</div>) }] : []),
    // L2 改规则自愈
    ...(rh ? [{ name: "L2 改规则自愈", status: (llm?.healed_rule || rh.outcome === "committed" ? "ok" : "fail") as StepT["status"],
      io: { in: "选中表 + 逐维偏差", out: (llm?.healed_rule || rh.outcome === "committed") ? `提规则 delta → 过锚${rh.rounds_used ? `(${rh.rounds_used}轮)` : ""}` : (rh.outcome || "delta 没修好") } as IO,
      detail: (<div>{rh.reason || "LLM 提 revenue.yaml 增量(切桶/认列/单位)，过锚+复核才进版本池"}</div>) }] : []),
    // L3 抽表自愈（换参/camelot 重抽）
    ...(eh ? [{ name: "L3 抽表自愈", status: (llm?.healed_extract || eh.outcome === "fixed" ? "ok" : "fail") as StepT["status"],
      io: { in: "选中页(pdfplumber抽残)", out: eh.outcome === "fixed" ? `换 ${eh.profile} 重抽 → 数据出来·过锚` : (eh.reason || eh.outcome || "重抽仍不过锚") } as IO,
      detail: (<div>outcome=<b>{eh.outcome}</b>{eh.profile && ` · profile=${eh.profile}`}{Array.isArray(eh.tries) && ` · 试了${eh.tries.length}种策略`}</div>) }] : []),
    // 代码生成（终极层）
    ...(cg ? [{ name: "代码生成 codegen", status: (llm?.certified_parser ? "ok" : "fail") as StepT["status"],
      io: { in: "现有解析器源码 + 错输出", out: llm?.certified_parser ? `写出专用解析器·过双闸${cg.rounds ? `(${cg.rounds}轮)` : ""}` : `${cg.rounds || "?"}轮没过双闸` } as IO,
      detail: (<div>{llm?.certified_parser ? <>过 smoke 闸(沙箱跑通)后注册认证解析器 <code className="text-[10px] bg-gray-100 px-1 rounded">{llm.certified_parser}</code> → 下次同版式走 heal-0 按骨架复用、免 LLM</> : (llm?.cert && llm.cert.certified === false ? `解析器没过 smoke 闸(${llm.cert.reason})→ 不登记` : `转人工(escalate=${cg.escalate || "human"})`)}</div>) }] : []),
    // 重复核（自愈后再判）
    ...(llm?.reverify ? [{ name: "重复核", status: (llm.reverify === "pass" ? "ok" : llm.reverify === "hold" ? "fail" : "neutral") as StepT["status"],
      io: { in: "自愈后的解析值 + 源文", out: `verdict=${llm.reverify}` } as IO,
      detail: (<div>verdict=<b>{llm.reverify}</b>{(llm.reverify_detail?.suspects || []).map((s, i) => <div key={i} className="text-rose-700">⚠ [{s.issue}] {s.reason}</div>)}</div>) }] : []),
    // 🤵 管家二次裁决
    ...(sw ? [{ name: "🤵 管家裁决", status: (sw.decision === "commit" ? "ok" : sw.decision === "real_hold" ? "fail" : "neutral") as StepT["status"],
      io: { in: "过锚但弱模型 hold", out: sw.decision === "commit" ? "强模型判假 hold → 入库" : sw.decision === "real_hold" ? "强模型确认真 hold" : "维持 hold" } as IO,
      detail: (<div>强模型判 <b>{sw.strong_verdict}</b> · {sw.cause || sw.strong_summary}</div>) }] : []),
  ];

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-xl shadow-sm border border-gray-200 p-5">
        <div className="flex items-start gap-3">
          <div>
            <h2 className="text-base font-semibold text-gray-900">营收解析流水线</h2>
            <p className="text-xs text-gray-400 mt-0.5">抽表 → 选表 → 解析 → 过锚 → 复核 →（自愈循环 ↻）· 绿灯 = 自主过锚可入库</p>
          </div>
          <div className="ml-auto flex items-center gap-2 shrink-0">
            <button
              onClick={async () => {
                if (running) return;
                if (!confirm("对 DB 里全部报告跑完整 LLM 流水线（复核 + 选表自愈 + L2 改规则 + 诊断）？会发 LLM，可在进度条看实时。")) return;
                const { data } = await apiPost<{ started?: boolean; error?: string } | null>("/pipeline/run_llm", { field: REV }, null);
                if (data?.error) setErr(data.error);
              }}
              disabled={!!running}
              className="px-3 py-1.5 rounded-lg bg-emerald-600 text-white text-xs font-medium shadow-sm hover:bg-emerald-700 disabled:opacity-50">
              {running ? "跑批中…" : "▶ 跑完整流水线"}
            </button>
            <button onClick={load} className="px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 text-xs font-medium hover:bg-gray-50">刷新</button>
          </div>
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
        {(running || rev) && (() => {
          const cnt: Record<string, number> = running ? liveCnt : (rev as unknown as Record<string, number>);
          const rate = running ? liveRate : rev!.success_rate;
          const denom = running ? (prog?.total || 1) : rev!.anchored_denominator;
          const subtitle = running ? `跑批中 · 已完成 ${done.length}/${prog?.total}` : `${res?.n_reports} 份 · ${res?.year}`;
          const STAT: [string, string][] = [["committed", "入库"], ["green", "待复核"], ["verify_hold", "复核否决"], ["non_green", "非绿灯"], ["no_such_table", "无此表"], ["no_data", "无数据"]];
          return (
            <div className="mt-4 flex items-end gap-5 flex-wrap">
              <div className="pr-5 border-r border-gray-100">
                <div className="text-[2.6rem] font-bold text-emerald-600 tabular-nums leading-none">{pct(rate)}</div>
                <div className="text-[11px] text-gray-400 mt-1.5">营收自主成功率 · {subtitle}</div>
              </div>
              <div className="flex gap-1.5 flex-wrap">
                {STAT.map(([k, label]) => (
                  <div key={k} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-gray-100 bg-gray-50">
                    <span className={`w-2 h-2 rounded-full ${OUTCOME[k]?.dot}`} />
                    <span className="text-sm font-semibold tabular-nums text-gray-800">{cnt[k] || 0}</span>
                    <span className="text-[11px] text-gray-400">{label}</span>
                  </div>
                ))}
              </div>
              <div className="flex-1 min-w-[180px] self-center">
                <div className="flex h-2.5 rounded-full overflow-hidden bg-gray-100">
                  {STAT.map(([k]) => { const n = cnt[k] || 0; return n ? <div key={k} className={OUTCOME[k]?.bar} style={{ width: `${(n / denom) * 100}%` }} title={`${OUTCOME[k]?.label} ${n}`} /> : null; })}
                </div>
              </div>
            </div>
          );
        })()}
      </div>

      <div className="flex gap-4 items-start">
        {/* 报告列表（营收） */}
        <div className="w-72 shrink-0 bg-white rounded-xl shadow-sm border border-gray-200 p-2 max-h-[72vh] overflow-auto">
          <div className="text-[11px] font-medium text-gray-400 px-2 py-1.5 uppercase tracking-wide">
            {running ? `跑批中 · ${gridReports.length} 份` : `公司链路 · ${gridReports.length} 份`}
          </div>
          {gridReports.map((rep) => {
            const o = rep.fields.find((x) => x.field === REV)?.outcome || "no_input";
            return (
              <button key={rep.code} onClick={() => openChain(rep.code)}
                className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-lg mb-0.5 text-left transition-colors ${sel === rep.code ? "bg-blue-50 ring-1 ring-blue-200" : "hover:bg-gray-50"}`}>
                <span className={`w-2 h-2 rounded-full shrink-0 ${OUTCOME[o]?.dot || "bg-gray-300"}`} />
                <span className="text-sm text-gray-700 truncate flex-1">{codeLabel(rep.code)}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded-md border shrink-0 ${OUTCOME[o]?.cls}`}>{OUTCOME[o]?.label || o}</span>
              </button>
            );
          })}
          {!gridReports.length && <div className="text-xs text-gray-400 px-2 py-6 text-center">{running ? "刚开始，等第一家跑完…" : "无结果"}</div>}
        </div>

        {/* 链路 */}
        <div id="chain-detail" className="flex-1 min-w-0 bg-white rounded-xl shadow-sm border border-gray-200 p-5 sticky top-20 scroll-mt-20">
          {!sel && (
            <div className="flex flex-col items-center justify-center py-20 text-gray-300">
              <div className="text-3xl mb-2">🔬</div>
              <div className="text-sm">← 点左边一家公司，看营收整条链路的每步输入/输出</div>
            </div>
          )}
          {sel && (
            <>
              <div className="flex items-center gap-2 mb-3 pb-3 border-b border-gray-100">
                <span className={`w-2.5 h-2.5 rounded-full ${OUTCOME[chain?.outcome || ""]?.dot || "bg-gray-300"}`} />
                <div className="text-sm font-semibold text-gray-900">{codeLabel(sel)}</div>
                <span className="text-xs text-gray-400">营收链路</span>
                {chain?._from_db && <span className="text-[10px] text-gray-300 border border-gray-200 rounded px-1.5 py-0.5">DB 存档</span>}
                <button onClick={() => openChain(sel, true)} disabled={chainLoading}
                  className="ml-auto px-2.5 py-1 rounded-lg border border-gray-200 text-gray-500 text-xs disabled:opacity-40 hover:bg-gray-50">
                  {chainLoading ? "重跑中…" : "🔄 重跑"}
                </button>
              </div>
              {chainLoading && <div className="text-xs text-gray-400">重新解析中…</div>}
              {chain && (
                <>
                  <div className={`rounded-lg px-3 py-2 text-sm mb-3 border ${OUTCOME[chain.outcome]?.cls || ""}`}>
                    <b>{OUTCOME[chain.outcome]?.label || chain.outcome}</b>
                    <div className="text-xs font-normal mt-0.5">{chain.reason}</div>
                  </div>

                  {/* 一条平级的步骤流：抽表→选表→冷启动解析→锚判→复核→(没绿灯则自愈级联) heal-0 复用认证解析器→选表自愈→L2→L3→codegen→重复核→管家裁决… 不分"第一次/第二次" */}
                  <div className="rounded-lg border shadow-sm p-3 bg-white space-y-2">
                    <div className="font-semibold text-sm">营收解析链路 · 每步平级{heal && <span className="text-violet-600">（含自愈循环 ↻）</span>}</div>
                    <Stepper steps={[...pass1Steps, ...pass2Steps]} />
                    <PdfPage code={sel} year={res?.year || 2025} page={pass1Page} />
                    {chain.source_preview && (
                      <details><summary className="text-[11px] text-gray-500 cursor-pointer">选中表格文本</summary>
                        <pre className="mt-1 text-[10px] bg-gray-50 border rounded p-2 whitespace-pre overflow-auto max-h-56">{chain.source_preview}</pre></details>)}
                    {(() => { const j = llm?.value ?? chain.value; const has = j != null && (Array.isArray(j) ? j.length : Object.keys(j as object).length);
                      return has ? (<details><summary className="text-[11px] text-gray-500 cursor-pointer">解析结果 JSON</summary>
                        <pre className="mt-1 text-[10px] bg-gray-50 border rounded p-2 whitespace-pre-wrap overflow-auto max-h-72">{JSON.stringify(j, null, 2)}</pre></details>) : null; })()}
                    <ChatView chat={llm?.chat} label="与复核 LLM 的对话" />
                    {heal?.source_preview && (
                      <details><summary className="text-[11px] text-gray-500 cursor-pointer">自愈重选的表格文本</summary>
                        <pre className="mt-1 text-[10px] bg-gray-50 border rounded p-2 whitespace-pre overflow-auto max-h-56">{heal.source_preview}</pre></details>)}
                    <ChatView chat={llm?.reverify_chat} label="重复核对话" />
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
                    <button onClick={runSteward} disabled={stewardLoading}
                      className="px-3 py-1.5 rounded bg-violet-600 text-white text-xs disabled:opacity-40 hover:bg-violet-700">
                      {stewardLoading ? "🤵 管家诊断中…" : steward ? "🤵 重新诊断" : "🤵 让管家诊断根因"}
                    </button>
                    {steward?.diagnosed_at && <span className="text-[10px] text-gray-400">已存档 · 诊断于 {steward.diagnosed_at}</span>}
                  </div>
                  {steward && !steward.error && (
                    <div className="mt-2 rounded border border-violet-300 bg-violet-50 p-2.5 text-xs space-y-1">
                      <div className="font-semibold text-violet-800">🤵 管家根因诊断（分层归因）</div>
                      <div>失败层 <b className="text-rose-700">{steward.failure_layer}</b>
                        {steward.fixable_now ? <span className="ml-1 text-emerald-700">· 现可修</span> : <span className="ml-1 text-amber-700">· 需新能力</span>}</div>
                      <div><b>根因</b>：{steward.root_cause}</div>
                      {steward.evidence && <div className="text-gray-600"><b>证据</b>：{steward.evidence}</div>}
                      <div><b>为什么没自愈</b>：{steward.why_no_heal}</div>
                      <div className="bg-white border rounded p-1.5"><b>处方</b>：{steward.prescription}</div>
                      {steward.adjudication?.decision && (
                        <div className="bg-white border rounded p-1.5">
                          <b>管家裁决</b>：{(() => { const a = steward.adjudication!; const d = a.decision;
                            if (d === "commit") return <span className="text-emerald-700 font-medium">假 hold → 可入库（强模型 + 金额锚都过）</span>;
                            if (d === "real_hold") return <span className="text-rose-700 font-medium">真 hold → 强模型也确认有问题，建议交人工</span>;
                            if (d === "n/a") return <span className="text-slate-500">{a.reason || "不适用"}</span>;
                            if (d === "error") return <span className="text-red-500">出错：{a.reason}</span>;
                            return <span className="text-amber-700">维持 hold（{a.reason || "强模型无结论"}）</span>; })()}
                          {steward.adjudication.cause && <div className="text-gray-500 mt-0.5">强模型病因：{steward.adjudication.cause}</div>}
                        </div>
                      )}
                      <div className="flex items-center gap-2 pt-1">
                        <span className="text-gray-500">看完管家结论 →</span>
                        <a href="/console/triage" className="px-2 py-1 rounded-md bg-rose-600 text-white text-[11px] font-medium hover:bg-rose-700">人工介入（分诊队列）</a>
                      </div>
                    </div>
                  )}
                  {steward?.error && <div className="text-red-500 text-xs mt-1">管家诊断出错：{steward.error}</div>}
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
