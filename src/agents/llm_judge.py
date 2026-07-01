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

# 字段名 → select_table 的信号名（拿"该字段选中表"的网格用）
_FIELD_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd",
              "employees": "employee", "top_clients": "client", "top_suppliers": "supplier"}

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


# 溯源抠图向上多带的"表头带"高度(pt)。要够高才能带上"本期数/上期数/年份/占营业收入比重"等列标题——
# 否则 LLM 拿不到期间锚点，会对"两组并排数据"臆测年份（比亚迪把左右两组编成 2023/2022 的幻觉即此）。
_HEADER_BAND_PT = 78


def _source_from_provenance(code: str, year: int, prov: Dict) -> str:
    """主路：按溯源(page+bbox)从 PDF 抠出**值出处的原表区域文本**(比模糊 RAG 精准)，向上多抠一段表头带。"""
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
        y0 = min(b[1] for b in bbs) - _HEADER_BAND_PT  # 上扩一整段"表头带"：带上 本期/上期/年份/占比 等列标题
        x1 = max(b[2] for b in bbs) + 8
        y1 = max(b[3] for b in bbs) + 8
        parts.append(doc[idx].get_text("text", clip=fitz.Rect(x0, y0, x1, y1)))
    doc.close()
    return "\n".join(p for p in parts if p.strip())


def _grid_to_text(table: list, max_rows: int = 45) -> str:
    """把二维表格网格渲染成**行列清晰**的表格文本(去全空列/空行)——优于抠图的碎文本，
    LLM 能直接看清每个数字在哪一列/哪一期，不必靠出现顺序去脑补对齐。"""
    rows = [[(c or "").replace("\n", "").strip() for c in row] for row in (table or [])[:max_rows]]
    nc = max((len(r) for r in rows), default=0)
    if not nc:
        return ""
    rows = [r + [""] * (nc - len(r)) for r in rows]
    keep = [ci for ci in range(nc) if any(rows[ri][ci] for ri in range(len(rows)))]   # 去全空列
    lines = []
    for r in rows:
        cells = [r[ci] for ci in keep]
        if any(cells):                                                                # 去全空行
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _source_grid(code: str, year: int, field: str) -> str:
    """该字段选中表的二维网格 → 结构化表格文本。走 select_table(和解析同源的那张表)。"""
    try:
        from src.eval.table_cache import get_tables
        from src.parsers.infra.table_recall import select_table
        tables = get_tables(code, year)
        if not tables:
            return ""
        sel = select_table(tables, code, year, _FIELD_SIG.get(field, "revenue"))
        return _grid_to_text(sel.get("table")) if sel else ""
    except Exception:
        return ""


def _ensure_prov(field, code, year, field_value, provenance, spec):
    if provenance is not None:
        return provenance
    from src.eval.provenance import attach_provenance
    from src.eval.field_spec import get_spec
    from src.eval.table_cache import get_tables
    return attach_provenance(field_value, get_tables(code, year), spec or get_spec(field))


