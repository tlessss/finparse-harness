from src.parsers.base import BaseParser
"""前五大供应商/客户解析器 TopSupplierParser

扫描全文 → 按关键词匹配客户/供应商表 → 提取明细
"""

import re
from typing import Dict, Optional
from src.parsers.infra.table_scanner import scan_pdf, cell_str


class TopSupplierParser(BaseParser):
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None, code: str = None, year: int = None) -> Dict:
        """
        一次解出"前五大客户"和"前五大供应商"两个字段。
        code/year 仅为与营收解析器签名统一(本解析器暂不用)。

        入参：pdf_path: str；pre_scan: list[dict]|None。
        返回：{"top_clients": {...}|None, "top_suppliers": {...}|None, "status": ...}
              每个字段含 items(明细:rank/name/amount/ratio)、total_amount、total_ratio_pct、
              related_party_ratio_pct(关联方占比)。
        做法：遍历所有表，文本里出现"客户名称+销售额"→当客户明细表抽；
              "供应商名称+采购额"→当供应商明细表抽；"前五名客户/供应商合计"→抽汇总数。
              (注:准则规定明细名单"鼓励非强制"，常缺失且合规，所以 items 可能为空。)
        """
        all_tables = pre_scan if pre_scan is not None else scan_pdf(pdf_path)
        result = {"top_clients": None, "top_suppliers": None}

        for item in all_tables:
            t = item["table"]
            if not t:
                continue

            # 合并所有行的文本
            rows_text = []
            for row in t:
                row_text = " ".join(c.replace("\n", " ") for c in row if c)
                rows_text.append(row_text)
            full_text = " ".join(rows_text)

            # 客户明细表
            if "客户名称" in full_text and "销售额" in full_text:
                if result["top_clients"] is None or not result["top_clients"].get("items"):
                    detail = self._parse_rows(rows_text, "客户")
                    # 保留汇总数据
                    if result["top_clients"] and detail:
                        detail["total_amount"] = result["top_clients"].get("total_amount")
                        detail["total_ratio_pct"] = result["top_clients"].get("total_ratio_pct")
                        detail["related_party_ratio_pct"] = result["top_clients"].get("related_party_ratio_pct")
                    result["top_clients"] = detail

            # 供应商明细表
            if "供应商名称" in full_text and "采购额" in full_text:
                if result["top_suppliers"] is None or not result["top_suppliers"].get("items"):
                    detail = self._parse_rows(rows_text, "供应商")
                    if result["top_suppliers"] and detail:
                        detail["total_amount"] = result["top_suppliers"].get("total_amount")
                        detail["total_ratio_pct"] = result["top_suppliers"].get("total_ratio_pct")
                        detail["related_party_ratio_pct"] = result["top_suppliers"].get("related_party_ratio_pct")
                    result["top_suppliers"] = detail

            # 客户汇总
            if "前五名客户合计" in full_text:
                if result["top_clients"] is None:
                    result["top_clients"] = {}
                for row_text in rows_text:
                    if "前五名客户合计销售金额" in row_text:
                        v = self._extract_number(row_text)
                        if v is not None:
                            result["top_clients"]["total_amount"] = v
                    if "占年度销售总额比例" in row_text and "关联" not in row_text:
                        result["top_clients"]["total_ratio_pct"] = self._extract_ratio(row_text)
                    if "关联方" in row_text:
                        result["top_clients"]["related_party_ratio_pct"] = self._extract_ratio(row_text)

            # 供应商汇总
            if "前五名供应商合计" in full_text:
                if result["top_suppliers"] is None:
                    result["top_suppliers"] = {}
                for row_text in rows_text:
                    if "前五名供应商合计采购金额" in row_text:
                        v = self._extract_number(row_text)
                        if v is not None:
                            result["top_suppliers"]["total_amount"] = v
                    if "占年度采购总额比例" in row_text and "关联" not in row_text:
                        result["top_suppliers"]["total_ratio_pct"] = self._extract_ratio(row_text)
                    if "关联" in row_text and "比例" in row_text:
                        result["top_suppliers"]["related_party_ratio_pct"] = self._extract_ratio(row_text)

        for key in ["top_clients", "top_suppliers"]:
            data = result.get(key)
            if data and isinstance(data, dict) and data.get("total_amount"):
                data["total_amount_yuan"] = round(data["total_amount"], 2)

        result["status"] = "ok" if result["top_clients"] or result["top_suppliers"] else "no_table_found"
        return result

    def _parse_rows(self, rows_text: list, type_name: str) -> Dict:
        """从文本行中解析明细项"""
        items = []
        for row_text in rows_text:
            parts = row_text.split()
            # 找序号
            rank = None
            for p in parts:
                if p.isdigit() and 1 <= int(p) <= 5:
                    rank = int(p)
                    break
            if rank is None:
                continue

            # 找金额
            amount = None
            for p in parts:
                try:
                    v = float(p.replace(",", "").replace("，", ""))
                    if v > 100000:
                        amount = v
                        break
                except ValueError:
                    pass

            # 找占比
            ratio = None
            for p in parts:
                if "%" in p:
                    try:
                        r = float(p.replace("%", "").strip())
                        if 0 <= r <= 100:
                            ratio = r
                            break
                    except ValueError:
                        pass

            # 名称（在序号和金额之间的文本）
            name = "第{}名".format(rank)
            for p in parts:
                if p.isdigit() and 1 <= int(p) <= 5:
                    continue
                if "%" in p:
                    continue
                try:
                    float(p.replace(",", ""))
                    continue
                except ValueError:
                    pass
                if p in ("第{}名".format(rank),) or re.match(r"^第\d+名$", p):
                    continue
                if p in ("--", "-"):
                    continue
                name = p
                break

            items.append({
                "rank": rank, "name": name, "amount": amount,
                "amount_yuan": amount if amount else None,
                "ratio_pct": ratio,
            })

        return {"items": items}

    @staticmethod
    def _extract_number(text: str) -> Optional[float]:
        """从文本中提取数字"""
        for part in text.split():
            cleaned = part.replace(",", "").replace("，", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_ratio(text: str) -> Optional[float]:
        """从文本中提取百分比"""
        for part in text.split():
            if "%" in part:
                try:
                    return float(part.replace("%", "").strip())
                except ValueError:
                    pass
        return None
