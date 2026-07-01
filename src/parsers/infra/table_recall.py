"""向量召回选表 —— 选表解耦的第一段。

职责：只负责"广而稳地找候选表"（recall），**不负责精判**（哪张是营收、哪列是金额 → 交给锚）。
做法：每张表去掉数字、只留文字（表头 + 行名 + 维度标记）→ BGE 语义嵌入 → 跟字段"意图参照"比余弦 →
      top-k 过阈值的作为候选。
为什么去数字：数字是语义噪音，且同类表不同公司数字天差地别；去掉后"同类表"才彼此相似（已实测验证）。

参照 query 目前是手写"意图"；后续可换成"确认过的真实表头样例"集合（会随认证长大的库）。
BGE 不可用 / 无参照 / 无表 → 优雅退回原表，绝不阻断主流程。
"""

import re
from typing import List, Dict

# 字段 → 语义参照（意图 query）。与 llm_judge._QUERIES 同源，后续可换成真实样例集。
_FIELD_QUERY = {
    "revenue": "营业收入构成 分行业 分产品 分地区 分销售模式 占营业收入比重",
    "cost": "营业成本构成 分行业 分产品 占营业成本比重 原材料 人工 折旧",
    "rnd": "研发费用 明细 职工薪酬 折旧摊销 合计",
    "client": "前五名客户 客户名称 销售额 占年度销售总额比例",
    "supplier": "前五名供应商 供应商名称 采购额 占年度采购总额比例",
    "employee": "员工 专业构成 教育程度 在职员工人数",
}


def _table_textdoc(table: List[list], caption: str = "") -> str:
    """把一张表压成"纯文字文档"：标题(caption)前置 + 表内去数字/百分比/年份、只留 ≥2 汉字的单元格，去重保序。
    caption 是表格上文标题（如'（1）营业收入构成'），最点题 → 放最前，既加权语义又保证不被 300 字截掉。"""
    if not table and not caption:
        return ""
    cells = []
    cap = (caption or "").strip()
    if cap:
        cells.append(cap)                          # 标题最点题，放最前
    for row in (table or []):
        for c in row:
            if not c:
                continue
            core = re.sub(r"[\d.,%()（）\-—\s]", "", c).replace("年", "")
            if len(re.findall(r"[一-鿿]", core)) >= 2:
                cells.append(c.strip())
    seen = set()
    uniq = [x for x in cells if not (x in seen or seen.add(x))]
    return " ".join(uniq)[:300]


def vector_recall(tables: List[Dict], field: str = "revenue",
                  top_k: int = 6, threshold: float = 0.5) -> List[Dict]:
    """向量召回候选表。

    Args:
        tables: pre_scan 的表项列表，每项含 "table"(二维网格) 等键。
        field:  revenue/cost/rnd/client/supplier/employee。
        top_k:  最多返回几张候选。
        threshold: 相似度下限（过滤明显不相关）。

    Returns: [{**表项, "recall_score": float}] 按分降序（过阈值的；全被刷掉则退回按分排序的 top_k）。
             BGE 不可用 / 无参照 → 原样返回 tables（不阻断）。
    """
    query = _FIELD_QUERY.get(field)
    if not tables or not query:
        return tables
    try:
        from src.validators.vector_validator import _embed
        from sklearn.metrics.pairwise import cosine_similarity
        docs = [_table_textdoc(t.get("table"), t.get("caption", "")) for t in tables]
        qv = _embed([query])
        dv = _embed(docs)
        sims = cosine_similarity(qv, dv)[0]
    except Exception:
        return tables    # BGE 挂了/模型缺失 → 不阻断
    ranked = sorted(
        ({**t, "recall_score": round(float(s), 4)} for t, s in zip(tables, sims)),
        key=lambda x: -x["recall_score"])
    keep = [t for t in ranked if t["recall_score"] >= threshold]
    return (keep or ranked)[:top_k]


# ── 选表解耦第二段：锚精判 ──

_ANCHOR_KEY = {"revenue": "revenue", "cost": "cost", "rnd": "rnd_expense"}
_UNIT_MULTS = (1, 1000, 10000, 100000000)   # 元/千元/万元/亿元 —— 锚本身能反推是哪种


