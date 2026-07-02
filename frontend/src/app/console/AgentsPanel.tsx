"use client";

// Agent 管理：左列出全部 agent，右编辑 prompt / 选模型 / 试跑。
// 后端：GET /agents、GET|POST /agents/{id}、GET|POST /agents/routing。

import { useEffect, useState } from "react";
import { apiGet, apiPost } from "./api";
import AgentPlayground from "./AgentPlayground";

type AgentItem = {
  id: string; version: string; role: string; model: string;
  has_template: boolean; has_playground: boolean; playground?: string; note?: string;
};
type Detail = {
  id: string; version?: string; role: string; model: string; system?: string; user?: string;
  output_schema?: unknown; has_template: boolean; has_playground?: boolean; playground?: string;
  note?: string; error?: string;
};
type Routing = { models: Record<string, string>; available: string[]; default: string };

const ROLE_CN: Record<string, string> = { judge: "裁判/复核", extract: "抽取", codegen: "写代码" };

export default function AgentsPanel() {
  const [agents, setAgents] = useState<AgentItem[]>([]);
  const [routing, setRouting] = useState<Routing | null>(null);
  const [sel, setSel] = useState<string>("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [sys, setSys] = useState("");
  const [usr, setUsr] = useState("");
  const [ver, setVer] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"prompt" | "model" | "run">("prompt");

  const loadAgents = () => apiGet<{ agents: AgentItem[] } | null>("/agents", null).then(({ data }) => setAgents(data?.agents || []));

  useEffect(() => {
    loadAgents();
    apiGet<Routing | null>("/agents/routing", null).then(({ data }) => setRouting(data));
  }, []);

  const select = async (id: string) => {
    setSel(id); setMsg(""); setErr(""); setDetail(null);
    const { data, live } = await apiGet<Detail | null>(`/agents/${id}`, null);
    if (!live || !data) { setErr("后端无响应（确认 :8200）"); return; }
    setDetail(data);
    setSys(data.system || ""); setUsr(data.user || ""); setVer(data.version || "");
    setTab(data.has_template ? "prompt" : "model");
  };

  const savePrompt = async () => {
    if (!detail) return;
    setSaving(true); setMsg(""); setErr("");
    const { data, live } = await apiPost<{ ok?: boolean; error?: string; version?: string } | null>(
      `/agents/${detail.id}`, { system: sys, user: usr, version: ver }, null);
    setSaving(false);
    if (!live || !data) { setErr("保存失败（后端无响应）"); return; }
    if (data.error) { setErr(data.error); return; }
    setMsg(`已保存（版本 ${data.version}）`);
    loadAgents();
  };

  const setModel = async (model: string) => {
    if (!detail) return;
    const { data, live } = await apiPost<{ models?: Record<string, string>; error?: string } | null>(
      "/agents/routing", { agent_id: detail.id, model }, null);
    if (!live || !data || data.error) { setErr(data?.error || "改模型失败"); return; }
    setDetail({ ...detail, model: model || (routing?.default || "") });
    if (data.models && routing) setRouting({ ...routing, models: data.models });
    loadAgents();
    setMsg("模型已更新");
  };

  const dirty = detail?.has_template && (sys !== (detail.system || "") || usr !== (detail.user || "") || ver !== (detail.version || ""));

  return (
    <div className="flex gap-4">
      {/* 左：agent 列表 */}
      <div className="w-56 shrink-0 bg-white rounded-lg shadow-sm border p-2 h-fit">
        <div className="text-xs text-gray-400 px-2 py-1">全部 Agent（{agents.length}）</div>
        {agents.map((a) => (
          <button key={a.id} onClick={() => select(a.id)}
            className={`w-full text-left px-2 py-1.5 rounded mb-0.5 ${sel === a.id ? "bg-blue-600 text-white" : "hover:bg-gray-100"}`}>
            <div className="flex items-center justify-between">
              <span className="font-medium text-sm">{a.id}</span>
              <span className={`text-[10px] px-1 rounded ${sel === a.id ? "bg-blue-500" : "bg-gray-100 text-gray-500"}`}>{a.version}</span>
            </div>
            <div className={`text-[10px] ${sel === a.id ? "text-blue-100" : "text-gray-400"}`}>
              {ROLE_CN[a.role] || a.role} · {a.model}{!a.has_template && " · 内联"}
            </div>
          </button>
        ))}
      </div>

      {/* 右：详情 */}
      <div className="flex-1 min-w-0 space-y-3">
        {!detail && <div className="bg-white rounded-lg shadow-sm border p-6 text-sm text-gray-400">← 选一个 agent</div>}
        {err && <div className="text-red-500 text-sm">{err}</div>}

        {detail && (
          <>
            <div className="bg-white rounded-lg shadow-sm border p-3 flex items-center gap-3">
              <h2 className="font-semibold">{detail.id}</h2>
              <span className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-500">{ROLE_CN[detail.role] || detail.role}</span>
              <span className="text-xs px-2 py-0.5 rounded bg-indigo-50 text-indigo-600 border border-indigo-200">模型 {detail.model}</span>
              <div className="ml-auto flex gap-1 text-sm">
                {(["prompt", "model", "run"] as const).map((t) => {
                  const disabled = (t === "prompt" && !detail.has_template) || (t === "run" && !detail.has_playground);
                  return (
                    <button key={t} disabled={disabled} onClick={() => setTab(t)}
                      className={`px-3 py-1 rounded disabled:opacity-30 ${tab === t ? "bg-blue-600 text-white" : "hover:bg-gray-100 text-gray-600"}`}>
                      {t === "prompt" ? "Prompt" : t === "model" ? "模型" : "试跑"}
                    </button>
                  );
                })}
              </div>
            </div>

            {/* Prompt 编辑 */}
            {tab === "prompt" && (
              <div className="bg-white rounded-lg shadow-sm border p-4 space-y-3">
                {!detail.has_template ? (
                  <div className="text-sm text-gray-500">{detail.note}</div>
                ) : (
                  <>
                    <div>
                      <div className="text-xs text-gray-500 mb-1">system（角色设定）</div>
                      <textarea value={sys} onChange={(e) => setSys(e.target.value)}
                        className="w-full text-xs border rounded p-2 font-mono" rows={Math.min(14, sys.split("\n").length + 1)} />
                    </div>
                    <div>
                      <div className="text-xs text-gray-500 mb-1">user（模板，{"{{变量}}"} 运行时替换）</div>
                      <textarea value={usr} onChange={(e) => setUsr(e.target.value)}
                        className="w-full text-xs border rounded p-2 font-mono" rows={Math.min(28, usr.split("\n").length + 1)} />
                    </div>
                    {detail.output_schema != null && (
                      <details className="text-xs">
                        <summary className="text-gray-500 cursor-pointer">output_schema（只读）</summary>
                        <pre className="mt-1 bg-gray-50 border rounded p-2 whitespace-pre-wrap">{JSON.stringify(detail.output_schema, null, 2)}</pre>
                      </details>
                    )}
                    <div className="flex items-center gap-2">
                      <label className="text-xs text-gray-500">版本</label>
                      <input value={ver} onChange={(e) => setVer(e.target.value)} className="border rounded px-2 py-1 w-24 text-sm" />
                      <button onClick={savePrompt} disabled={saving || !dirty}
                        className="px-4 py-1.5 rounded bg-green-600 text-white disabled:opacity-40 hover:bg-green-700 text-sm">
                        {saving ? "保存中…" : dirty ? "💾 保存到 YAML" : "无改动"}
                      </button>
                      {msg && <span className="text-green-600 text-xs">{msg}</span>}
                    </div>
                    <div className="text-[11px] text-gray-400">保存即写回 src/prompts/templates/{detail.id}.yaml 并热生效（写前做 system/user 往返自校验）。</div>
                  </>
                )}
              </div>
            )}

            {/* 模型 */}
            {tab === "model" && (
              <div className="bg-white rounded-lg shadow-sm border p-4 space-y-2">
                <div className="text-sm">给 <b>{detail.id}</b> 选模型（仅影响这一个 agent）：</div>
                <select value={detail.model} onChange={(e) => setModel(e.target.value)} className="border rounded px-2 py-1 text-sm">
                  {routing && !routing.available.includes(detail.model) && <option value={detail.model}>{detail.model}（当前）</option>}
                  {routing?.available.map((m) => <option key={m} value={m}>{m}{m === routing.default ? "（默认）" : ""}</option>)}
                </select>
                {routing && detail.model !== routing.default && (
                  <button onClick={() => setModel("")} className="ml-2 text-xs text-gray-500 hover:text-red-500">回退默认（{routing.default}）</button>
                )}
                {msg && <span className="text-green-600 text-xs ml-2">{msg}</span>}
                <div className="text-[11px] text-gray-400">路由存于 goldset/llm_routing.json；缺省回退 {routing?.default}。本期仅切同一 OpenAI 兼容 endpoint 下的 model。</div>
              </div>
            )}

            {/* 试跑 */}
            {tab === "run" && detail.has_playground && (
              <div className="bg-white rounded-lg shadow-sm border p-4">
                <AgentPlayground kind={detail.playground || detail.id} hasHistory={detail.playground !== "heal"} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
