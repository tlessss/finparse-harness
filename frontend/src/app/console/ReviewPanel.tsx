"use client";

import { useState, useMemo, useEffect } from "react";
import {
  ERROR_TYPE_LABEL, FIELD_LABEL, fmtMoney, codeLabel, loadStockNames,
  type Confidence, type Verdict,
} from "./consoleData";
import { apiGet, apiPost, liveLabel } from "./api";

type Item = { name: string; revenue_yuan: number | null; ratio_pct: number | null };
type Prov = { page: number; bbox: [number, number, number, number] };
type Task = {
  stock_code: string; year: number; page: number; page_w_pt: number; page_h_pt: number;
  page_image: string; parser_code: string;
  result: Record<string, Item[]>; provenance: Record<string, Prov>;
};
type LoadState = "loading" | "error" | "ok";

export type ReviewPanelProps = {
  code?: string;
  year?: number;
  field?: string;
};

export default function ReviewPanel({
  code: stockCode = "002407",
  year = 2025,
  field: judgeField = "revenue_breakdown",
}: ReviewPanelProps) {
  const [task, setTask] = useState<Task | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [result, setResult] = useState<any>({});
  const [code, setCode] = useState("");
  const [conf, setConf] = useState<Record<string, { confidence: Confidence; anchor: number | null }>>({});
  const [sel, setSel] = useState<string | null>(null);
  const [recode, setRecode] = useState<{ score: number; exact: boolean } | null>(null);
  const [judge, setJudge] = useState<Verdict | null>(null);
  const [judging, setJudging] = useState(false);
  const dimLabel: Record<string, string> = {
    segments: "分产品", industries: "分行业", regions: "分地区", by_channel: "分销售模式",
    rnd_detail: "研发明细", by_specialty: "专业构成", by_education: "教育程度", 明细: "明细",
  };
  // 把任意字段结果归一成 {分组: 行[]}：dict-of-list 直接用；纯 list 包成"明细"；标量(如合计)忽略
  const sections: Record<string, any[]> = useMemo(() => {
    if (Array.isArray(result)) return { 明细: result };
    const o: Record<string, any[]> = {};
    for (const [k, v] of Object.entries(result || {})) if (Array.isArray(v) && v.length) o[k] = v as any[];
    return o;
  }, [result]);
  // 某组的数值列(name 之外的 number 字段，如 revenue_yuan/amount_yuan/ratio_pct/...)
  const numCols = (rows: any[]) => {
    const s = new Set<string>();
    for (const r of rows) for (const [k, v] of Object.entries(r)) if (k !== "name" && typeof v === "number") s.add(k);
    return [...s];
  };

  const [, setNamesTick] = useState(0);
  useEffect(() => { loadStockNames().then(() => setNamesTick((t) => t + 1)); }, []);

  useEffect(() => {
    setState("loading"); setRecode(null); setJudge(null);
    apiGet<Task | null>(`/review/task?stock_code=${stockCode}&year=${year}&field=${judgeField}`, null).then(({ data, live }) => {
      // 请求成功(且后端没报 error)就进面板 —— 该字段无数据(result=null)显示"无数据",不当加载失败
      if (live && data && !(data as { error?: string }).error) {
        setTask(data); setResult(data.result || {}); setCode(data.parser_code || ""); setState("ok");
      } else setState("error");
    });
    apiGet<{ signals: Record<string, { confidence: Confidence; anchor: number | null }> } | null>(
      `/review/signals?stock_code=${stockCode}&year=${year}`, null).then(({ data }) => {
      setConf(data?.signals || {});
    });
  }, [stockCode, year, judgeField]);

  const ratioSum = (rows: any[]) =>
    rows.reduce((a, r) => a + (typeof r.ratio_pct === "number" ? r.ratio_pct : 0), 0);
  const hasRatio = (rows: any[]) => rows.some((r) => r.ratio_pct != null);

  const editVal = (dim: string, i: number, key: string, v: string) =>
    setResult((prev: any) => {
      const nv = v === "" ? null : Number(v);
      if (Array.isArray(prev)) return prev.map((it: any, j: number) => j === i ? { ...it, [key]: nv } : it);
      return { ...prev, [dim]: prev[dim].map((it: any, j: number) => j === i ? { ...it, [key]: nv } : it) };
    });

  const runJudge = async () => {
    setJudging(true);
    const { data, live } = await apiPost<Verdict | null>("/review/judge",
      { stock_code: stockCode, year, field: judgeField }, null);
    setJudging(false);
    if (live && data) setJudge(data); else alert("LLM 裁判失败：后端 /review/judge 未响应");
  };
  const runRecode = async () => {
    const { data, live } = await apiPost<{ score: number; exact: boolean } | null>("/review/recode",
      { stock_code: stockCode, year, code }, null);
    if (live && data) setRecode({ score: data.score ?? 0, exact: !!data.exact });
    else alert("重过闸失败：后端 /review/recode 未响应");
  };
  const runCertify = async () => {
    await apiPost("/review/golden", { stock_code: stockCode, year, revenue_breakdown: result }, null);
    const { data, live } = await apiPost<{ certified?: boolean }>("/review/certify", { stock_code: stockCode, year, code }, {});
    alert(live ? `认证结果：${JSON.stringify(data)}` : "认证失败：后端 /review/certify 未响应");
  };

  if (state === "loading") return <Box text={`加载审核任务 ${codeLabel(stockCode)} / ${year} …`} />;
  if (state === "error" || !task) return <Box text={`无法加载 ${codeLabel(stockCode)}/${year}：若首次解析该报告(全量扫描较慢)可能超时，请稍候重试；否则确认后端 :8200 已启动。`} />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold">审核认证关 · <span>{codeLabel(task.stock_code)} / {task.year}</span>
          <span className="ml-2 text-xs font-normal text-blue-600">{FIELD_LABEL[judgeField] || judgeField}</span>
          <span className={`ml-2 text-xs font-normal ${liveLabel(true).cls}`}>{liveLabel(true).text}</span>
        </h2>
        <span className="text-xs text-gray-400">点左边任一数字 → 右边 PDF 高亮其出处</span>
      </div>

      <div className="bg-white rounded-lg shadow-sm border p-3 flex flex-wrap gap-2">
        <span className="text-xs text-gray-500 self-center mr-1">置信度：</span>
        {Object.keys(conf).length === 0 && <span className="text-xs text-gray-300">（/review/signals 无数据）</span>}
        {Object.entries(conf).map(([f, c]) => <ConfBadge key={f} field={f} conf={c.confidence} anchor={c.anchor} />)}
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white rounded-lg shadow-sm border p-4 space-y-4">
          {Object.keys(sections).length === 0 && (
            <div className="text-sm text-gray-400">该字段无结构化结果（解析失败或无数据）</div>
          )}
          {Object.entries(sections).map(([dim, rows]) => {
            const cols = numCols(rows);
            return (
              <div key={dim}>
                <div className="flex items-center justify-between mb-1">
                  <h3 className="text-sm font-medium text-gray-600">{dimLabel[dim] || dim}</h3>
                  {hasRatio(rows) ? (
                    <span className={`text-xs ${Math.abs(ratioSum(rows) - 100) <= 2 ? "text-green-600" : "text-orange-500"}`}>
                      占比和 {ratioSum(rows).toFixed(1)}% {Math.abs(ratioSum(rows) - 100) <= 2 ? "✓" : "⚠ 偏离100%"}
                    </span>
                  ) : (
                    <span className="text-xs text-gray-400">无占比列</span>
                  )}
                </div>
                <table className="w-full text-sm">
                  <tbody>
                    {rows.map((it, i) => (
                      <tr key={i} className="border-t">
                        <td className="py-1 pr-2 text-gray-700">{it.name}</td>
                        {cols.map((nk) => (
                          <Cell key={nk} path={`${dim}[${i}].${nk}`} val={it[nk]} sel={sel} setSel={setSel}
                            onEdit={(v) => editVal(dim, i, nk, v)} prov={task.provenance} />
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
        </div>

        <div className="bg-white rounded-lg shadow-sm border p-2">
          <div className="text-xs text-gray-400 px-2 py-1">PDF 第 {task.page} 页（溯源高亮）</div>
          <div className="relative w-full" style={{ aspectRatio: `${task.page_w_pt} / ${task.page_h_pt}` }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            {task.page_image && <img src={task.page_image} alt="pdf page" className="absolute inset-0 w-full h-full object-contain" />}
            {Object.entries(task.provenance).map(([path, p]) => {
              const [x0, y0, x1, y1] = p.bbox;
              const style = {
                left: `${(x0 / task.page_w_pt) * 100}%`, top: `${(y0 / task.page_h_pt) * 100}%`,
                width: `${((x1 - x0) / task.page_w_pt) * 100}%`, height: `${((y1 - y0) / task.page_h_pt) * 100}%`,
              };
              const active = sel === path;
              return <div key={path} onClick={() => setSel(path)} style={style}
                className={`absolute cursor-pointer transition ${active ? "ring-2 ring-red-500 bg-red-500/20" : "ring-1 ring-red-300/40 hover:bg-red-400/10"}`} />;
            })}
          </div>
        </div>
      </div>

      <div className="bg-white rounded-lg shadow-sm border p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-gray-600">LLM 裁判（对抗式找茬，~10-20s）</h3>
          <button onClick={runJudge} disabled={judging}
            className="px-3 py-1.5 rounded text-sm bg-purple-600 text-white hover:bg-purple-700 disabled:bg-gray-300">
            {judging ? "裁判中…" : "🔍 跑 LLM 裁判"}
          </button>
        </div>
        {judge && (
          <div>
            <div className="flex items-center gap-2 text-sm">
              <span className={`px-2 py-0.5 rounded text-xs font-medium ${judge.verdict === "ok" ? "bg-green-100 text-green-700" : judge.verdict === "suspicious" ? "bg-red-100 text-red-700" : "bg-gray-200 text-gray-600"}`}>
                {judge.verdict === "ok" ? "✓ 通过" : judge.verdict === "suspicious" ? "✗ 可疑" : "? 未知"}
              </span>
              <span className="text-gray-500">置信 {(judge.confidence * 100).toFixed(0)}%</span>
              <span className="text-gray-400 text-xs">依据：{judge.grounding}</span>
            </div>
            <p className="text-sm text-gray-700 mt-1">{judge.summary}</p>
            {judge.issues?.length > 0 && (
              <table className="w-full text-xs mt-2">
                <thead className="text-gray-400 text-left">
                  <tr><th className="py-1">位置</th><th>现值</th><th>正确值</th><th>类型</th><th>依据</th></tr>
                </thead>
                <tbody>
                  {judge.issues.map((is, i) => (
                    <tr key={i} className="border-t">
                      <td className="py-1 font-mono">{is.field}</td>
                      <td className="text-red-600">{fmtMoney(is.current_value)}</td>
                      <td className="text-green-600">{fmtMoney(is.correct_value)}</td>
                      <td><span className="px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">{ERROR_TYPE_LABEL[is.error_type] || is.error_type}</span></td>
                      <td className="text-gray-500">{is.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      <div className="bg-white rounded-lg shadow-sm border p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-medium text-gray-600">专用解析器源码（LLM 生成，可直接改）</h3>
          <button onClick={runRecode} className="px-3 py-1.5 rounded text-sm bg-blue-600 text-white hover:bg-blue-700">💾 改代码并重过闸</button>
        </div>
        <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false}
          className="w-full h-56 font-mono text-xs border rounded p-2 bg-gray-50" />
        {recode && (
          <div className={`mt-2 text-sm ${recode.exact ? "text-green-600" : "text-amber-600"}`}>
            重过闸结果：得分 <b>{recode.score.toFixed(2)}</b> {recode.exact ? "· ✓ exact，可批准认证" : "· 仍未 exact"}
          </div>
        )}
      </div>

      <div className="flex gap-3">
        <button onClick={runCertify} disabled={!(recode?.exact)} title={recode?.exact ? "" : "需重过闸 exact 后才能认证"}
          className={`px-5 py-2 rounded font-medium ${recode?.exact ? "bg-green-600 text-white hover:bg-green-700" : "bg-gray-200 text-gray-400 cursor-not-allowed"}`}>
          ✅ 批准认证（POST /review/golden + /review/certify）
        </button>
        <button className="px-5 py-2 rounded font-medium bg-blue-100 text-blue-700 hover:bg-blue-200">✏️ 仅改数值（修本份）</button>
        <button className="px-5 py-2 rounded font-medium bg-red-100 text-red-700 hover:bg-red-200">❌ 打回重写</button>
      </div>
    </div>
  );
}

function ConfBadge({ field, conf, anchor }: { field: string; conf: Confidence | undefined; anchor: number | null }) {
  const cls = conf === "high" ? "bg-green-50 text-green-700 border-green-200"
    : conf === "low" ? "bg-orange-50 text-orange-700 border-orange-200"
    : "bg-gray-100 text-gray-500 border-gray-200";
  const tip = anchor != null ? `DB 权威值(锚) ${(anchor / 1e8).toFixed(2)}亿` : "无 DB 锚（如客户/供应商）";
  return (
    <span className={`px-2 py-1 rounded border text-xs ${cls}`} title={tip}>
      {FIELD_LABEL[field] || field}：{conf || "unknown"}
    </span>
  );
}

function Cell({ path, val, sel, setSel, onEdit, prov }: {
  path: string; val: number | null; sel: string | null; setSel: (p: string) => void;
  onEdit: (v: string) => void; prov: Record<string, Prov>;
}) {
  const [editing, setEditing] = useState(false);
  const hasProv = path in prov;
  const active = sel === path;
  const display = editing
    ? (val ?? "")
    : (val == null ? "" : Number(val).toLocaleString("en-US", { maximumFractionDigits: 2 }));
  return (
    <td className="py-1">
      <input
        value={display}
        onChange={(e) => onEdit(e.target.value.replace(/,/g, ""))}
        onFocus={() => { setEditing(true); if (hasProv) setSel(path); }}
        onBlur={() => setEditing(false)}
        title={hasProv ? `溯源 p${prov[path].page}` : "无溯源"}
        className={`w-28 text-right text-sm px-1 rounded border ${active ? "border-red-500 bg-red-50" : "border-transparent hover:border-gray-300"}`} />
    </td>
  );
}

function Box({ text }: { text: string }) {
  return <div className="bg-white rounded-lg shadow-sm border p-10 text-center text-gray-400">{text}</div>;
}
