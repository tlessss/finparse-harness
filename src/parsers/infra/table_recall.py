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


def _table_textdoc(table: List[list]) -> str:
    """把一张表压成"纯文字文档"：去数字/百分比/年份，只留有 ≥2 汉字的单元格，去重保序。"""
    if not table:
        return ""
    cells = []
    for row in table:
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
        docs = [_table_textdoc(t.get("table")) for t in tables]
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
