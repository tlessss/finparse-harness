"""
字段解析器路由 — "选择即验证" 的核心实现
================================================

一句话：来一份报告，**不预测**该用哪个解析器，而是把"候选的已认证专用解析器"
都跑一遍，**用硬规则当尺子，谁解得干净就用谁**。没有一个达标 → needs_repair(回退冷启动/触发生成)。

为什么用硬规则当尺？
  运行期(生产)没有标准答案(golden)，没法判"到底对不对"。但硬规则(占比和≈100 等)
  是个**便宜可靠的代理信号**：算得平 ≈ 大概率解对了。
  (认证期才用 golden 真值，那是 parser_catalog.pick_mother 干的事；本文件是生产期。)

三类字段的"解对没"信号(field_plausibility)：
  A 类(营收/成本)：各维度占比之和 ≈ 100%
  B 类(研发)：     明细金额之和 ≈ 合计(±1%)
  C 类(员工)：     各维度人数之和 = 总数(±2)
"""

import os
from typing import Dict, List, Optional

from src.eval.table_cache import get_tables          # 取"引擎已抽好的表"(缓存)
from src.eval.sandbox_exec import version_parse_fn   # 把一个解析器.py 文件变成可跑的函数
from src.eval.field_spec import FieldSpec, REVENUE, as_dims
from src.eval.parser_catalog import candidates_for, tag_fingerprint   # 指纹缩候选
from src.eval.route_index import fingerprint_of, route_get, route_set, route_invalidate  # 版式→解析器缓存


def _plaus_b(spec: FieldSpec, value) -> Dict:
    """B 类(明细和≈合计)信号：研发明细 amount 之和 与 合计 相差 ≤1% 算 clean。"""
    value = value or {}
    total = value.get(spec.total_key)
    details = value.get(spec.detail_key) or []
    amts = [d.get(spec.amount_key) for d in details if d.get(spec.amount_key) is not None]
    if not (total and total > 0 and len(amts) >= 2):
        return {"clean": False, "rows": len(details), "diff_pct": None}
    diff_pct = abs(sum(amts) - total) / total * 100
    return {"clean": diff_pct <= 1.0, "rows": len(details), "diff_pct": round(diff_pct, 2)}


def _plaus_c(spec: FieldSpec, value) -> Dict:
    """C 类(分项和=总数)信号：员工每个维度(专业/教育)人数之和 与 总数 相差 ≤2 算 clean。"""
    value = value or {}
    total = value.get(spec.total_key)
    if not (total and total > 0):
        return {"clean": False, "ok_dims": 0, "n_dims": 0, "rows": 0}
    ok, rows, ndims = 0, 0, 0
    for d in spec.dims:                       # 遍历该字段的各维度(如 composition/education)
        items = value.get(d) or []
        counts = [r.get(spec.amount_key) for r in items if r.get(spec.amount_key) is not None]
        if not counts:
            continue
        ndims += 1
        rows += len(items)
        if abs(sum(counts) - total) <= 2:    # 这个维度加起来≈总数
            ok += 1
    return {"clean": ndims >= 1 and ok == ndims, "ok_dims": ok, "n_dims": ndims, "rows": rows}


def _plaus_a(spec: FieldSpec, value) -> Dict:
    """A 类(占比构成)：把结果拆成各维度，逐维度看占比之和是否落在 97~103。"""
    dd = as_dims(value, spec)
    dims = [d for d in dd if dd[d]]
    if not dims:
        return {"clean": False, "ratio_ok_dims": 0, "n_dims": 0, "rows": 0}
    ok, rows = 0, 0
    for d in dims:
        ratios = [r.get(spec.ratio_key) for r in dd[d] if r.get(spec.ratio_key) is not None]
        rows += len(dd[d])
        if ratios and 97 <= sum(ratios) <= 103:
            ok += 1
    return {"clean": ok == len(dims) and rows >= 2,
            "ratio_ok_dims": ok, "n_dims": len(dims), "rows": rows}


