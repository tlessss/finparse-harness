"use client";

// 逐份失败分析(实时读 DB /pipeline/failures)：每份非 committed 的结局/类别/占锚/LLM是否跑成 + 类别汇总 + 自愈路线。
import { Fragment, useEffect, useState } from "react";
import Link from "next/link";
import { apiGet } from "./api";
import { codeLabel, loadStockNames } from "./consoleData";

const chainHref = (code: string) => `/console/pipeline?code=${code}`;

type Item = { code: string; outcome: string; llm_ran: boolean; best: number; ndim: number; cat: string; reason: string };
type Cat = { cat: string; n: number; why: string; heal: string; tag: "have" | "light" | "new" | "out" };
type Data = {
  total: number; committed: number; success_rate: number | null; denom: number;
  n_fail: number; llm_missed: number; tally: Record<string, number>; cats: Cat[]; items: Item[];
};

const OUT: Record<string, [string, string]> = {
  verify_hold: ["bg-amber-50 text-amber-700 border-amber-200", "复核否决"],
  non_green: ["bg-orange-50 text-orange-700 border-orange-200", "不过锚"],
  no_such_table: ["bg-slate-100 text-slate-600 border-slate-200", "真无表"],
  green: ["bg-emerald-50 text-emerald-700 border-emerald-200", "过锚·未复核"],
  no_input: ["bg-slate-100 text-slate-500 border-slate-200", "无输入"],
};
const TAG: Record<string, [string, string]> = {
  have: ["bg-emerald-50 text-emerald-700", "已有"],
  light: ["bg-blue-50 text-blue-700", "轻量补"],
  new: ["bg-violet-50 text-violet-700", "要新建"],
  out: ["bg-slate-100 text-slate-500", "域外"],
};
const BAR: Record<string, string> = { have: "bg-emerald-500", light: "bg-blue-500", new: "bg-violet-500", out: "bg-slate-400" };

const ROADMAP: { n: string; t: string; d: string; u: string; build?: boolean }[] = [
  { n: "1", t: "LLM 节流 + 失败重试", d: "跑批给 LLM 调用加限速与重试,让自愈链路真的跑完。根治本次 LLM 没跑成的报告。", u: "↑ 解锁「全空 / 过计」里大量本能自愈的报告" },
  { n: "2", t: "单维过锚 → 交复核放行", d: "有一维精确=锚(占锚 1.00)却被「全维±3%」硬毙的,改成「≥1 维干净过锚 → 交复核逐项核」。", u: "↑ 救回「单维不齐」里的假失败(比亚迪/爱尔眼科等)" },
  { n: "3", t: "选表自愈多轮 + L2 扩单位/名称", d: "选表自愈从单轮改成扫 top-N;L2 改规则 prompt 补「单位 override / 名称别名」。", u: "↑ 覆盖「选错表 / 取错列 / 抠错名」" },
  { n: "4", t: "金融股单独归类 + 取锚自愈", d: "无锚的多是银行/券商——营收构成不适用,移出「营收失败」;其余从利润表抽营业收入当锚。", u: "↑ 把「无锚」从失败里剔除(域外)" },
  { n: "5", t: "抽表自愈(L3 重抽)", d: "只对真漏行——pdfplumber 没抽全的页——换参数重抽 / 渲染页面 LLM 抽行,锚闸兜底。", u: "↑ 啃剩下少数真硬骨头(严重缺 / 中度缺)", build: true },
  { n: "6", t: "真无表:已达成", d: "报告确无营收构成表(江铃)→ 选表 agent 判「无」,正确交人工。数据不存在,自愈=确认缺失。", u: "✓ 无需改动" },
];

