"""
主表锚 — 跨表勾稽的"独立真值"来源

A 类(营收/成本)原判据"占比和≈100"是表内自洽，选错表也可能凑巧通过。本模块提供
**外部权威锚**(营业收入/营业成本/研发费用)，让 field_plausibility 额外要求
"分项金额之和 ≈ 锚"，把弱自洽升级成强锚定（审计师做法）。

锚来源优先级：
  ① DB financial_reports.income_statement(JSON) —— 权威、~99% 覆盖、与利润表一致。
     注意：扁平列 revenue/cost 是脏数据(有负值/0)，**只认 income_statement JSON**。
  ② 兜底：解析利润表(新报告还没入库时)。

键统一为 DB 命名：revenue / cost / rnd_expense。
"""

import json
from typing import Dict, Optional

from src.eval.table_cache import get_tables
from src.parsers.infra.table_scanner import parse_money

_KEYS = ("revenue", "cost", "rnd_expense")
_CACHE: Dict[str, Dict] = {}                       # 进程内缓存，避免重复查库


def _from_db(code: str, year: int) -> Dict[str, float]:
    try:
        from src.database import get_conn
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # 同一(代码,年)可能有 年报+季报 多条;我们解析的是**年报** → 必须优先取 annual,
                # 否则会拿到 q3 累计数当锚(如 601127 赛力斯:q3=1105亿 vs 年报=1650亿)。
                cur.execute("SELECT income_statement FROM financial_reports "
                            "WHERE stock_code=%s AND report_year=%s "
                            "ORDER BY (report_quarter='annual') DESC, report_date DESC LIMIT 1",
                            (code, year))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row or not row.get("income_statement"):
            return {}
        d = json.loads(row["income_statement"])
        out = {}
        for k in _KEYS:
            v = d.get(k)
            if isinstance(v, (int, float)) and v > 0:
                out[k] = float(v)
        return out
    except Exception:
        return {}                                  # 无库/查询失败 → 交给兜底


def _norm(s) -> str:
    return "".join(ch for ch in str(s or "") if ch not in " 　\t\n：:")


def _from_tables(code: str, year: int) -> Dict[str, float]:
    """兜底：从利润表(含'营业总收入'的表)抠营业收入/营业成本。"""
    tables = get_tables(code, year)
    if not tables:
        return {}
    profit = next((t for t in tables
                   if "营业总收入" in "".join((c or "") for row in (t.get("table") or []) for c in row)), None)
    if profit is None:
        return {}
    label = {"revenue": "营业收入", "cost": "营业成本"}
    out: Dict[str, float] = {}
    for row in profit.get("table") or []:
        name = _norm(next((c for c in row if c and _norm(c)), ""))
        for key, zh in label.items():
            if key in out or "总" in name or not name.endswith(zh):
                continue
            for c in row:
                m = parse_money(c) if c else None
                if m is not None and m > 0:
                    out[key] = m
                    break
    return out


def _row_money(row) -> Optional[float]:
    """行里第一个能解析成金额的单元格(跳过名称列)。"""
    for c in row[1:] if len(row) > 1 else row:
        m = parse_money(c) if c else None
        if m is not None and m > 0:
            return m
    return None


_UNIT_MULTS = (1, 10000, 1000, 100000000)   # 元/万元/千元/亿元


def _derive_main_business(code: str, year: int, revenue: float) -> Optional[float]:
    """给金额锚的**第二口径**:主营业务收入。会计准则里维度构成表披露的是"主营业务分行业/产品/地区",
    Σ分项 = 主营 ≤ 营业收入(差的是其他业务收入,常不按维度拆)。所以正确的主营构成表可能对不上营收锚、只对主营锚。
    抽法:找一张同时有"主营业务(收入)"和"其他业务(收入)"两行、且**两者之和≈营业收入**(试单位倍数)的表,取主营那行。
    两者和≈营收 = 交叉校验,确保抽的是营收拆分表、且单位对。抽不到 → None(退回只用营收锚)。"""
    if not revenue:
        return None
    for t in (get_tables(code, year) or []):
        g = t.get("table") or []
        names = "".join((row[0] or "") for row in g if row)   # 快速预筛:名称列同时含两标记才细看
        if "主营业务" not in names or "其他业务" not in names:
            continue
        main = other = None
        for row in g:
            name = _norm(row[0] if row else "")
            v = _row_money(row)
            if v is None:
                continue
            if "主营业务" in name and main is None:
                main = v
            elif "其他业务" in name and other is None:
                other = v
        if main and other:
            for mult in _UNIT_MULTS:
                if abs((main + other) * mult - revenue) <= 0.03 * revenue:
                    return main * mult
    return None


def get_anchors(code: str, year: int) -> Dict[str, float]:
    """返回 {revenue, cost, rnd_expense, [main_revenue]}（抽不到的键缺省）。DB 为主、利润表兜底，缓存。
    main_revenue = 主营业务收入,金额锚的第二口径(见 _derive_main_business)。"""
    ck = f"{code}_{year}"
    if ck in _CACHE:
        return _CACHE[ck]
    a = dict(_from_db(code, year) or _from_tables(code, year))
    if a.get("revenue"):
        mb = _derive_main_business(code, year, a["revenue"])
        if mb and mb < a["revenue"]:                     # 主营应 ≤ 营收
            a["main_revenue"] = mb
    _CACHE[ck] = a
    return a


def anchor_for(spec, code: str, year: int) -> Optional[float]:
    """取某字段对应的锚值（spec.anchor_key）；无则 None。"""
    key = getattr(spec, "anchor_key", "")
    if not key:
        return None
    return get_anchors(code, year).get(key)
