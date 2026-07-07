"""
解析器注册表 + 选择即验证 — 多agent编排设计 §二/§三 的骨架（M2）

核心思想（来自 docs/多agent编排设计.md）：
  - 解析器按版式多实例化：注册表 = { 版式key → 已认证专用解析器 }，通用解析器作冷启动。
  - **选择即验证**：不预测哪个解析器合适，把候选都跑一遍，**硬规则告诉你谁解对了**。
    消灭"猜错版式"那一整类错误。

本文件只搭骨架（确定性、可单测、零 LLM）：
  - ReportParser 统一契约（产出整份报告 6 字段 + 溯源占位）
  - GenericReportParser：包装现有 FinParseAI 引擎作冷启动
  - ParserRegistry：注册 + 候选 + route（跑候选 → 硬规则打分 → 选最优）

生成专用解析器（M5，LLM）、溯源 bbox（M1）、人审（M3）后续按依赖顺序接入。
"""

from typing import Dict, List, Optional, Callable

from src.validators.hard_rules import check_hard_rules

ALL_FIELDS = ["revenue_breakdown", "rnd_info", "employees",
              "cost_breakdown", "top_clients", "top_suppliers"]


# ── 统一契约 ──

class ReportParser:
    """整份报告解析器统一契约。专用解析器与通用解析器都实现它。"""
    key: str = "base"

    def matches(self, fingerprint: str) -> bool:
        """该解析器是否声称能处理此版式（专用解析器覆盖；通用恒 False，靠兜底进候选）。"""
        return False

    def parse(self, pdf_path: str, pre_scan: list, context: dict = None) -> Dict:
        """产出 {revenue_breakdown:..., ..., field_count, _provenance?}"""
        raise NotImplementedError


# ── 选择即验证打分 ──

def score_result(result: Optional[Dict]) -> tuple:
    """
    候选结果排序键（越大越好）：
      1) 硬规则是否通过（红线优先）
      2) 解析出的字段数
      3) 红线违规数取负（越少越好）
    """
    if not result:
        return (False, -1, -999)
    hard = check_hard_rules(result)
    fc = result.get("field_count")
    if fc is None:
        fc = sum(1 for f in ALL_FIELDS if result.get(f))
    return (hard["passed"], fc, -hard["red_count"])


# ── 通用解析器（冷启动）──

class GenericReportParser(ReportParser):
    """包装现有 FinParseAI 引擎作为冷启动通用解析器。"""
    key = "generic"

    def __init__(self, engine=None):
        if engine is None:
            from src.engine_orchestrator import FinParseAI
            engine = FinParseAI()
        self.engine = engine

    def parse(self, pdf_path: str, pre_scan: list, context: dict = None) -> Dict:
        ctx = context or {}
        r = self.engine.run(
            pdf_path,
            stock_code=ctx.get("stock_code"),
            report_year=ctx.get("report_year"),
            company_name=ctx.get("company_name"),
            db_write=False,
            pre_scan=pre_scan,
        )
        r["_parser"] = self.key
        return r


# ── 注册表 + 路由 ──

class ParserRegistry:
    """版式key → 专用解析器；通用解析器作冷启动兜底。"""

    def __init__(self, generic: Optional[ReportParser] = None):
        self.generic = generic or GenericReportParser()
        self.specialized: List[ReportParser] = []

    def register(self, parser: ReportParser):
        self.specialized.append(parser)

    def candidates(self, fingerprint: str) -> List[ReportParser]:
        """指纹缩候选：匹配的专用解析器 + 通用兜底。"""
        cands = [p for p in self.specialized if p.matches(fingerprint)]
        cands.append(self.generic)   # 冷启动永远在候选里兜底
        return cands

    def route(self, pdf_path: str, pre_scan: list, fingerprint: str = "",
              context: dict = None) -> Optional[Dict]:
        """
        选择即验证：跑所有候选 → 硬规则打分 → 选最优。
        共享 pre_scan，抽表只做一次。
        """
        results = []
        for p in self.candidates(fingerprint):
            try:
                r = p.parse(pdf_path, pre_scan, context)
                if r:
                    r.setdefault("_parser", getattr(p, "key", "?"))
                    results.append(r)
            except Exception as e:
                results.append(None)  # 单个候选失败不影响其它
        results = [r for r in results if r]
        if not results:
            return None
        best = max(results, key=score_result)
        best["_candidates_tried"] = len(results)
        return best
