"""员工构成解析器 EmployeeParser

内容特征识别：扫描全文表格 → 识别员工表 → 自动提取
"""

from typing import Dict
from src.parsers.table_scanner import scan_pdf, filter_by_signature


class EmployeeParser:
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        all_tables = pre_scan if pre_scan is not None else scan_pdf(pdf_path)
        matches = filter_by_signature(all_tables, "employee")
        if not matches:
            return {"employees": None, "status": "no_table_found"}

        # 合并所有匹配表的员工数据（专业构成和教育程度可能在两张表中）
        result = {"total": None, "parent": None, "composition": [], "education": []}
        for m in matches:
            partial = self._parse_table(m["table"])
            # 合并
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
        result = {"total": None, "parent": None, "composition": [], "education": []}
        mode = "initial"

        for row in table:
            cells = [str(c).strip() if c else "" for c in row]
            label = " ".join(cells).replace("\n", " ").strip()

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
