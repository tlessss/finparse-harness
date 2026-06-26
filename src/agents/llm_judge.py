"""
LLM 语义裁判（判定强化 #2）— 只用在"硬规则过了、却低置信"的少数 case

定位：硬规则(field_plausibility)+跨表锚(#1)是确定性主判官；它们判不准的(低置信/无锚)
少数，才送到这里。本裁判**不在运行期热路径**(保持"运行期零LLM")，跑在离线/异步复核队列上。

做法(参考 book-agent/web/ai_reviewer)：
  RAG 从向量库(quantification/rag_data)检索该字段的**年报源文片段**(权威)
  → 连同解析值喂 LLM → 让它对照源文判"语义上对吗" → 出结构化裁决。

返回：{"verdict": ok|suspicious|unknown, "confidence": 0~1,
        "issues": [{"field","current_value","correct_value","error_type","reason"}],
        "summary": str}
"""

import json
import re
from typing import Dict, List

from src.agents.llm_client import chat
from src.validators.vector_validator import _load_store, _query_store

# 每个字段检索源文用的查询语
_QUERIES = {
    "revenue_breakdown": "营业收入构成 分行业 分产品 分地区 占营业收入比重",
    "cost_breakdown": "营业成本构成 占营业成本比重 原材料 人工 折旧",
    "rnd_info": "研发费用 明细 职工薪酬 合计",
    "employees": "员工 专业构成 教育程度 在职员工人数",
    "top_clients": "前五名客户 销售额 占年度销售总额比例",
    "top_suppliers": "前五名供应商 采购额 占年度采购总额比例",
}

_SYS = "你是严谨的 A 股年报数据审核员。对照年报源文判断解析结果是否正确。只输出 JSON，不要解释。"

_TAXONOMY = ("常见错误类型：unit_error(单位错位:万元/元/亿元)、pnl_misid(把毛利率当占比/选错表)、"
             "dim_leak(维度串行:分产品/分地区/分行业混淆)、missing_row(漏行)、extra_row(多行/合计行混入)、"
             "wrong_year(拿了去年那一列)、name_error(科目/名称抠错)、other。")


def _extract_json(raw: str) -> Dict:
    m = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    txt = (m.group(1) if m else raw).strip()
    # 退而求其次：截取第一个 { 到最后一个 }
    if not txt.startswith("{"):
        i, j = txt.find("{"), txt.rfind("}")
        if i >= 0 and j > i:
            txt = txt[i:j + 1]
    try:
        return json.loads(txt)
    except Exception:
        return {"verdict": "unknown", "confidence": 0.0, "issues": [],
                "summary": "LLM 输出非 JSON", "_raw": raw[:300]}


def retrieve_source(code: str, year: int, field: str, top_k: int = 4) -> str:
    """兜底：从向量库检索该字段相关的年报源文片段（溯源失败时用）。"""
    store = _load_store(f"{code}_{year}_annual")
    if store is None:
        return ""
    hits = _query_store(store, _QUERIES.get(field, field), top_k=top_k)
    return "\n---\n".join(h["content"] for h in hits)


def _source_from_provenance(code: str, year: int, prov: Dict) -> str:
    """主路：按溯源(page+bbox)从 PDF 抠出**值出处的原表区域文本**(比模糊 RAG 精准)。"""
    if not prov:
        return ""
    by_page: Dict = {}
    for v in prov.values():
        if isinstance(v, dict) and v.get("page") and v.get("bbox"):
            by_page.setdefault(v["page"], []).append(v["bbox"])
    if not by_page:
        return ""
    import glob
    import fitz
    from src.config import Config
    pdfs = sorted(glob.glob(str(Config.PDF_CACHE_DIR / f"{code}_{year}*.pdf")))
    if not pdfs:
        return ""
    doc = fitz.open(pdfs[0])
    parts = []
    for pg, bbs in by_page.items():
        idx = pg - 1                                   # 溯源 page 为 1-indexed
        if not (0 <= idx < len(doc)):
            continue
        x0 = min(b[0] for b in bbs) - 8
        y0 = min(b[1] for b in bbs) - 25               # 上扩，带上表头
        x1 = max(b[2] for b in bbs) + 8
        y1 = max(b[3] for b in bbs) + 8
        parts.append(doc[idx].get_text("text", clip=fitz.Rect(x0, y0, x1, y1)))
    doc.close()
    return "\n".join(p for p in parts if p.strip())


