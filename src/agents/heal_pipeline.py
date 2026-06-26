"""
自愈管线单一入口 — 把"路由 → 修复/认证 → 转人工"串成一条
============================================================

死信(解不干净的报告)进到这里，heal_field 做决策：
  ① 路由(选择即验证)命中已认证解析器 → 直接用它(action=routed)
  ② 没命中：
     · 有 golden(认证期，有人给了真值) → 修复(复用/fork/新建，目标=exact)
            → 到 exact → 认证入目录(action=fork|new|reuse)；同版式下次自动路由
            → 仍不 exact → 转人工(action=escalate，不留半成品)
     · 无 golden(运行期，没真值) → 不能自动认证 → 转人工(队列里人给真值后再认证)

返回 heal 记录(给前端控制台/审核台用)：
  {stock_code, year, field, action, parser_key, score, rounds, status, result?}
  status ∈ ok(已路由) | certified(修复并认证) | needs_human(转人工)
"""

from typing import Dict, Optional

from src.parsers.revenue_router import route_field      # 选择即验证路由
from src.agents.code_generator import repair            # 修复(复用/fork/新建)到 exact
from src.eval.parser_catalog import certify             # 认证入目录(版式→解析器)
from src.eval.field_spec import REVENUE


def heal_field(spec, code: str, year: int, golden_entry: Optional[Dict] = None,
               log=print) -> Dict:
    """
    字段通用自愈：路由 → 修复(到 exact) → 认证；不行就转人工。

    ── 入参 ──
      spec         : FieldSpec  字段规格(REVENUE/RND/...)
      code, year   : str/int    股票代码、年份
      golden_entry : dict|None  该字段的真值(认证期才有)；None=运行期无真值
      log          : callable   打日志的函数，默认 print
    ── 返回 ── heal 记录 dict(见模块顶部)
    """
    base = {"stock_code": code, "year": year, "field": spec.field}

    from src.eval.triage_queue import resolve as _triage_resolve   # 搞定后销账

    # ① 路由：选择即验证命中已认证解析器就直接用
    route = route_field(spec, code, year)
    if route["status"] == "routed":
        log(f"  {code}/{spec.label}: ✅routed → {route['parser_key']}")
        if (route.get("signal") or {}).get("confidence") != "low":   # 低置信仍留队列待复核
            _triage_resolve(code, year, spec.field)
        return {**base, "action": "routed", "parser_key": route["parser_key"],
                "score": None, "rounds": 0, "status": "ok",
                "result": route["result"], "signal": route["signal"]}

    # ② 没命中
    if golden_entry is None:
        # 运行期无真值 → 不能自动认证(怕认证错的) → 转人工，等人给真值
        log(f"  {code}/{spec.label}: 🙋 无认证解析器命中且无 golden → 转人工")
        return {**base, "action": "escalate", "parser_key": None, "score": None,
                "rounds": 0, "status": "needs_human",
                "reason": "no_certified_fit_no_golden"}

    # 认证期：有真值 → 让 repair 把解析器修到 exact(复用/fork/新建)，输出成一个版本文件
    out_path = f"src/parsers/versions/{spec.version_prefix}_{code}_{year}.py"
    r = repair(code, year, golden_entry, lambda c, y: None, out_path, spec=spec, log=log)

    if r.get("accepted"):                       # 修到 exact 了
        if r.get("action") == "reuse":          # 母本本来就 exact(已认证)，无需新认证
            return {**base, "action": "reuse", "parser_key": None,
                    "score": r.get("score"), "rounds": 0, "status": "ok"}
        # fork/新建出的解析器 → 认证入目录，并打上版式指纹，下次同版式自动路由命中
        key = f"{code}-{year}-{spec.field}-认证"
        from src.eval.route_index import fingerprint_of
        fp = fingerprint_of(code, year)
        certify(key, r.get("parser") or out_path, field=spec.field,
                fingerprints=[fp] if fp else None)
        _triage_resolve(code, year, spec.field)             # 认证成功 → 销账
        log(f"  {code}/{spec.label}: 🎓 {r.get('action')} 到 exact → 认证入目录")
        return {**base, "action": r.get("action"), "parser_key": key,
                "score": r.get("score"), "rounds": r.get("rounds", 0),
                "status": "certified"}

    # 想尽办法仍不 exact → 转人工(绝不留半成品)
    log(f"  {code}/{spec.label}: 🙋 修复未到 exact(最好 {r.get('best_score')}) → 转人工")
    return {**base, "action": "escalate", "parser_key": None,
            "score": r.get("best_score"), "rounds": r.get("rounds"),
            "status": "needs_human"}


def heal_revenue(code: str, year: int, golden_entry: Optional[Dict] = None,
                 log=print) -> Dict:
    """营收便捷入口（= heal_field(REVENUE)）。"""
    return heal_field(REVENUE, code, year, golden_entry, log=log)