def _parsed_total(spec: FieldSpec, value):
    """解析出的'分项金额之和'，用于对锚。B 类取合计；A 类取各维度和的最大值(各维度都该≈锚)。"""
    if spec.cls == "B":
        return (value or {}).get(spec.total_key)
    best = None
    for rows in as_dims(value, spec).values():
        s = sum((r.get(spec.amount_key) or 0) for r in (rows or []))
        if s and (best is None or s > best):
            best = s
    return best


def _attach_confidence(spec: FieldSpec, value, anchors, sig: Dict) -> Dict:
    """跨表锚 → 置信度。分项和≈锚(DB营收/成本/研发,±3%)=high；没锚上=low；无锚=unknown。
    只加信号、不改 clean —— 路由仍按硬规则,只是把'凑巧达标却对不上锚'的标低置信→送 #2 复核。"""
    key = getattr(spec, "anchor_key", "")
    anchor = (anchors or {}).get(key) if key else None
    if not anchor:
        sig["anchored"], sig["confidence"] = None, "unknown"
        return sig
    if spec.cls == "B":
        total = (value or {}).get(spec.total_key)
        anchored = bool(total and abs(total - anchor) <= 0.03 * anchor)
    else:
        # A 类：各维度(行业/产品/地区/销售模式)是同一营收总额的不同切分 → **任一维度和≈锚即过锚**。
        # 某维度抓串/漏行(如000333的segments)不该否定整体；旧的"取最大维度"会被抓大的坏维度毒化。
        dim_sums = [s for s in (sum((r.get(spec.amount_key) or 0) for r in (rows or []))
                                for rows in as_dims(value, spec).values()) if s]
        anchored = any(abs(s - anchor) <= 0.03 * anchor for s in dim_sums)
        total = min(dim_sums, key=lambda s: abs(s - anchor)) if dim_sums else None  # 展示:最接近锚的维度和
    sig["anchored"] = anchored
    sig["confidence"] = "high" if anchored else "low"
    sig["anchor"] = anchor
    sig["parsed_total"] = total
    return sig


def field_plausibility(spec: FieldSpec, value, anchors: Dict = None) -> Dict:
    """
    运行期"解对没"硬规则信号(无 golden 时判对错的代理) + 跨表锚置信度。按字段类别 A/B/C 分派：
      B → 明细和≈合计；C → 分项和=总数；其余(A) → 各维度占比和≈100。

    ── 入参 value 的格式(就是某字段解析出来的结果，按 spec.cls 不同形状不同) ──
    A 类(营收/成本)：多维字典，每维是行列表
        {"segments":   [{"name": str, "revenue_yuan": float|None, "ratio_pct": float|None}, ...],
         "industries": [...], "regions": [...], "by_channel": [...]}        # 成本是扁平 list
    B 类(研发)：
        {"rnd_detail": [{"name": str, "amount_this": float, "amount_last": float}, ...],
         "total_this": float, "total_last": float}
    C 类(员工)：
        {"total": int,
         "composition": [{"type": str, "count": int}, ...],
         "education":   [{"type": str, "count": int}, ...]}

    ── 返回 ── {"clean": bool, ...各类各自的明细指标}
    """
    if spec.cls == "B":
        sig = _plaus_b(spec, value)
    elif spec.cls == "C":
        sig = _plaus_c(spec, value)
    else:
        sig = _plaus_a(spec, value)
    sig = _attach_confidence(spec, value, anchors, sig)
    # A 类：锚为主、占比为辅 —— 分项和过 DB 锚就算可信(不必有占比列;锚比占比和≈100 靠谱)。
    # 占比仍保留(_plaus_a)：救按产品成本(锚对不上但各产品占比和=100)和无锚兜底。
    if spec.cls == "A" and sig.get("anchored"):
        sig["clean"] = True
    return sig


def revenue_plausibility(rb: Optional[Dict]) -> Dict:
    """营收便捷入口。"""
    return field_plausibility(REVENUE, rb)