export default function FailuresPanel() {
  const [d, setD] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  const [tick, setTick] = useState(0);
  const load = () => apiGet<Data | null>("/pipeline/failures", null).then(({ data, live }) => {
    if (!live || !data) { setErr("后端无响应(确认 :8200)"); return; }
    setErr(""); setD(data);
  });
  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); }, []);
  useEffect(() => { load(); }, []);

  const nameOf = (code: string) => { const l = codeLabel(code); return l === code ? "" : l.replace(`(${code})`, ""); };
  const maxN = d?.cats?.[0]?.n || 1;
  const grouped: [string, Item[]][] = [];
  (d?.items || []).forEach((it) => {
    const g = grouped.find((x) => x[0] === it.cat);
    if (g) g[1].push(it); else grouped.push([it.cat, [it]]);
  });

  return (
    <div className="space-y-4" data-tick={tick}>
      {/* 标题 + 概览 */}
      <div className="bg-white rounded-lg shadow-sm border p-5">
        <div className="flex items-baseline gap-3 flex-wrap">
          <h2 className="text-lg font-bold tracking-tight">营收解析失败分析</h2>
          <span className="text-xs text-gray-400">实时读 DB · 每份非入库报告卡在哪、属于哪类、能不能自愈</span>
          <button onClick={load} className="ml-auto px-3 py-1 rounded bg-blue-600 text-white text-xs hover:bg-blue-700">刷新</button>
        </div>
        {err && <div className="text-red-500 text-sm mt-2">{err}</div>}
        {d && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mt-4">
              <Stat k="自主入库" v={d.committed} sub="committed" cls="text-emerald-600" />
              <Stat k="成功率" v={d.success_rate == null ? "—" : `${(d.success_rate * 100).toFixed(1)}%`} sub={`${d.committed} / ${d.denom} 可评判`} hl />
              <Stat k="复核否决" v={d.tally.verify_hold || 0} sub="verify_hold" cls="text-amber-600" />
              <Stat k="不过锚" v={d.tally.non_green || 0} sub="non_green" cls="text-orange-600" />
              <Stat k="失败明细" v={d.n_fail} sub="非 committed" cls="text-gray-700" />
            </div>
            {d.llm_missed > 0 && (
              <div className="flex gap-3 mt-4 bg-red-50 border border-red-200 border-l-4 border-l-red-500 rounded-lg p-3.5">
                <span className="text-red-600 font-extrabold text-lg leading-tight">!</span>
                <div>
                  <div className="text-sm font-semibold text-red-800">{d.n_fail} 份里有 {d.llm_missed} 份的 LLM(复核/自愈/诊断)跑批时出错,没跑成</div>
                  <p className="text-xs text-red-700/90 mt-1">密集调用被限流/超时(瞬时错误,重跑就正常)。所以成功率被<b>低估</b>、分类含噪声——「全空/过计」里大量是<b>本能自愈、这次没轮到</b>。优先补跑(节流+重试),再对着真正剩下的下手。</p>
                </div>
              </div>
            )}
          </>
        )}
      </div>

      {/* 类别分布 */}
      {d && (
        <div className="bg-white rounded-lg shadow-sm border p-5">
          <h3 className="font-semibold">{d.cats.length} 类失败 · 各自的自愈落点</h3>
          <p className="text-xs text-gray-400 mt-0.5 mb-3">
            <b className="text-emerald-600">已有</b>=现有能力可治(多因 LLM 没跑成才卡) · <b className="text-blue-600">轻量补</b>=改闸门/prompt/多轮 · <b className="text-violet-600">要新建</b>=抽表自愈 · <b className="text-slate-500">域外</b>=非解析问题
          </p>
          <div className="flex flex-col gap-2">
            {d.cats.map((c) => {
              const members = d.items.filter((i) => i.cat === c.cat);
              return (
                <div key={c.cat} className="border rounded-lg px-3.5 py-2.5">
                  <div className="grid grid-cols-1 md:grid-cols-[150px_1fr_320px] gap-3 md:items-center">
                    <div className="text-sm font-semibold">{c.cat}
                      <span className="block font-normal text-[11.5px] text-gray-500 mt-0.5">{c.why}</span>
                    </div>
                    <div className="flex items-center gap-2.5">
                      <div className={`h-2 rounded ${BAR[c.tag]}`} style={{ width: `${Math.max(5, (c.n / maxN) * 100)}%` }} />
                      <span className="font-bold text-[13px] tabular-nums w-5">{c.n}</span>
                    </div>
                    <div className="flex items-center gap-2 text-xs text-gray-600">
                      <span className={`px-2 py-0.5 rounded-full font-semibold whitespace-nowrap ${TAG[c.tag][0]}`}>{TAG[c.tag][1]}</span>
                      <span>{c.heal}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap gap-1.5 mt-2.5">
                    {members.map((m) => (
                      <Link key={m.code} href={chainHref(m.code)} target="_blank" rel="noopener"
                        title={`新页面看解析链路 · ${m.outcome}${m.best ? ` · 占锚${m.best.toFixed(2)}` : ""}${m.llm_ran ? "" : " · LLM没跑成"}`}
                        className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded border transition hover:border-blue-400 hover:bg-blue-50 hover:shadow-sm ${m.llm_ran ? "bg-gray-50 border-gray-200" : "bg-red-50 border-red-200"}`}>
                        <span className="font-mono text-gray-400 tabular-nums">{m.code}</span>
                        <span className="text-gray-700">{nameOf(m.code) || "—"}</span>
                        {!m.llm_ran && <span className="text-red-500 font-bold">⚠</span>}
                      </Link>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 逐份明细 */}
      {d && (
        <div className="bg-white rounded-lg shadow-sm border p-5">
          <h3 className="font-semibold">逐份明细 · {d.n_fail} 份</h3>
          <p className="text-xs text-gray-400 mt-0.5 mb-3">
            <span className="text-red-600 font-semibold">红色 N✗</span>=没跑成复核/自愈,结果不完全可信 · <span className="font-mono">占锚</span>=最大维度合计÷营收锚(1.00=某维精确对上却被「全维±3%」卡)
          </p>
          <div className="overflow-x-auto border rounded-lg">
            <table className="w-full text-[13px] min-w-[720px]">
              <thead>
                <tr className="bg-gray-50 text-[11px] uppercase tracking-wide text-gray-500">
                  <th className="text-left font-semibold px-3 py-2">代码</th>
                  <th className="text-left font-semibold px-3 py-2">公司</th>
                  <th className="text-left font-semibold px-3 py-2">结局</th>
                  <th className="text-left font-semibold px-3 py-2">占锚</th>
                  <th className="text-left font-semibold px-3 py-2">维</th>
                  <th className="text-left font-semibold px-3 py-2">失败原因</th>
                  <th className="text-left font-semibold px-3 py-2">LLM</th>
                </tr>
              </thead>
              <tbody>
                {grouped.map(([cat, rows]) => (
                  <Fragment key={cat}>
                    <tr><td colSpan={7} className="bg-gray-50/70 font-bold text-xs px-3 py-1.5 border-t">{cat} <span className="text-gray-400 font-semibold">· {rows.length} 份</span></td></tr>
                    {rows.map((it) => {
                      const [ocls, olab] = OUT[it.outcome] || ["bg-slate-100 text-slate-500 border-slate-200", it.outcome];
                      return (
                        <tr key={it.code} className="border-t border-gray-100 hover:bg-blue-50/40">
                          <td className="px-3 py-1.5 font-mono font-semibold tabular-nums">
                            <Link href={chainHref(it.code)} target="_blank" rel="noopener" className="text-blue-700 hover:underline">{it.code}</Link>
                          </td>
                          <td className="px-3 py-1.5">
                            <Link href={chainHref(it.code)} target="_blank" rel="noopener" className="hover:text-blue-700 hover:underline">{nameOf(it.code) || "—"}</Link>
                          </td>
                          <td className="px-3 py-1.5"><span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded-full border ${ocls}`}>{olab}</span></td>
                          <td className="px-3 py-1.5 font-mono text-gray-500 tabular-nums">{it.best ? it.best.toFixed(2) : "—"}</td>
                          <td className="px-3 py-1.5 font-mono text-gray-500 tabular-nums">{it.ndim || "—"}</td>
                          <td className="px-3 py-1.5 text-gray-700">{it.reason || "—"}</td>
                          <td className={`px-3 py-1.5 font-mono font-bold ${it.llm_ran ? "text-gray-300" : "text-red-600"}`}>{it.llm_ran ? "Y" : "N ✗"}</td>
                        </tr>
                      );
                    })}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 自愈路线 */}
      <div className="bg-white rounded-lg shadow-sm border p-5">
        <h3 className="font-semibold">让「全部可自愈」的修复路线</h3>
        <p className="text-xs text-gray-400 mt-0.5 mb-3">真正需要「新建能力」的只有少数;其余靠补跑 + 两处轻量改动 + 金融股单独归类即可覆盖。</p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {ROADMAP.map((s) => (
            <div key={s.n} className="border rounded-xl p-4">
              <span className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-white text-xs font-bold ${s.build ? "bg-violet-600" : "bg-blue-600"}`}>{s.n}</span>
              <h4 className="font-semibold text-[15px] mt-2.5 mb-1">{s.t}</h4>
              <p className="text-[13px] text-gray-500">{s.d}</p>
              <div className={`text-xs font-semibold mt-2.5 ${s.build ? "text-violet-600" : "text-emerald-600"}`}>{s.u}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Stat({ k, v, sub, cls, hl }: { k: string; v: React.ReactNode; sub: string; cls?: string; hl?: boolean }) {
  return (
    <div className={`border rounded-lg px-4 py-3 ${hl ? "border-amber-300 bg-amber-50/40" : ""}`}>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{k}</div>
      <div className={`text-2xl font-bold tracking-tight mt-1 leading-none ${cls || ""}`}>{v}</div>
      <div className="text-[11px] text-gray-500 mt-1.5">{sub}</div>
    </div>
  );
}
