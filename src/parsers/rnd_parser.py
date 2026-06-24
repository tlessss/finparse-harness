"""研发费用明细解析器 RndParser

内容特征识别：扫描全文表格 → 识别研发费用表 → 自动列检测
"""

from typing import Dict
from src.parsers.table_scanner import (scan_pdf, filter_by_signature, detect_column_types,
                                        parse_money, parse_ratio, cell_str)


class RndParser:
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        all_tables = pre_scan if pre_scan is not None else scan_pdf(pdf_path)
        matches = filter_by_signature(all_tables, "rnd")
        if not matches:
            return {"rnd_info": None, "status": "no_table_found"}

        target = None
        for m in matches:
            t = m["table"]
            text = " ".join(c for row in t for c in row if c)
            if "研发材料" in text or "职工薪酬" in text:
                if target is None or len(t) > len(target):
                    target = t

        if target is None and matches:
            # 选行数最多的
            target = max(matches, key=lambda x: len(x["table"]))["table"]
        elif target is None:
            return {"rnd_info": None, "status": "no_table_found"}

        return {"rnd_info": self._extract_rows(target), "status": "ok"}

    def _extract_rows(self, table: list) -> Dict:
        cols = detect_column_types(table)
        details = []
        total = None

        for row in table:
            cells = [c.strip() if c else "" for c in row]
            if not cells:
                continue

            name = cell_str(cells, cols["name_col"])
            if not name or name in ("项目", "项 目"):
                continue

            # 取金额
            amount_this = None
            amount_last = None
            if cols["amount_col"] is not None:
                amount_this = parse_money(cell_str(cells, cols["amount_col"]))

            # 再找第二笔金额（上期）
            for ci in range(len(cells)):
                if ci != cols["amount_col"]:
                    v = parse_money(cell_str(cells, ci))
                    if v is not None and amount_this is not None:
                        if amount_last is None or abs(v) < abs(amount_this):
                            amount_last = v

            if name == "合计":
                total = {"total_this": amount_this, "total_last": amount_last}
            else:
                details.append({"name": name, "amount_this": amount_this, "amount_last": amount_last})

        return {
            "rnd_detail": details,
            "total_this": total["total_this"] if total else None,
            "total_last": total["total_last"] if total else None,
        }
