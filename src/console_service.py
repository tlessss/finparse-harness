"""
控制/审核台后端服务 — 给前端 console 提供真数据

三个真功能：
  · 控制开关 pause/resume/stop（跑批是否继续）
  · 自愈活动记录 /heal/records（按选择即验证路由实跑产出）
  · recode 重过闸：人改解析器代码 → 在缓存表上跑 → 对 golden 打分 → 返回 {score, exact, mismatches}
"""

import json
import os
import tempfile
from typing import Dict, List, Optional

from src.parsers.revenue_router import route_revenue
from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import load_parser
from src.eval.revenue_score import score_revenue
from src.eval.run_eval import load_golden

# ── 控制开关（跑批闸）──
_control = {"running": True}


def control(action: str) -> Dict:
    if action == "pause":
        _control["running"] = False
    elif action == "resume":
        _control["running"] = True
    elif action == "stop":
        _control["running"] = False
        _control["stopped"] = True
    return dict(_control)


def control_state() -> Dict:
    return dict(_control)


# ── 自愈活动记录 ──

def _route_to_record(code: str, year: int) -> Dict:
    r = route_revenue(code, year)
    if r["status"] == "routed":
        return {"stock_code": code, "year": year, "action": "reuse",
                "parser_key": r["parser_key"], "score": 1.0, "rounds": 0,
                "status": "certified"}
    sig = r.get("signal") or {}
    frac = (sig.get("ratio_ok_dims", 0) / sig["n_dims"]) if sig.get("n_dims") else 0.0
    return {"stock_code": code, "year": year, "action": "escalate",
            "parser_key": None, "score": round(frac, 2), "rounds": 0,
            "status": "needs_human"}


def heal_records(codes: Optional[List[str]] = None, year: int = 2025) -> List[Dict]:
    """对一批报告实跑路由，产出自愈活动记录（缓存表，秒级）。默认用 golden 里的报告。"""
    if codes is None:
        codes = [e["stock_code"] for e in load_golden()] or ["000425"]
    out = []
    for c in codes:
        if get_tables(c, year) is None:
            continue
        try:
            out.append(_route_to_record(c, year))
        except Exception as e:
            out.append({"stock_code": c, "year": year, "action": "escalate",
                        "parser_key": None, "score": 0.0, "rounds": 0,
                        "status": "needs_human", "error": str(e)[:80]})
    return out


# ── recode：人改代码 → 重过闸 ──

def _golden_for(code: str, year: int) -> Optional[Dict]:
    for e in load_golden():
        if e["stock_code"] == code and e["year"] == year:
            return e.get("revenue_breakdown")
    return None


def recode(code: str, year: int, new_code: str) -> Dict:
    """人改的解析器代码 → 缓存表上跑 → 对 golden 打分。返回 {score, exact, mismatches, error?}。"""
    tables = get_tables(code, year)
    if tables is None:
        return {"error": "无缓存表"}
    gold = _golden_for(code, year)
    if gold is None:
        return {"error": "该报告无 golden（无法判 exact，请人工核对原文）"}
    tf = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
    try:
        tf.write(new_code)
        tf.close()
        rb = load_parser(tf.name)(tables)
        s = score_revenue(rb, gold)
        return {"score": s["score"], "exact": s["exact"],
                "mismatches": [{"dim": m.get("dim"), "name": m.get("name"),
                                "issue": m.get("issue")} for m in s["mismatches"][:10]]}
    except Exception as e:
        return {"error": f"代码运行报错: {str(e)[:200]}"}
    finally:
        os.unlink(tf.name)
