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
  planned?: boolean; // 规划中（尚未接入 run_field）→ 虚线描边区分
  deprecated?: boolean; // 现状但将被取代 → 淡化 + 细点描边
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
const Y = [60, 175, 290, 405, 520, 635, 750, 865, 980, 1095, 1210, 1325, 1440]; // y0..y12（y10+ = 规划中 L3 代码生成）
const RW = 190, RH = 54, DW = 172, DH = 72, TW = 168, TH = 50, EW = 122, EH = 44;

const NODES: Node[] = [
  { id: "scan", kind: "stage", x: XMAIN, y: Y[0], w: RW, h: RH, label: "① 抽表 · 找 PDF", sub: "get_tables + PDF",
    detail: "拿到该报告已抽好的全部表格 + PDF 路径。缺任一 → no_input。" },
  { id: "no_input", kind: "exit", x: XDEAD, y: Y[0], w: EW, h: EH, label: "no_input", sub: "无表 / 无PDF",
    detail: "没有可解析的输入，直接退出。" },
  { id: "d_route", kind: "decision", deprecated: true, x: XMAIN, y: Y[1], w: DW, h: DH, label: "命中认证解析器?", sub: "route_field · 文档级 (现状·将废弃)",
    detail: "【现状,将被取代】route_field 用**文档级版式指纹**(doc_type + 章节相对页桶 → md5)缩候选、逐个跑过金额锚才命中 → routed。它坐在『②选表』**之前**，是因为现在的认证解析器是**端到端·自带选表**，故 routed 跳过确定性选表。\n⚠️ 为什么会废弃：①文档级太粗(同指纹的两家营收表可能是占比表/成本毛利率表/矩阵表,要不同 parser)；②表+标题级匹配**需要选中的那张表才能算嵌入 → 只能放在选表之后**。选表解耦后 parser 退化成 **parse-only(吃选中表)**，路由统一到『②选表 → 向量匹 parser』这一个点，此节点消失。" },
  { id: "route_v2", kind: "decision", planned: true, x: 700, y: Y[2], w: DW, h: DH, label: "向量匹 parser", sub: "表+标题嵌入 · 选表后",
    detail: "规划：取代上面文档级 route 的表级路由。选中目标表后 → 把它的『去数字骨架(表头+维度标记+行名) + 标题』做 BGE 嵌入（复用 vector_recall），与每个认证解析器登记的同款嵌入算**余弦最近邻**缩候选 → 跑候选、过金额锚+复核双闸才 routed。\n表头是最强判别位(金额|占比 vs 营收|成本|毛利率 vs 矩阵)；标题辅助但别主导(有的真表标题是废话)。有无线框属抽表层(L3)、不进此向量。\n命中 → routed 绿灯去复核；否 → 继续 ③解析。" },
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
    detail: "L2 改规则也修不了 → 先试 L3 抽表自愈（extract_heal：换 pdfplumber 参数重抽 / camelot 抽无线框表，过锚+复核兜底）；仍不行 → 分层诊断，定位跨页 / 口径 / 需改代码 → 转下面的代码生成。" },
  { id: "t_diag", kind: "human", x: XDEAD, y: Y[9], w: 150, h: TH, label: "转人工", sub: "诊断不可自动化",
    detail: "诊断结论为不可自动修复（如源文本身缺失）→ 交人工。可自动的走代码生成。" },

  // ── 规划中 · L3 代码生成自愈层（尚未接入 run_field；核心改造 = 验收从 golden 换成双闸）──
  { id: "cg_match", kind: "decision", planned: true, x: XMAIN, y: Y[10], w: DW, h: DH, label: "母本匹配 · 向量", sub: "去数字表→余弦最近解析器",
    detail: "pick_mother 用**向量**找最像的已认证解析器（与上面『向量匹 parser』同一套表征）：把目标表『去数字骨架(表头+维度标记+行名) + 标题』做 BGE 嵌入，与每个认证解析器登记的同款嵌入算余弦——最近的即最可能适配。**表+标题级**(非文档级)：表头/维度标记是判别位，标题辅助。比脆的版式指纹鲁棒，解析器越多越准。\n三岔：母本对本报告 exact → 直接复用(不调 LLM)；部分像 → fork 改；都不像 → 从零写。" },
  { id: "cg_gen", kind: "heal", planned: true, x: XHEAL, y: Y[10], w: RW, h: RH, label: "代码生成 (LLM)", sub: "generate_parser_autonomous · prompt=codegen.yaml", agent: "codegen",
    detail: "LLM 按 spec 契约写 parse(tables)。fork = 在最像母本上改；新建 = 从零写。\n生成闭环**已建**(generate_parser_autonomous:无 golden,验收=双闸)，待接『触发(诊断→L3)』+『强 codegen 模型』(DeepSeek 太弱,连 000333 的其中嵌套都写不对)。\nprompt 已抽进 Prompt Registry(codegen.yaml,下方可看/管理页可热编辑) v2 修了:①去占比中心 ②前置结构坑(其中父子重复/跨页续表/双口径前导) ③给营收锚数值自查漏行 ④**带上『现有解析器源码 + 它在这份的错输出(逐维合计/锚偏差)』**——让 LLM 看着代码和症状定位 bug 改对,而非从零瞎写(如 300005:regions/by_channel=0.00×锚→一眼看出没抓到 p24 的分地区/分销售)。" },
  { id: "cg_sandbox", kind: "stage", planned: true, x: XMID, y: Y[10], w: RW, h: RH, label: "沙箱执行", sub: "subprocess 隔离 + 超时",
    detail: "version_parse_fn 把生成的代码在**缓存表**上跑（毫秒级）。⚠️ 现为本进程 importlib exec；跑 LLM 现写的任意代码前须升级为 subprocess + 超时 / 资源限制。" },
  { id: "cg_gate", kind: "decision", planned: true, x: XMID, y: Y[11], w: DW, h: DH, label: "双闸: 过锚 + 复核?", sub: "替代 golden 真值",
    detail: "**核心改造点**：自主态没有 golden 真值，验收改用两个独立真值锚——金额锚(field_plausibility=high，各维度和≈营收/主营) AND 复核(verify_field=pass)。两个都过才收。护栏 accept_candidate 保证不比 base 退步。LLM 写错 → 过不了锚 → reject 重试（弱模型也不会错填）。" },
  { id: "cg_commit", kind: "commit", planned: true, x: XTERM, y: Y[11], w: TW, h: TH, label: "入库 + 注册解析器", sub: "登记表+标题嵌入 → 复利",
    detail: "过双闸 → 入库 + 把该认证解析器连同它目标表的『去数字骨架 + 标题』嵌入注册进目录。下次报告选完表 → 在『向量匹 parser』用同款嵌入**余弦命中** → routed 免 LLM（越跑越省 = 固化复利）。写(此处登记嵌入)和读(选表后余弦匹配)用的是同一套表征。" },
  { id: "cg_human", kind: "human", planned: true, x: XMID, y: Y[12], w: TW, h: TH, label: "转人工", sub: "K 轮仍不过双闸",
    detail: "生成 ≤K 轮仍过不了双闸 → 不留半成品，转人工。" },
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
  { s: "diag", ss: "left", t: "t_diag", ts: "right", label: "不可自动修", tone: "human" },
  // 规划 · 表+标题级向量路由（移到选表之后，取代文档级 d_route）
  { s: "select", ss: "right", t: "route_v2", ts: "left", label: "规划 · 选表后", tone: "yes", dash: true },
  { s: "route_v2", ss: "bottom", t: "verify", ts: "top", label: "命中 → routed 绿灯", tone: "yes", dash: true, tOff: -20 },
  // 规划中 · 代码生成层
  { s: "diag", ss: "bottom", t: "cg_match", ts: "top", label: "需改代码 · L3", tone: "no" },
  { s: "cg_match", ss: "right", t: "cg_gen", ts: "left", label: "fork / 新建", tone: "no" },
  { s: "cg_match", ss: "bottom", t: "cg_gate", ts: "top", label: "exact母本 · 复用(免LLM)", tone: "yes", dash: true, tOff: -22 },
  { s: "cg_gen", ss: "right", t: "cg_sandbox", ts: "left" },
  { s: "cg_sandbox", ss: "bottom", t: "cg_gate", ts: "top", tOff: 16 },
  { s: "cg_gate", ss: "right", t: "cg_commit", ts: "left", label: "过双闸 ✓", tone: "commit" },
  { s: "cg_gate", ss: "left", t: "cg_gen", ts: "bottom", label: "不过 · 回喂偏差 ≤K", tone: "no", dash: true },
  { s: "cg_gate", ss: "bottom", t: "cg_human", ts: "top", label: "K轮仍不过", tone: "human" },
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
  const VW = 1360, VH = 1520;

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
          <p className="text-xs text-gray-400 mt-0.5">抽表 → 选表 → 解析(base优先扫版本池) → 过锚 → 复核 / 选表自愈 / L2改规则自愈 → 诊断 → <span className="text-indigo-400">L3 代码生成(规划·虚线)</span>。点节点看说明与 prompt。</p>
        </div>
        <div className="flex items-center gap-3 text-xs flex-wrap">
          {Object.values(C).map((s) => (
            <span key={s.name} className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded" style={{ background: s.fill, border: `2px solid ${s.stroke}` }} />
              {s.name}
            </span>
          ))}
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-3 rounded" style={{ background: "#eef2ff", border: "2px dashed #6366f1", opacity: 0.7 }} />
            规划中 (L3 代码生成)
          </span>
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
              const common = { fill: c.fill, stroke: c.stroke, strokeWidth: active ? 3 : 2, cursor: "pointer" as const,
                fillOpacity: n.planned ? 0.55 : n.deprecated ? 0.4 : 1,
                strokeOpacity: n.deprecated ? 0.5 : 1,
                strokeDasharray: n.planned ? "6 4" : n.deprecated ? "2 3" : undefined };
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
                多层自愈：<b className="text-violet-600">复核</b>(绿灯不当场入库) → <b className="text-amber-600">选表自愈</b>(选错表就重选、拿到新表回主流程重解过锚) →
                <b className="text-amber-600"> L2 改规则</b>(选对表解不出就让 LLM 提规则增量) → <b className="text-amber-600">L3 抽表</b>(pdfplumber/camelot 换参重抽) →
                <b className="text-indigo-500"> L3 代码生成</b>(规划：向量找母本 → LLM 写解析器 → 双闸验收 → 注册复利)。
              </p>
              <p className="text-xs mt-2"><b>过锚 gate</b> 是安全闸：LLM 提议(改规则 / 重抽 / 写代码)一律要过<b>金额锚 + 复核</b>双闸才采纳，绝不因 LLM「说得像」就入库。</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
