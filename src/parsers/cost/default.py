"""
成本构成解析器（signature 派，冷启动）
========================================

套路同其它 signature 派：从 pre_scan 全表里按"成本表特征"挑表，逐行抽数。
成本表通常按 (行业, 项目) 列出 金额 + 占营业成本比重。

输出：{"cost_breakdown": [{industry, item, amount_yuan, ratio_pct, ...}], "status": ...}
"""

from typing import Dict
from src.parsers.base import BaseParser
from src.parsers.infra.table_scanner import scan_pdf, filter_by_signature, detect_column_types, parse_money, cell_str
from src.parsers.infra.unit_detector import detect_unit, convert_to_yuan


class CostParser(BaseParser):
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None, code: str = None, year: int = None) -> Dict:
        """入参：pdf_path: str；pre_scan: list[dict]|None。code/year 仅为与营收解析器签名统一(本解析器暂不用)。返回见模块顶部。"""
        all_tables = pre_scan if pre_scan is not None else scan_pdf(pdf_path)
        matches = filter_by_signature(all_tables, "cost")     # 按"成本表"签名挑候选
        if not matches:
            return {"cost_breakdown": None, "status": "no_table_found"}

        # 优先选明确含"占营业成本比重/营业成本构成"的表(排除利润表等噪声)
        best = None
        for m in matches:
            text = " ".join(c for row in m["table"] for c in row if c)
            if "占营业成本比重" in text or "营业成本构成" in text:
                best = m
                break
        if best is None:
            best = matches[0]                                 # 没有就用最高分

        # 从该表所在页探测金额单位(万元/亿元 → 倍率)
        unit_ratio = 1
        if pre_scan:
            for item in pre_scan:
                if item["page"] == best.get("page", 0):
                    unit_ratio = detect_unit(item["text"])
                    break
        return {"cost_breakdown": self._parse_table(best["table"], unit_ratio=unit_ratio), "status": "ok"}

    def _parse_table(self, table: list, unit_ratio: int = 1) -> list:
        """
        逐行抽成本项。入参 table: 二维网格；unit_ratio: 金额单位倍率。
        返回 list[dict]：每项 {industry, item, amount_yuan, ratio_pct, amount_last_yuan, ratio_last_pct, yoy_change_pp}。
        约定：第1列=行业，第2列=项目；整行扫描，按出现顺序把数字归到 本期/上期金额、本期/上期占比、同比。
        """
        items = []
        for row in table:
            cells = [c.strip() if c else "" for c in row]
            if not cells or len(cells) < 3:
                continue

            industry = cells[0]                      # 第1列:行业
            item_name = cells[1] if len(cells) > 1 else ""   # 第2列:项目
            if not item_name or item_name in ("项目", "金额", "同比增", "减", ""):
                continue
            if not industry or industry in ("行业分类", ""):
                continue

            # 扫整行的数字：带%的依次归 本期占比/上期占比/同比；不带%的依次归 本期金额/上期金额
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
