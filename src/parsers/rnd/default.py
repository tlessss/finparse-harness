"""
研发费用明细解析器（signature 派，冷启动）
============================================

signature 派的套路(研发/员工/成本/供应商都一样)：
  scan_pdf 抽好的全表 → filter_by_signature 按"研发表特征"挑出候选 → 挑一张 → 逐行抽数。
不像营收那样自己找页/认表，而是直接从共享的 pre_scan 里按关键词签名捞表。

输出：{"rnd_info": {"rnd_detail":[{name,amount_this,amount_last}],
                   "total_this": float, "total_last": float}, "status": "ok"|"no_table_found"}
"""

from typing import Dict
from src.parsers.base import BaseParser
from src.parsers.infra.table_scanner import (scan_pdf, filter_by_signature, detect_column_types,
                                        parse_money, parse_ratio, cell_str)


class RndParser(BaseParser):
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        """
        入参：pdf_path: str；pre_scan: list[dict]|None (引擎抽好的全表；没有就自己 scan_pdf)。
        返回：见模块顶部。
        """
        all_tables = pre_scan if pre_scan is not None else scan_pdf(pdf_path)
        matches = filter_by_signature(all_tables, "rnd")        # 按"研发表"签名挑候选
        if not matches:
            return {"rnd_info": None, "status": "no_table_found"}

        # 优先选含"研发材料/职工薪酬"(研发费用明细表的典型科目)、且行数最多的表
        target = None
        for m in matches:
            t = m["table"]
            text = " ".join(c for row in t for c in row if c)
            if "研发材料" in text or "职工薪酬" in text:
                if target is None or len(t) > len(target):
                    target = t

        if target is None and matches:
            target = max(matches, key=lambda x: len(x["table"]))["table"]   # 退而求其次:行数最多
        elif target is None:
            return {"rnd_info": None, "status": "no_table_found"}

        return {"rnd_info": self._extract_rows(target), "status": "ok"}

    def _extract_rows(self, table: list) -> Dict:
        """从研发表逐行抽：名称 + 本期金额 + 上期金额；"合计"行存为 total。
        入参 table: 二维网格。返回 {"rnd_detail":[...], "total_this":..., "total_last":...}。"""
        cols = detect_column_types(table)        # 统计法认列(名称列/金额列)
        details = []
        total = None

        for row in table:
            cells = [c.strip() if c else "" for c in row]
            if not cells:
                continue

            name = cell_str(cells, cols["name_col"])
            if not name or name in ("项目", "项 目"):     # 跳过表头
                continue

            # 本期金额 = 认出来的金额列
            amount_this = None
            amount_last = None
            if cols["amount_col"] is not None:
                amount_this = parse_money(cell_str(cells, cols["amount_col"]))

            # 上期金额 = 行内另一个金额，取"绝对值比本期小"的那个(上期通常 < 本期)
            for ci in range(len(cells)):
                if ci != cols["amount_col"]:
                    v = parse_money(cell_str(cells, ci))
                    if v is not None and amount_this is not None:
                        if amount_last is None or abs(v) < abs(amount_this):
                            amount_last = v

            if name == "合计":                  # 合计行单独存(不算明细)
                total = {"total_this": amount_this, "total_last": amount_last}
            else:
                details.append({"name": name, "amount_this": amount_this, "amount_last": amount_last})

        return {
            "rnd_detail": details,
            "total_this": total["total_this"] if total else None,
            "total_last": total["total_last"] if total else None,
        }
