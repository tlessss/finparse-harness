"""
修复沙箱 — Phase 2 验证或回滚

让任何"自主修复"都可信：候选修复必须在**重解析后硬规则严格变好**才被接受，
否则回滚。这是"正确率优先 / 绝不把对的改坏"的执行机制。

核心思想：
  before = 校验(用基线规则解析)
  for 每个候选规则:
      after = 校验(用候选规则解析)
      若 after 严格优于 before  → 候选可接受
  取最优候选；无人为干预即可决定 accept / rollback。

比较逻辑（fix_outcome）是纯函数，可脱离 PDF 单测。

用法：
  from src.agents.sandbox import run_sandbox
  out = run_sandbox(parse_fn, base_rule, candidate_rules)
  if out["accepted"]:
      apply(out["best"]["rule"])   # 持久化获胜规则
"""

from typing import Callable, Dict, List, Optional

from src.validators.hard_rules import check_hard_rules


def fix_outcome(before: Dict, after: Dict) -> str:
    """
    判断候选修复相对基线是 accept 还是 reject（纯函数）。

    accept 条件（满足其一）：
      - 从未通过 → 通过
      - red 违规数严格减少（且不新增 red 字段）
    其余一律 reject（包括打平、变差、把通过改成不通过）。
    """
    b_pass, a_pass = before.get("passed", False), after.get("passed", False)
    b_red, a_red = before.get("red_count", 0), after.get("red_count", 0)
    b_fields = set(before.get("red_fields", []))
    a_fields = set(after.get("red_fields", []))

    # 绝不接受"把已通过的改成不通过"或引入新红线字段
    if b_pass and not a_pass:
        return "reject"
    if a_fields - b_fields:        # 出现了原来没有的红线字段
        return "reject"

    if not b_pass and a_pass:
        return "accept"
    if a_red < b_red:
        return "accept"
    return "reject"


def run_sandbox(parse_fn: Callable[[Dict], Dict],
                base_rule: Dict,
                candidate_rules: List[Dict],
                validator: Callable[[Dict], Dict] = check_hard_rules) -> Dict:
    """
    在沙箱中评估候选规则，返回最优可接受方案（不做任何持久化）。

    Args:
        parse_fn: rule -> parse_result（重解析；调用方注入，便于测试/隔离）
        base_rule: 当前生效规则
        candidate_rules: 候选规则列表
        validator: 校验函数（默认硬规则）

    Returns:
        {
          "before": <hard_report>,
          "accepted": bool,
          "best": {"rule": dict, "after": <hard_report>, "index": int} | None,
          "evaluated": int,
        }
    """
    before = validator(parse_fn(base_rule))
    best = None
    for i, cand in enumerate(candidate_rules):
        try:
            after = validator(parse_fn(cand))
        except Exception:
            continue
        if fix_outcome(before, after) != "accept":
            continue
        if best is None or after.get("red_count", 0) < best["after"].get("red_count", 0):
            best = {"rule": cand, "after": after, "index": i}

    return {
        "before": before,
        "accepted": best is not None,
        "best": best,
        "evaluated": len(candidate_rules),
    }


def propose_exclude_candidates(base_rule: Dict, parse_result: Dict,
                               section: str = "revenue_section") -> List[Dict]:
    """
    为"占比之和偏高（过度计数）"类错误生成候选规则：
    把营收分项里疑似多余的行名逐个加入 extra_exclude_names。

    仅适用于 sum>100 的过度计数场景；漏行(<100)无法靠排除修复。
    """
    rev = parse_result.get("revenue_breakdown") or {}
    candidates = []
    base_excl = (base_rule.get(section, {}) or {}).get("extra_exclude_names", [])
    for dim in ("segments", "industries", "regions"):
        items = rev.get(dim) or []
        ratios = [i.get("ratio_pct") for i in items if i.get("ratio_pct") is not None]
        if not ratios or sum(ratios) <= 102:
            continue
        # 占比最大的行最可能是混入的合计/总项
        for it in sorted(items, key=lambda x: -(x.get("ratio_pct") or 0))[:3]:
            name = (it.get("name") or "").strip()
            if not name or name in base_excl:
                continue
            new_rule = {**base_rule}
            sec = dict(new_rule.get(section, {}) or {})
            sec["extra_exclude_names"] = base_excl + [name]
            new_rule[section] = sec
            candidates.append(new_rule)
    return candidates
