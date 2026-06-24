"""营收结构解析器 — 包入口"""

from src.parsers.revenue.default import RevenueParser as DefaultRevenueParser

# 默认导出通用版
RevenueParser = DefaultRevenueParser

# 其他版式的解析器由 selector 动态选择
# AI 新建的 bank.py / insurance.py 等会自动被 selector 发现
