/** 营收解析器教学用 mock 数据 — 形态对齐 002254 泰和新材 2025 年报（示意，非真值） */

export type FlowNode = {
  id: string;
  label: string;
  sub: string;
  col: number;
  row: number;
  fn: string;
  file: string;
  what: string;
  points?: string[];
  mock?: [string, string][];
  sample?: string;
};

export type FlowLink = { s: string; t: string; label?: string; kind?: "main" | "branch" | "fallback" };

export const FLOW_NODES: FlowNode[] = [
  {
    id: "entry",
    label: "parse()",
    sub: "入口",
    col: 0,
    row: 1,
    fn: "parse",
    file: "revenue/default.py:53",
    what: "接收 pdf_path + 可选 pre_scan（引擎 scan_pdf 的全量表）。优先走缓存表路径。",
    points: ["有 pre_scan → _parse_from_prescan", "失败或无缓存 → 自己找页抽表"],
    mock: [["入参", "pdf_path, pre_scan?"], ["出参", "revenue_breakdown + 溯源 + status"]],
  },
  {
    id: "prescan",
    label: "filter_by_signature",
    sub: "选营收表",
    col: 2,
    row: 0,
    fn: "_parse_from_prescan",
    file: "default.py:75 · table_scanner.py:382",
    what: "从 pre_scan 全量表里按 caption + 章节 + must_have 打分，取得分最高的一张营收构成表。",
    points: ["caption 命中 +40", "must_have 至少 1 个", "exclude 词大幅扣分"],
    mock: [
      ["候选表数", "47"],
      ["选中页码", "p24"],
      ["caption", "（1）营业收入构成"],
      ["得分", "125"],
    ],
    sample: "filter_by_signature(pre_scan, 'revenue')[0]",
  },
  {
    id: "find_pages",
    label: "_find_candidate_pages",
    sub: "信号打分找页",
    col: 2,
    row: 2,
    fn: "_find_candidate_pages",
    file: "default.py:108 · revenue.yaml",
    what: "无 pre_scan 时：按 revenue.yaml 的 strong/weak 关键词给页打分，取 top 12，不用页码盲截断。",
    points: ["强信号：占营业收入比重、分产品…", "prefer_section: MD&A", "legacy 回退：关键词 + 页≥16"],
    mock: [["命中页", "22, 23, 24, 25"], ["top_n", "12"], ["min_page", "6"]],
  },
  {
    id: "extract",
    label: "_extract_tables",
    sub: "pdfplumber",
    col: 3,
    row: 2,
    fn: "_extract_tables",
    file: "default.py:157",
    what: "在候选页 find_tables()，保留每格 bbox 到 _bbox_map；要求表内含分产品/行业/地区且≥5行。",
    mock: [["抽出表", "3 张"], ["保留 bbox", "是"], ["示例页", "p24"]],
  },
  {
    id: "filter",
    label: "_filter_revenue_tables",
    sub: "打分过滤",
    col: 4,
    row: 2,
    fn: "_filter_revenue_tables",
    file: "default.py:186",
    what: "有占比列+35、分产品/行业/地区+30；员工/供应商/研发表扣分；占比列最大值>100% 直接淘汰。",
    mock: [["通过", "2 张"], ["淘汰", "1 张(KPI表)"], ["最高分", "110"]],
  },
  {
    id: "select_ab",
    label: "_select_best_table",
    sub: "A/B 表择优",
    col: 5,
    row: 2,
    fn: "_select_best_table",
    file: "default.py:92 · header_columns",
    what: "在候选里优先选 composition (A) 占比构成表，避免挑到 margin (B) 毛利率表。",
    mock: [["选中类型", "(A) composition"], ["避坑", "毛利率≠占比"]],
    sample: "徐工类：毛利率表被降级",
  },
  {
    id: "unit",
    label: "detect_unit",
    sub: "万元→元",
    col: 3,
    row: 0,
    fn: "detect_unit",
    file: "unit_detector.py · default.py:86",
    what: "从 caption + 表文本识别「万元/百万元」等，classify 时 convert_to_yuan 统一为元。",
    mock: [["原文单位", "人民币万元"], ["unit_ratio", "10000"], ["示例", "3,595,000 → 35.95亿"]],
  },
  {
    id: "columns",
    label: "_resolve_columns",
    sub: "认列",
    col: 4,
    row: 0,
    fn: "_resolve_columns",
    file: "default.py:37 · revenue.yaml",
    what: "表头别名优先定 name/amount/ratio 列；占比列闸门：表头无「占营业收入比重」则 ratio_col=None。",
    points: ["统计法 _detect_columns 兜底", "禁止毛利率顶替占比"],
    mock: [
      ["name_col", "0"],
      ["amount_col", "1"],
      ["ratio_col", "2"],
    ],
  },
  {
    id: "classify",
    label: "_classify",
    sub: "分桶逐行",
    col: 5,
    row: 0,
    fn: "_classify",
    file: "default.py:313",
    what: "见「分产品/分行业/分地区/销售模式」切桶；逐行取 name/金额/占比；跳过合计与「其中」；去重写入四维数组。",
    mock: [
      ["segments", "2 行"],
      ["industries", "1 行"],
      ["regions", "2 行"],
      ["by_channel", "0 行"],
    ],
  },
  {
    id: "out",
    label: "revenue_breakdown",
    sub: "JSON + 溯源",
    col: 6,
    row: 1,
    fn: "return",
    file: "default.py:90",
    what: "输出四维结构 + 每项 revenue_yuan(元) / ratio_pct + 溯源 bbox。",
    mock: [["status", "ok"], ["溯源条数", "12"], ["锚(示意)", "≈35.95亿"]],
  },
];

