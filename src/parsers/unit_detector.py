"""单位检测工具 — 从 PDF 表头/页眉中识别金额单位"""

from typing import Optional
import re


# 单位文本 → 到元的倍率
_UNIT_MAP = {
    "元": 1,
    "人民币元": 1,
    "千元": 1000,
    "万元": 10000,
    "十万元": 100000,
    "百万元": 1000000,
    "千万元": 10000000,
    "亿元": 100000000,
    "人民币千元": 1000,
    "人民币万元": 10000,
    "人民币百万元": 1000000,
    "人民币亿元": 100000000,
}

# 检测模式（优先级从高到低）
_UNIT_PATTERNS = [
    (r"[（(]货币单位[：:]\s*人民币?(\S+?)[)）]", 1),
    (r"[（(]单位[：:]\s*人民币?(\S+?)[)）]", 1),
    (r"单位[：:]\s*人民币?(\S+)", 1),
]


def detect_unit(text: str) -> int:
    """
    从文本中检测金额单位，返回到「元」的倍率。

    Args:
        text: 待分析的文本（表头/附近上下文）

    Returns:
        到元的倍率。默认返回 1（元）。
    """
    for pattern, group_idx in _UNIT_PATTERNS:
        m = re.search(pattern, text)
        if m:
            unit_text = m.group(group_idx).strip()
            # 匹配到已知单位
            for key, ratio in _UNIT_MAP.items():
                if key in unit_text or unit_text in key:
                    return ratio
    return 1


def convert_to_yuan(value: float, from_unit_ratio: int) -> float:
    """
    将数值从原始单位转为「元」。

    Args:
        value: 原始数值
        from_unit_ratio: 原始单位对应的到元倍率（如「万元」=10000）

    Returns:
        转换后的元数值
    """
    return round(value * from_unit_ratio, 2)
