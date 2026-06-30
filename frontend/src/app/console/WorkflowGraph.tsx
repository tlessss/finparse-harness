"use client";

import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";

type Status = "live" | "partial" | "frontend" | "design";
type Node = {
  id: string; label: string; sub: string; col: number; row: number; status: Status;
  detail: string; metric: string; mock: [string, string][]; sample?: string;
};
type Link = { s: string; t: string; label?: string; kind?: "clean" | "red" | "design" };

const STATUS: Record<Status, { c: string; name: string }> = {
  live: { c: "#10b981", name: "在用（生产中跑）" },
  partial: { c: "#f59e0b", name: "已建·未接入主流程" },
  frontend: { c: "#3b82f6", name: "前端已建（mock）" },
  design: { c: "#9ca3af", name: "纯设计（未实现）" },
};

// ── 当前真实工作流 + 每节点 mock/真实数据（数字来自全量 1249 跑批）──
const NODES: Node[] = [
  { id: "cache", label: "PDF 缓存", sub: "1289 份", col: 0, row: 2, status: "live", metric: "1289",
    detail: "../book-agent/output/pdf_cache，按 stock_code 去重", sample: "002407_2025.pdf · 000333_2025.pdf …",
    mock: [["目录", "book-agent/output/pdf_cache"], ["文件命名", "{code}_{year}.pdf"], ["总数", "1289"]] },
  { id: "discover", label: "discover", sub: "去重/筛年份", col: 1, row: 2, status: "live", metric: "1249",
    detail: "扫缓存、按 stock_code 去重、筛 2025、断点续跑跳过已完成",
    mock: [["去重后", "1249 份"], ["年份", "2025"], ["续跑", "跳过已完成"]] },
  { id: "pool", label: "ProcessPool", sub: "并发 ×6", col: 2, row: 2, status: "live", metric: "×6",
    detail: "多进程并行 + SIGALRM 单份硬超时 + 故障隔离",
    mock: [["workers", "6"], ["单份超时", "150s"], ["全量耗时", "~70 min"], ["崩溃", "0"]] },
  { id: "fp", label: "算指纹", sub: "fingerprint", col: 3, row: 0, status: "live", metric: "262 版式",
    detail: "layout_fingerprint：doc_type + 板块组合，用于缩候选 / 经验复用",
    mock: [["版式数", "262"], ["摊薄率", "4.8x"], ["doc_type", "normal 1240 / bank 3 / 券商 6"]] },
  { id: "scan", label: "scan_pdf", sub: "find_tables+bbox", col: 3, row: 2, status: "live", metric: "+bbox",
    detail: "M1：find_tables() 抽表并保留每格 (页码,bbox) 坐标——溯源地基", sample: "工业 | 7,987,… | 84.67% @p24 bbox(241,565,…)",
    mock: [["API", "find_tables()"], ["每格", "(页码, bbox)"], ["扫描范围", "15~215 页"]] },
  { id: "engine", label: "engine.run", sub: "selector 选解析器", col: 4, row: 2, status: "live", metric: "6 字段",
    detail: "按字段 can_handle() 评分选解析器，共享 pre_scan 抽表",
    mock: [["字段数", "6"], ["selector", "can_handle 评分"], ["pre_scan", "共享，抽表只一次"]] },
  { id: "rev", label: "营收解析", sub: "找页·A/B·认列·溯源", col: 5, row: 0, status: "live", metric: "81% 覆盖",
    detail: "M1/M2：信号打分找页 + A/B 表择优 + 表头驱动认列(占比闸门) + 吐溯源", sample: "氟基新材料 29.56% ✓占比和100%",
    mock: [["覆盖率", "81%"], ["找页", "信号打分取 top-N"], ["认表", "A/B 择优(避毛利率表)"], ["认列", "表头驱动+占比闸门"], ["溯源", "27 条/份"]] },
  { id: "sig", label: "研发/员工/成本/供应商", sub: "signature 特征", col: 5, row: 3, status: "live", metric: "76~98%",
    detail: "filter_by_signature 内容特征匹配", sample: "员工 868=总数（部分公司跨页漏行）",
    mock: [["员工", "98%"], ["供应商", "94%"], ["客户", "93%"], ["研发", "85%"], ["成本", "76%"]] },
  { id: "assemble", label: "组装 JSON", sub: "6 字段", col: 6, row: 2, status: "live", metric: "JSON",
    detail: "6 字段 + parse_flags + field_count + 溯源",
    mock: [["字段", "6"], ["附带", "溯源 / field_count"], ["净通过 6/6", "574 份(46%)"]] },
  { id: "hard", label: "hard_rules", sub: "红线校验", col: 7, row: 2, status: "live", metric: "红线",
    detail: "占比和≈100 / 三和相等 / 分项≤合计，不可被 LLM 绕过",
    mock: [["营收", "各维度占比和≈100"], ["员工", "专业和=教育和=总数"], ["研发", "明细和=合计"]] },
  { id: "decide", label: "分类", sub: "clean / red", col: 8, row: 2, status: "live", metric: "988/261",
    detail: "硬规则通过=clean；红线/异常/超时=死信",
    mock: [["clean", "988"], ["red", "261"], ["error/timeout", "0 / 0"]] },
  { id: "results", label: "results.jsonl", sub: "归档", col: 9, row: 1, status: "live", metric: "988*",
    detail: "清洁结果落盘（口径=硬规则 clean，≠ golden-exact，待核来源）",
    mock: [["clean*", "988 (79.1%)"], ["净通过 6/6", "574 (46%)"], ["口径", "硬规则clean≠exact · 待核"]] },
  { id: "dead", label: "deadletter", sub: "死信队列", col: 9, row: 3, status: "live", metric: "261*",
    detail: "red/error/timeout 归集，喂入自愈入口（口径待核）",
    mock: [["总数*", "261 份"], ["employee:count_sum", "166"], ["rnd:sum_vs_total", "86"], ["revenue:ratio_sum", "32"], ["失败版式", "107 个"], ["口径", "待核来源"]] },
  { id: "board", label: "run_status", sub: "看板", col: 10, row: 1, status: "live", metric: "79.1%*",
    detail: "清洁率/净通过/字段覆盖/死信聚类（* 口径=硬规则clean，非golden-exact，待核）",
    mock: [["清洁率*", "44% → 79.1%"], ["净通过", "46%"], ["口径", "硬规则clean，待核"], ["命令", "scripts.run_status"]] },

  // ── 自愈轨（heal_pipeline）：已建+跑通 ──
  { id: "heal", label: "自愈入口", sub: "heal_revenue", col: 2, row: 5, status: "partial", metric: "入口",
    detail: "agents/heal_pipeline.heal_revenue：有golden→修复到exact入库；无golden→转人工",
    mock: [["模块", "agents/heal_pipeline"], ["认证期", "修复→exact→认证"], ["运行期", "无golden→转人工"]] },
  { id: "select", label: "选择即验证", sub: "找最像母本", col: 3, row: 5, status: "partial", metric: "router",
    detail: "parsers/revenue_router.route_revenue：跑候选→打分，选最像母本（不靠猜靠跑）",
    mock: [["模块", "parsers/revenue_router"], ["机制", "跑候选→打分选优"], ["输出", "母本 + 差在哪"]] },
  { id: "gen", label: "生成解析器", sub: "fork/新建", col: 4, row: 5, status: "partial", metric: "已建",
    detail: "agents/code_generator：fork 母本改钩子 / 从零新建（构建期写代码，运行期冻结）",
    mock: [["模块", "agents/code_generator"], ["策略", "fork 优先，从零兜底"], ["运行期", "零 LLM（冻结）"]] },
  { id: "sandbox", label: "代码沙箱", sub: "隔离跑", col: 5, row: 5, status: "partial", metric: "已建",
    detail: "eval/sandbox_exec + eval/run_eval：隔离跑生成的解析器，出结果",
    mock: [["模块", "eval/sandbox_exec, run_eval"], ["隔离", "单份跑"], ["输出", "结果 + 得分"]] },
  { id: "score", label: "打分器", sub: "golden=那把尺", col: 6, row: 5, status: "partial", metric: "golden",
    detail: "eval/revenue_score 对 goldset/revenue_golden.json 打分——真值标尺", sample: "score=1.0 → exact ✓",
    mock: [["模块", "eval/revenue_score"], ["真值", "goldset/revenue_golden.json"], ["指标", "exact / 部分匹配"]] },
  { id: "gate", label: "版本闸", sub: "终点恒 exact", col: 7, row: 5, status: "partial", metric: "exact",
    detail: "终点恒 exact：到 exact 才认证入库；想尽办法仍不 exact → 转人工（不留半成品）",
    mock: [["判据", "score == exact"], ["exact ✓", "认证入注册表"], ["非exact", "转人工兜底"]] },
  { id: "registry", label: "注册表", sub: "认证入库", col: 8, row: 5, status: "partial", metric: "已建",
    detail: "parsers/registry：认证解析器入注册表，同版式后续免审复用（经验库物理实现）",
    mock: [["模块", "parsers/registry"], ["键", "版式 key"], ["收益", "同版式免审"]] },
  { id: "review", label: "人审兜底", sub: "撞墙才进·非必经", col: 8, row: 4, status: "partial", metric: "兜底",
    detail: "撞墙兜底（非必经）：仅 gate 非exact / 无golden 才进；后端 /review 真接口在线",
    mock: [["触发", "gate非exact / 无golden"], ["后端", "console_service + /review 在线"], ["非必经", "exact 直接认证，不经人审"]] },
];

