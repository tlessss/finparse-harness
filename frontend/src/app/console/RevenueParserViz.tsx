"use client";

import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import {
  FLOW_NODES,
  FLOW_LINKS,
  MOCK_RAW_TABLE,
  MOCK_COLUMN_MAP,
  MOCK_CLASSIFY_ROWS,
  MOCK_OUTPUT,
  DIM_CN,
  yi,
  type FlowNode,
} from "./revenueParserMock";

const COLW = 128;
const ROWH = 88;
const NW = 108;
const NH = 50;
const MX = 36;
const MY = 36;

const KIND_STROKE: Record<string, string> = {
  main: "#3b82f6",
  fallback: "#f59e0b",
  default: "#94a3b8",
};

export default function RevenueParserViz() {
  const ref = useRef<SVGSVGElement | null>(null);
  const [sel, setSel] = useState<FlowNode>(FLOW_NODES[0]);
  const [playStep, setPlayStep] = useState(0);
  const [playing, setPlaying] = useState(false);

  const playOrder = ["entry", "prescan", "unit", "columns", "classify", "out"];

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setPlayStep((s) => {
        if (s >= playOrder.length - 1) {
          setPlaying(false);
          return s;
        }
        const next = s + 1;
        const node = FLOW_NODES.find((n) => n.id === playOrder[next]);
        if (node) setSel(node);
        return next;
      });
    }, 1400);
    return () => clearInterval(id);
  }, [playing]);

  useEffect(() => {
    const svg = d3.select(ref.current);
    svg.selectAll("*").remove();
    const pos = (n: FlowNode) => ({ x: MX + n.col * COLW, y: MY + n.row * ROWH });
    const byId = Object.fromEntries(FLOW_NODES.map((n) => [n.id, n]));
    const W = MX * 2 + 7 * COLW + NW;
    const H = MY * 2 + 3 * ROWH + NH;
    svg.attr("viewBox", `0 0 ${W} ${H}`);
    const g = svg.append("g");
    svg.call(
      d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.45, 2.2]).on("zoom", (e) => g.attr("transform", e.transform)) as never,
    );

    const defs = svg.append("defs");
    defs
      .append("marker")
      .attr("id", "rp-arrow")
      .attr("viewBox", "0 0 10 10")
      .attr("refX", 9)
      .attr("refY", 5)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto-start-reverse")
      .append("path")
      .attr("d", "M0,0 L10,5 L0,10 z")
      .attr("fill", "#94a3b8");

    g.append("g")
      .selectAll("path")
      .data(FLOW_LINKS)
      .join("path")
      .attr("d", (l) => {
        const a = pos(byId[l.s]);
        const b = pos(byId[l.t]);
        const x1 = a.x + NW;
        const y1 = a.y + NH / 2;
        const x2 = b.x;
        const y2 = b.y + NH / 2;
        const mx = (x1 + x2) / 2;
        return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
      })
      .attr("fill", "none")
      .attr("stroke", (l) => KIND_STROKE[l.kind || "default"] || "#94a3b8")
      .attr("stroke-width", 2)
      .attr("stroke-dasharray", (l) => (l.kind === "fallback" ? "6,4" : "0"))
      .attr("marker-end", "url(#rp-arrow)");

    g.append("g")
      .selectAll("text")
      .data(FLOW_LINKS.filter((l) => l.label))
      .join("text")
      .attr("x", (l) => (pos(byId[l.s]).x + NW + pos(byId[l.t]).x) / 2)
      .attr("y", (l) => (pos(byId[l.s]).y + pos(byId[l.t]).y) / 2 + NH / 2 - 6)
      .attr("text-anchor", "middle")
      .attr("font-size", 9)
      .attr("fill", (l) => KIND_STROKE[l.kind || "default"] || "#94a3b8")
      .text((l) => l.label!);

    const node = g
      .append("g")
      .selectAll("g")
      .data(FLOW_NODES)
      .join("g")
      .attr("transform", (n) => {
        const p = pos(n);
        return `translate(${p.x},${p.y})`;
      })
      .style("cursor", "pointer")
      .on("click", (_e, n) => {
        setSel(n);
        const idx = playOrder.indexOf(n.id);
        if (idx >= 0) setPlayStep(idx);
      });

    node
      .append("rect")
      .attr("width", NW)
      .attr("height", NH)
      .attr("rx", 8)
      .attr("fill", (n) => (n.id === sel.id ? "#eff6ff" : "#fff"))
      .attr("stroke", (n) => (n.id === sel.id ? "#2563eb" : "#cbd5e1"))
      .attr("stroke-width", (n) => (n.id === sel.id ? 2.5 : 1.5));

    node.append("rect").attr("width", 4).attr("height", NH).attr("rx", 2).attr("fill", "#2563eb");
    node
      .append("text")
      .attr("x", 12)
      .attr("y", 20)
      .attr("font-size", 11)
      .attr("font-weight", 600)
      .attr("fill", "#1e293b")
      .text((n) => n.label);
    node
      .append("text")
      .attr("x", 12)
      .attr("y", 36)
      .attr("font-size", 9)
      .attr("fill", "#94a3b8")
      .text((n) => n.sub);
  }, [sel]);

  const showTable = ["prescan", "find_pages", "extract", "filter", "select_ab", "columns", "classify"].includes(sel.id);
  const showClassify = sel.id === "classify" || sel.id === "out";
  const showJson = sel.id === "out";

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <div>
            <h2 className="font-semibold">营收解析器逻辑可视化</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              <code className="text-blue-600">src/parsers/revenue/default.py</code> · mock 示意数据（002254 风格）· 点节点看细节
            </p>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                setPlayStep(0);
                setSel(FLOW_NODES[0]);
                setPlaying(true);
              }}
              className="px-3 py-1.5 text-sm rounded bg-blue-600 text-white hover:bg-blue-700"
            >
              ▶ 逐步演示
            </button>
            <button
              type="button"
              onClick={() => {
                setPlaying(false);
                setSel(FLOW_NODES.find((n) => n.id === "classify")!);
              }}
              className="px-3 py-1.5 text-sm rounded border hover:bg-gray-50"
            >
              跳到 _classify
            </button>
          </div>
        </div>
        <div className="flex gap-3 text-[10px] text-gray-500 mb-1">
          <span className="flex items-center gap-1">
            <span className="w-3 h-0.5 bg-blue-500 inline-block" /> 主路径（pre_scan）
          </span>
          <span className="flex items-center gap-1">
            <span className="w-3 h-0.5 bg-amber-500 inline-block border-dashed" /> 回退自抽
          </span>
        </div>
        <svg ref={ref} className="w-full border rounded-lg bg-slate-50" style={{ height: 320 }} />
      </div>

      <div className="grid lg:grid-cols-2 gap-4">
        {/* 左侧：节点说明 */}
        <div className="bg-white rounded-lg shadow-sm border p-4">
          <h3 className="font-semibold text-blue-700">{sel.label}</h3>
          <div className="text-xs font-mono text-gray-500 mt-1">
            {sel.fn}() · {sel.file}
          </div>
          <p className="text-sm text-gray-700 mt-3 leading-relaxed">{sel.what}</p>
          {sel.points && sel.points.length > 0 && (
            <ul className="list-disc pl-4 mt-2 text-xs text-gray-600 space-y-1">
              {sel.points.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {sel.mock && (
            <div className="grid grid-cols-2 gap-2 mt-3">
              {sel.mock.map(([k, v]) => (
                <div key={k} className="bg-gray-50 rounded px-2 py-1.5 text-xs flex justify-between gap-2">
                  <span className="text-gray-500 shrink-0">{k}</span>
                  <span className="font-mono text-gray-800 text-right">{v}</span>
                </div>
              ))}
            </div>
          )}
          {sel.sample && (
            <div className="mt-2 text-xs font-mono bg-slate-900 text-green-300 rounded px-3 py-2">{sel.sample}</div>
          )}
        </div>

        {/* 右侧：mock 数据面板 */}
        <div className="bg-white rounded-lg shadow-sm border p-4 space-y-3">
          <h3 className="font-semibold text-sm text-gray-700">Mock 数据随步骤变化</h3>

          {showTable && (
            <div>
              <div className="text-xs text-gray-500 mb-1">
                选中表（{MOCK_COLUMN_MAP.unit_label}）· 高亮列 = 认列结果
              </div>
              <div className="overflow-x-auto">
                <table className="text-xs border-collapse w-full">
                  <thead>
                    <tr className="bg-slate-100">
                      {MOCK_RAW_TABLE[0].map((h, ci) => (
                        <th
                          key={ci}
                          className={`border px-2 py-1 text-left ${
                            ci === MOCK_COLUMN_MAP.name_col
                              ? "bg-blue-100"
                              : ci === MOCK_COLUMN_MAP.amount_col
                                ? "bg-emerald-100"
                                : ci === MOCK_COLUMN_MAP.ratio_col
                                  ? "bg-amber-100"
                                  : ""
                          }`}
                        >
                          {h}
                          {ci === MOCK_COLUMN_MAP.name_col && " ←name"}
                          {ci === MOCK_COLUMN_MAP.amount_col && " ←amount"}
                          {ci === MOCK_COLUMN_MAP.ratio_col && " ←ratio"}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {MOCK_RAW_TABLE.slice(1).map((row, ri) => (
                      <tr key={ri} className={row[0]?.startsWith("分") ? "bg-violet-50" : ""}>
                        {row.map((cell, ci) => (
                          <td
                            key={ci}
                            className={`border px-2 py-1 font-mono ${
                              ci === MOCK_COLUMN_MAP.name_col
                                ? "bg-blue-50/50"
                                : ci === MOCK_COLUMN_MAP.amount_col
                                  ? "bg-emerald-50/50"
                                  : ci === MOCK_COLUMN_MAP.ratio_col
                                    ? "bg-amber-50/50"
                                    : ""
                            }`}
                          >
                            {cell || "—"}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {sel.id === "unit" && (
                <p className="text-xs text-emerald-700 mt-2">
                  unit_ratio = {MOCK_COLUMN_MAP.unit_ratio.toLocaleString()} → 3,595,000 万元 = {yi(35950000000)}
                </p>
              )}
            </div>
          )}

          {showClassify && (
            <div>
              <div className="text-xs text-gray-500 mb-1">_classify 逐行处理（mock）</div>
              <div className="space-y-1 max-h-48 overflow-y-auto">
                {MOCK_CLASSIFY_ROWS.map((r, i) => (
                  <div
                    key={i}
                    className={`text-xs rounded px-2 py-1 flex flex-wrap gap-x-2 gap-y-0.5 ${
                      r.skipped ? "bg-gray-100 text-gray-500" : "bg-slate-50"
                    }`}
                  >
                    <span className="font-medium">{r.name}</span>
                    {r.skipped ? (
                      <span className="text-violet-600">{r.dimLabel}</span>
                    ) : (
                      <>
                        <span className="text-violet-600">{DIM_CN[r.dim] || r.dim}</span>
                        {r.revenue_yuan != null && (
                          <span className="text-emerald-700">{yi(r.revenue_yuan)}</span>
                        )}
                        {r.ratio_pct != null && <span className="text-amber-700">{r.ratio_pct}%</span>}
                      </>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {showJson && (
            <div>
              <div className="text-xs text-gray-500 mb-1">最终 revenue_breakdown（mock JSON）</div>
              <pre className="text-[10px] font-mono bg-slate-900 text-slate-100 rounded p-3 overflow-x-auto max-h-56">
                {JSON.stringify(MOCK_OUTPUT.revenue_breakdown, null, 2)}
              </pre>
              <div className="grid grid-cols-2 gap-2 mt-2 text-xs">
                {Object.entries(MOCK_OUTPUT.revenue_breakdown).map(([dim, rows]) => (
                  <div key={dim} className="bg-gray-50 rounded px-2 py-1">
                    <span className="text-gray-500">{DIM_CN[dim] || dim}</span>
                    <span className="float-right font-mono">{Array.isArray(rows) ? rows.length : 0} 项</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {sel.id === "entry" && (
            <div className="text-sm text-gray-500 leading-relaxed">
              点击流程图节点，或点「逐步演示」沿主路径浏览。蓝色路径 = 引擎传入 <code>pre_scan</code>；橙色虚线 =
              解析器自己找页抽表。
            </div>
          )}

          {(sel.id === "find_pages" || sel.id === "extract" || sel.id === "filter" || sel.id === "select_ab") && (
            <div className="text-xs text-amber-800 bg-amber-50 rounded px-3 py-2">
              回退路径：无 pre_scan 时走找页 → 抽表 → 过滤 → A/B 择优，之后与主路径汇合到 detect_unit → classify。
            </div>
          )}
        </div>
      </div>

      {/* 演示进度条 */}
      <div className="bg-white rounded-lg shadow-sm border p-3">
        <div className="flex gap-1">
          {playOrder.map((id, i) => {
            const n = FLOW_NODES.find((x) => x.id === id)!;
            const active = i <= playStep;
            return (
              <button
                key={id}
                type="button"
                onClick={() => {
                  setSel(n);
                  setPlayStep(i);
                  setPlaying(false);
                }}
                className={`flex-1 text-[10px] py-2 rounded transition ${
                  active ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-500 hover:bg-gray-200"
                }`}
              >
                {n.label}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
