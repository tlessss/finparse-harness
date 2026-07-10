"""超级管家·诊断探针 —— 确定性地为一份失败报告收集"分层证据",喂给管家(强模型)做根因归因。

每个探针只取**事实**、不下结论(结论交给 steward_diagnose 的 LLM 分层推理)。复用现有
select_table / heal_debug / extract_heal / _diagnose_category,不重造。探针全 try/except 兜底,
任何一层挂了返回 error 字段、不阻断整体取证。

分层(自顶向下 = 流水线顺序):选表 → 抽表 → 解析 → 锚 → 路由/自愈。
"""

from typing import Dict, List

_SIG = {"revenue_breakdown": "revenue", "cost_breakdown": "cost", "rnd_info": "rnd"}


def probe_selection(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """选表层:选中哪张表、锚对不对(某列合计≈营收锚=选对表的强信号)、表有多少行。"""
    try:
        from src.parsers.infra.table_recall import select_table, _best_col_vs_anchor
        from src.eval.table_cache import get_tables
        from src.eval.anchors import get_anchors
        sf = _SIG.get(field, "revenue")
        anchor = (get_anchors(code, year) or {}).get(sf)
        sel = select_table(get_tables(code, year), code, year, sf) or {}
        table = sel.get("table") or []
        rel = None
        if table and anchor:
            _, rel = _best_col_vs_anchor(table, anchor)
        return {"chosen_page": sel.get("page"), "caption": (sel.get("caption") or "")[:60],
                "via": sel.get("via"), "n_rows": len(table), "anchor": anchor,
                "anchor_col_rel": round(rel, 4) if rel is not None else None,
                "anchor_matches": bool(rel is not None and rel <= 0.05)}
    except Exception as e:
        return {"error": str(e)[:100]}


def probe_parse(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """解析层:逐维度输出(n / 和 / 占锚)。"""
    try:
        from src.console_service import heal_debug
        d = heal_debug(code, year, field) or {}
        a = d.get("anchor")
        dims = {}
        for x in (d.get("dims") or []):
            s = x.get("sum") or 0
            dims[x.get("dim")] = {"n": x.get("n"), "rel": round(s / a, 3) if (a and s) else 0.0}
        return {"anchor": a, "dims": dims,
                "empty_dims": [k for k, v in dims.items() if not v["n"]],
                "all_empty": (not dims) or all(not v["n"] for v in dims.values())}
    except Exception as e:
        return {"error": str(e)[:100]}


def probe_extraction(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """抽表层(最关键):期望的维度数据行,在**任何**已抽出的表里存在吗?
    data_extracted=false = pdfplumber 把表抽残了(数据在 PDF 但没进任何表)→ 抽表层锅,不是选表/解析。"""
    try:
        from src.eval.table_cache import get_tables
        markers = ["分行业", "分产品", "分地区", "分销售", "按行业", "按产品", "按地区"]
        tables = get_tables(code, year) or []
        found: List[Dict] = []
        for t in tables:
            rows = t.get("table") or []
            flat = " ".join(str(c) for r in rows for c in r if c)
            hits = [m for m in markers if m in flat]
            if not hits:
                continue
            n_data = sum(1 for r in rows
                         if any(c and any(ch.isdigit() for ch in str(c)) for c in r)
                         and any(c and any('一' <= ch <= '鿿' for ch in str(c)) for c in r))
            found.append({"page": t.get("page"), "n_rows": len(rows), "markers": hits, "n_data_rows": n_data})
        return {"tables_with_dim_markers": found,
                "data_extracted": any(f["n_data_rows"] >= 3 for f in found)}
    except Exception as e:
        return {"error": str(e)[:100]}


def probe_reextract(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """L3探针:换 pdfplumber 参数 / camelot 重抽选中页,能不能把数据抽出来、过锚?(不发 LLM)
    recovers=true 但路由没触发 L3 = 路由漏;换参也救不回 = 抽表能力缺口(需视觉/更强)。"""
    try:
        from src.parsers.infra.table_recall import select_table
        from src.eval.table_cache import get_tables
        from src.agents.extract_heal_agent import extract_heal
        sf = _SIG.get(field, "revenue")
        sel = select_table(get_tables(code, year), code, year, sf) or {}
        chosen = {"page": sel.get("page"), "caption": sel.get("caption"), "table": sel.get("table")}
        eh = extract_heal(code, year, field, chosen, debug=False)
        return {"outcome": eh.get("outcome"), "profile": eh.get("profile"),
                "recovers": eh.get("outcome") == "fixed"}
    except Exception as e:
        return {"outcome": "error", "error": str(e)[:100], "recovers": False}


def probe_route(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """路由/自愈层:归到哪一类、该类在当前级联会不会触发 L3 / codegen。"""
    try:
        from src.pipeline import _diagnose_category, _L3_CATS, _CODEGEN_CATS
        cat = _diagnose_category(code, year, field)
        return {"category": cat, "routes_to_L3": cat in _L3_CATS, "routes_to_codegen": cat in _CODEGEN_CATS}
    except Exception as e:
        return {"error": str(e)[:100]}


def collect_dossier(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """一次跑齐全部探针 → 分层证据档案(喂给 steward_diagnose)。"""
    return {
        "code": code, "year": year, "field": field,
        "selection": probe_selection(code, year, field),
        "extraction": probe_extraction(code, year, field),
        "parse": probe_parse(code, year, field),
        "routing": probe_route(code, year, field),
        "reextract_probe": probe_reextract(code, year, field),
    }
