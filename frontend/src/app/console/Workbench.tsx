"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPost, liveLabel } from "./api";

// ── 类型(对齐 steward_probes / steward_diagnose / steward_review) ──
type Dossier = {
  selection?: { chosen_page?: number; caption?: string; via?: string; n_rows?: number; anchor?: number; anchor_matches?: boolean; anchor_col_rel?: number | null };
  extraction?: { data_extracted?: boolean; tables_with_dim_markers?: { page: number; n_rows: number; markers: string[]; n_data_rows: number }[] };
  parse?: { anchor?: number; dims?: Record<string, { n: number; rel: number }>; empty_dims?: string[]; all_empty?: boolean };
  routing?: { category?: string; routes_to_L3?: boolean; routes_to_codegen?: boolean };
  reextract_probe?: { outcome?: string; profile?: string; recovers?: boolean };
};
type Wb = { code: string; year: number; outcome?: string | null; dossier: Dossier };
type Diag = { failure_layer?: string; root_cause?: string; evidence?: string; why_no_heal?: string; prescription?: string; fixable_now?: boolean; note?: string; error?: string };
type RoadItem = { action?: string; targets?: string; effort?: string; expected?: string };
type Roadmap = { n_failures?: number; buckets?: Record<string, number>; roadmap?: { biggest_bucket?: string; system_health?: string; roadmap?: RoadItem[]; next?: string } };

type Tone = "ok" | "bad" | "warn" | "muted";
const TONE: Record<Tone, { bar: string; pill: string }> = {
  ok:    { bar: "bg-emerald-500", pill: "bg-emerald-50 text-emerald-700 border-emerald-300" },
  bad:   { bar: "bg-red-500",     pill: "bg-red-50 text-red-700 border-red-300" },
  warn:  { bar: "bg-amber-500",   pill: "bg-amber-50 text-amber-700 border-amber-300" },
  muted: { bar: "bg-slate-300",   pill: "bg-slate-50 text-slate-500 border-slate-300" },
};

