from src.parsers.base import BaseParser
"""员工构成解析器 EmployeeParser

内容特征识别：扫描全文表格 → 识别员工表 → 自动提取

注：曾尝试"标准类别白名单 + mode 跨表持续"修跨页漏行（000088 等），
但 mode 跨表会把 education 段落漏进不相关表 → 300001/300005 回归（净通过 0 提升 + 有回归）。
按"不把对的改坏"已回滚。员工跨页漏行留待专用解析器方案（见 docs/多agent编排设计.md）。
"""

from typing import Dict
from src.parsers.infra.table_scanner import scan_pdf, filter_by_signature


class EmployeeParser(BaseParser):
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        """
        入参：pdf_path: str；pre_scan: list[dict]|None (引擎抽好的全表)。
        返回：{"employees": {"total": int, "parent": int,
                            "composition":[{type,count}], "education":[{type,count}]}, "status": ...}
        """
        all_tables = pre_scan if pre_scan is not None else scan_pdf(pdf_path)
        matches = filter_by_signature(all_tables, "employee")     # 按"员工表"签名挑候选
        if not matches:
            return {"employees": None, "status": "no_table_found"}

        # 合并所有匹配表的员工数据（专业构成和教育程度可能分散在多张表）
        result = {"total": None, "parent": None, "composition": [], "education": []}
        for m in matches:
            partial = self._parse_table(m["table"])
            if partial.get("total") and result["total"] is None:
                result["total"] = partial["total"]
            if partial.get("parent") and result["parent"] is None:
                result["parent"] = partial["parent"]
            if partial.get("composition"):
                result["composition"] = partial["composition"]
            if partial.get("education"):
                result["education"] = partial["education"]

        return {"employees": result, "status": "ok"}

    def _parse_table(self, table: list) -> Dict:
        """从一张员工表抽数。入参 table: 二维网格。返回 {total,parent,composition,education}。
        逻辑：遇到"专业构成/教育程度"这种段落标题就切 mode，之后的数据行按当前 mode 归类。
        (mode 在单表内有效；跨页续表会漏 → 已知局限，见模块顶部说明。)"""
        result = {"total": None, "parent": None, "composition": [], "education": []}
        mode = "initial"        # 当前在哪个段落:initial/composition(专业构成)/education(教育程度)

        for row in table:
            cells = [str(c).strip() if c else "" for c in row]
            label = " ".join(cells).replace("\n", " ").strip()      # 整行文字

            # 取这一行的第一个整数(人数)
            value = None
            for c in cells:
                cleaned = c.replace(",", "").replace("，", "")
                try:
                    value = int(cleaned)
                    break
                except ValueError:
                    pass

            if "在职员工的数量合计" in label:
                result["total"] = value
            elif "母公司在职员工" in label:
                result["parent"] = value
            elif label.strip() == "专业构成":
                mode = "composition"
            elif label.strip() == "教育程度":
                mode = "education"
            elif mode == "composition" and value is not None and "类别" not in label and "合计" not in label:
                result["composition"].append({"type": label.split(" ")[0], "count": value})
            elif mode == "education" and value is not None and "类别" not in label and "合计" not in label:
                result["education"].append({"type": label.split(" ")[0], "count": value})

        return result
