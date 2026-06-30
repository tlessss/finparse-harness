"use client";

import { useEffect, useRef, useState } from "react";
import { codeLabel, loadStockNames, FIELD_LABEL } from "./consoleData";
import { apiGet, apiPost } from "./api";

type Progress = {
  running: boolean; paused?: boolean; total: number; done: number;
  skipped: number; errors: number; fields_with_data: number;
  by_reason: Record<string, number>;
  recent: { code: string; status: string; fields?: Record<string, string>; error?: string }[];
  current?: string | null; stage?: string | null;
  awaiting?: boolean; step_data?: Record<string, unknown> | null;
};

// 字段结果配色：绿可信 / 红待写 / 黄待核验 / 橙低置信 / 橙红可疑
const FSTATUS: Record<string, string> = {
  ok: "bg-green-100 text-green-700", healed: "bg-green-200 text-green-800",
  needs_write: "bg-red-100 text-red-600", unverified: "bg-amber-100 text-amber-700",
  low_confidence: "bg-orange-100 text-orange-700", suspicious: "bg-rose-100 text-rose-700",
};
type Candidate = { code: string; year: number; name?: string };
type LoadState = "loading" | "error" | "ok";
const PAGE = 24;

export default function BatchControl() {
  const [cands, setCands] = useState<Candidate[]>([]);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [p, setP] = useState<Progress | null>(null);
  const [heal, setHeal] = useState(false);
  const [step, setStep] = useState(false);
  const [state, setState] = useState<LoadState>("loading");
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const firstLoad = useRef(true);

  const loadProgress = () =>
    apiGet<Progress | null>("/batch/progress", null).then(({ data }) => { if (data) setP(data); });
  const loadCands = (query: string, off: number) =>
    apiGet<{ candidates: Candidate[]; total: number } | null>(
      `/batch/candidates?q=${encodeURIComponent(query)}&offset=${off}&limit=${PAGE}`, null
    ).then(({ data, live }) => {
      if (live && data) {
        setCands(data.candidates); setTotal(data.total); setState("ok");
        if (firstLoad.current) {                                          // 首次默认选前 10
          setSel(new Set(data.candidates.slice(0, 10).map((c) => c.code)));
          firstLoad.current = false;
        }
      } else setState("error");
    });

  useEffect(() => { loadStockNames().then(loadProgress); }, []);
  // 搜索/翻页（250ms 防抖）
  useEffect(() => {
    const t = setTimeout(() => loadCands(q, offset), 250);
    return () => clearTimeout(t);
  }, [q, offset]);
  // 运行中每 2s 轮询进度
  useEffect(() => {
    if (p?.running) {
      timer.current = setInterval(loadProgress, 2000);
      return () => { if (timer.current) clearInterval(timer.current); };
    }
  }, [p?.running]);

  const toggle = (code: string) =>
    setSel((s) => { const n = new Set(s); if (n.has(code)) n.delete(code); else n.add(code); return n; });

  const start = async () => {
    const codes = cands.filter((c) => sel.has(c.code)).map((c) => c.code);
    if (!codes.length) return;
    await apiPost("/batch/start", { codes, year: 2025, heal, step }, null);
    setTimeout(loadProgress, 500);
  };
  const ctl = (action: string) =>
    apiPost(`/batch/control/${action}`, {}, null).then(() => setTimeout(loadProgress, 500));
  const stepContinue = () =>
    apiPost("/batch/step/continue", {}, null).then(() => setTimeout(loadProgress, 300));

  if (state === "loading") return <Box text="加载中…" />;
  if (state === "error") return <Box text="无法连接后端（确认 http://localhost:8200 已启动）" retry={() => loadCands(q, offset)} />;

  const running = !!p?.running;
  const pct = p && p.total ? (p.done / p.total) * 100 : 0;

  return (
    <div className="space-y-4">
      {/* 单步暂停面板：显示当前阶段详细数据 + 继续/停止 */}
      {p?.awaiting && (
        <div className="bg-amber-50 border-2 border-amber-300 rounded-lg p-4 space-y-3">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-medium">⏸ 单步暂停 · 阶段：<b className="text-amber-700">{p.stage}</b></span>
            {p.current && <span className="text-xs text-gray-500">{codeLabel(p.current)}</span>}
            <span className="text-xs text-gray-400">确认无误→继续；有问题→停止</span>
            <div className="ml-auto flex gap-2">
              <button onClick={stepContinue} className="px-3 py-1.5 rounded text-sm bg-green-600 text-white hover:bg-green-700">▶ 继续</button>
              <button onClick={() => ctl("stop")} className="px-3 py-1.5 rounded text-sm bg-red-600 text-white">■ 停止</button>
            </div>
          </div>
          <StepData data={p.step_data} />
        </div>
      )}

      {/* 任务列表 + 开始解析 */}
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <div className="flex items-center gap-3 mb-3 flex-wrap">
          <span className="font-medium text-sm">任务列表</span>
          <input value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }}
            placeholder="搜 code / 公司名…" disabled={running}
            className="border rounded px-2 py-1 text-xs w-40" />
          <span className="text-xs text-gray-400">已选 {sel.size}</span>
          <button onClick={() => setSel((s) => new Set([...s, ...cands.map((c) => c.code)]))} className="text-xs text-blue-600 hover:underline">全选本页</button>
          <button onClick={() => setSel(new Set())} className="text-xs text-gray-500 hover:underline">清空</button>
          <div className="ml-auto flex items-center gap-2">
            {!running ? (
              <>
                <label className="flex items-center gap-1 text-xs text-gray-600" title="每阶段(抽表/解析判定)暂停,人工确认没问题再继续">
                  <input type="checkbox" checked={step} onChange={(e) => setStep(e.target.checked)} />
                  单步调试
                </label>
                <label className="flex items-center gap-1 text-xs text-gray-600" title="失败字段自动用 LLM 抽 golden+写解析器(完整生产流程,慢)">
                  <input type="checkbox" checked={heal} onChange={(e) => setHeal(e.target.checked)} />
                  完整流程(LLM自愈·慢)
                </label>
                <button onClick={start} disabled={!sel.size}
                  className="px-4 py-1.5 rounded text-sm bg-green-600 text-white disabled:opacity-40 hover:bg-green-700">
                  ▶ 开始解析（{sel.size} 份）
                </button>
              </>
            ) : (
              <>
                <button onClick={() => ctl(p?.paused ? "resume" : "pause")} className="px-3 py-1.5 rounded text-sm bg-amber-500 text-white">
                  {p?.paused ? "⏵ 继续" : "⏸ 暂停"}
                </button>
                <button onClick={() => ctl("stop")} className="px-3 py-1.5 rounded text-sm bg-red-600 text-white">■ 停止</button>
              </>
            )}
          </div>
        </div>
        <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-1.5 max-h-48 overflow-auto">
          {cands.map((c) => (
            <label key={c.code}
              className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs border cursor-pointer ${sel.has(c.code) ? "bg-blue-50 border-blue-300" : "bg-gray-50 border-gray-200"}`}>
              <input type="checkbox" checked={sel.has(c.code)} onChange={() => toggle(c.code)} disabled={running} />
              {codeLabel(c.code)}
            </label>
          ))}
        </div>
        <div className="flex items-center justify-between mt-2 text-xs text-gray-400">
          <span>共 {total} 份可选</span>
          <div className="flex items-center gap-2">
            <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="px-2 py-0.5 rounded border disabled:opacity-40">上一页</button>
            <span>{Math.floor(offset / PAGE) + 1} / {Math.max(1, Math.ceil(total / PAGE))}</span>
            <button disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)} className="px-2 py-0.5 rounded border disabled:opacity-40">下一页</button>
          </div>
        </div>
      </div>

      {/* 进度 */}
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <div className="flex items-center justify-between text-sm mb-1">
          <span className="font-medium flex items-center gap-2">
            跑批进度
            <span className={`px-2 py-0.5 rounded text-xs ${running ? "bg-green-100 text-green-700" : "bg-gray-200 text-gray-500"}`}>
              {running ? (p?.paused ? "⏸ 暂停" : "● 运行中") : "■ 已停止"}
            </span>
          </span>
          <span className="text-gray-500">
            {(p?.done ?? 0).toLocaleString()} / {(p?.total ?? 0).toLocaleString()}（{pct.toFixed(1)}%）
          </span>
        </div>
        <div className="w-full h-3 bg-gray-100 rounded overflow-hidden">
          <div className="h-full bg-blue-600 transition-all" style={{ width: `${pct}%` }} />
        </div>
        {/* 实时活动：正在解析谁的哪个字段 */}
        {running && p?.current && (
          <div className="mt-2 text-sm flex items-center gap-2">
            <span className="inline-block w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
            正在解析 <b>{codeLabel(p.current)}</b>
            {p.stage && <span className="text-gray-500">· {p.stage}</span>}
          </div>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3 text-sm">
          <Stat label="字段有数据" value={p?.fields_with_data ?? 0} cls="text-green-600" />
          <Stat label="需写解析器" value={p?.by_reason?.needs_write ?? 0} cls="text-red-600" />
          <Stat label="待核验/低置信" value={(p?.by_reason?.unverified ?? 0) + (p?.by_reason?.low_confidence ?? 0)} cls="text-amber-600" />
          <Stat label="跳过/异常" value={(p?.skipped ?? 0) + (p?.errors ?? 0)} cls="text-gray-500" />
        </div>
      </div>

      {/* 最近处理 */}
      <div className="bg-white rounded-lg shadow-sm border p-4">
        <h3 className="text-sm font-medium mb-2">最近处理</h3>
        {(!p?.recent || !p.recent.length) && <div className="text-xs text-gray-400">暂无（选几份点"开始解析"）</div>}
        <ul className="text-sm space-y-1.5">
          {(p?.recent || []).map((r, i) => (
            <li key={i} className="flex items-center gap-2 flex-wrap">
              <span className="text-xs w-36 truncate shrink-0">{codeLabel(r.code)}</span>
              {r.status === "error" ? <span className="text-xs text-red-500">⚠ {r.error}</span>
                : r.status === "no_pdf" ? <span className="text-xs text-gray-400">无 PDF</span>
                  : Object.entries(r.fields || {}).map(([f, st]) => (
                    <span key={f} className={`text-xs px-1.5 py-0.5 rounded ${FSTATUS[st] || "bg-gray-100 text-gray-500"}`}
                      title={st}>
                      {FIELD_LABEL[f] || f}{st === "ok" ? " ✓" : st === "healed" ? " ✨" : ""}
                    </span>
                  ))}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: number; cls: string }) {
  return (
    <div className="bg-gray-50 rounded px-3 py-2">
      <div className={`text-xl font-bold ${cls}`}>{value.toLocaleString()}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}

function Box({ text, retry }: { text: string; retry?: () => void }) {
  return (
    <div className="bg-white rounded-lg shadow-sm border p-10 text-center text-gray-400">
      {text}
      {retry && <button onClick={retry} className="ml-3 text-blue-600 hover:underline text-sm">重试</button>}
    </div>
  );
}

// 单步断点的阶段数据：抽表→候选表预览；解析+判定→每字段信号
function StepData({ data }: { data?: Record<string, unknown> | null }) {
  if (!data) return null;
  if (data.fields) {
    const fields = data.fields as Record<string, { status?: string; source?: string; confidence?: string; anchored?: boolean; n?: number }>;
    return (
      <div className="space-y-1 text-xs bg-white rounded p-2">
        {Object.entries(fields).map(([f, s]) => (
          <div key={f} className="flex items-center gap-2">
            <span className="w-24 shrink-0">{FIELD_LABEL[f] || f}</span>
            <span className={`px-1.5 py-0.5 rounded ${FSTATUS[s.status || ""] || "bg-gray-100 text-gray-500"}`}>{s.status || "—"}</span>
            <span className="text-gray-500">{s.source || "?"} · {s.confidence || "?"}{s.anchored ? " · 锚✓" : ""} · {s.n ?? 0}行</span>
          </div>
        ))}
      </div>
    );
  }
  const labels = ["营收", "成本", "研发", "员工"];
  return (
    <div className="space-y-2 text-xs">
      <div className="text-gray-500">共抽到 {String(data._total_tables ?? "?")} 张表 · 各字段候选表（确认抽对没）：</div>
      <div className="grid md:grid-cols-2 gap-2">
        {labels.map((lab) => {
          const t = data[lab] as { page?: number; rows?: string[][]; verdict?: { is_target?: boolean; clean?: boolean; issue?: string; confidence?: string } } | undefined;
          const v = t?.verdict;
          const vOk = v?.is_target && v?.clean;
          return (
            <div key={lab} className="bg-white rounded p-2 overflow-auto max-h-44">
              <div className="text-gray-600 mb-1 flex items-center gap-2 flex-wrap">
                <span>{lab}{t ? `（第${t.page}页）` : "：无候选表"}</span>
                {v && (
                  <span className={`px-1.5 py-0.5 rounded text-[10px] ${vOk ? "bg-green-100 text-green-700" : "bg-red-100 text-red-600"}`}>
                    LLM：{v.is_target ? "✓目标表" : "✗非目标表"} / {v.clean ? "结构清晰" : "抽取错位"}
                  </span>
                )}
              </div>
              {v?.issue ? <div className="text-[10px] text-red-500 mb-1">⚠ {v.issue}</div> : null}
              {t && (
                <table className="border-collapse text-[10px]">
                  <tbody>
                    {(t.rows || []).map((row, i) => (
                      <tr key={i}>{row.map((c, j) => <td key={j} className="border px-1 py-0.5 text-gray-600 whitespace-nowrap">{c}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