function Pill({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return <span className={`inline-block text-xs px-2 py-0.5 rounded-full border ${TONE[tone].pill}`}>{children}</span>;
}

function Step({ n, title, tone, pill, children }: { n: string; title: string; tone: Tone; pill: React.ReactNode; children?: React.ReactNode }) {
  return (
    <div className="relative flex">
      <div className={`w-1.5 rounded-l ${TONE[tone].bar}`} />
      <div className="flex-1 border border-l-0 rounded-r bg-white px-3 py-2 shadow-sm">
        <div className="flex items-center justify-between gap-2">
          <span className="font-semibold text-sm text-gray-800"><span className="text-gray-400 mr-1">{n}</span>{title}</span>
          {pill}
        </div>
        {children && <div className="text-xs text-gray-600 mt-1 leading-relaxed">{children}</div>}
      </div>
    </div>
  );
}
const Arrow = () => <div className="flex justify-center text-gray-300 text-sm leading-none my-0.5">▼</div>;

const outcomeTone = (o?: string | null): Tone =>
  o === "committed" ? "ok" : o === "verify_hold" ? "warn" : o === "out_of_scope" || o === "no_such_table" ? "muted" : "bad";

export default function Workbench() {
  const [code, setCode] = useState("000878");
  const [wb, setWb] = useState<Wb | null>(null);
  const [live, setLive] = useState(true);
  const [loading, setLoading] = useState(false);
  const [diag, setDiag] = useState<Diag | null>(null);
  const [diagLoading, setDiagLoading] = useState(false);
  const [road, setRoad] = useState<Roadmap | null>(null);

  const load = async (c: string) => {
    setLoading(true); setDiag(null);
    const { data, live } = await apiGet<Wb | null>(`/steward/workbench/${c}`, null);
    setWb(data); setLive(live); setLoading(false);
  };
  const runDiag = async () => {
    if (!wb) return;
    setDiagLoading(true);
    const { data } = await apiPost<Diag>(`/steward/diagnose/${wb.code}`, {}, {});
    setDiag(data); setDiagLoading(false);
  };
  useEffect(() => { load(code); apiGet<Roadmap>("/steward/roadmap", {}).then(({ data }) => setRoad(data)); }, []);

  const d = wb?.dossier;
  const sel = d?.selection, ext = d?.extraction, par = d?.parse, rt = d?.routing, re = d?.reextract_probe;
  const dims = par?.dims || {};
  const rm = road?.roadmap;

  return (
    <div className="bg-gray-50 rounded-lg border p-4">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
        <div>
          <h2 className="font-semibold text-lg">超级工作台 · 每家公司的自愈链路 + 管家诊断</h2>
          <p className="text-xs text-gray-400 mt-0.5">抽表 → 选表 → 解析 → 过锚 → 路由/自愈 → 结局，逐层看事实；管家给根因与处方。{" "}
            <span className={liveLabel(live).cls}>{liveLabel(live).text}</span></p>
        </div>
        <div className="flex items-center gap-2">
          <input value={code} onChange={(e) => setCode(e.target.value.trim())}
            onKeyDown={(e) => e.key === "Enter" && load(code)}
            placeholder="股票代码" className="border rounded px-2 py-1 text-sm w-28 font-mono" />
          <button onClick={() => load(code)} className="px-3 py-1 rounded bg-blue-600 text-white text-sm hover:bg-blue-700">加载链路</button>
          <button onClick={runDiag} disabled={!wb || diagLoading}
            className="px-3 py-1 rounded bg-violet-600 text-white text-sm hover:bg-violet-700 disabled:opacity-40">
            {diagLoading ? "管家诊断中…" : "🤵 让管家诊断"}</button>
        </div>
      </div>

      <div className="flex gap-4 items-start flex-wrap lg:flex-nowrap">
        {/* 左：某公司的链路流程图 */}
        <div className="flex-1 min-w-[320px] space-y-0.5">
          {loading && <p className="text-sm text-gray-400">加载中…</p>}
          {!loading && wb && (
            <>
              <div className="text-sm font-medium text-gray-700 mb-1">
                {wb.code} · {wb.year} 营收构成 —— 结局 <Pill tone={outcomeTone(wb.outcome)}>{wb.outcome || "未跑"}</Pill>
              </div>

              <Step n="①" title="抽表 (pdfplumber)" tone={ext?.data_extracted ? "ok" : "bad"}
                pill={<Pill tone={ext?.data_extracted ? "ok" : "bad"}>{ext?.data_extracted ? "数据行已抽出" : "抽残·数据行没进任何表"}</Pill>}>
                含维度标记的表：{(ext?.tables_with_dim_markers || []).map((t) => `p${t.page}(${t.n_data_rows}数据行)`).join("、") || "无"}
              </Step>
              <Arrow />
              <Step n="②" title="选表 (向量召回 + 锚精判)" tone={sel?.anchor_matches ? "ok" : "warn"}
                pill={<Pill tone={sel?.anchor_matches ? "ok" : "warn"}>{sel?.anchor_matches ? "选对表·某列≈营收锚" : "选表存疑"}</Pill>}>
                p{sel?.chosen_page} 「{sel?.caption}」· {sel?.n_rows}行 · via {sel?.via}
                {sel?.anchor_col_rel != null && <> · 列对锚偏差 {sel.anchor_col_rel}</>}
              </Step>
              <Arrow />
              <Step n="③" title="解析 (认列 + 切桶)" tone={par?.all_empty ? "bad" : "ok"}
                pill={<Pill tone={par?.all_empty ? "bad" : "ok"}>{par?.all_empty ? "全空" : "有输出"}</Pill>}>
                {par?.all_empty ? "选中表里解不出任何分项" :
                  Object.entries(dims).map(([k, v]) => `${k}:${v.n}行/${v.rel}×锚`).join("  ") || "—"}
              </Step>
              <Arrow />
              <Step n="④" title="路由 / 自愈分诊" tone={rt?.category ? "warn" : "muted"}
                pill={<Pill tone="warn">{rt?.category || "—"}</Pill>}>
                派药：{rt?.routes_to_L3 ? "→ L3抽表自愈 " : ""}{rt?.routes_to_codegen ? "→ codegen " : ""}
                {!rt?.routes_to_L3 && !rt?.routes_to_codegen ? "→ 诊断/人工" : ""}
              </Step>
              <Arrow />
              <Step n="⑤" title="L3 重抽探针 (换参 / camelot)" tone={re?.recovers ? "ok" : "bad"}
                pill={<Pill tone={re?.recovers ? "ok" : "bad"}>{re?.recovers ? "能救回·过锚" : "重抽也救不回"}</Pill>}>
                {re?.recovers ? `换 ${re?.profile} 重抽 → 数据出来、过金额锚` : `试遍策略仍不过锚（${re?.outcome}）——多半需更强抽表/视觉`}
              </Step>
            </>
          )}
        </div>

        {/* 右：管家诊断 + 全局路线图 */}
        <aside className="w-full lg:w-96 shrink-0 space-y-3">
          <div className="border rounded bg-violet-50 p-3">
            <h3 className="font-semibold text-sm text-violet-800 mb-1">🤵 管家诊断（强模型分层归因）</h3>
            {!diag && <p className="text-xs text-gray-500">点上方「让管家诊断」——它会自顶向下分层，把失败定位到真正的根因层。</p>}
            {diag?.error && <p className="text-xs text-red-500">诊断出错：{diag.error}</p>}
            {diag && !diag.error && (
              <div className="text-xs space-y-1.5 text-gray-700">
                <div>失败层：<Pill tone="bad">{diag.failure_layer}</Pill> {diag.fixable_now ? <Pill tone="ok">现可修</Pill> : <Pill tone="warn">需新能力</Pill>}</div>
                <div><b>根因</b>：{diag.root_cause}</div>
                <div><b>证据</b>：{diag.evidence}</div>
                <div><b>为什么没自愈</b>：{diag.why_no_heal}</div>
                <div className="bg-white border rounded p-1.5"><b>处方</b>：{diag.prescription}</div>
              </div>
            )}
          </div>

          <div className="border rounded bg-white p-3">
            <h3 className="font-semibold text-sm text-gray-800 mb-1">📋 管家批量复盘 · 自驱路线图</h3>
            {!road && <p className="text-xs text-gray-400">加载中…</p>}
            {road && (
              <div className="text-xs text-gray-700 space-y-1.5">
                <div>本轮失败 <b>{road.n_failures}</b> 家 · 根因桶 {Object.entries(road.buckets || {}).map(([k, v]) => `${k}×${v}`).join("、")}</div>
                {rm?.system_health && <div className="text-gray-500 italic">{rm.system_health}</div>}
                {rm?.biggest_bucket && <div>最大桶：<Pill tone="bad">{rm.biggest_bucket}</Pill></div>}
                <ol className="space-y-1 mt-1">
                  {(rm?.roadmap || []).map((it, i) => (
                    <li key={i} className="border rounded p-1.5 bg-gray-50">
                      <div className="flex items-center gap-1 font-medium text-gray-800">
                        <span className="text-gray-400">#{i + 1}</span>{it.action}
                        <Pill tone={it.effort === "小" ? "ok" : it.effort === "中" ? "warn" : "bad"}>{it.effort}</Pill>
                      </div>
                      <div className="text-gray-500">覆盖 {it.targets} · 预期 {it.expected}</div>
                    </li>
                  ))}
                </ol>
                {rm?.next && <div className="bg-violet-50 border border-violet-200 rounded p-1.5 mt-1"><b>下一步</b>：{rm.next}</div>}
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