def build_judge_messages(field: str, code: str, year: int, field_value,
                         provenance: Dict = None, spec=None, unit_label: str = None):
    """构造发给 LLM 的 messages(不调用 LLM)。返回 (messages|None, grounding)。
    抽出来是为了：① judge_field 复用 ② 调试台先拿到可编辑的对话。
    unit_label: 源文金额单位(如'千元')。给 LLM 说明"解析值已换算为元",避免它把单位差误判 unit_error。"""
    prov = _ensure_prov(field, code, year, field_value, provenance, spec)
    source, grounding = _source_grid(code, year, field), "选中表网格"           # 主:结构化表格,行列清晰
    if not source:
        source, grounding = _source_from_provenance(code, year, prov), "溯源原表"   # 退:抠图碎文本
    if not source:
        source, grounding = retrieve_source(code, year, field), "RAG检索"
    if not source:
        return None, grounding
    unit_note = ""
    if unit_label:
        unit_note = (f"\n【单位提示】源文金额单位为「{unit_label}」；下面解析结果已统一换算为「元」。"
                     f"对照数值时请先按单位换算（{unit_label}→元），换算后一致即正确，"
                     f"不要因单位不同而误判 unit_error。\n")
    prompt = (
        f"年报源文（{grounding}，来自解析值的出处，权威）：\n{source}\n{unit_note}\n"
        f"待核对的解析结果（字段 {field}）：\n"
        f"{json.dumps(field_value, ensure_ascii=False, indent=2)}\n\n"
        f"请对照源文判断解析结果是否正确。{_TAXONOMY}\n"
        '只输出 JSON：{"verdict":"ok|suspicious","confidence":0~1,'
        '"issues":[{"field":"如segments[0].ratio_pct","current_value":"解析值",'
        '"correct_value":"源文正确值","error_type":"见上","reason":"源文依据"}],'
        '"summary":"一句话结论"}'
    )
    return [{"role": "system", "content": _SYS}, {"role": "user", "content": prompt}], grounding


def judge_field(field: str, code: str, year: int, field_value,
                provenance: Dict = None, spec=None, debug: bool = False, unit_label: str = None) -> Dict:
    """对某字段做 LLM 语义裁判：按溯源抠原表区域(主) / RAG 检索(兜底) 取源文 + 对照。
    debug=True 时把发给 LLM 的 system/prompt 原文 + LLM 原始回复一起返回(给调试台看)。"""
    messages, grounding = build_judge_messages(field, code, year, field_value, provenance, spec, unit_label)
    if messages is None:
        return {"verdict": "unknown", "confidence": 0.0, "issues": [],
                "summary": "溯源+RAG均无源文，无法语义核对", "field": field,
                "_system": _SYS if debug else None}
    raw = chat(messages, role="judge", temperature=0.1)
    verdict = _extract_json(raw)
    verdict["field"] = field
    verdict["grounding"] = grounding
    if debug:                                   # 给调试台看"我是怎么跟 LLM 对话的"
        verdict["_system"] = _SYS
        verdict["_prompt"] = messages[1]["content"]
        verdict["_raw"] = raw
    return verdict


# ── 复核 agent（绿灯专用）──
# 与 judge_field 是"反立场兄弟"：judge_field 面对锚对不上的可疑数据、默认疑、找病根；
# 本 agent 面对锚已过的绿灯、默认信、只审锚证明不了的盲区。锚不再是"过就免审"——绿灯全量
# 过这个 agent，它点头(pass)才算真过。跨表锚只证明了"表选对 + 至少一维金额合计≈营业收入"，
# 对"其他维度完整性 / 摘行质量 / '其中'重复计数 / 名称脏 / 占比合理"零覆盖，正是这里要审的。

_SYS_VERIFY = ("你是严谨的 A 股年报数据复核员。你的任务：把解析出的结构化结果**逐项拿到源文表格里对照**，"
               "核对每个名称/金额/占比是否与表格一致。跨表锚已保证'表选对、每个维度合计都≈营业收入'，"
               "所以总额、选表、维度是否漏行/串行你不必再核——只管逐项比对数据与源文表格。"
               "宁可放过也别乱改，只输出 JSON，不要解释。")


