"""
占比构成类(A类)字段打分器 — 字段通用(营收/成本…)

给定 某解析器版本的输出 vs 值级 golden，算出客观分数。纯函数、确定性、不碰 PDF。
按 FieldSpec(amount_key/dims) 同构处理多维字典(营收)与扁平列表(成本)。

用途(见 docs/多agent编排设计.md)：版本选优/沙箱闸/让LLM自改判对错/M0′干净数。
golden 与解析输出同构。

用法：
  from src.eval.revenue_score import score_field, score_revenue
  from src.eval.field_spec import REVENUE, COST
  s = score_field(COST, pred_cost, gold_cost)   # 通用
  s = score_revenue(pred_rb, gold_rb)            # 营收便捷(= score_field(REVENUE))
"""

from typing import Dict, List, Optional

from src.eval.field_spec import FieldSpec, REVENUE, as_dims

# 容差：金额按相对、占比按绝对百分点
_AMOUNT_REL_TOL = 0.01
_RATIO_ABS_TOL = 0.5


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


def _amount_close(a, b) -> bool:
    a, b = _num(a), _num(b)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= _AMOUNT_REL_TOL * max(abs(a), abs(b), 1.0)


def _ratio_close(a, b) -> bool:
    a, b = _num(a), _num(b)
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= _RATIO_ABS_TOL


def score_dimension(pred_rows: List[Dict], gold_rows: List[Dict],
                    amount_key: str = "revenue_yuan", ratio_key: str = "ratio_pct") -> Dict:
    """单维度打分：按名称匹配行，再逐值比对（金额相对容差、占比绝对容差）。"""
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
        amt_ok = _amount_close(prow.get(amount_key), grow.get(amount_key))
        rat_ok = _ratio_close(prow.get(ratio_key), grow.get(ratio_key))
        if amt_ok and rat_ok:
            value_ok += 1
        else:
            mismatches.append({
                "name": grow.get("name"), "issue": "值不符",
                "金额": None if amt_ok else {"输出": prow.get(amount_key), "真值": grow.get(amount_key)},
                "占比": None if rat_ok else {"输出": prow.get(ratio_key), "真值": grow.get(ratio_key)},
            })
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


def _score_b(spec: FieldSpec, pred, gold) -> Dict:
    """B类(明细和≈合计)打分：比合计 + 按名称比明细金额。合计计为一项。"""
    pred, gold = pred or {}, gold or {}
    sd = score_dimension(pred.get(spec.detail_key), gold.get(spec.detail_key),
                         spec.amount_key, spec.ratio_key)
    total_ok = _amount_close(pred.get(spec.total_key), gold.get(spec.total_key))
    mismatches = [{"dim": spec.detail_key, **m} for m in sd["mismatches"]]
    if not total_ok:
        mismatches.append({"dim": "total", "issue": "合计不符",
                           "金额": {"输出": pred.get(spec.total_key), "真值": gold.get(spec.total_key)}})
    n_items = sd["n_gold"] + 1                       # 明细项 + 合计
    correct = sd["value_ok"] + (1 if total_ok else 0)
    recall_value = correct / n_items if n_items else 0.0
    precision = sd["row_precision"]
    score = recall_value * precision
    return {"exact": (score >= 0.999 and not mismatches), "score": round(score, 4),
            "recall_value": round(recall_value, 4), "precision": round(precision, 4),
            "per_dim": {spec.detail_key: sd}, "mismatches": mismatches, "evaluated_dims": 1}


def _score_c(spec: FieldSpec, pred, gold) -> Dict:
    """C类(分项和=总数)打分：比总数 + 各维度按名称比人数。总数计为一项。"""
    pred, gold = pred or {}, gold or {}
    total_ok = _amount_close(pred.get(spec.total_key), gold.get(spec.total_key))
    per_dim, mismatches, value_ok, n_gold, precisions = {}, [], 0, 0, []
    for d in spec.dims:
        grows = gold.get(d) or []
        if not grows:
            continue
        sd = score_dimension(pred.get(d), grows, spec.amount_key, spec.ratio_key)
        per_dim[d] = sd
        value_ok += sd["value_ok"]
        n_gold += sd["n_gold"]
        precisions.append(sd["row_precision"])
        for m in sd["mismatches"]:
            mismatches.append({"dim": d, **m})
    if not total_ok:
        mismatches.append({"dim": "total", "issue": "总数不符",
                           "金额": {"输出": pred.get(spec.total_key), "真值": gold.get(spec.total_key)}})
    n_items = n_gold + 1                              # 各维度行 + 总数
    correct = value_ok + (1 if total_ok else 0)
    recall_value = correct / n_items if n_items else 0.0
    precision = sum(precisions) / len(precisions) if precisions else 1.0
    score = recall_value * precision
    return {"exact": (score >= 0.999 and not mismatches), "score": round(score, 4),
            "recall_value": round(recall_value, 4), "precision": round(precision, 4),
            "per_dim": per_dim, "mismatches": mismatches, "evaluated_dims": len(per_dim)}


def score_field(spec: FieldSpec, pred, gold) -> Dict:
    """字段通用打分。A类(占比构成)：评 golden 有内容的维度,均(召回×值正确率)×均精确率。
    B类(明细和≈合计)：比合计+明细金额。C类(分项和=总数)：比总数+各维度人数。"""
    if spec.cls == "B":
        return _score_b(spec, pred, gold)
    if spec.cls == "C":
        return _score_c(spec, pred, gold)
    pred_d, gold_d = as_dims(pred, spec), as_dims(gold, spec)
    per_dim, mismatches, recalls, precisions = {}, [], [], []
    for d, grows in gold_d.items():
        if not grows:
            continue
        sd = score_dimension(pred_d.get(d), grows, spec.amount_key, spec.ratio_key)
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
    return {
        "exact": (score >= 0.999 and not mismatches),
        "score": round(score, 4),
        "recall_value": round(recall_value, 4),
        "precision": round(precision, 4),
        "per_dim": per_dim, "mismatches": mismatches, "evaluated_dims": len(per_dim),
    }


def score_revenue(pred: Optional[Dict], gold: Optional[Dict], dims=None) -> Dict:
    """营收便捷入口（= score_field(REVENUE)）。"""
    return score_field(REVENUE, pred, gold)


_DIMS = REVENUE.dims      # 向后兼容
