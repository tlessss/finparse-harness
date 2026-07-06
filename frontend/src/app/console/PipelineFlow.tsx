"use client";

import { useEffect, useState } from "react";
import { apiGet } from "./api";
import RenderedPromptExamples from "./RenderedPromptExamples";

// ── 当前「一份报告·一个字段」的真实决策流（以 pipeline.run_field / field_chain 为准）──
// 抽表 → 选表 → 解析(base优先扫版本池) → 过锚 → 复核 / 选表自愈 / L2改规则自愈 → 诊断
type Kind = "stage" | "decision" | "verify" | "heal" | "commit" | "human" | "exit";
type Side = "top" | "bottom" | "left" | "right";
type Node = {
  id: string; label: string; sub?: string; kind: Kind;
  x: number; y: number; w: number; h: number; detail: string;
  agent?: string;   // 该节点对应的 LLM agent_id → 详情里展示其 prompt
};
type Link = {
  s: string; ss: Side; t: string; ts: Side;
  label?: string; tone?: "yes" | "no" | "commit" | "human"; sOff?: number; tOff?: number;
  mid?: number;    // 强制横向折线走这个 x 通道（回边走空白区，避免穿主流程）
  dash?: boolean;  // 虚线：标记回边/循环，和前向实线区分
};

const C: Record<Kind, { fill: string; stroke: string; name: string }> = {
  stage:    { fill: "#eff6ff", stroke: "#3b82f6", name: "解析步骤" },
  decision: { fill: "#f8fafc", stroke: "#64748b", name: "判定" },
  verify:   { fill: "#f5f3ff", stroke: "#8b5cf6", name: "复核 (LLM)" },
  heal:     { fill: "#fffbeb", stroke: "#f59e0b", name: "自愈 (LLM)" },
  commit:   { fill: "#ecfdf5", stroke: "#10b981", name: "入库 ✓" },
  human:    { fill: "#fef2f2", stroke: "#ef4444", name: "人工 / L3" },
  exit:     { fill: "#f9fafb", stroke: "#9ca3af", name: "确定性出口" },
};

// 严格网格：5 条纵向车道 × 等距横向行
const XDEAD = 130, XMAIN = 390, XHEAL = 720, XMID = 1000, XTERM = 1235;
const Y = [60, 175, 290, 405, 520, 635, 750, 865, 980, 1095]; // y0..y9
const RW = 190, RH = 54, DW = 172, DH = 72, TW = 168, TH = 50, EW = 122, EH = 44;

