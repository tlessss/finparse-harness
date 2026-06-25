"""
自愈管线单一入口 — 把"路由→修复/认证→转人工"串成一条，产出统一的 heal 记录

heal_revenue(code, year, golden_entry?) 决策：
  ① 路由(选择即验证)命中认证解析器 → 用它(action=routed)
  ② 没命中：
     · 有 golden(认证期) → 修复(复用/fork/新建,终点exact) → exact则认证入目录(action=fork|new|reuse)
                                                          → 仍不exact → 转人工(action=escalate)
     · 无 golden(运行期) → 不能自动认证 → 转人工(给真值后再认证)

返回 heal 记录(给前端控制台/审核台用)：
  {stock_code, year, action, parser_key, score, rounds, status, result?}
  status ∈ ok(已路由) | certified(修复并认证) | needs_human(转人工)
"""

from typing import Dict, Optional

from src.parsers.revenue_router import route_revenue
from src.agents.code_generator import repair
from src.eval.parser_catalog import certify


def heal_revenue(code: str, year: int, golden_entry: Optional[Dict] = None,
                 log=print) -> Dict:
    base = {"stock_code": code, "year": year}

    # ① 路由：选择即验证命中认证解析器就用
    route = route_revenue(code, year)
    if route["status"] == "routed":
        log(f"  {code}: ✅routed → {route['parser_key']}")
        return {**base, "action": "routed", "parser_key": route["parser_key"],
                "score": None, "rounds": 0, "status": "ok",
                "result": route["result"], "signal": route["signal"]}

    # ② 没命中
    if golden_entry is None:
        # 运行期无真值 → 不能自动认证，转人工(队列里人给真值后再走认证)
        log(f"  {code}: 🙋 无认证解析器命中且无 golden → 转人工")
        return {**base, "action": "escalate", "parser_key": None, "score": None,
                "rounds": 0, "status": "needs_human",
                "reason": "no_certified_fit_no_golden"}

    # 认证期：有真值 → 修复到 exact → 认证入目录
    out_path = f"src/parsers/versions/rev_{code}_{year}.py"
    r = repair(code, year, golden_entry, lambda c, y: None, out_path, log=log)

    if r.get("accepted"):
        if r.get("action") == "reuse":          # 母本本就 exact(已认证)
            return {**base, "action": "reuse", "parser_key": None,
                    "score": r.get("score"), "rounds": 0, "status": "ok"}
        key = f"{code}-{year}-认证"
        from src.eval.route_index import fingerprint_of
        fp = fingerprint_of(code, year)
        certify(key, r.get("parser") or out_path,   # 登记入目录 → 下次同版式自动路由
                fingerprints=[fp] if fp else None)
        log(f"  {code}: 🎓 {r.get('action')} 到 exact → 认证入目录")
        return {**base, "action": r.get("action"), "parser_key": key,
                "score": r.get("score"), "rounds": r.get("rounds", 0),
                "status": "certified"}

    # 想尽办法仍不 exact → 转人工(不留半成品)
    log(f"  {code}: 🙋 修复未到 exact(最好 {r.get('best_score')}) → 转人工")
    return {**base, "action": "escalate", "parser_key": None,
            "score": r.get("best_score"), "rounds": r.get("rounds"),
            "status": "needs_human"}