def build_verify_messages(field: str, code: str, year: int, field_value, sig: Dict,
                          provenance: Dict = None, spec=None, unit_label: str = None):
    """构造发给复核 agent 的 messages。返回 (messages|None, grounding)。
    sig: 该字段的 field_plausibility 信号（带 anchor / parsed_total，用来告诉 agent 锚证明了什么）。"""
    prov = _ensure_prov(field, code, year, field_value, provenance, spec)
    source, grounding = _source_grid(code, year, field), "选中表网格"           # 主:结构化表格,行列清晰
    if not source:
        source, grounding = _source_from_provenance(code, year, prov), "溯源原表"   # 退:抠图碎文本
    if not source:
        source, grounding = retrieve_source(code, year, field), "RAG检索"
    if not source:
        return None, grounding
    anchor = (sig or {}).get("anchor")
    anchor_note = ""
    if anchor:
        anchor_note = (f"\n【锚已确认】权威营业收入≈{anchor:,.0f} 元，解析的**每个维度**合计都对上了(±3%内)"
                       f"——说明表选对了、金额列/单位没错、且各维度切分**完整**(漏行/串行会让维度和对不上、"
                       f"过不了这关)。**别再校总额，也别再怀疑维度漏行/串行——锚已保证。**\n")
    unit_note = ""
    if unit_label:
        unit_note = (f"\n【单位提示】源文金额单位为「{unit_label}」；解析结果已换算为「元」，"
                     f"对照前先换算，别因单位不同误判。\n")
    prompt = (
        f"年报源文（{grounding}，权威）：\n{source}\n{anchor_note}{unit_note}\n"
        f"待复核的解析结果（字段 {field}）：\n"
        f"{json.dumps(field_value, ensure_ascii=False, indent=2)}\n\n"
        f"请把**解析结果里每一项逐一拿到上面源文表格里对照**，核对解析结果**实际含有的字段**（名称、金额）：\n"
        f"名称有没有抠错/串到别行、金额有没有取错格(取到隔壁列或上期那一列)、"
        f"有没有把'其中：X'这类子项当成顶层项、把合计行当明细行混进来。\n"
        f"⚠ **占比不用核**：营收解析结果不含占比(ratio_pct)是有意为之（占比由 金额/营收 另算，不解析）——"
        f"**别因源文表格里有占比列、而解析结果没有占比，就判 hold**。\n"
        f"（总额和各维度切分是否完整——跨表锚已保证，这些不用你再核；你只管**逐项比对数据与源文表格是否一致**。）\n"
        f"⚠ 只依据源文里**真实出现**的文字判断；源文没写的信息（尤其**年份/期间**）**绝不臆测**——"
        f"表里若是两组数据并排、又没标年份，就用'第一组/第二组（左/右）'或'本期/上期'描述，**不要编造'20XX年'**。\n"
        f"每一项都能在表格里对上就判 pass；发现对不上的才 hold，别为改而改。\n"
        '只输出 JSON：{"verdict":"pass|hold","suspects":[{"field":"如segments[2].revenue_yuan",'
        '"issue":"name_error|amount_error|dup_count|extra_row|other",'
        '"reason":"源文表格里的正确值/依据"}],"summary":"一句话结论"}'
    )
    return [{"role": "system", "content": _SYS_VERIFY}, {"role": "user", "content": prompt}], grounding


def verify_field(field: str, code: str, year: int, field_value, sig: Dict = None,
                 provenance: Dict = None, spec=None, debug: bool = False, unit_label: str = None) -> Dict:
    """复核 agent：对**锚已过的绿灯**逐字段复核锚的盲区。pass=真可信→可入库；hold=有疑点→送人审。
    无源文可对照时判 unknown（既不放行也不拦，交回人工），不因缺证据误杀绿灯。"""
    messages, grounding = build_verify_messages(field, code, year, field_value, sig, provenance, spec, unit_label)
    if messages is None:
        return {"verdict": "unknown", "suspects": [], "field": field,
                "summary": "溯源+RAG均无源文，无法复核", "_system": _SYS_VERIFY if debug else None}
    raw = chat(messages, role="judge", temperature=0.1)
    v = _extract_json(raw)
    # 复核 agent 的裁决字段是 verdict=pass|hold；_extract_json 失败会给 verdict=unknown，透传即可
    v.setdefault("suspects", v.get("issues", []))
    v["field"] = field
    v["grounding"] = grounding
    if debug:
        v["_system"] = _SYS_VERIFY
        v["_prompt"] = messages[1]["content"]
        v["_raw"] = raw
    return v


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