const NODES: Node[] = [
  { id: "scan", kind: "stage", x: XMAIN, y: Y[0], w: RW, h: RH, label: "① 抽表 · 找 PDF", sub: "get_tables + PDF",
    detail: "拿到该报告已抽好的全部表格 + PDF 路径。缺任一 → no_input。" },
  { id: "no_input", kind: "exit", x: XDEAD, y: Y[0], w: EW, h: EH, label: "no_input", sub: "无表 / 无PDF",
    detail: "没有可解析的输入，直接退出。" },
  { id: "d_route", kind: "decision", x: XMAIN, y: Y[1], w: DW, h: DH, label: "命中认证解析器?", sub: "route_field",
    detail: "生产路由（选择即验证）：若该报告命中已认证的专用解析器且过硬规则 → 直接绿灯。认证解析器自带选表，故 routed 路径跳过下面的确定性选表。" },
  { id: "select", kind: "stage", x: XMAIN, y: Y[2], w: RW, h: RH, label: "② 选表 (确定性)", sub: "向量召回 + 锚精判",
    detail: "table_recall.select_table：整表去数字留文字骨架做向量召回候选 → 锚精判（哪列合计≈营收锚）定表、定金额列 → 维度数闸。选不准时交给下面的『选表自愈』(LLM)。" },
  { id: "parse", kind: "stage", x: XMAIN, y: Y[3], w: 206, h: 62, label: "③ 解析", sub: "认列切桶 · base优先扫版本池",
    detail: "在选中表上认列(header_aliases)、切桶(dimensions)。先用 base 规则；不过锚 → 逐个规则版本(delta)合并重解，取第一个过锚的赢家(base 优先 = 不回归)。\n仍不过锚 → 冷启动就地试**跨页拼接**(选中表被截断则拼上下一页续表,锚闸兜底)——全程不发 LLM，能自救的不必绕选表自愈。" },
  { id: "no_data", kind: "exit", x: XDEAD, y: Y[3], w: EW, h: EH, label: "no_data", sub: "选不到表/解不出",
    detail: "选表没命中目标表，或解析器在选中表上没解出结构化数据。" },
  { id: "d_anchor", kind: "decision", x: XMAIN, y: Y[4], w: DW, h: DH, label: "过锚?", sub: "所有维度 ±3%",
    detail: "跨表锚判 field_plausibility：每个解析出的维度合计都要 ≈ 营业收入锚(±3%)才算过。正确率优先——单个维度串/漏行就整体不过。" },
  { id: "no_anchor", kind: "exit", x: XDEAD, y: Y[4], w: EW, h: EH, label: "no_anchor", sub: "该字段无锚",
    detail: "字段没有外部权威锚，确定性判不了对错。" },

  { id: "verify", kind: "verify", x: XHEAL, y: Y[4], w: RW, h: RH, label: "复核 verify", sub: "verify_field", agent: "verify",
    detail: "绿灯不当场入库：复核 agent 逐项核对数据、并体检是否选错表 / 跨页截断。routed 绿灯与冷启动绿灯都在此汇合。" },
  { id: "d_verify", kind: "decision", x: XHEAL, y: Y[5], w: DW, h: DH, label: "复核结论?", sub: "verdict",
    detail: "pass → 入库；喊 wrong_table → 选表自愈；其它疑点 → 人工。" },
  { id: "t_commit", kind: "commit", x: XTERM, y: Y[4], w: TW, h: TH, label: "入库 committed", sub: "_auto_commit ✓",
    detail: "复核通过，自主入库。成功率的分子。" },
  { id: "t_human", kind: "human", x: XTERM, y: Y[5], w: TW, h: TH, label: "人工 verify_hold", sub: "复核否决",
    detail: "复核有疑点且非选表问题 → 交人工。" },

  { id: "heal", kind: "heal", x: XHEAL, y: Y[6], w: RW, h: RH, label: "选表自愈", sub: "选表 agent 重选表", agent: "select_table",
    detail: "非绿灯 / 复核喊选错表：select_table_llm 从全表重选正确构成表。这是确定性选表(②)拿不准时的 LLM 兜底。" },
  { id: "d_heal", kind: "decision", x: XHEAL, y: Y[7], w: DW, h: DH, label: "选表自愈结果?", sub: "heal_select",
    detail: "认不出(chosen=-1) → 真无表；认出一张新表 → 拿它回 ③解析 重解、重走过锚（闭环）；认出的表仍解不出(still_bad) → 交 L2 改规则。\n跨页表：选中表若被截断不过锚，会自动把物理紧邻的下一页续表拼上去再判锚（锚闸兜底：拼上过锚才采纳）。" },
  { id: "t_nosuch", kind: "human", x: XMID, y: Y[7], w: 176, h: TH, label: "no_such_table", sub: "真无表 · 人工",
    detail: "选表 agent 确认全表确实没有营收构成表 → 交人工（不诊断）。江铃汽车即此类。" },

  { id: "rule", kind: "heal", x: XHEAL, y: Y[8], w: 200, h: RH, label: "L2 改规则自愈", sub: "rule_heal · LLM 提 delta", agent: "rule_heal",
    detail: "选对表但 base 解不出：把选中表 + 逐维对锚偏差喂 LLM，让它提最小规则增量（切桶标记 / 认列别名，只加不删）。" },
  { id: "d_gate", kind: "decision", x: XHEAL, y: Y[9], w: DW, h: DH, label: "过锚 gate?", sub: "delta 重解后",
    detail: "合并 delta 重解那张表 → 过锚？安全闸：LLM 说得再像，不过锚一律不采纳。盈方微(碎表)即被此闸挡下转 L3。" },

  { id: "verifyT", kind: "verify", x: XMID, y: Y[8], w: 182, h: RH, label: "复核 (信任源文)", sub: "只逐项核数据", agent: "verify",
    detail: "L2 自愈已确认表选对，复核信任该表不再判选表，只核名称/金额/单位（叠加信任提示）。" },
  { id: "d_verifyT", kind: "decision", x: XMID, y: Y[9], w: DW, h: DH, label: "pass?", sub: "",
    detail: "pass → 入库（入库条件与首次一致）；否则人工。" },
  { id: "t_commitH", kind: "commit", x: XTERM, y: Y[8], w: TW, h: TH, label: "入库 committed", sub: "改规则 + 进池 ✓",
    detail: "L2 自愈后复核通过入库；save_version 把这条规则固化进版本池，以后同写法直接命中。" },
  { id: "t_humanT", kind: "human", x: XTERM, y: Y[9], w: TW, h: TH, label: "人工 verify_hold", sub: "自愈复核否决",
    detail: "L2 自愈过锚但复核仍有疑点 → 交人工。" },

  { id: "diag", kind: "stage", x: XMAIN, y: Y[9], w: RW, h: RH, label: "诊断 judge_diagnose", sub: "分层表层诊断", agent: "judge_diagnose",
    detail: "改规则也修不了（如 pdfplumber 把表抽碎、标记跨格）→ 分层诊断，定位跨页 / 口径 / 需改代码。" },
  { id: "t_diag", kind: "human", x: XDEAD, y: Y[9], w: 150, h: TH, label: "人工 / L3", sub: "改代码 · 下一层",
    detail: "诊断结论：交人工，或进 L3（改解析器代码 / 改抽表）。" },
];

