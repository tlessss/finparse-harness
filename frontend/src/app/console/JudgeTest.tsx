"use client";

import { useState, useEffect, useMemo } from "react";
import { FIELD_LABEL, codeLabel, loadStockNames, STOCK_NAMES } from "./consoleData";
import { apiGet, apiPost } from "./api";

const FIELDS = ["revenue_breakdown", "cost_breakdown", "rnd_info", "employees", "top_clients", "top_suppliers"];
type Msg = { role: string; content: string };
type Chat = { id: number; created_at: string; stock_code: string; field: string; messages: Msg[]; reply: string };

const roleStyle = (r: string) => r === "assistant" ? "bg-purple-50 border-purple-200" : r === "system" ? "bg-gray-50 border-gray-200" : "bg-blue-50 border-blue-200";
const roleCn = (r: string) => r === "assistant" ? "🤖 LLM 回复" : r === "system" ? "⚙ system（角色设定）" : "👤 user（发给它的）";

export default function JudgeTest({ initial }: { initial?: { code: string; year: number; field: string } } = {}) {
  const [code, setCode] = useState(initial?.code || "000333");
  const [year, setYear] = useState(initial?.year || 2025);
  const [field, setField] = useState(initial?.field || "revenue_breakdown");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);

  const [messages, setMessages] = useState<Msg[]>([]);
  const [grounding, setGrounding] = useState("");
  const [unit, setUnit] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  const [history, setHistory] = useState<Chat[]>([]);
  const [judgement, setJudgement] = useState<{ all_ok?: boolean | null; verdict?: string; confidence?: number; issues?: unknown[] } | null>(null);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    const all = Object.entries(STOCK_NAMES);
    return (q ? all.filter(([c, n]) => c.includes(q) || (n || "").toLowerCase().includes(q)) : all).slice(0, 50);
  }, [query, err]);

  const loadHistory = (c = code, f = field) =>
    apiGet<{ chats: Chat[] } | null>(`/debug/judge/chats?code=${c}&field=${f}`, null).then(({ data }) => setHistory(data?.chats || []));
  useEffect(() => { loadStockNames().then(() => setTick((t) => t + 1)); loadHistory(); }, []);

  const pick = (c: string, y: number, f: string) => { setCode(c); setYear(y); setField(f); setMessages([]); setErr(""); setJudgement(null); loadHistory(c, f); };

  const prepare = async () => {
    setLoading(true); setErr(""); setMessages([]);
    const { data, live } = await apiGet<{ messages?: Msg[]; grounding?: string; unit?: string; error?: string } | null>(
      `/debug/judge/prepare?stock_code=${code}&year=${year}&field=${field}`, null);
    setLoading(false);
    if (!live || !data) { setErr("后端无响应（确认 :8200；该报告需先解析过有缓存）"); return; }
    if (data.error) { setErr(data.error); return; }
    setGrounding(data.grounding || "");
    setUnit(data.unit || "");
    setMessages(data.messages || []);
  };

  const send = async () => {
    setSending(true); setErr("");
    const { data, live } = await apiPost<{ reply?: string; error?: string; all_ok?: boolean | null; verdict?: string; confidence?: number; issues?: unknown[] } | null>(
      `/debug/judge/chat`, { code, year, field, messages }, null);
    setSending(false);
    if (!live || !data) { setErr("发送失败（后端无响应）"); return; }
    if (data.error) { setErr(data.error); return; }
    setJudgement({ all_ok: data.all_ok, verdict: data.verdict, confidence: data.confidence, issues: data.issues });
    setMessages((m) => [...m, { role: "assistant", content: data.reply || "" }, { role: "user", content: "" }]);
    loadHistory();
  };

  const editMsg = (i: number, content: string) => setMessages((m) => m.map((x, j) => j === i ? { ...x, content } : x));
  const removeMsg = (i: number) => setMessages((m) => m.filter((_, j) => j !== i));

  return (
    <div className="space-y-4">
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h2 className="font-semibold mb-1">LLM 判定 · 对话调试台
          <span className="text-xs font-normal text-gray-400 ml-2">— 选公司 → 准备对话 → 改 prompt → 发送 → 全程记录</span>
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
                  <div key={c} onMouseDown={() => { pick(c, year, field); setOpen(false); }}
                    className="px-2 py-1.5 hover:bg-blue-50 cursor-pointer">{n} <span className="text-gray-400 text-xs">({c})</span></div>
                ))}
              </div>
            )}
          </div>
          <input value={year} onChange={(e) => setYear(Number(e.target.value))} className="border rounded px-2 py-1 w-20" />
          <select value={field} onChange={(e) => { setField(e.target.value); setMessages([]); loadHistory(code, e.target.value); }} className="border rounded px-2 py-1">
            {FIELDS.map((f) => <option key={f} value={f}>{FIELD_LABEL[f] || f}</option>)}
          </select>
          <button onClick={prepare} disabled={loading || !code}
            className="px-4 py-1.5 rounded bg-blue-600 text-white disabled:opacity-40 hover:bg-blue-700">
            {loading ? "准备中…" : "🔧 准备对话"}
          </button>
        </div>
        {err && <div className="text-red-500 text-sm mt-2">{err}</div>}
      </div>

      {messages.length > 0 && (
        <div className="bg-white rounded-lg shadow-sm border p-4 space-y-3">
          <div className="text-xs text-gray-400">源文依据：{grounding}{unit && <span className="ml-1 text-gray-500">· 源文单位：<b>{unit}</b>（已告知 LLM，解析值为元）</span>} · 任意消息可编辑，改完点发送（改 prompt 就能纠偏 LLM）</div>

          {judgement && (
            <div className={`rounded p-2 text-sm font-medium border ${judgement.all_ok ? "bg-green-50 text-green-700 border-green-300" : judgement.all_ok === false ? "bg-red-100 text-red-700 border-red-200" : "bg-gray-100 text-gray-600 border-gray-200"}`}>
              {judgement.all_ok ? "✅ LLM 判定：完全正确（verdict=ok 且无 issue）"
                : judgement.all_ok === false ? `⚠ LLM 判定：有问题（verdict=${judgement.verdict || "?"}，${judgement.issues?.length || 0} 处 issue）`
                  : "? 无法从回复解析出判定（回复非标准 JSON）"}
              {judgement.confidence != null && <span className="ml-2 text-xs font-normal">置信 {(judgement.confidence * 100).toFixed(0)}%</span>}
              {judgement.all_ok && <span className="ml-2 text-xs font-normal">→ 已送「⑤ 入库审核」队列，等人通过后入库</span>}
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`rounded border p-2 ${roleStyle(m.role)}`}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-medium text-gray-600">{roleCn(m.role)}</span>
                {m.role !== "assistant" && <button onClick={() => removeMsg(i)} className="text-xs text-gray-400 hover:text-red-500">删除</button>}
              </div>
              {m.role === "assistant"
                ? <pre className="text-xs whitespace-pre-wrap break-words text-gray-800">{m.content}</pre>
                : <textarea value={m.content} onChange={(e) => editMsg(i, e.target.value)}
                    className="w-full text-xs border rounded p-1.5 font-mono bg-white"
                    rows={Math.min(22, m.content.split("\n").length + 1)} />}
            </div>
          ))}
          <div className="flex gap-2">
            <button onClick={send} disabled={sending}
              className="px-4 py-1.5 rounded bg-purple-600 text-white disabled:opacity-40 hover:bg-purple-700">
              {sending ? "发送中…(~15s)" : "🚀 发送给 LLM"}
            </button>
            <button onClick={() => setMessages((m) => [...m, { role: "user", content: "" }])}
              className="px-3 py-1.5 rounded bg-gray-100 text-gray-600 hover:bg-gray-200 text-sm">+ 加一条 user 消息</button>
          </div>
        </div>
      )}

      <details className="bg-white rounded-lg shadow-sm border p-3">
        <summary className="text-sm text-gray-600 cursor-pointer select-none">📜 历史对话（{history.length}）— 这家公司这个字段记录下来的所有对话</summary>
        <div className="mt-2 space-y-2">
          {history.map((h) => (
            <details key={h.id} className="border rounded p-2 text-xs">
              <summary className="cursor-pointer text-gray-500">#{h.id} · {h.created_at?.slice(5, 16)} · {h.messages.length}条消息 · 回复 {h.reply?.length || 0}字</summary>
              <div className="mt-1 space-y-1">
                {h.messages.map((m, j) => (
                  <div key={j} className={`rounded p-1.5 ${roleStyle(m.role)}`}>
                    <div className="text-gray-500 mb-0.5">{roleCn(m.role)}</div>
                    <pre className="whitespace-pre-wrap break-words max-h-40 overflow-auto">{m.content}</pre>
                  </div>
                ))}
                <div className="rounded p-1.5 bg-purple-50 border border-purple-200">
                  <div className="text-gray-500 mb-0.5">🤖 LLM 回复</div>
                  <pre className="whitespace-pre-wrap break-words max-h-40 overflow-auto">{h.reply}</pre>
                </div>
              </div>
            </details>
          ))}
          {!history.length && <div className="text-xs text-gray-400 px-1">还没有对话记录（准备对话 → 发送一次就有了）</div>}
        </div>
      </details>
    </div>
  );
}
