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
    cls: str = "A"                   # 判据类：A 占比构成 / B 明细和≈合计 / C 分项和=总数
    label: str = ""
    version_prefix: str = "ver"      # 版本解析器文件名前缀
    total_key: str = ""              # B/C 类：合计/总数 字段名
    detail_key: str = ""             # B 类：明细列表 字段名
    # ── 准则口径（规范驱动，喂给生成 prompt / 认表认列 / 校验）──
    spec_note: str = ""              # 准则要点
    categories: tuple = ()           # 标准类目白名单
    table_markers: tuple = ()        # 认表的表头/语义白名单
    section_anchors: tuple = ()      # 章节定位锚点


# 依据《公开发行证券的公司信息披露内容与格式准则第2号(2025版)》，见 docs/规范要点-解析映射.md
REVENUE = FieldSpec(
    "revenue_breakdown", "revenue_yuan",
    ("industries", "segments", "regions", "by_channel"), label="营收", version_prefix="rev",
    spec_note=("准则第二十五条：按行业/产品/地区/销售模式披露营业收入构成。"
               "目标是(A)占营业收入比重表(金额+占比)，不是(B)收入/成本/毛利率表。"
               "若表头只有毛利率而无占比列 → 占比置空，严禁把毛利率当占比(徐工踩坑根因)。"),
    categories=("分行业", "分产品", "分地区", "分销售模式"),
    table_markers=("占营业收入比重", "营业收入比重", "占比"),
    section_anchors=("主要经营情况", "收入与成本"))

COST = FieldSpec(
    "cost_breakdown", "amount_yuan", (), label="成本", version_prefix="cst",
    spec_note=("准则第二十五条：披露营业成本主要构成项目(按性质)占成本总额比重。"
               "目标是占营业成本比重表；各项占比之和≈100%。"),
    categories=("原材料", "人工", "职工薪酬", "折旧", "能源", "动力", "制造费用"),
    table_markers=("占营业成本比重", "营业成本构成", "成本构成"),
    section_anchors=("收入与成本", "主要经营情况"))

RND = FieldSpec(
    "rnd_info", "amount_this", (), cls="B", label="研发", version_prefix="rnd",
    total_key="total_this", detail_key="rnd_detail",
    spec_note=("准则第二十五条·研发投入；附注研发费用科目明细。"
               "判据(B类)：明细 amount_this 之和 ≈ 合计 total_this。"),
    categories=("职工薪酬", "研发材料", "折旧摊销", "直接投入", "委托研发"),
    table_markers=("研发费用", "研发投入"),
    section_anchors=("研发投入", "研发费用"))

FIELDS: Dict[str, FieldSpec] = {f.field: f for f in (REVENUE, COST, RND)}


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