const LINKS: Link[] = [
  { s: "scan", ss: "left", t: "no_input", ts: "right" },
  { s: "scan", ss: "bottom", t: "d_route", ts: "top" },
  { s: "d_route", ss: "right", t: "verify", ts: "top", label: "是 · routed 绿灯", tone: "yes" },
  { s: "d_route", ss: "bottom", t: "select", ts: "top", label: "否", tone: "no" },
  { s: "select", ss: "bottom", t: "parse", ts: "top" },
  { s: "parse", ss: "left", t: "no_data", ts: "right", label: "空", sOff: 12 },
  { s: "parse", ss: "bottom", t: "d_anchor", ts: "top" },
  { s: "d_anchor", ss: "left", t: "no_anchor", ts: "right", label: "无锚" },
  { s: "d_anchor", ss: "right", t: "verify", ts: "left", label: "是 · 绿灯", tone: "yes" },
  { s: "d_anchor", ss: "bottom", t: "heal", ts: "left", label: "否 · 非绿灯", tone: "no" },
  { s: "verify", ss: "bottom", t: "d_verify", ts: "top" },
  { s: "d_verify", ss: "right", t: "t_commit", ts: "left", label: "pass", tone: "commit", sOff: -16 },
  { s: "d_verify", ss: "right", t: "t_human", ts: "left", label: "其它 hold", tone: "human", sOff: 16 },
  { s: "d_verify", ss: "bottom", t: "heal", ts: "top", label: "wrong_table", tone: "no" },
  { s: "heal", ss: "bottom", t: "d_heal", ts: "top" },
  { s: "d_heal", ss: "right", t: "t_nosuch", ts: "left", label: "认不出(真无表)", tone: "human" },
  { s: "d_heal", ss: "left", t: "parse", ts: "left", label: "选到新表 ↑ 回③重解", tone: "yes", mid: 250, tOff: -15, dash: true },
  { s: "d_heal", ss: "bottom", t: "rule", ts: "top", label: "选对表仍解不出 · still_bad", tone: "no" },
  { s: "rule", ss: "bottom", t: "d_gate", ts: "top" },
  { s: "d_gate", ss: "right", t: "verifyT", ts: "left", label: "过锚 ✓", tone: "yes", tOff: 13 },
  { s: "d_gate", ss: "left", t: "diag", ts: "right", label: "修不了", tone: "no" },
  { s: "verifyT", ss: "bottom", t: "d_verifyT", ts: "top" },
  { s: "d_verifyT", ss: "right", t: "t_commitH", ts: "left", label: "pass", tone: "commit", sOff: -16 },
  { s: "d_verifyT", ss: "right", t: "t_humanT", ts: "left", label: "hold", tone: "human", sOff: 16 },
  { s: "diag", ss: "left", t: "t_diag", ts: "right", label: "转人工/L3", tone: "human" },
];

