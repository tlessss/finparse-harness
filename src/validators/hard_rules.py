"""
关键字段勾稽硬规则 — Phase 0.4 红线校验

设计原则（已与用户拍板）：
  正确率优先。这些规则是**不可被 LLM 绕过的红线**：
  只要触发 red 级违规，无论校验 Agent（LLM/向量）怎么判，该字段数据一律视为错误。

覆盖的硬勾稽：
  - 营收结构：分产品/分行业/分地区 各维度占比之和 ≈ 100%
  - 研发费用：明细 amount_this 之和 ≈ total_this（合计）
  - 员工数据：专业构成人数之和 = 总数；教育程度人数之和 = 总数
  - 取值合法性：占比 ∈ [0,100]、人数/金额非负

严重级别：
  - "red"  : 数学上不可能/明显错误 → 字段判错（阻断入库）
  - "warn" : 可疑但不绝对 → 不阻断，仅标记供复核

用法：
  from src.validators.hard_rules import check_hard_rules
  report = check_hard_rules(parse_result)
  if not report["passed"]:
      ...  # 存在 red 违规，数据不可信
"""

from typing import Dict, List, Optional


# ── 容差配置 ──
RATIO_SUM_OK = (98.0, 102.0)      # 占比之和：clean 区间
RATIO_SUM_WARN = (90.0, 110.0)    # 占比之和：warn 区间（超出即 red）
RATIO_MIN_ITEMS = 3                # 占比之和校验所需的最少分项数（太少无法下结论）

RND_DIFF_OK = 1.0                  # 研发明细 vs 合计：差异 % ≤ 此值为 clean
RND_DIFF_WARN = 5.0               # 差异 % ≤ 此值为 warn（超出即 red）

EMP_DIFF_OK = 0                    # 员工人数：差异 = 0 为 clean
EMP_DIFF_WARN = 2                  # |差异| ≤ 此值为 warn（超出即 red）


def _violation(field: str, rule: str, severity: str, detail: str,
               expected=None, actual=None) -> Dict:
    return {
        "field": field,
        "rule": rule,
        "severity": severity,
        "detail": detail,
        "expected": expected,
        "actual": actual,
    }


def _check_revenue(rev: Dict) -> List[Dict]:
    """营收：各维度占比之和 ≈ 100%，且占比 ∈ [0,100]。"""
    violations = []
    if not isinstance(rev, dict):
        return violations

    dim_labels = {"segments": "分产品", "industries": "分行业", "regions": "分地区",
                  "by_channel": "分销售模式"}
    for dim, label in dim_labels.items():
        items = rev.get(dim) or []
        if not items:
            continue

        ratios = []
        for it in items:
            r = it.get("ratio_pct")
            if r is None:
                continue
            # 取值合法性红线：占比不可能 <0 或 >100
            if r < 0 or r > 100:
                violations.append(_violation(
                    f"revenue_breakdown.{dim}", "ratio_range", "red",
                    f"{label} 存在非法占比 {r}%（应 ∈ [0,100]）",
                    expected="[0,100]", actual=r,
                ))
            else:
                ratios.append(r)

        # 占比之和勾稽（分项数足够才下结论）
        if len(ratios) >= RATIO_MIN_ITEMS:
            total = round(sum(ratios), 2)
            if RATIO_SUM_OK[0] <= total <= RATIO_SUM_OK[1]:
                pass  # clean
            elif RATIO_SUM_WARN[0] <= total <= RATIO_SUM_WARN[1]:
                violations.append(_violation(
                    f"revenue_breakdown.{dim}", "ratio_sum", "warn",
                    f"{label} 占比之和 {total}%（轻微偏离 100%）",
                    expected="≈100%", actual=total,
                ))
            else:
                violations.append(_violation(
                    f"revenue_breakdown.{dim}", "ratio_sum", "red",
                    f"{label} 占比之和 {total}%（严重偏离 100%，疑似漏行/重复/合计行混入）",
                    expected="≈100%", actual=total,
                ))
    return violations


