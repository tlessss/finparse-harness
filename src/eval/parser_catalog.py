"""
母本目录 + 选母本（选择即验证，营收字段级）— fork 优先的基础

已认证的营收专用解析器登记在此。修复一份失败报告时，先用"选择即验证"在母本里
挑最像的（跑一遍对 golden 打分），据分决定 复用 / fork / 新建（见 code_generator.repair）。

注：这里用 golden 打分选母本——适用于"正在认证某失败报告"的构建场景。
生产运行时对无 golden 的新报告，选母本/路由用硬规则代理（见 parsers/registry.py）。
"""

import json
import os
from typing import List, Dict, Tuple

from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import version_parse_fn
from src.eval.revenue_score import score_revenue

# 认证清单（持久化，认证流程/前端可追加）。首次缺失则以种子写入。
_MANIFEST = "goldset/certified_parsers.json"
_SEED: List[Dict] = [
    {"key": "000425-工程机械占比构成表", "path": "src/parsers/versions/rev_000425_v1.py"},
]


def load_certified() -> List[Dict]:
    """读认证清单；缺失则用种子初始化并落盘。"""
    if os.path.exists(_MANIFEST):
        return json.load(open(_MANIFEST, encoding="utf-8")).get("parsers", [])
    os.makedirs(os.path.dirname(_MANIFEST) or ".", exist_ok=True)
    json.dump({"parsers": _SEED}, open(_MANIFEST, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return list(_SEED)


def certify(key: str, path: str) -> None:
    """把一个已 exact 的解析器登记进认证清单（去重）。下次同版式自动路由到它。"""
    cur = load_certified()
    if any(c["path"] == path for c in cur):
        return
    cur.append({"key": key, "path": path})
    json.dump({"parsers": cur}, open(_MANIFEST, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


# 向后兼容：模块级常量（首次 import 时加载/初始化）
CERTIFIED: List[Dict] = load_certified()


def pick_mother(code: str, year: int, golden_rb: Dict,
                catalog: List[Dict] = None) -> Tuple:
    """选择即验证：跑每个已认证解析器 → 对 golden 打分 → 返回 (最优path, 分, key)。"""
    catalog = catalog if catalog is not None else load_certified()
    if get_tables(code, year) is None:
        return (None, -1.0, None)
    best = (None, -1.0, None)
    for c in catalog:
        if not os.path.exists(c["path"]):
            continue
        try:
            rb = version_parse_fn(c["path"])(code, year)
            s = score_revenue(rb, golden_rb)["score"]
        except Exception:
            s = -1.0
        if s > best[1]:
            best = (c["path"], s, c["key"])
    return best
