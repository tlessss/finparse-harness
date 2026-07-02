"use client";

// 通用试跑台：prepare(拿 messages) → 手改 → chat(发 LLM) → 看回复/裁决 + 历史。
// 复用现有 /debug/{kind}/{prepare,chat,chats} 端点；kind = judge | verify | heal(diagnose)。
// 旧的 JudgeTest/VerifyTest/HealTest 保持不动，这里是 Agent 管理页里的内嵌轻量版。

import { useState, useEffect } from "react";
import { FIELD_LABEL, FIELDS, codeLabel } from "./consoleData";
import { apiGet, apiPost } from "./api";

type Msg = { role: string; content: string };
type Chat = { id: number; created_at: string; messages: Msg[]; reply: string };

const roleStyle = (r: string) => r === "assistant" ? "bg-purple-50 border-purple-200" : r === "system" ? "bg-gray-50 border-gray-200" : "bg-blue-50 border-blue-200";
const roleCn = (r: string) => r === "assistant" ? "🤖 LLM 回复" : r === "system" ? "⚙ system（角色设定）" : "👤 user（发给它的）";

// 从 chat 返回里挑出"裁决"字段（各 agent 形状不同：judge=all_ok/verdict/issues，verify=passed/suspects，heal=fix）
const verdictOf = (d: Record<string, unknown>) => {
  const pick: Record<string, unknown> = {};
  for (const k of ["verdict", "all_ok", "passed", "confidence", "issues", "suspects", "summary", "fix", "committed", "commit_id"]) {
    if (d[k] !== undefined && d[k] !== null) pick[k] = d[k];
  }
  return Object.keys(pick).length ? pick : null;
};

export default function AgentPlayground({ kind, hasHistory = true }: { kind: string; hasHistory?: boolean }) {
  const [code, setCode] = useState("000333");
  const [year, setYear] = useState(2025);
  const [field, setField] = useState("revenue_breakdown");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [grounding, setGrounding] = useState("");
  const [unit, setUnit] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState("");
  const [verdict, setVerdict] = useState<Record<string, unknown> | null>(null);
  const [history, setHistory] = useState<Chat[]>([]);

  const loadHistory = (c = code, f = field) => {
    if (!hasHistory) return;
    apiGet<{ chats: Chat[] } | null>(`/debug/${kind}/chats?code=${c}&field=${f}`, null).then(({ data }) => setHistory(data?.chats || []));
  };
  useEffect(() => { loadHistory(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const prepare = async () => {
    setLoading(true); setErr(""); setMessages([]); setVerdict(null);
    const { data, live } = await apiGet<{ messages?: Msg[]; grounding?: string; unit?: string; error?: string } | null>(
      `/debug/${kind}/prepare?stock_code=${code}&year=${year}&field=${field}`, null);
    setLoading(false);
    if (!live || !data) { setErr("后端无响应（确认 :8200；该报告需先解析过、有缓存）"); return; }
    if (data.error) { setErr(data.error); return; }
    setGrounding(data.grounding || "");
    setUnit(data.unit || "");
    setMessages(data.messages || []);
  };

  const send = async () => {
    setSending(true); setErr("");
    const { data, live } = await apiPost<Record<string, unknown> | null>(
      `/debug/${kind}/chat`, { code, year, field, messages }, null);
    setSending(false);
    if (!live || !data) { setErr("发送失败（后端无响应）"); return; }
    if (data.error) { setErr(String(data.error)); return; }
    setVerdict(verdictOf(data));
    setMessages((m) => [...m, { role: "assistant", content: String(data.reply || "") }, { role: "user", content: "" }]);
    loadHistory();
  };

  const editMsg = (i: number, content: string) => setMessages((m) => m.map((x, j) => j === i ? { ...x, content } : x));
  const removeMsg = (i: number) => setMessages((m) => m.filter((_, j) => j !== i));

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 flex-wrap text-sm">
        <input value={code} onChange={(e) => setCode(e.target.value.trim())} placeholder="股票代码"
          className="border rounded px-2 py-1 w-28" title={codeLabel(code)} />
        <input value={year} onChange={(e) => setYear(Number(e.target.value))} className="border rounded px-2 py-1 w-20" />
        <select value={field} onChange={(e) => { setField(e.target.value); setMessages([]); loadHistory(code, e.target.value); }} className="border rounded px-2 py-1">
          {FIELDS.map((f) => <option key={f} value={f}>{FIELD_LABEL[f] || f}</option>)}
        </select>
        <button onClick={prepare} disabled={loading || !code}
          className="px-4 py-1.5 rounded bg-blue-600 text-white disabled:opacity-40 hover:bg-blue-700">
          {loading ? "准备中…" : "🔧 准备对话"}
        </button>
      </div>
      {err && <div className="text-red-500 text-sm">{err}</div>}

      {messages.length > 0 && (
        <div className="space-y-2">
          <div className="text-xs text-gray-400">
            {grounding && <>源文依据：{grounding}</>}
            {unit && <span className="ml-1 text-gray-500">· 源文单位：<b>{unit}</b>（已告知 LLM，解析值为元）</span>}
            · 任意消息可编辑，改完点发送
          </div>
          {verdict && (
            <pre className="rounded p-2 text-xs bg-amber-50 border border-amber-200 whitespace-pre-wrap break-words">
              裁决：{JSON.stringify(verdict, null, 2)}
            </pre>
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

      {hasHistory && (
        <details className="border rounded p-2">
          <summary className="text-xs text-gray-500 cursor-pointer select-none">📜 历史对话（{history.length}）</summary>
          <div className="mt-2 space-y-1">
            {history.map((h) => (
              <details key={h.id} className="border rounded p-1.5 text-xs">
                <summary className="cursor-pointer text-gray-500">#{h.id} · {h.created_at?.slice(5, 16)} · {h.messages.length}条 · 回复 {h.reply?.length || 0}字</summary>
                <pre className="whitespace-pre-wrap break-words max-h-40 overflow-auto mt-1 text-gray-700">{h.reply}</pre>
              </details>
            ))}
            {!history.length && <div className="text-xs text-gray-400">还没有对话记录</div>}
          </div>
        </details>
      )}
    </div>
  );
}