def _check_cost(cost) -> List[Dict]:
    """成本：占成本比重之和 ≈ 100%（扁平列表），占比 ∈ [0,100]。"""
    violations = []
    if not isinstance(cost, list):
        return violations
    ratios = []
    for it in cost:
        r = it.get("ratio_pct")
        if r is None:
            continue
        if r < 0 or r > 100:
            violations.append(_violation(
                "cost_breakdown", "ratio_range", "red",
                f"成本构成存在非法占比 {r}%（应 ∈ [0,100]）", expected="[0,100]", actual=r))
        else:
            ratios.append(r)
    if len(ratios) >= RATIO_MIN_ITEMS:
        total = round(sum(ratios), 2)
        if RATIO_SUM_OK[0] <= total <= RATIO_SUM_OK[1]:
            pass
        elif RATIO_SUM_WARN[0] <= total <= RATIO_SUM_WARN[1]:
            violations.append(_violation(
                "cost_breakdown", "ratio_sum", "warn",
                f"成本构成占比之和 {total}%（轻微偏离 100%）", expected="≈100%", actual=total))
        else:
            violations.append(_violation(
                "cost_breakdown", "ratio_sum", "red",
                f"成本构成占比之和 {total}%（严重偏离，疑似漏行/合计行混入）",
                expected="≈100%", actual=total))
    return violations


def _check_rnd(rnd: Dict) -> List[Dict]:
    """研发：明细 amount_this 之和 ≈ total_this。"""
    violations = []
    if not isinstance(rnd, dict):
        return violations

    total = rnd.get("total_this")
    details = rnd.get("rnd_detail") or []

    # 合计非负红线
    if total is not None and total < 0:
        violations.append(_violation(
            "rnd_info.total_this", "non_negative", "red",
            f"研发合计为负 {total}", expected="≥0", actual=total,
        ))

    amounts = [d.get("amount_this") for d in details if d.get("amount_this") is not None]
    if total and total > 0 and amounts:
        detail_sum = sum(amounts)
        diff_pct = abs(detail_sum - total) / total * 100
        if diff_pct <= RND_DIFF_OK:
            pass  # clean
        elif diff_pct <= RND_DIFF_WARN:
            violations.append(_violation(
                "rnd_info.rnd_detail", "sum_vs_total", "warn",
                f"研发明细之和 {detail_sum:.0f} vs 合计 {total:.0f}（差异 {diff_pct:.2f}%）",
                expected=round(total, 0), actual=round(detail_sum, 0),
            ))
        else:
            violations.append(_violation(
                "rnd_info.rnd_detail", "sum_vs_total", "red",
                f"研发明细之和 {detail_sum:.0f} vs 合计 {total:.0f}（差异 {diff_pct:.2f}%，疑似漏项/单位错位）",
                expected=round(total, 0), actual=round(detail_sum, 0),
            ))
    return violations


def _check_employees(emp: Dict) -> List[Dict]:
    """员工：专业构成、教育程度人数之和 = 总数。"""
    violations = []
    if not isinstance(emp, dict):
        return violations

    total = emp.get("total")
    if total is not None and total < 0:
        violations.append(_violation(
            "employees.total", "non_negative", "red",
            f"员工总数为负 {total}", expected="≥0", actual=total,
        ))

    if total and total > 0:
        for dim, label in [("composition", "专业构成"), ("education", "教育程度")]:
            items = emp.get(dim) or []
            counts = [c.get("count") for c in items if c.get("count") is not None]
            if not counts:
                continue
            s = sum(counts)
            diff = abs(s - total)
            if diff <= EMP_DIFF_OK:
                pass  # clean
            elif diff <= EMP_DIFF_WARN:
                violations.append(_violation(
                    f"employees.{dim}", "count_sum", "warn",
                    f"{label}之和 {s} vs 总数 {total}（差 {diff}）",
                    expected=total, actual=s,
                ))
            else:
                violations.append(_violation(
                    f"employees.{dim}", "count_sum", "red",
                    f"{label}之和 {s} vs 总数 {total}（差 {diff}，疑似漏行/误匹配）",
                    expected=total, actual=s,
                ))
    return violations


def check_hard_rules(parse_result: Dict) -> Dict:
    """
    对一次解析结果执行全部关键字段勾稽硬规则。

    Returns:
        {
          "passed": bool,              # 无 red 违规 = True
          "red_count": int,
          "warn_count": int,
          "violations": [ ... ],       # 含 red 和 warn
          "red_fields": [str],         # 触发 red 的字段（去重，顶层字段名）
        }
    """
    violations: List[Dict] = []
    violations += _check_revenue(parse_result.get("revenue_breakdown"))
    violations += _check_cost(parse_result.get("cost_breakdown"))
    violations += _check_rnd(parse_result.get("rnd_info"))
    violations += _check_employees(parse_result.get("employees"))

    red = [v for v in violations if v["severity"] == "red"]
    warn = [v for v in violations if v["severity"] == "warn"]
    red_fields = sorted({v["field"].split(".")[0] for v in red})

    return {
        "passed": len(red) == 0,
        "red_count": len(red),
        "warn_count": len(warn),
        "violations": violations,
        "red_fields": red_fields,
    }
