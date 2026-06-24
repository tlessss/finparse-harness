
"""
底层解析引擎：固定不变
AI 只修改 rules 配置，不修改此文件
"""
from typing import Dict, List
import camelot
from mineru import pdf_extract

class FinanceParserEngine:
    def __init__(self, rule: Dict):
        # 加载AI更新后的配置规则
        self.rule = rule
        self.keyword_mapping = rule.get("keyword_mapping", {})
        self.table_area = rule.get("table_area", {})
        self.regex_rules = rule.get("regex_rules", [])
        self.unit_convert = rule.get("unit_convert", {})

    def parse_pdf(self, pdf_path: str) -> Dict:
        """统一解析入口"""
        # 1. 布局+文本解析（MinerU固定能力）
        text_blocks = pdf_extract(pdf_path)
        
        # 2. 表格精准解析（Camelot固定能力）
        tables = camelot.read_pdf(
            pdf_path,
            pages=self.table_area.get("pages", "all"),
            flavor=self.table_area.get("flavor", "lattice")
        )

        # 3. 按AI配置的关键词映射抓取指标
        result = self.extract_by_keywords(text_blocks)
        
        # 4. 按AI配置的正则清洗数据
        result = self.clean_by_regex(result)
        
        # 5. 单位转换（AI可动态修改单位规则）
        result = self.convert_unit(result)

        return result

    def extract_by_keywords(self, text_blocks: List) -> Dict:
        """根据配置文件关键词匹配字段"""
        res = {}
        for block in text_blocks:
            text = block["text"]
            for standard_key, alias_list in self.keyword_mapping.items():
                for alias in alias_list:
                    if alias in text:
                        res[standard_key] = text
        return res

    def clean_by_regex(self, data: Dict) -> Dict:
        """AI配置的正则清洗规则"""
        import re
        for rule in self.regex_rules:
            field = rule["field"]
            pattern = rule["pattern"]
            if field in data:
                match = re.findall(pattern, data[field])
                if match:
                    data[field] = match[0]
        return data

    def convert_unit(self, data: Dict) -> Dict:
        """单位换算适配"""
        multiple = self.unit_convert.get("multiple", 1)
        for k, v in data.items():
            if isinstance(v, float):
                data[k] = round(v * multiple, 2)
        return data
