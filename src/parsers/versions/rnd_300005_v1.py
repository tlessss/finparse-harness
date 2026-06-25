"""
研发专用解析器 v1 — 300005 版式（附注·研发费用科目表，B类:明细和≈合计）

契约：parse(tables) -> {total_this, total_last, rnd_detail:[{name, amount_this, amount_last}]}
认表(诚实说明)：研发费用科目表在附注段(section=fuzhu)，但表内无"研发费用"字样、且与
  销售/管理费用表同构 → 靠该公司研发特征科目(新品开发费/物料消耗)认表。这是 per-layout
  专用解析器的合理取舍；更稳的做法是按"研发费用"小节标题锚定(待 LLM 规范驱动生成时用)。
法：两金额列(左=本期/右=上期)取大额数字最多的两列；名称=行首文本；含"合计"行→total。
"""

from src.parsers.infra.table_scanner import parse_money, is_total_row


def _first_text(row):
    for c in row:
        if c and any("一" <= ch <= "鿿" for ch in str(c)):
            return str(c).replace("\n", "").strip()
    return ""


def parse(tables, context=None):
    best = None
    for t in tables:
        if t.get("section") != "fuzhu":
            continue
        grid = t.get("table") or []
        flat = "".join((c or "") for row in grid for c in row)
        # 研发独有科目认表(新品开发费/检测费/设计费 不出现在管理/销售费用表) → 排歧
        if "本期发生额" not in flat or "新品开发费" not in flat or "检测费" not in flat:
            continue
        ncols = max((len(r) for r in grid), default=0)

        # 两金额列：大额数字出现最多的两列，索引小的=本期
        money_cols = [c for c in range(ncols)
                      if sum(1 for row in grid if c < len(row) and (parse_money(row[c]) or 0) > 1000) >= 3]
        if not money_cols:
            continue
        this_c = money_cols[0]
        last_c = money_cols[1] if len(money_cols) > 1 else None

        detail, total_this, total_last = [], None, None
        for row in grid:
            name = _first_text(row)
            this_amt = parse_money(row[this_c]) if this_c < len(row) else None
            last_amt = parse_money(row[last_c]) if (last_c is not None and last_c < len(row)) else None
            if not name or this_amt is None or name == "项目":
                continue
            if "合计" in name or is_total_row(name):
                total_this, total_last = this_amt, last_amt
                continue
            detail.append({"name": name, "amount_this": this_amt, "amount_last": last_amt})
        if not detail:
            continue
        cand = {"total_this": total_this, "total_last": total_last, "rnd_detail": detail}
        # 勾稽自检：明细和≈合计 → 选中这张表(用判据消歧多张同构表)
        s = sum(d["amount_this"] for d in detail if d["amount_this"] is not None)
        if total_this and abs(s - total_this) <= max(1.0, total_this * 1e-4):
            return cand
        best = best or cand
    return best or {"total_this": None, "total_last": None, "rnd_detail": []}
