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
# 注意 (?:人民币)? —— 整个"人民币"可选；旧写法 人民币? 是"人民"必需只"币"可选,
# 会漏掉"单位：千元"(无人民币前缀) → 千元当成元、金额差1000倍。
_UNIT_PATTERNS = [
    (r"[（(]货币单位[：:]\s*(?:人民币)?(\S+?)[)）]", 1),
    (r"[（(]单位[：:]\s*(?:人民币)?(\S+?)[)）]", 1),
    (r"单位[：:]\s*(?:人民币)?(\S+)", 1),
    (r"单位为\s*(?:人民币)?(\S+)", 1),         # 附注常用"金额单位为人民币千元"(无冒号)
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
            # 在"是 unit_text 子串"的已知单位里取**最长**那个：
            #   - 只用 key in unit_text(不反向),否则 "万元" in "百万元" 会把万元误判成百万元;
            #   - 取最长,否则 "元"(=1) 截胡千元/万元/亿元(因"元"是它们子串)。
            matches = [(k, r) for k, r in _UNIT_MAP.items() if k in unit_text]
            if matches:
                return max(matches, key=lambda kr: len(kr[0]))[1]
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