const LINKS: Link[] = [
  { s: "cache", t: "discover" }, { s: "discover", t: "pool" },
  { s: "pool", t: "scan" }, { s: "pool", t: "fp" },
  { s: "fp", t: "engine" }, { s: "scan", t: "engine" },
  { s: "engine", t: "rev" }, { s: "engine", t: "sig" },
  { s: "rev", t: "assemble" }, { s: "sig", t: "assemble" },
  { s: "assemble", t: "hard" }, { s: "hard", t: "decide" },
  { s: "decide", t: "results", label: "clean ✓", kind: "clean" },
  { s: "decide", t: "dead", label: "red ✗", kind: "red" },
  { s: "results", t: "board" },
  // ── 自愈轨真实流向 ──
  { s: "dead", t: "heal", label: "失败→自愈", kind: "design" },     // 死信喂入自愈
  { s: "heal", t: "select" },
  { s: "select", t: "gen", label: "fork/新建" },                    // 三岔之二
  { s: "select", t: "gate", label: "复用 exact", kind: "clean" },   // 三岔之一：母本本就exact，跳过生成
  { s: "gen", t: "sandbox" }, { s: "sandbox", t: "score" }, { s: "score", t: "gate" },
  { s: "gate", t: "registry", label: "exact ✓", kind: "clean" },    // 到exact→认证
  { s: "registry", t: "results", label: "认证→归档", kind: "clean" },
  { s: "gate", t: "review", label: "非exact ✗", kind: "red" },      // 撞墙→人审兜底
  { s: "heal", t: "review", label: "无golden→转人工", kind: "red" },// 无真值→转人工
  { s: "review", t: "registry", label: "人工修正后认证", kind: "design" },
];

