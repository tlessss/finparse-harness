"""
经验数据库 — 记录解析失败模式和修复方案

每次修复完一个问题，记录到经验库：
  - 失败模式（哪个字段、什么特征、受影响股票）
  - 修复方案（改哪个文件、什么函数、怎么改）
  - 修复效果（前/后字段数）

下次遇到同样问题直接查经验库，不用重新跑校验。
"""

import json
import os
import time
from typing import List, Dict, Optional
from pathlib import Path


# 经验库文件路径
_EXPERIENCE_FILE = Path(__file__).parent.parent / "experience_db.json"


def load_experiences() -> List[Dict]:
    if _EXPERIENCE_FILE.exists():
        with open(_EXPERIENCE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_experiences(exps: List[Dict]):
    with open(_EXPERIENCE_FILE, "w", encoding="utf-8") as f:
        json.dump(exps, f, ensure_ascii=False, indent=2)


def record_fix(
    stock_code: str,
    report_year: int,
    field: str,
    issue_type: str,
    root_file: str,
    root_function: str,
    fix_summary: str,
    before_field_count: int,
    after_field_count: int,
):
    """记录一次修复经验"""
    exps = load_experiences()

    # 检查是否已有相同记录
    for exp in exps:
        if (exp.get("stock_code") == stock_code and
            exp.get("field") == field and
            exp.get("root_function") == root_function):
            # 更新存在次数和最新效果
            exp["hit_count"] = exp.get("hit_count", 1) + 1
            exp["last_fixed"] = time.strftime("%Y-%m-%d %H:%M:%S")
            exp["after_field_count"] = after_field_count
            save_experiences(exps)
            return

    exps.append({
        "stock_code": stock_code,
        "report_year": report_year,
        "field": field,
        "issue_type": issue_type,
        "root_file": root_file,
        "root_function": root_function,
        "fix_summary": fix_summary,
        "before_field_count": before_field_count,
        "after_field_count": after_field_count,
        "hit_count": 1,
        "first_detected": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_fixed": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    save_experiences(exps)


def find_known_fix(stock_code: str, field: str) -> Optional[Dict]:
    """
    在经验库中查找已知的修复方案。

    匹配逻辑：
    - 完全匹配：同一股票 + 同一字段
    - 模糊匹配：不同股票 + 同一字段 + 相同 issue_type

    Returns:
        最匹配的经验条目
    """
    exps = load_experiences()
    if not exps:
        return None

    # 精确匹配（同股票+同字段）
    for exp in exps:
        if exp.get("stock_code") == stock_code and exp.get("field") == field:
            return exp

    # 模糊匹配（同字段+高频）
    field_matches = [e for e in exps if e.get("field") == field]
    if field_matches:
        # 按命中次数降序
        field_matches.sort(key=lambda x: -x.get("hit_count", 0))
        return field_matches[0]

    return None


def summarize() -> Dict:
    """输出经验库统计"""
    exps = load_experiences()
    field_stats = {}
    for exp in exps:
        f = exp.get("field", "?")
        if f not in field_stats:
            field_stats[f] = 0
        field_stats[f] += 1

    return {
        "total_experiences": len(exps),
        "field_distribution": field_stats,
        "top_fixes": sorted(
            [e for e in exps if e.get("hit_count", 0) > 1],
            key=lambda x: -x.get("hit_count", 0),
        )[:5],
    }