def _col_values(table: List[list], ci: int) -> list:
    from src.parsers.infra.table_scanner import parse_money
    out = []
    for row in table:
        if ci < len(row) and row[ci]:
            m = parse_money(row[ci])
            if m is not None:
                out.append(m)
    return out


def _best_col_vs_anchor(table: List[list], anchor: float):
    """这张表里"最能解释 anchor"的数字列 + 相对误差(0最好)。
    检查每列(试单位倍数): 含≈anchor的值(合计行/单维大值) 或 列和≈k*anchor(k=1..6,多维堆叠)。"""
    ncols = max((len(r) for r in table), default=0)
    best_col, best_rel = None, 1e18
    for ci in range(ncols):
        vals = _col_values(table, ci)
        if not vals:
            continue
        for mult in _UNIT_MULTS:
            scaled = [v * mult for v in vals]
            for v in scaled:                                   # 合计行/单维大值 ≈ 锚
                rel = abs(v - anchor) / anchor
                if rel < best_rel:
                    best_rel, best_col = rel, ci
            s = sum(scaled)                                    # 多维堆叠:列和 ≈ k*锚
            for k in range(1, 7):
                rel = abs(s - k * anchor) / anchor
                if rel < best_rel:
                    best_rel, best_col = rel, ci
    return best_col, best_rel


def anchor_select(tables: List[Dict], code: str, year: int,
                  field: str = "revenue", tol: float = 0.03) -> List[Dict]:
    """锚精判:召回候选里,哪张表的哪列数字能解释锚(营业收入/成本) → 定表+定金额列。
    返回按"对锚误差"升序的候选 [{**表项, amount_col, anchor_rel, matched}];无锚返回 None(交回退)。"""
    from src.eval.anchors import get_anchors
    key = _ANCHOR_KEY.get(field)
    anchor = (get_anchors(code, year) or {}).get(key) if (key and code and year) else None
    if not anchor:
        return None
    scored = []
    for t in tables:
        grid = t.get("table")
        if not grid:
            continue
        col, rel = _best_col_vs_anchor(grid, anchor)
        scored.append({**t, "amount_col": col, "anchor_rel": round(rel, 4), "matched": rel <= tol})
    scored.sort(key=lambda x: x["anchor_rel"])
    return scored


def _dimension_map() -> dict:
    from src.parsers.infra.rule_loader import load_rule
    dims = ((load_rule("revenue") or {}).get("revenue_breakdown", {}) or {}).get("dimensions") or {}
    return dims or {"分行业": "industries", "分产品": "segments", "分地区": "regions", "分销售模式": "by_channel"}


def _dimension_count(table: List[list]) -> int:
    """表覆盖了几个不同维度(industries/segments/regions/by_channel)。
    标准营收构成表覆盖多个(2~4);附注里的单一分类收入表只覆盖1个 → 用来区分,选覆盖最多的。"""
    dmap = _dimension_map()
    found = set()
    for row in (table or []):
        for c in row:
            if not c:
                continue
            for marker, dim in dmap.items():
                if marker in c:
                    found.add(dim)
    return len(found)


def select_table(tables: List[Dict], code: str, year: int,
                 field: str = "revenue", tol: float = 0.03):
    """选表解耦全流程:① 向量召回候选 → ② 锚精判定表定列（营收/成本再按"覆盖维度数"闸,区分构成表 vs 附注单一分类表）。
    返回 {table_item, amount_col, anchor_rel, matched, dim_count, via} 或 None(召回空)。"""
    recalled = vector_recall(tables, field, top_k=8, threshold=0.0)
    if not recalled:
        return None
    judged = anchor_select(recalled, code, year, field, tol)
    if judged is None:                       # 无锚 → 退回召回第一名(纯语义)
        top = recalled[0]
        return {**top, "amount_col": None, "anchor_rel": None, "matched": None, "via": "recall_only"}
    if field in ("revenue", "cost"):
        for c in judged:
            c["dim_count"] = _dimension_count(c.get("table"))
        # 候选池:对锚够近(放宽到5%容口径差) → 里面选"覆盖维度最多"的,平票取对锚最近
        pool = [c for c in judged if c["anchor_rel"] <= 0.05 and c["dim_count"] >= 1] or judged
        pool.sort(key=lambda c: (-c["dim_count"], c["anchor_rel"]))
        return {**pool[0], "via": "recall+anchor+dims"}
    return {**judged[0], "via": "recall+anchor"}
