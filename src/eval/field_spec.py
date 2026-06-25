"""
字段插件 — 让自愈框架从"营收专用"变"字段通用"

机器层(路由/缓存/索引/heal/certify/沙箱/溯源)字段无关；按字段不同的只有三处，
其中"形状"和"判据类"由本规格描述：
  · amount_key: 金额字段名(营收 revenue_yuan / 成本 amount_yuan)
  · dims:       多维字段的维度键；空=扁平列表(成本)
  · cls:        判据类 A=占比构成(占比和≈100)；B/C/D 待接

as_dims() 把两种容器(多维字典 / 扁平列表)统一成 {维度: [行]}，让打分器/plausibility 同构处理。
"""

from dataclasses import dataclass, field as _f
from typing import Dict, List


@dataclass(frozen=True)
class FieldSpec:
    field: str                       # 顶层字段名
    amount_key: str                  # 金额字段名
    dims: tuple = ()                 # 多维字段的维度键；空=扁平列表
    ratio_key: str = "ratio_pct"
    cls: str = "A"                   # 判据类：A 占比构成
    label: str = ""


REVENUE = FieldSpec("revenue_breakdown", "revenue_yuan",
                    ("industries", "segments", "regions", "by_channel"), label="营收")
COST = FieldSpec("cost_breakdown", "amount_yuan", (), label="成本")   # 扁平列表

FIELDS: Dict[str, FieldSpec] = {f.field: f for f in (REVENUE, COST)}


def get_spec(field: str) -> FieldSpec:
    return FIELDS[field]


def as_dims(value, spec: FieldSpec) -> Dict[str, List]:
    """统一成 {维度: [行]}。多维字典取 dims；扁平列表包成单维 '_all'。"""
    if spec.dims and isinstance(value, dict):
        return {d: (value.get(d) or []) for d in spec.dims}
    if isinstance(value, list):
        return {"_all": value}
    if isinstance(value, dict):       # 兜底：dict 但无 dims 配置
        return {"_all": [v for vs in value.values() if isinstance(vs, list) for v in vs]}
    return {}