def _ensure_prov(field, code, year, field_value, provenance, spec):
    if provenance is not None:
        return provenance
    from src.eval.provenance import attach_provenance
    from src.eval.field_spec import get_spec
    from src.eval.table_cache import get_tables
    return attach_provenance(field_value, get_tables(code, year), spec or get_spec(field))


def judge_field(field: str, code: str, year: int, field_value,
                provenance: Dict = None, spec=None) -> Dict:
    """对某字段做 LLM 语义裁判：按溯源抠原表区域(主) / RAG 检索(兜底) 取源文 + 对照。"""
    prov = _ensure_prov(field, code, year, field_value, provenance, spec)
    source, grounding = _source_from_provenance(code, year, prov), "溯源原表"
    if not source:
        source, grounding = retrieve_source(code, year, field), "RAG检索"
    if not source:
        return {"verdict": "unknown", "confidence": 0.0, "issues": [],
                "summary": "溯源+RAG均无源文，无法语义核对", "field": field}
    prompt = (
        f"年报源文（{grounding}，来自解析值的出处，权威）：\n{source}\n\n"
        f"待核对的解析结果（字段 {field}）：\n"
        f"{json.dumps(field_value, ensure_ascii=False, indent=2)}\n\n"
        f"请对照源文判断解析结果是否正确。{_TAXONOMY}\n"
        '只输出 JSON：{"verdict":"ok|suspicious","confidence":0~1,'
        '"issues":[{"field":"如segments[0].ratio_pct","current_value":"解析值",'
        '"correct_value":"源文正确值","error_type":"见上","reason":"源文依据"}],'
        '"summary":"一句话结论"}'
    )
    raw = chat([{"role": "system", "content": _SYS},
                {"role": "user", "content": prompt}], role="judge", temperature=0.1)
    verdict = _extract_json(raw)
    verdict["field"] = field
    verdict["grounding"] = grounding
    return verdict


def review_queue(reason: str = "low_confidence", limit: int = 20) -> List[Dict]:
    """复核驱动(闭合"分诊→复核")：取队列里某 reason 的待办 → 重路由拿解析值 → 跑 #2 裁判
    → ok 则销账(救回#1误标的低置信)、suspicious 则改标为 suspicious(留队列待修)。"""
    from src.eval.triage_queue import list_open, resolve, enqueue
    from src.eval.field_spec import get_spec
    from src.parsers.revenue_router import route_field
    out = []
    for rec in list_open(reason=reason)[:limit]:
        code, year, field = rec["code"], rec["year"], rec["field"]
        spec = get_spec(field)
        rt = route_field(spec, code, year)
        if rt.get("status") != "routed":
            continue                                    # 已不可路由 → 留给 needs_write
        value = rt["result"]
        if isinstance(value, dict) and field in value:  # D类富结构解包
            value = value[field]
        v = judge_field(field, code, year, value, spec=spec)
        verdict = v.get("verdict")
        if verdict == "ok":
            resolve(code, year, field)                  # LLM 确认对的 → 销账
        elif verdict == "suspicious":
            enqueue(code, year, field, "suspicious", note=v.get("summary", ""))
        out.append({"code": code, "year": year, "field": field, "verdict": verdict,
                    "summary": v.get("summary"), "issues": v.get("issues")})
    return out


def review_low_confidence(parse_result: Dict, signals: Dict = None) -> Dict:
    """复核驱动：对 parse_result 里**低置信/无锚**的字段逐个 LLM 裁判。
    signals: {field: field_plausibility信号}；缺省则复核所有有值字段。"""
    code = parse_result.get("stock_code")
    year = parse_result.get("report_year")
    out = {}
    for field in _QUERIES:
        val = parse_result.get(field)
        if not val:
            continue
        conf = (signals or {}).get(field, {}).get("confidence")
        if signals and conf == "high":          # 高置信(已锚定)→ 不必上 LLM，省钱
            continue
        out[field] = judge_field(field, code, year, val)
    return out
