"use client";

import { useState, useEffect, useMemo } from "react";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet, apiPost } from "./api";

type Msg = { role: string; content: string };
type ApplyResult = {
  ok?: boolean; fixed?: boolean; message?: string; apply?: { message?: string };
  before?: { verdict?: string; need_heal?: boolean; dims?: { dim: string; sum: number }[] };
  after?: { verdict?: string; need_heal?: boolean; dims?: { dim: string; sum: number }[] };
};
const roleStyle = (r: string) => r === "assistant" ? "bg-purple-50 border-purple-200" : r === "system" ? "bg-gray-50 border-gray-200" : "bg-blue-50 border-blue-200";
const roleCn = (r: string) => r === "assistant" ? "🤖 AI 的根因+修复建议" : r === "system" ? "⚙ system" : "👤 调试包（病历+原表+配置+代码，可编辑）";

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"];
const DIM_CN: Record<string, string> = { industries: "分行业", segments: "分产品", regions: "分地区", by_channel: "分销售模式", 明细: "明细" };
const yi = (n: number) => (typeof n === "number" && Math.abs(n) >= 1e8 ? (n / 1e8).toFixed(2) + "亿" : String(n));

type Dim = { dim: string; n: number; sum: number; match: boolean };
type Resp = {
  code?: string; field?: string; anchor?: number | null; dims?: Dim[];
  verdict?: string; reason?: string; need_heal?: boolean; fix_hint?: string | null;
  any_match?: boolean; dims_agree?: boolean | null; error?: string;
};

// 判定 → 颜色
const verdictStyle = (v?: string, need?: boolean) =>
  need ? "bg-red-100 text-red-700 border-red-300"
    : v?.includes("口径") ? "bg-amber-100 text-amber-700 border-amber-300"
      : v?.includes("无锚") ? "bg-gray-100 text-gray-600 border-gray-300"
        : "bg-green-100 text-green-700 border-green-300";

