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
from typing import Dict, List, Optional

from src.agents.llm_client import chat
from src.agents.llm_routing import resolve_model
from src.prompts.registry import build_messages, load_template
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

def _sys_text(agent_id: str) -> str:
    """模板 system 文本（无源文兜底时给调试台展示用）。"""
    return load_template(agent_id).get("system") or ""


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
    """该字段 select_table 选中表 → 结构化表格文本（可能与认证解析器实际用表不一致，仅作兜底）。"""
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


def _source_meta_line(pick: Optional[Dict] = None, via: str = "") -> str:
    """表出处一行：写进 source 正文，避免 grounding 里出现 LLM 会误当成表名的系统词。"""
    parts = []
    if pick and pick.get("page"):
        parts.append(f"第{pick['page']}页")
    if pick:
        cap = (pick.get("caption") or "").strip()
        if cap:
            parts.append(f"表格标题「{cap}」")
    if via:
        parts.append(via)
    if not parts:
        return ""
    return "【表出处】" + " · ".join(parts) + "（系统标注，不是表格内文字）\n"


def _resolve_source_text(code: str, year: int, field: str, provenance: Dict = None,
                         source_override: str = None) -> tuple:
    """解析 verify/judge 应用哪张表当源文。优先：override → 溯源对齐表 → select_table → 抠图 → RAG。
    返回 (source正文含表出处行, grounding)。grounding 只用稳定、不含内部术语的短标签。"""
    if source_override:
        return source_override, "选表自愈后重选表"
    tables = None
    try:
        from src.eval.table_cache import get_tables
        tables = get_tables(code, year)
    except Exception:
        pass
    if provenance and tables:
        from src.prompts.context.table import pick_table_from_provenance, grid_text_from_pick
        pick = pick_table_from_provenance(provenance, tables)
        if pick:
            grid = grid_text_from_pick(pick)
            if grid:
                meta = _source_meta_line(pick, "与解析结果同源")
                return meta + grid, "选中表网格"
    source = _source_grid(code, year, field)
    if source:
        return source, "选中表网格"
    if provenance:
        clip = _source_from_provenance(code, year, provenance)
        if clip:
            return clip, "PDF溯源抠图"
    rag = retrieve_source(code, year, field)
    if rag:
        return rag, "RAG检索片段"
    return "", ""


def _ensure_prov(field, code, year, field_value, provenance, spec):
    if provenance is not None:
        return provenance
    from src.eval.provenance import attach_provenance
    from src.eval.field_spec import get_spec
    from src.eval.table_cache import get_tables
    return attach_provenance(field_value, get_tables(code, year), spec or get_spec(field))


def build_judge_messages(field: str, code: str, year: int, field_value,
                         provenance: Dict = None, spec=None, unit_label: str = None,
                         agent_id: str = "judge", extra_vars: Optional[Dict] = None):
    """构造发给 LLM 的 messages(不调用 LLM)。返回 (messages|None, grounding)。
    抽出来是为了：① judge_field 复用 ② 调试台先拿到可编辑的对话。
    unit_label: 源文金额单位(如'千元')。给 LLM 说明"解析值已换算为元",避免它把单位差误判 unit_error。"""
    prov = _ensure_prov(field, code, year, field_value, provenance, spec)
    source, grounding = _resolve_source_text(code, year, field, prov)
    if not source:
        return None, grounding
    unit_note = ""
    if unit_label:
        unit_note = (f"\n【单位提示】源文金额单位为「{unit_label}」；下面解析结果已统一换算为「元」。"
                     f"对照数值时请先按单位换算（{unit_label}→元），换算后一致即正确，"
                     f"不要因单位不同而误判 unit_error。\n")
    variables = {
        "grounding": grounding, "source": source, "unit_note": unit_note, "field": field,
        "field_value_json": json.dumps(field_value, ensure_ascii=False, indent=2),
    }
    if extra_vars:
        variables.update(extra_vars)
    built = build_messages(agent_id, variables)
    return built["messages"], grounding


def judge_field(field: str, code: str, year: int, field_value,
                provenance: Dict = None, spec=None, debug: bool = False, unit_label: str = None) -> Dict:
    """对某字段做 LLM 语义裁判：按溯源抠原表区域(主) / RAG 检索(兜底) 取源文 + 对照。
    debug=True 时把发给 LLM 的 system/prompt 原文 + LLM 原始回复一起返回(给调试台看)。"""
    messages, grounding = build_judge_messages(field, code, year, field_value, provenance, spec, unit_label)
    if messages is None:
        return {"verdict": "unknown", "confidence": 0.0, "issues": [],
                "summary": "溯源+RAG均无源文，无法语义核对", "field": field,
                "_system": _sys_text("judge") if debug else None}
    raw = chat(messages, role="judge", temperature=0.1, model=resolve_model("judge"))
    verdict = _extract_json(raw)
    verdict["field"] = field
    verdict["grounding"] = grounding
    if debug:                                   # 给调试台看"我是怎么跟 LLM 对话的"
        verdict["_system"] = messages[0]["content"]
        verdict["_prompt"] = messages[1]["content"]
        verdict["_raw"] = raw
    return verdict