const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
function anchor(n: Node, s: Side, off = 0): [number, number] {
  if (s === "top") return [n.x + off, n.y - n.h / 2];
  if (s === "bottom") return [n.x + off, n.y + n.h / 2];
  if (s === "left") return [n.x - n.w / 2, n.y + off];
  return [n.x + n.w / 2, n.y + off];
}
const horiz = (s: Side) => s === "left" || s === "right";
// 正交（直角）连线：两段/三段折线，读起来工整
function ortho(l: Link): { pts: [number, number][]; label: [number, number] } {
  const [sx, sy] = anchor(byId[l.s], l.ss, l.sOff || 0);
  const [tx, ty] = anchor(byId[l.t], l.ts, l.tOff || 0);
  let pts: [number, number][];
  if (horiz(l.ss) && horiz(l.ts)) { const mx = l.mid ?? (sx + tx) / 2; pts = [[sx, sy], [mx, sy], [mx, ty], [tx, ty]]; }
  else if (!horiz(l.ss) && !horiz(l.ts)) { const my = (sy + ty) / 2; pts = [[sx, sy], [sx, my], [tx, my], [tx, ty]]; }
  else if (horiz(l.ss)) pts = [[sx, sy], [tx, sy], [tx, ty]];      // 横出 → 竖入
  else pts = [[sx, sy], [sx, ty], [tx, ty]];                       // 竖出 → 横入
  const m = pts[Math.floor((pts.length - 1) / 2)];
  const m2 = pts[Math.ceil((pts.length - 1) / 2)];
  return { pts, label: [(m[0] + m2[0]) / 2, (m[1] + m2[1]) / 2] };
}
const TONE: Record<string, string> = { yes: "#059669", no: "#dc2626", commit: "#059669", human: "#dc2626" };
const toPath = (pts: [number, number][]) => pts.map((p, i) => `${i ? "L" : "M"}${p[0]},${p[1]}`).join(" ");

type Prompt = { system?: string; user?: string; version?: string; model?: string };