const COLW = 150, ROWH = 92, NW = 124, NH = 52, MX = 40, MY = 40;

export default function WorkflowGraph() {
  const ref = useRef<SVGSVGElement | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; node: Node } | null>(null);
  const [sel, setSel] = useState<Node | null>(null);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    const pos = (n: Node) => ({ x: MX + n.col * COLW, y: MY + n.row * ROWH });
    const byId = Object.fromEntries(NODES.map((n) => [n.id, n]));
    const W = MX * 2 + 10 * COLW + NW, H = MY * 2 + 5 * ROWH + NH;
    svg.attr("viewBox", `0 0 ${W} ${H}`);
    const g = svg.append("g");
    svg.call(d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.5, 2.5]).on("zoom", (e) => g.attr("transform", e.transform)) as never);

    const defs = svg.append("defs");
    defs.append("marker").attr("id", "arrow").attr("viewBox", "0 0 10 10").attr("refX", 9).attr("refY", 5)
      .attr("markerWidth", 6).attr("markerHeight", 6).attr("orient", "auto-start-reverse")
      .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("fill", "#94a3b8");

    g.append("g").selectAll("path").data(LINKS).join("path")
      .attr("d", (l) => {
        const a = pos(byId[l.s]), b = pos(byId[l.t]);
        const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
        return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
      })
      .attr("fill", "none")
      .attr("stroke", (l) => l.kind === "red" ? "#ef4444" : l.kind === "clean" ? "#10b981" : l.kind === "design" ? "#cbd5e1" : "#94a3b8")
      .attr("stroke-width", (l) => l.kind === "design" ? 1.5 : 2)
      .attr("stroke-dasharray", (l) => l.kind === "design" ? "5,4" : "0")
      .attr("class", (l) => l.kind && l.kind !== "design" ? "wf-flow" : "")
      .attr("marker-end", "url(#arrow)");

    g.append("g").selectAll("text").data(LINKS.filter((l) => l.label)).join("text")
      .attr("x", (l) => (pos(byId[l.s]).x + NW + pos(byId[l.t]).x) / 2)
      .attr("y", (l) => (pos(byId[l.s]).y + pos(byId[l.t]).y) / 2 + NH / 2 - 6)
      .attr("text-anchor", "middle").attr("font-size", 10)
      .attr("fill", (l) => l.kind === "red" ? "#ef4444" : l.kind === "clean" ? "#10b981" : "#94a3b8")
      .text((l) => l.label!);

    const node = g.append("g").selectAll("g").data(NODES).join("g")
      .attr("transform", (n) => { const p = pos(n); return `translate(${p.x},${p.y})`; })
      .style("cursor", "pointer")
      .on("mousemove", (e, n) => setTip({ x: e.offsetX, y: e.offsetY, node: n }))
      .on("mouseleave", () => setTip(null))
      .on("click", (_e, n) => setSel(n));
    node.append("rect").attr("width", NW).attr("height", NH).attr("rx", 8)
      .attr("fill", "#fff").attr("stroke", (n) => STATUS[n.status].c).attr("stroke-width", 2)
      .attr("stroke-dasharray", (n) => n.status === "design" ? "4,3" : "0");
    node.append("rect").attr("width", 5).attr("height", NH).attr("rx", 2).attr("fill", (n) => STATUS[n.status].c);
    node.append("text").attr("x", 14).attr("y", 22).attr("font-size", 12).attr("font-weight", 600).attr("fill", "#1f2937").text((n) => n.label);
    node.append("text").attr("x", 14).attr("y", 38).attr("font-size", 9.5).attr("fill", "#9ca3af").text((n) => n.sub);
    // 指标徽标（右上角）
    node.append("text").attr("x", NW - 8).attr("y", 16).attr("text-anchor", "end").attr("font-size", 9.5)
      .attr("font-weight", 700).attr("fill", (n) => STATUS[n.status].c).text((n) => n.metric);
  }, []);

  return (
    <div className="bg-white rounded-lg shadow-sm border p-4 relative">
      <div className="flex items-center justify-between mb-2">
        <h2 className="font-semibold">当前工作流（以代码为准 · 缩放拖拽 · 点节点看数据）</h2>
        <div className="flex gap-3 text-xs">
          {Object.values(STATUS).map((s) => (
            <span key={s.name} className="flex items-center gap-1">
              <span className="inline-block w-3 h-3 rounded" style={{ background: s.c }} />{s.name}
            </span>
          ))}
        </div>
      </div>
      <svg ref={ref} className="w-full" style={{ height: 600 }} />
      <p className="text-xs text-gray-400 mt-1">* 标星数据(79.1% clean / 261 死信)口径=硬规则 clean，非 golden-exact，来源待核。自愈轨：复用/fork/新建 三岔 → 闸恒 exact，否则转人工；人审是撞墙兜底，非必经。</p>

      {tip && (
        <div className="absolute pointer-events-none bg-gray-900 text-white text-xs rounded px-2 py-1 max-w-xs z-20"
          style={{ left: tip.x + 12, top: tip.y + 12 }}>
          <b>{tip.node.label}</b> · {STATUS[tip.node.status].name}
          <div className="text-gray-300 mt-0.5">{tip.node.detail}</div>
        </div>
      )}

      {/* 点击节点 → 数据详情面板 */}
      {sel && (
        <div className="mt-3 border-t pt-3">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold flex items-center gap-2">
              <span className="inline-block w-3 h-3 rounded" style={{ background: STATUS[sel.status].c }} />
              {sel.label} <span className="text-xs font-normal text-gray-400">{sel.sub} · {STATUS[sel.status].name}</span>
            </h3>
            <button onClick={() => setSel(null)} className="text-gray-400 hover:text-gray-600 text-sm">✕ 关闭</button>
          </div>
          <p className="text-sm text-gray-500 mt-1">{sel.detail}</p>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mt-2">
            {sel.mock.map(([k, v]) => (
              <div key={k} className="bg-gray-50 rounded px-3 py-2 text-sm flex justify-between">
                <span className="text-gray-500">{k}</span><span className="font-mono font-medium text-gray-800">{v}</span>
              </div>
            ))}
          </div>
          {sel.sample && (
            <div className="mt-2 text-xs font-mono bg-gray-900 text-green-300 rounded px-3 py-2">样例 → {sel.sample}</div>
          )}
        </div>
      )}

      <style>{`.wf-flow{stroke-dasharray:6,4;animation:wfdash 1s linear infinite}@keyframes wfdash{to{stroke-dashoffset:-10}}`}</style>
    </div>
  );
}
