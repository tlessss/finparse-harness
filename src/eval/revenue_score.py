"""
营收解析打分器 — "多解析器 × 多版本" 验证地基的心脏

给定 某解析器某版本的输出 vs 值级 golden（正确值本身），算出客观分数。
纯函数、确定性、不碰 PDF，可脱离环境单测。

用途（一鱼三吃，见 docs/多agent编排设计.md）：
  1. 沙箱/版本选优：哪个解析器版本对某份/某版式分最高 → registry 认证它
  2. 让 LLM 自改解析器：改完用它判对错，沙箱才能 accept/reject
  3. M0′ 拿干净数：认列对了才聚得准

golden 与解析输出**同构**（都是 revenue_breakdown 的 industries/segments/regions），
所以 seed 时可直接拷"已确认正确"的解析输出，无需另立格式。

用法：
  from src.eval.revenue_score import score_revenue
  s = score_revenue(parse_result["revenue_breakdown"], gold["revenue_breakdown"])
  s["exact"]      # 是否逐行逐值完全命中
  s["score"]      # 0~1 综合分
  s["mismatches"] # 逐条差异，给 LLM/人看哪错了
"""

from typing import Dict, List, Optional

_DIMS = ("industries", "segments", "regions", "by_channel")

# 容差：金额按相对、占比按绝对百分点
_REV_REL_TOL = 0.01      # 收入相对误差 ≤1%
_RATIO_ABS_TOL = 0.5     # 占比绝对误差 ≤0.5 个百分点


def _norm_name(s: Optional[str]) -> str:
    if not s:
        return ""
    return "".join(ch for ch in str(s) if ch not in " 　\t\n、,，()（）")


def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rev_close(a, b) -> bool:
    a, b = _num(a), _num(b)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= _REV_REL_TOL * max(abs(a), abs(b), 1.0)


def _ratio_close(a, b) -> bool:
    a, b = _num(a), _num(b)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= _RATIO_ABS_TOL


def score_dimension(pred_rows: List[Dict], gold_rows: List[Dict]) -> Dict:
    """单维度（如 industries）打分：按名称匹配行，再逐值比对。"""
    pred_rows = pred_rows or []
    gold_rows = gold_rows or []
    gold_by = {_norm_name(r.get("name")): r for r in gold_rows}
    pred_by = {_norm_name(r.get("name")): r for r in pred_rows}

    matched, value_ok, mismatches = 0, 0, []
    for gname, grow in gold_by.items():
        prow = pred_by.get(gname)
        if prow is None:
            mismatches.append({"name": grow.get("name"), "issue": "漏行(golden有,输出无)"})
            continue
        matched += 1
        rev_ok = _rev_close(prow.get("revenue_yuan"), grow.get("revenue_yuan"))
        rat_ok = _ratio_close(prow.get("ratio_pct"), grow.get("ratio_pct"))
        if rev_ok and rat_ok:
            value_ok += 1
        else:
            mismatches.append({
                "name": grow.get("name"),
                "issue": "值不符",
                "收入": None if rev_ok else {"输出": prow.get("revenue_yuan"), "真值": grow.get("revenue_yuan")},
                "占比": None if rat_ok else {"输出": prow.get("ratio_pct"), "真值": grow.get("ratio_pct")},
            })
    # 多抽的行（输出有，golden 无）
    for pname, prow in pred_by.items():
        if pname not in gold_by:
            mismatches.append({"name": prow.get("name"), "issue": "多行(输出有,golden无)"})

    n_gold, n_pred = len(gold_by), len(pred_by)
    return {
        "row_recall": matched / n_gold if n_gold else (1.0 if n_pred == 0 else 0.0),
        "row_precision": matched / n_pred if n_pred else (1.0 if n_gold == 0 else 0.0),
        "value_acc": value_ok / matched if matched else (1.0 if n_gold == 0 else 0.0),
        "n_gold": n_gold, "n_pred": n_pred, "matched": matched, "value_ok": value_ok,
        "mismatches": mismatches,
    }


def score_revenue(pred: Optional[Dict], gold: Optional[Dict],
                  dims=_DIMS) -> Dict:
    """
    整份营收打分：只评 golden 里有内容的维度（没标的维度不评，不冤枉）。

    Returns:
      {"exact": bool, "score": float, "per_dim": {dim: 维度分}, "mismatches": [...]}
      score = 各评估维度 (row_recall * value_acc) 的均值，再乘整体精确率惩罚多抽。
    """
    pred = pred or {}
    gold = gold or {}
    per_dim, mismatches = {}, []
    recalls, precisions = [], []
    for d in dims:
        if not (gold.get(d)):       # golden 这个维度没内容 → 不评
            continue
        sd = score_dimension(pred.get(d), gold.get(d))
        per_dim[d] = sd
        recalls.append(sd["row_recall"] * sd["value_acc"])
        precisions.append(sd["row_precision"])
        for m in sd["mismatches"]:
            mismatches.append({"dim": d, **m})

    if not per_dim:
        return {"exact": False, "score": 0.0, "per_dim": {},
                "mismatches": [{"issue": "golden 无任何可评维度"}], "evaluated_dims": 0}

    recall_value = sum(recalls) / len(recalls)
    precision = sum(precisions) / len(precisions)
    score = recall_value * precision
    exact = (score >= 0.999 and not mismatches)
    return {
        "exact": exact,
        "score": round(score, 4),
        "recall_value": round(recall_value, 4),
        "precision": round(precision, 4),
        "per_dim": per_dim,
        "mismatches": mismatches,
        "evaluated_dims": len(per_dim),
    }