def route_field(spec: FieldSpec, code: str, year: int,
                catalog: List[Dict] = None, fingerprint: str = None) -> Dict:
    """
    字段通用"选择即验证"路由。

    返回 dict：
      status      : "routed"(命中专用解析器) | "needs_repair"(无人达标→回退/生成)
      parser/_key : 命中的解析器路径/标识
      result      : 命中解析器解出的结果
      signal      : 硬规则信号详情
      tried       : 试过哪些候选、各自 clean 否
      fingerprint : 这份报告的版式指纹
      cache_hit   : 是否走了"缓存路由"快路径

    传 catalog 参数时走"纯候选"模式(测试用)，不碰缓存/指纹索引。
    """
    field = spec.field
    base = {"field": field}
    # 前提：表必须已在缓存里(引擎 cache_put 进去的)。没有就没法跑候选。
    if get_tables(code, year) is None:
        return {"status": "needs_repair", "parser": None, "parser_key": None,
                "result": None, "signal": None, "tried": [], "reason": "无缓存表", **base}
    use_index = catalog is None                       # 生产模式 vs 测试模式
    fp = fingerprint if fingerprint is not None else (fingerprint_of(code, year) if use_index else None)
    # 跨表锚(DB营收/成本/研发)：给信号附置信度。测试模式(传 catalog)不查库。
    anchors = None
    if use_index:
        try:
            from src.eval.anchors import get_anchors
            anchors = get_anchors(code, year)
        except Exception:
            anchors = None

    # ── ① 缓存命中快路径：这个版式以前路由过谁，就直接跑那一个 ──
    if use_index and fp:
        cached = route_get(field, fp)                 # 查"版式指纹 → 解析器路径"
        if cached and os.path.exists(cached):
            try:
                rb = version_parse_fn(cached)(code, year)    # 只跑这一个(冻结代码)
                sig = field_plausibility(spec, rb, anchors)
            except Exception:
                rb, sig = None, {"clean": False}
            if sig.get("clean"):                      # 硬规则仍达标 → 直接用，最快
                return {"status": "routed", "parser": cached, "parser_key": "(缓存路由)",
                        "result": rb, "signal": sig, "tried": [], "fingerprint": fp,
                        "cache_hit": True, "candidates": 1, **base}
            route_invalidate(field, fp)               # 不达标=版式漂移了→作废缓存，往下重选

    # ── ② 指纹缩候选 → 逐个跑 → 硬规则选优 ──
    cands = candidates_for(field, fp, catalog)         # 用指纹把一堆解析器缩成几个候选
    best, tried = None, []
    for c in cands:
        try:
            rb = version_parse_fn(c["path"])(code, year)     # 跑这个候选(它会解出一份结果)
            sig = field_plausibility(spec, rb)               # 给它的结果打硬规则信号
        except Exception:
            rb, sig = None, {"clean": False, "ratio_ok_dims": 0, "n_dims": 0, "rows": 0}
        tried.append((c["key"], sig["clean"]))
        # 排序键：先看干不干净，再看几个维度达标，再看行数 → 选"最像解对了"的
        key = (sig.get("clean"), sig.get("ratio_ok_dims", 0), sig.get("rows", 0))
        if best is None or key > best[0]:
            best = (key, c, rb, sig)

    # 最优候选确实 clean → 命中，并记住"这个版式以后用它"(写缓存，下次走①)
    if best and best[3]["clean"]:
        _, c, rb, sig = best
        if use_index and fp:
            route_set(field, fp, c["path"])           # 版式→解析器，记忆
            tag_fingerprint(c["path"], fp)
        return {"status": "routed", "parser": c["path"], "parser_key": c["key"],
                "result": rb, "signal": sig, "tried": tried, "fingerprint": fp,
                "cache_hit": False, "candidates": len(cands), **base}
    # 谁都没解干净 → 让上层回退冷启动 / 触发"生成专用解析器"
    return {"status": "needs_repair", "parser": None, "parser_key": None,
            "result": None, "signal": (best[3] if best else None), "tried": tried,
            "fingerprint": fp, "candidates": len(cands),
            "reason": "无认证解析器硬规则达标 → 冷启动/生成", **base}


def route_revenue(code: str, year: int, catalog: List[Dict] = None,
                  fingerprint: str = None) -> Dict:
    """营收便捷入口（= route_field(REVENUE)）。"""
    return route_field(REVENUE, code, year, catalog, fingerprint)
