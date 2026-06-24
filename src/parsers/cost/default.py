from src.parsers.base import BaseParser
"""成本构成解析器 CostParser

内容特征识别：扫描全文表格 → 识别成本表 → 自动提取
"""

from typing import Dict
from src.parsers.infra.table_scanner import scan_pdf, filter_by_signature, detect_column_types, parse_money, cell_str
from src.parsers.infra.unit_detector import detect_unit, convert_to_yuan


class CostParser(BaseParser):
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        all_tables = pre_scan if pre_scan is not None else scan_pdf(pdf_path)
        matches = filter_by_signature(all_tables, "cost")
        if not matches:
            return {"cost_breakdown": None, "status": "no_table_found"}

        # 选得分最高且含"占营业成本比重"的表（排除利润表）
        best = None
        for m in matches:
            text = " ".join(c for row in m["table"] for c in row if c)
            if "占营业成本比重" in text or "营业成本构成" in text:
                best = m
                break
        if best is None:
            best = matches[0]
        # 从所在页检测单位
        unit_ratio = 1
        if pre_scan:
            for item in pre_scan:
                if item["page"] == best.get("page", 0):
                    unit_ratio = detect_unit(item["text"])
                    break
        return {"cost_breakdown": self._parse_table(best["table"], unit_ratio=unit_ratio), "status": "ok"}

    def _parse_table(self, table: list, unit_ratio: int = 1) -> list:
        items = []
        for row in table:
            cells = [c.strip() if c else "" for c in row]
            if not cells or len(cells) < 3:
                continue

            # 第一列是行业，第二列是项目
            industry = cells[0]
            item_name = cells[1] if len(cells) > 1 else ""
            if not item_name or item_name in ("项目", "金额", "同比增", "减", ""):
                continue
            if not industry or industry in ("行业分类", ""):
                continue

            # 扫描整行找金额和占比
            amount = None
            ratio = None
            amount_last = None
            ratio_last = None
            yoy = None

            for cell in cells:
                cleaned = cell.replace(",", "").replace("，", "").replace("%", "")
                if "%" in cell:
                    try:
                        v = float(cleaned)
                        if ratio is None:
                            ratio = v
                        elif ratio_last is None:
                            ratio_last = v
                        else:
                            yoy = v
                    except ValueError:
                        pass
                else:
                    try:
                        v = float(cleaned)
                        if amount is None:
                            amount = v
                        elif amount_last is None:
                            amount_last = v
                    except ValueError:
                        pass

            items.append({
                "industry": industry,
                "item": item_name,
                "amount_yuan": convert_to_yuan(amount, unit_ratio) if amount else None,
                "ratio_pct": ratio,
                "amount_last_yuan": convert_to_yuan(amount_last, unit_ratio) if amount_last else None,
                "ratio_last_pct": ratio_last,
                "yoy_change_pp": yoy,
            })

        return items