export default function PipelineFlow() {
  const [sel, setSel] = useState<Node | null>(null);
  const [full, setFull] = useState(false);
  const [prompt, setPrompt] = useState<Prompt | null>(null);
  const [pState, setPState] = useState<"idle" | "loading" | "ok" | "off">("idle");
  const VW = 1360, VH = 1180;

  useEffect(() => {
    if (!full) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setFull(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [full]);

  // 选中的节点若对应某个 LLM agent → 拉它的 prompt 模板展示
  useEffect(() => {
    if (!sel?.agent) { setPrompt(null); setPState("idle"); return; }
    let cancel = false;
    setPrompt(null); setPState("loading");
    apiGet<Prompt | null>(`/agents/${sel.agent}`, null).then(({ data, live }) => {
      if (cancel) return;
      if (live && data) { setPrompt(data); setPState("ok"); } else setPState("off");
    });
    return () => { cancel = true; };
  }, [sel]);

  const paneH = full ? "calc(100vh - 150px)" : 760;

  return (
    <div className={full ? "fixed inset-0 z-50 bg-white p-4 overflow-auto" : "bg-white rounded-lg shadow-sm border p-4"}>
      <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div>
          <h2 className="font-semibold">解析流程图 · 一份报告一个字段的真实决策链（以 pipeline.run_field 为准）</h2>
          <p className="text-xs text-gray-400 mt-0.5">抽表 → 选表 → 解析(base优先扫版本池) → 过锚 → 复核 / 选表自愈 / L2改规则自愈 → 诊断。点节点看说明与 prompt。</p>
        </div>
        <div className="flex items-center gap-3 text-xs flex-wrap">
          {Object.values(C).map((s) => (
            <span key={s.name} className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded" style={{ background: s.fill, border: `2px solid ${s.stroke}` }} />
              {s.name}
            </span>
          ))}
          <button onClick={() => setFull((v) => !v)}
            className="px-3 py-1 rounded border border-gray-300 text-gray-600 hover:bg-gray-100 font-medium">
            {full ? "✕ 退出全屏 (Esc)" : "⛶ 全屏"}
          </button>
        </div>
      </div>

      <div className="flex gap-4 items-stretch">
        <div className="flex-1 min-w-0 overflow-hidden border rounded bg-gray-50" style={{ height: paneH }}>
          <svg viewBox={`0 0 ${VW} ${VH}`} preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%", display: "block" }}>
            <defs>
              <marker id="pf-arrow" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L10,5 L0,10 z" fill="context-stroke" />
              </marker>
            </defs>

            {/* 连线（正交折线） */}
            {LINKS.map((l, i) => {
              const { pts, label } = ortho(l);
              const col = l.tone ? TONE[l.tone] : "#94a3b8";
              return (
                <g key={i}>
                  <path d={toPath(pts)} fill="none" stroke={col} strokeWidth={l.tone ? 2 : 1.6}
                    strokeLinejoin="round" strokeDasharray={l.dash ? "7 5" : undefined} markerEnd="url(#pf-arrow)" opacity={0.9} />
                  {l.label && (
                    <text x={label[0]} y={label[1] - 5} textAnchor="middle" fontSize="10.5"
                      fill={l.tone ? col : "#64748b"} style={{ paintOrder: "stroke", stroke: "#f9fafb", strokeWidth: 3.5 }}>
                      {l.label}
                    </text>
                  )}
                </g>
              );
            })}

            {/* 节点 */}
            {NODES.map((n) => {
              const c = C[n.kind];
              const active = sel?.id === n.id;
              const common = { fill: c.fill, stroke: c.stroke, strokeWidth: active ? 3 : 2, cursor: "pointer" as const };
              return (
                <g key={n.id} onClick={() => setSel(n)}>
                  {n.kind === "decision" ? (
                    <polygon points={`${n.x},${n.y - n.h / 2} ${n.x + n.w / 2},${n.y} ${n.x},${n.y + n.h / 2} ${n.x - n.w / 2},${n.y}`} {...common} />
                  ) : (
                    <rect x={n.x - n.w / 2} y={n.y - n.h / 2} width={n.w} height={n.h} rx={9} {...common} />
                  )}
                  <text x={n.x} y={n.sub ? n.y - 3 : n.y + 4} textAnchor="middle" fontSize="12.5" fontWeight={600} fill="#1f2937" pointerEvents="none">
                    {n.label}
                  </text>
                  {n.sub && (
                    <text x={n.x} y={n.y + 13} textAnchor="middle" fontSize="9.5" fill="#94a3b8" pointerEvents="none">{n.sub}</text>
                  )}
                </g>
              );
            })}
          </svg>
        </div>

        <aside className={`${sel?.agent === "verify" ? "w-[28rem]" : "w-80"} shrink-0 border rounded bg-gray-50 p-3 overflow-auto`} style={{ maxHeight: paneH }}>
          {sel ? (
            <>
              <div className="flex items-start justify-between gap-2">
                <h3 className="font-semibold flex items-center gap-2 leading-tight">
                  <span className="inline-block w-3 h-3 rounded shrink-0 mt-1" style={{ background: C[sel.kind].fill, border: `2px solid ${C[sel.kind].stroke}` }} />
                  <span>{sel.label}
                    <span className="block text-xs font-normal text-gray-400">{sel.sub ? `${sel.sub} · ` : ""}{C[sel.kind].name}</span>
                  </span>
                </h3>
                <button onClick={() => setSel(null)} className="text-gray-400 hover:text-gray-600 text-sm shrink-0">✕</button>
              </div>
              <p className="text-sm text-gray-600 mt-2 leading-relaxed whitespace-pre-line">{sel.detail}</p>

              {sel.agent && (
                <div className="mt-3 border-t pt-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-semibold text-gray-600">
                      Prompt · {sel.agent}{prompt?.version ? ` ${prompt.version}` : ""}
                    </span>
                    {prompt?.model && <span className="text-[10px] text-gray-400 font-mono">{prompt.model}</span>}
                  </div>
                  {pState === "loading" && <p className="text-xs text-gray-400 mt-1">加载中…</p>}
                  {pState === "off" && <p className="text-xs text-gray-400 mt-1">后端未就绪，无法取 prompt（需 localhost:8200）。</p>}
                  {pState === "ok" && (
                    <>
                      {prompt?.system && (
                        <>
                          <div className="text-[10px] text-gray-400 mt-1.5">system（角色设定）</div>
                          <pre className="text-[11px] leading-snug whitespace-pre-wrap break-words bg-white border rounded p-2 mt-0.5 max-h-40 overflow-auto text-gray-700">{prompt.system}</pre>
                        </>
                      )}
                      {prompt?.user && (
                        <>
                          <div className="text-[10px] text-gray-400 mt-1.5">user 模板（{"{{变量}}"} 运行时替换）</div>
                          <pre className="text-[11px] leading-snug whitespace-pre-wrap break-words bg-white border rounded p-2 mt-0.5 max-h-56 overflow-auto text-gray-700">{prompt.user}</pre>
                        </>
                      )}
                      <p className="text-[10px] text-gray-400 mt-1">在「Agent 管理」页可编辑并热生效。</p>
                    </>
                  )}
                  <RenderedPromptExamples agentId={sel.agent} />
                </div>
              )}
            </>
          ) : (
            <div className="text-sm text-gray-500 leading-relaxed">
              <p className="font-medium text-gray-600 mb-2">点左侧节点看该步说明 / prompt</p>
              <p className="text-xs">
                三层自愈：<b className="text-violet-600">复核</b>(绿灯不当场入库) → <b className="text-amber-600">选表自愈</b>(选错表就重选、拿到新表回主流程重解过锚) →
                <b className="text-amber-600"> L2 改规则</b>(选对表解不出就让 LLM 提规则增量)。
              </p>
              <p className="text-xs mt-2"><b>过锚 gate</b> 是安全闸：LLM 提议不过锚一律不采纳。</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
