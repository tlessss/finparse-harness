"""
解析器基类 — 所有解析器继承此类

子类只需实现 parse() 方法，返回统一的 Dict 格式。
"""

from typing import Dict


class BaseParser:
    """解析器基类"""

    # 解析器标识（子类覆盖）
    name: str = "base"
    description: str = ""

    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        """解析 PDF 返回结构化数据"""
        raise NotImplementedError

    @classmethod
    def can_handle(cls, pdf_path: str, hint: str = "") -> float:
        """
        判断此解析器是否适合处理该 PDF。
        返回 0-1 的置信度，越高越适合。

        子类可覆盖此方法：检查 PDF 关键词、表格特征等。
        """
        return 0.5