export default function HealTest({ initial }: { initial?: { code: string; year: number; field: string } } = {}) {
  const [code, setCode] = useState(initial?.code || "601127");
  const [year, setYear] = useState(initial?.year || 2025);
  const [field, setField] = useState(initial?.field || "revenue_breakdown");
  const [resp, setResp] = useState<Resp | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [preparing, setPreparing] = useState(false);
  const [sending, setSending] = useState(false);
  const [chatErr, setChatErr] = useState("");
  const [fix, setFix] = useState<{ tool?: string; text?: string; dim?: string } | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);

  const applyFix = async () => {
    setApplying(true);
    const { data } = await apiPost<ApplyResult | null>(`/tool/apply_fix`, { code, year, field, fix }, null);
    setApplying(false);
    setApplyResult(data || { ok: false, message: "无响应" });
    run();   // 刷新上面的诊断
  };

  const prepare = async () => {
    setPreparing(true); setChatErr(""); setMessages([]);
    const { data, live } = await apiGet<{ messages?: Msg[]; error?: string } | null>(
      `/debug/heal/prepare?stock_code=${code}&year=${year}&field=${field}`, null);
    setPreparing(false);
    if (!live || !data) { setChatErr("后端无响应"); return; }
    if (data.error) { setChatErr(data.error); return; }
    setMessages(data.messages || []);
  };
  const send = async () => {
    setSending(true); setChatErr("");
    const { data, live } = await apiPost<{ reply?: string; error?: string } | null>(
      `/debug/heal/chat`, { code, year, field, messages }, null);
    setSending(false);
    if (!live || !data) { setChatErr("发送失败"); return; }
    if (data.error) { setChatErr(data.error); return; }
    setMessages((m) => [...m, { role: "assistant", content: data.reply || "" }, { role: "user", content: "" }]);
    setFix((data as { fix?: { tool?: string; text?: string; dim?: string } }).fix || null);
    setApplyResult(null);
  };
  const editMsg = (i: number, content: string) => setMessages((m) => m.map((x, j) => j === i ? { ...x, content } : x));

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = Object.entries(STOCK_NAMES);
    return (q ? all.filter(([c, n]) => c.includes(q) || (n || "").toLowerCase().includes(q)) : all).slice(0, 50);
  }, [query, resp]);

  const run = async (c = code, y = year, f = field) => {
    setLoading(true); setResp(null); setMessages([]); setChatErr(""); setFix(null); setApplyResult(null);
    await loadStockNames(); setTick((t) => t + 1);
    const { data, live } = await apiGet<Resp | null>(`/debug/heal?stock_code=${c}&year=${y}&field=${f}`, null);
    setLoading(false);
    setResp(live && data ? data : { error: "后端无响应（确认 :8200；该报告需先解析过有缓存）" });
  };
  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); }, []);

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">自愈测试台
          <span className="text-xs font-normal text-gray-400 ml-2">— 真失败筛子：先判要不要自愈（锚/维度一致当裁判，别修没坏的），要修才出病历</span>
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
            {loading ? "诊断中…" : "▶ 诊断要不要自愈"}
          </button>
        </div>
      </div>

      {resp?.error && <div className="bg-white rounded-lg border p-6 text-center text-red-400">{resp.error}</div>}

      {resp && !resp.error && (
        <>
          <div className="bg-white rounded-lg shadow-sm border p-4 space-y-2">
            <div className="flex items-center gap-3 flex-wrap text-sm">
              <span>{codeLabel(resp.code || code)} · {FIELD_LABEL[resp.field || field]}</span>
              <span className={`px-3 py-1 rounded text-sm font-medium border ${verdictStyle(resp.verdict, resp.need_heal)}`}>
                {resp.need_heal ? "🔧 需自愈" : "✓ "}{resp.verdict}
              </span>
              <span className="text-xs text-gray-500">锚 {resp.anchor != null ? yi(resp.anchor) : "无"}</span>
            </div>
            <p className="text-sm text-gray-700">{resp.reason}</p>
            {resp.fix_hint && <p className="text-sm text-red-600">修复方向：{resp.fix_hint}</p>}
            <div className="text-xs text-gray-400">
              裁判依据：任一维度过锚={String(resp.any_match)} · 各维度互相一致={resp.dims_agree === null ? "—" : String(resp.dims_agree)}
            </div>
          </div>

          <div className="bg-white rounded-lg shadow-sm border p-4">
            <h3 className="text-sm font-medium mb-2">证据：各维度分项和 vs 锚</h3>
            <table className="w-full text-sm">
              <thead className="text-gray-400 text-left text-xs"><tr><th className="py-1">维度</th><th>条数</th><th>分项和</th><th>对锚</th></tr></thead>
              <tbody>
                {(resp.dims || []).map((d, i) => (
                  <tr key={i} className="border-t">
                    <td className="py-1.5">{DIM_CN[d.dim] || d.dim}</td>
                    <td>{d.n}</td>
                    <td className="tabular-nums">{yi(d.sum)}</td>
                    <td>{d.match ? <span className="text-green-600 text-xs">✓ 过锚</span> : <span className="text-orange-500 text-xs">✗</span>}</td>
                  </tr>
                ))}
                {!(resp.dims || []).length && <tr><td colSpan={4} className="py-6 text-center text-gray-400">解析为空</td></tr>}
              </tbody>
            </table>
            <div className="text-xs text-gray-400 mt-2">规则：任一维度过锚 → 无需自愈；都不过但各维度互相一致 → 多是口径差(非bug)；维度互相矛盾 → 真bug，进自愈。</div>
          </div>

          {/* 自愈对话：给 AI 看代码+配置找根因 */}
          <div className="bg-white rounded-lg shadow-sm border p-4 space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-gray-700">让 AI 看代码+配置找根因</span>
              <button onClick={prepare} disabled={preparing}
                className="px-3 py-1.5 rounded bg-indigo-600 text-white text-sm disabled:opacity-40 hover:bg-indigo-700">
                {preparing ? "组装调试包…" : "🔧 准备调试包（病历+原表+配置+代码）"}
              </button>
              <span className="text-xs text-gray-400">不管要不要自愈都能问；改完 prompt 再发</span>
            </div>
            {chatErr && <div className="text-red-500 text-sm">{chatErr}</div>}
            {messages.map((m, i) => (
              <div key={i} className={`rounded border p-2 ${roleStyle(m.role)}`}>
                <div className="text-xs font-medium text-gray-600 mb-1">{roleCn(m.role)}</div>
                {m.role === "assistant"
                  ? <pre className="text-xs whitespace-pre-wrap break-words text-gray-800">{m.content}</pre>
                  : <textarea value={m.content} onChange={(e) => editMsg(i, e.target.value)}
                      className="w-full text-xs border rounded p-1.5 font-mono bg-white"
                      rows={Math.min(24, m.content.split("\n").length + 1)} />}
              </div>
            ))}
            {messages.length > 0 && (
              <button onClick={send} disabled={sending}
                className="px-4 py-1.5 rounded bg-purple-600 text-white disabled:opacity-40 hover:bg-purple-700">
                {sending ? "AI 分析中…(~20s)" : "🚀 发送给 AI，要根因+最小修复"}
              </button>
            )}

            {/* AI 给的结构化修复 → 一键应用 + 回链重测 */}
            {fix && fix.tool === "add_section_marker" && (
              <div className="rounded border border-indigo-300 bg-indigo-50 p-3 space-y-2">
                <div className="text-sm">
                  <span className="font-medium">AI 给的可执行修复：</span>
                  <code className="px-1.5 py-0.5 rounded bg-white border text-xs">add_section_marker(&quot;{fix.text}&quot; → {fix.dim})</code>
                </div>
                <button onClick={applyFix} disabled={applying}
                  className="px-4 py-1.5 rounded bg-green-600 text-white text-sm disabled:opacity-40 hover:bg-green-700">
                  {applying ? "应用 + 重测中…" : "🔧 一键应用并重测"}
                </button>
                {applyResult && (
                  <div className="text-sm space-y-1">
                    <div className="text-xs text-gray-500">{applyResult.apply?.message || applyResult.message}</div>
                    <div className={`px-3 py-1.5 rounded font-medium ${applyResult.fixed ? "bg-green-100 text-green-700" : "bg-orange-100 text-orange-600"}`}>
                      {applyResult.fixed ? "✅ 修好了！回链重测：need_heal 由 真 → 假" : "⚠ 应用了，但锚没回到正常（可能修不对、或本就不该修）"}
                    </div>
                    <div className="text-xs text-gray-600">
                      修前：{applyResult.before?.verdict}（{(applyResult.before?.dims || []).map((d) => `${d.dim} ${(d.sum / 1e8).toFixed(0)}亿`).join(" · ")}）<br />
                      修后：{applyResult.after?.verdict}（{(applyResult.after?.dims || []).map((d) => `${d.dim} ${(d.sum / 1e8).toFixed(0)}亿`).join(" · ")}）
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
