// 控制台共享：类型 + 标签 + 工具函数（无任何 mock 数据；数据全部来自后端）

export const API_BASE = "http://localhost:8200";

// 金额千分位（en-US 分组），null/NaN → "—"
export const fmtMoney = (n: number | null | undefined) =>
  n == null || isNaN(Number(n)) ? "—" : Number(n).toLocaleString("en-US", { maximumFractionDigits: 2 });

// code → 公司名。从后端 /stocks/names 拉全量填充（DB stocks 表）；拉到前显示 code。
export const STOCK_NAMES: Record<string, string> = {};
let _namesLoaded = false;
export async function loadStockNames(): Promise<void> {
  if (_namesLoaded) return;
  try {
    const r = await fetch(`${API_BASE}/stocks/names`);
    if (r.ok) { const d = await r.json(); Object.assign(STOCK_NAMES, d.names || {}); _namesLoaded = true; }
  } catch { /* 后端没起 → 保持空，显示 code */ }
}
export const codeLabel = (code: string) =>
  STOCK_NAMES[code] ? `${STOCK_NAMES[code]}(${code})` : code;

export const FIELDS = [
  "revenue_breakdown", "cost_breakdown", "rnd_info",
  "employees", "top_clients", "top_suppliers",
] as const;
export type Field = (typeof FIELDS)[number];
export const FIELD_LABEL: Record<string, string> = {
  revenue_breakdown: "营收", cost_breakdown: "成本", rnd_info: "研发",
  employees: "员工", top_clients: "前五客户", top_suppliers: "前五供应商",
};

// ── 分诊 reason ──
// routed 是 status=ok 记录的 reason（绿，可信），不在待办里；其余是待办的分类。
export type Reason = "routed" | "needs_write" | "low_confidence" | "unverified" | "suspicious" | "needs_human";
export const REASON_META: Record<Reason, { label: string; todo: string; cls: string }> = {
  routed:         { label: "可信",       todo: "已认证路由 + 锚验证过",       cls: "bg-green-100 text-green-700" },
  needs_write:    { label: "需写解析器", todo: "写新解析器(给 golden→自愈)", cls: "bg-red-100 text-red-700" },
  low_confidence: { label: "低置信",     todo: "锚对不上，复核(LLM/人)",      cls: "bg-orange-100 text-orange-700" },
  unverified:     { label: "待核验",     todo: "无 DB 锚可验，抽查或跑 #2",    cls: "bg-amber-100 text-amber-700" },
  suspicious:     { label: "可疑",       todo: "改解析器",                   cls: "bg-rose-100 text-rose-700" },
  needs_human:    { label: "纯人工",     todo: "无 golden/修复失败",          cls: "bg-gray-200 text-gray-600" },
};
// 待办列表里展示的 reason（不含 routed=可信）
export const OPEN_REASONS: Reason[] = ["needs_write", "low_confidence", "unverified", "suspicious", "needs_human"];

export type TriageStatus = "ok" | "open" | "in_progress" | "resolved";
export type Confidence = "high" | "low" | "unknown";

// 后端 /triage/summary 返回
export type Summary = {
  total: number;
  verified: number; verified_pct: number;   // 绿：锚验证过，真可信
  parsed: number; parsed_pct: number;       // 绿+黄+橙：解出数据
  open: number;
  by_reason: Record<string, number>;
  by_status: Record<string, number>;
  ok_by_field: Record<string, number>;
};

export type TriageRecord = {
  code: string; year: number; field: Field;
  reason: Reason;
  signal: { clean: boolean; confidence: Confidence; anchored: boolean; diff_pct: number | null };
  note: string; status: TriageStatus;
  created_at: string; updated_at: string;
};

export function triageSummary(records: TriageRecord[]) {
  const open = records.filter((r) => r.status !== "resolved");
  const by_reason: Record<string, number> = {};
  const by_field: Record<string, number> = {};
  for (const r of open) {
    by_reason[r.reason] = (by_reason[r.reason] || 0) + 1;
    by_field[r.field] = (by_field[r.field] || 0) + 1;
  }
  return { total: records.length, open: open.length, by_reason, by_field };
}

// ── LLM 裁判结果 ──
export type Issue = {
  field: string; current_value: number; correct_value: number;
  error_type: string; reason: string;
};
export type Verdict = {
  verdict: "ok" | "suspicious" | "unknown"; confidence: number;
  issues: Issue[]; summary: string; grounding: string; field: string;
};
export const ERROR_TYPE_LABEL: Record<string, string> = {
  unit_error: "单位错位", pnl_misid: "毛利率当占比/选错表", dim_leak: "维度串行",
  missing_row: "漏行", extra_row: "多行", wrong_year: "年份错", name_error: "名称错", other: "其它",
};