export const FLOW_LINKS: FlowLink[] = [
  { s: "entry", t: "prescan", label: "有 pre_scan", kind: "main" },
  { s: "entry", t: "find_pages", label: "无/失败", kind: "fallback" },
  { s: "find_pages", t: "extract" },
  { s: "extract", t: "filter" },
  { s: "filter", t: "select_ab" },
  { s: "prescan", t: "unit" },
  { s: "select_ab", t: "unit" },
  { s: "unit", t: "columns" },
  { s: "columns", t: "classify" },
  { s: "classify", t: "out" },
];

/** mock 原始表（二维网格，单位：万元） */
export const MOCK_RAW_TABLE: string[][] = [
  ["项目", "2025年营业收入", "占营业收入比重"],
  ["分行业", "", ""],
  ["化学纤维制造业", "3,595,000", "100.00%"],
  ["分产品", "", ""],
  ["氨纶纤维", "2,100,000", "58.42%"],
  ["芳纶纤维", "1,495,000", "41.58%"],
  ["分地区", "", ""],
  ["境内", "3,210,000", "89.29%"],
  ["境外", "385,000", "10.71%"],
];

export const MOCK_COLUMN_MAP = {
  name_col: 0,
  amount_col: 1,
  ratio_col: 2,
  unit_ratio: 10000,
  unit_label: "人民币万元",
};

export type MockRow = {
  name: string;
  revenue_yuan: number | null;
  ratio_pct: number | null;
  dim: string;
  dimLabel: string;
  skipped?: string;
};

export const MOCK_CLASSIFY_ROWS: MockRow[] = [
  { name: "分行业", revenue_yuan: null, ratio_pct: null, dim: "-", dimLabel: "切桶 → industries", skipped: "章节标题行" },
  { name: "化学纤维制造业", revenue_yuan: 35950000000, ratio_pct: 100, dim: "industries", dimLabel: "分行业" },
  { name: "分产品", revenue_yuan: null, ratio_pct: null, dim: "-", dimLabel: "切桶 → segments", skipped: "章节标题行" },
  { name: "氨纶纤维", revenue_yuan: 21000000000, ratio_pct: 58.42, dim: "segments", dimLabel: "分产品" },
  { name: "芳纶纤维", revenue_yuan: 14950000000, ratio_pct: 41.58, dim: "segments", dimLabel: "分产品" },
  { name: "分地区", revenue_yuan: null, ratio_pct: null, dim: "-", dimLabel: "切桶 → regions", skipped: "章节标题行" },
  { name: "境内", revenue_yuan: 32100000000, ratio_pct: 89.29, dim: "regions", dimLabel: "分地区" },
  { name: "境外", revenue_yuan: 3850000000, ratio_pct: 10.71, dim: "regions", dimLabel: "分地区" },
];

export const MOCK_OUTPUT = {
  revenue_breakdown: {
    segments: [
      { name: "氨纶纤维", revenue_yuan: 21000000000, ratio_pct: 58.42 },
      { name: "芳纶纤维", revenue_yuan: 14950000000, ratio_pct: 41.58 },
    ],
    industries: [{ name: "化学纤维制造业", revenue_yuan: 35950000000, ratio_pct: 100 }],
    regions: [
      { name: "境内", revenue_yuan: 32100000000, ratio_pct: 89.29 },
      { name: "境外", revenue_yuan: 3850000000, ratio_pct: 10.71 },
    ],
    by_channel: [],
  },
  status: "ok",
};

export const DIM_CN: Record<string, string> = {
  segments: "分产品",
  industries: "分行业",
  regions: "分地区",
  by_channel: "分销售模式",
};

export function yi(n: number) {
  return (n / 1e8).toFixed(2) + " 亿";
}