# ── 复核 agent（绿灯专用）──
# 与 judge_field 是"反立场兄弟"：judge_field 面对锚对不上的可疑数据、默认疑、找病根；
# 本 agent 面对锚已过的绿灯、默认信、只审锚证明不了的盲区。锚不再是"过就免审"——绿灯全量
# 过这个 agent，它点头(pass)才算真过。跨表锚只证明了"表选对 + 至少一维金额合计≈营业收入"，
# 对"其他维度完整性 / 摘行质量 / '其中'重复计数 / 名称脏 / 占比合理"零覆盖，正是这里要审的。

def _field_prompt_notes(field: str, spec=None) -> str:
    """字段准则注入 verify/judge prompt，减少 LLM 对表形态的误判。"""
    from src.eval.field_spec import get_spec
    sp = spec or get_spec(field)
    lines = []
    if sp.spec_note:
        lines.append(f"【字段准则】{sp.spec_note}")
    if field == "revenue_breakdown" and sp.categories:
        cats = "、".join(sp.categories)
        lines.append(
            f"【表形态】营收构成表**常见**在同一张表里依次列出 {cats} 四段维度标记——"
            f"这是正常形态，**不得**因「一张表里有多个维度段」就判 wrong_table。"
        )
        markers = "、".join(sp.table_markers) if sp.table_markers else "占营业收入比重"
        lines.append(f"【认表 marker】{markers}")
        lines.append(
            "【wrong_table 指】表头主列是营业成本/毛利率/销售情况/签约额等，或整表口径不是营业收入构成——"
            "不是「维度段超过一个」。"
        )
    return ("\n" + "\n".join(lines) + "\n") if lines else ""


def build_verify_messages(field: str, code: str, year: int, field_value, sig: Dict,
                          provenance: Dict = None, spec=None, unit_label: str = None,
                          source_override: str = None, extra_note: str = None):
    """构造发给复核 agent 的 messages。返回 (messages|None, grounding)。
    sig: 该字段的 field_plausibility 信号（带 anchor / parsed_total，用来告诉 agent 锚证明了什么）。
    source_override: 选表自愈后传入"纠正后那张表"的网格文本，绕过 _source_grid(否则会重挑回错表)。
    extra_note: 追加提示（如选表自愈后"表已确认、只核数据"的信任提示）。"""
    prov = _ensure_prov(field, code, year, field_value, provenance, spec)
    if source_override:
        source, grounding = source_override, "选表自愈表"
    else:
        source, grounding = _resolve_source_text(code, year, field, prov)
    if not source:
        return None, grounding
    anchor = (sig or {}).get("anchor")
    main_anchor = (sig or {}).get("main_anchor")
    anchor_note = ""
    if anchor:
        anchor_note = (f"\n【锚参考】权威营业收入≈{anchor:,.0f} 元。跨表锚只证明“**被解析出的那些维度**金额合计≈营收”，"
                       f"**不证明表身份对、也不证明维度齐全**——请照第一步 A/B 做体检；被解析维度的总额不必再校。\n")
        if main_anchor:
            anchor_note += (f"【主营口径】主营业务收入≈{main_anchor:,.0f} 元(= 营业收入 − 其他业务收入)。会计准则里维度构成表"
                            f"披露的是**主营业务**分行业/产品/地区,所以**分项和≈主营(而非≈营收)是完整的正确形态**——差的那块"
                            f"是其他业务收入,准则允许不按维度拆。**分项和≈主营时不要判 cross_page/不完整**;只有连主营都明显对不上、"
                            f"或某分项行明显缺失(如源文有 X 行而解析漏了),才算截断/漏行。\n")
    unit_note = ""
    if unit_label:
        unit_note = (f"\n【单位提示】源文金额单位为「{unit_label}」；解析结果已换算为「元」，"
                     f"对照前先换算，别因单位不同误判。\n")
    if extra_note:
        unit_note += extra_note
    from src.eval.field_spec import get_spec
    sp = spec or get_spec(field)
    built = build_messages("verify", {
        "grounding": grounding, "source": source, "anchor_note": anchor_note, "unit_note": unit_note,
        "field": field, "field_label": sp.label or field, "field_spec_note": _field_prompt_notes(field, sp),
        "field_value_json": json.dumps(field_value, ensure_ascii=False, indent=2),
    })
    return built["messages"], grounding


def verify_field(field: str, code: str, year: int, field_value, sig: Dict = None,
                 provenance: Dict = None, spec=None, debug: bool = False, unit_label: str = None,
                 source_override: str = None, extra_note: str = None) -> Dict:
    """复核 agent：对**锚已过的绿灯**逐字段复核锚的盲区。pass=真可信→可入库；hold=有疑点→送人审。
    无源文可对照时判 unknown（既不放行也不拦，交回人工），不因缺证据误杀绿灯。
    source_override: 选表自愈后拿"纠正后那张表"当源文；extra_note: 追加信任提示。"""
    messages, grounding = build_verify_messages(field, code, year, field_value, sig, provenance, spec,
                                                unit_label, source_override=source_override,
                                                extra_note=extra_note)
    if messages is None:
        return {"verdict": "unknown", "suspects": [], "field": field,
                "summary": "溯源+RAG均无源文，无法复核", "_system": _sys_text("verify") if debug else None}
    raw = chat(messages, role="judge", temperature=0, model=resolve_model("verify"))   # 复核要可复现,temp=0
    v = _extract_json(raw)
    # 复核 agent 的裁决字段是 verdict=pass|hold；_extract_json 失败会给 verdict=unknown，透传即可
    v.setdefault("suspects", v.get("issues", []))
    v["field"] = field
    v["grounding"] = grounding
    if debug:
        v["_system"] = messages[0]["content"]
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
