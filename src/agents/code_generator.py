"""
生成专用解析器智能体 (M5 自动化) — LLM 写代码 → 沙箱跑 → 闸判 → 失败重试

闭环（构建期，自进化的心脏）：
  失败案例的表 + v0错在哪(不给golden真值,防硬编码) → LLM 写 parse(tables)
    → 沙箱在缓存表上跑 → 打分器 vs golden → 版本闸 accept_candidate
    → accept 则收；reject 则把"分数+哪错了"回灌，重试 ≤K 轮

正确性与模型强弱解耦：弱模型(DeepSeek)写错→闸 reject→多迭代，不会错填。

用法：
  from src.agents.code_generator import generate_parser
  r = generate_parser("000425", 2025, golden_entry, base_fn, "archive/demo-parsers/rev_000425_auto.py")
"""

import re
from typing import Dict, Callable

from src.agents.llm_client import chat
from src.agents.llm_routing import resolve_model
from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import version_parse_fn, version_parse_fn_sandboxed
from src.eval.run_eval import eval_version, accept_candidate
from src.eval.field_spec import REVENUE
from src.parsers.infra.table_scanner import filter_by_signature

# 字段 → filter_by_signature 的信号类型
_SIG_TYPE = {"revenue_breakdown": "revenue", "cost_breakdown": "cost",
             "rnd_info": "rnd", "employees": "employee",
             "top_clients": "client", "top_suppliers": "supplier"}


def _shape_str(spec) -> str:
    row = '{"name":str,"%s":float,"ratio_pct":float}' % spec.amount_key
    if spec.dims:
        return '{"%s":[%s, ...], ...}  # 维度键: %s' % (spec.dims[0], row, "/".join(spec.dims))
    return '[%s, ...]  # 扁平列表' % row


def _build_contract(spec) -> str:
    """规范驱动的生成契约：注入该字段的准则口径(类目/表特征/章节/校验)。"""
    bucket = (f"按 {'/'.join(spec.categories)} 标记切桶" if spec.dims else "扁平列表(无需切桶)")
    marker = spec.table_markers[0] if spec.table_markers else "占比"
    return f'''你要写一个 A 股年报"{spec.label}"专用解析器。严格实现契约：

def parse(tables, context=None):
    # tables: 列表,每项={{"page","table"(二维数组,每格字符串或None),"text","section","cell_bbox"}}
    # 返回: {_shape_str(spec)}

准则口径(《公开发行证券的公司信息披露内容与格式准则第2号》)：
  {spec.spec_note}
  标准类目(白名单): {'/'.join(spec.categories) or '(扁平,无固定类目)'}
  目标表特征(表头/语义含其一): {'/'.join(spec.table_markers)}
  所在章节: {'/'.join(spec.section_anchors)}

要点：
- {bucket}。占比列必须由"{marker}"类表头/语义命中——准则口径优先,别只靠字面。
- 判别占比列：在(维度桶内)占比列各行 % 求和≈100(避开毛利率/同比列)；无占比列则置空,严禁拿毛利率顶替。
- 金额列=占比列左侧最近的大额数字列；取当年(最左)那组；跳过合计/小计行。
- 可 import: from src.parsers.infra.table_scanner import parse_money, parse_ratio, is_total_row
- 只输出 Python 代码,不要解释。'''


def _render_candidates(tables, spec, n=3) -> str:
    ranked = filter_by_signature(tables, _SIG_TYPE.get(spec.field, "revenue"))[:n]
    out = [f"{spec.label}候选表 {len(ranked)} 张（仅供了解结构，运行时你拿到的是全部 tables）："]
    for i, c in enumerate(ranked):
        out.append(f"\n候选{i} 页{c['page']}:")
        for row in c["table"][:14]:
            out.append("  " + " | ".join((x or "").replace("\n", " ").strip()[:16] for x in row))
    return "\n".join(out)


def _extract_code(raw: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
    return (m.group(1) if m else raw).strip()


def _feedback(rep: Dict, out_rb, spec=None) -> str:
    """有诊断性的负反馈（给结构知识，不泄露 golden 真值）。"""
    spec = spec or REVENUE
    n_out = (sum(len(v) for v in out_rb.values()) if isinstance(out_rb, dict)
             else len(out_rb or []))
    marker = spec.table_markers[0] if spec.table_markers else "占比"
    lines = [f"你的解析器得分 {rep['score']}（需无漏行/错值才通过）。"]
    if rep.get("error"):
        lines.append(f"运行报错：{rep['error']}")
    elif n_out == 0 and spec.dims:        # 多维字段(营收)的空输出诊断
        lines.append(
            "你的输出**完全为空**，八成是选表失败。最常见的坑："
            "❶ 不要用『整列%求和≈100』判占比列！构成表里同一列把多个维度(各~100)叠成 ~200/300，"
            "落不进 [95,105]，于是一张表都没选中。正确做法：先按维度标记把行切桶，再**在每个桶内**对%求和≈100。"
            "❷ is_total_row 的参数是『行名字符串』不是整行列表。"
            "❸ 选表取『桶内%求和最接近100』的列。")
    elif n_out == 0:                       # 扁平字段(成本等)的空输出诊断
        lines.append(
            f"你的输出**完全为空**，八成是选表/认列失败。注意：占比列要由『{marker}』类表头命中；"
            "各行%求和≈100；金额列在占比列左侧；is_total_row 传行名字符串。")
    else:
        probs = "; ".join(f"[{m.get('dim')}]{m.get('name')}:{m.get('issue')}"
                          for m in (rep.get("mismatches") or [])[:8])
        lines.append(f"问题：{probs}。")
        # 整维度漏失的诊断（仅多维字段，如营收）
        miss = ([d for d in spec.dims
                 if isinstance(out_rb, dict) and not out_rb.get(d)
                 and any(m.get("dim") == d for m in (rep.get("mismatches") or []))]
                if spec.dims else [])
        if "industries" in miss:
            lines.append(
                "你**完全漏了 industries 维度**。常见原因：该维度的标记被吸收/缺失——"
                "唯一的行业行(如『工程机械行业』)往往直接出现在『分产品』标记**之前**，没有单独『分行业』标记行。"
                "处理：把任何『分X』标记出现**之前**的数据行(有金额+占比的行)默认归到 industries；"
                "遇到『分产品』/『分地区』标记再切到对应桶。")
        elif miss:
            lines.append(f"你完全漏了维度 {miss}，检查这些『分X』标记是否被你的切桶逻辑识别。")
        lines.append("针对这些修正。")
    lines.append("重新只输出完整代码。")
    return "\n".join(lines)


def generate_parser(code: str, year: int, golden_entry: Dict,
                    base_fn: Callable, out_path: str,
                    max_rounds: int = 8, mother_path: str = None,
                    spec=None, log=print) -> Dict:
    """终点=完全正确(exact)。没到 exact 就继续想办法；到上限仍不 exact → 转人工。
    绝不在半成品上停下。base_fn 仅作安全护栏(不退步)。mother_path 给定则 fork 模式。
    spec 决定字段(默认营收) + 注入准则口径(规范驱动)。"""
    spec = spec or REVENUE
    tables = get_tables(code, year)
    if tables is None:
        return {"accepted": False, "error": "无缓存表"}

    failure = (f"现有解析器在这份的{spec.label}上失败。请按上面准则口径，"
               f"正确选目标表、认对列、提全所有分项。")
    contract = _build_contract(spec)
    cand = _render_candidates(tables, spec)
    if mother_path:
        with open(mother_path, encoding="utf-8") as f:
            mother_src = f.read()
        first = (f"{contract}\n\n股票 {code} {year}。\n{cand}\n\n{failure}\n\n"
                 f"下面是一个**相似版式的已认证解析器(母本)**，请**在它基础上改(fork)**来适配本报告，"
                 f"尽量复用对的部分、只改差异处：\n```python\n{mother_src}\n```")
    else:
        first = f"{contract}\n\n股票 {code} {year}。\n{cand}\n\n{failure}"
    messages = [
        {"role": "system", "content": "你是资深 Python 工程师，精通解析中文财报表格。"},
        {"role": "user", "content": first},
    ]

    for r in range(1, max_rounds + 1):
        raw = chat(messages, role="codegen", model=resolve_model("codegen"))
        src = _extract_code(raw)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(src)

        try:
            v1_fn = version_parse_fn(out_path)
            out_rb = v1_fn(code, year)
            ev = eval_version(v1_fn, [golden_entry], spec)
            safety = accept_candidate(base_fn, v1_fn, [golden_entry], spec)  # 仅护栏：是否比v0退步
        except Exception as e:
            import traceback as _tb
            frames = _tb.format_exc().strip().splitlines()
            tail = "\n".join(frames[-4:])      # 末几帧 = 出错的具体行
            hint = ("（提示：is_total_row 的参数是行名字符串如 row[0]，不是整行列表 row）"
                    if "has no attribute 'replace'" in str(e) else "")
            log(f"  [第{r}轮] 代码报错: {str(e)[:100]}")
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content":
                          f"上面的代码运行报错：\n{tail}\n{hint}\n定位到具体那行修正，重新只输出完整代码。"}]
            continue

        rep = ev["per_report"][0]
        score = rep["score"]
        log(f"  [第{r}轮] 分={score} exact={rep.get('exact')} (不退步={not safety['regressions']})")

        # 终点 = 完全正确。只有 exact 才算成功收下。
        if rep.get("exact"):
            return {"accepted": True, "rounds": r, "score": score, "out_path": out_path}

        # 没全对 → 继续想办法（把还差哪喂回去）
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": _feedback(rep, out_rb, spec)}]

    # 想尽 max_rounds 仍没到 exact → 不留半成品，转人工
    return {"accepted": False, "escalate": "human", "rounds": max_rounds,
            "best_score": score, "out_path": out_path}


def _feedback_autonomous(spec, out_rb, sig, verify_res, anchors, err=None) -> str:
    """无 golden 的负反馈:喂『各维度 vs 金额锚偏差 + 复核疑点』——给结构方向,不泄真值(自主态本就没真值)。"""
    if err:
        return f"上面的代码运行报错：\n{err}\n定位到具体那行修正，重新只输出完整代码。"
    anchor = (anchors or {}).get(spec.anchor_key or "revenue")
    n_out = (sum(len(v) for v in out_rb.values()) if isinstance(out_rb, dict)
             else len(out_rb or [])) if out_rb else 0
    lines = ["还没过双闸（金额锚 + 复核）。"]
    if n_out == 0:
        lines.append("输出**完全为空**——多半选表/认列失败：先确认选对目标构成表、认对金额列"
                     "（占比列左侧的大额数字列，跳合计/小计行）、按维度标记(分行业/分产品/分地区/分销售)切桶。")
    elif anchor and isinstance(out_rb, dict):
        parts = []
        for d, rows in out_rb.items():
            if rows:
                s = sum((r.get(spec.amount_key) or 0) for r in rows)
                parts.append(f"{d}={s / anchor:.2f}(n={len(rows)})")
        lines.append(f"各维度合计/营收锚(目标每维≈1.00±3%): {' '.join(parts) or '无维度'}。")
        lines.append("偏小=漏行(该维度行没抽全 / 跨页续表没拼上 / 行名被吸并)；"
                     "偏大=重复计数(父项与『其中』子项都计了 / 合计行混入 / 多维堆进一个桶)。")
    if verify_res and verify_res.get("verdict") != "pass":
        sus = "; ".join(f"[{s.get('field')}]{s.get('issue')}:{(s.get('reason') or '')[:50]}"
                        for s in (verify_res.get("suspects") or [])[:6])
        if sus:
            lines.append(f"复核疑点: {sus}。")
    lines.append("针对这些修正，重新只输出完整代码。")
    return "\n".join(lines)


def _output_summary(spec, val, anchor) -> str:
    """一版解析输出的可读小结:每维 n、合计/锚、前几个行名——喂给 LLM 看『哪维偏 1.00』。"""
    if isinstance(val, dict):
        lines = []
        for d, rows in val.items():
            s = sum((r.get(spec.amount_key) or 0) for r in rows)
            names = "、".join((r.get("name") or "")[:12] for r in rows[:6])
            rel = f"{s / anchor:.2f}×锚" if anchor else f"{s:,.0f}"
            lines.append(f"  {d}: n={len(rows)} 合计={rel} → {names}")
        return "\n".join(lines) or "(输出为空——多半选表/认列失败)"
    return str(val)[:400] if val else "(输出为空——多半选表/认列失败)"


def _anchor_score(spec, val, anchor) -> tuple:
    """给一版输出打分做爬山:**主看总偏差**(越小越好,防某维爆表如 n=192/7.67×被误当最好),
    再看几维在 ±3% 内(越多越好)。空维记满偏差 1.0。"""
    if not isinstance(val, dict) or not anchor:
        return (-9e9, -1)
    ok, dev = 0, 0.0
    for _, rows in val.items():
        if not rows:
            dev += 1.0
            continue
        rel = sum((r.get(spec.amount_key) or 0) for r in rows) / anchor
        if abs(rel - 1) <= 0.03:
            ok += 1
        dev += abs(rel - 1)
    return (-dev, ok)


def _current_parser_evidence(code, year, spec, tables) -> tuple:
    """『现有(基线)解析器的源码 + 它在这份报告上的(错)输出』——让 LLM 看着代码和症状定位 bug,
    而不是从零瞎写。源码取当前生效的字段解析器类;输出跑一遍它、按维度列合计/锚偏差。"""
    import inspect
    src, out_txt = "(取源码失败)", "(取输出失败)"
    try:
        from src.parsers.infra.pdf_locator import ensure_pdf
        from src.engine_orchestrator import FinParseAI
        from src.eval.anchors import get_anchors
        pdf = ensure_pdf(code, year)
        parser = FinParseAI()._get_parser(spec.field, pdf)
        src = inspect.getsource(type(parser))
        val = (parser.parse(pdf, pre_scan=tables, code=code, year=year) or {}).get(spec.field)
        anchor = (get_anchors(code, year) or {}).get(spec.anchor_key or "revenue")
        out_txt = _output_summary(spec, val, anchor)
    except Exception as e:
        out_txt = f"(运行现有解析器报错: {str(e)[:100]})"
    return src, out_txt


def generate_parser_autonomous(code: str, year: int, spec, out_path: str,
                               max_rounds: int = 6, mother_path: str = None, log=print) -> Dict:
    """自主态代码生成（自愈用,无 golden 真值）。验收 = **双闸**：金额锚(field_plausibility=high)
    AND 复核(verify_field=pass)。两个都过才收；上限仍不过 → 转人工。
    与构建期 generate_parser 的唯一区别就是这道闸(golden.exact → 双闸)——正确性靠锚兜底,不靠真值对照。
    ⚠️ 现仍走本进程 sandbox_exec；接入无人值守前须升级 subprocess 隔离(Gap #2)。"""
    from src.eval.anchors import get_anchors
    from src.agents.llm_judge import verify_field
    from src.parsers.revenue_router import field_plausibility
    from src.prompts.registry import build_messages
    tables = get_tables(code, year)
    if tables is None:
        return {"accepted": False, "error": "无缓存表"}
    anchors = get_anchors(code, year) or {}
    field = spec.field
    anchor = anchors.get(spec.anchor_key or "revenue")
    mother_block = ""
    if mother_path:
        with open(mother_path, encoding="utf-8") as f:
            mother_src = f.read()
        mother_block = ("\n\n下面是**相似版式的已认证解析器(母本)**，在它基础上改(fork)适配本报告，"
                        f"复用对的部分、只改差异处：\n```python\n{mother_src}\n```")
    cand = _render_candidates(tables, spec)
    # 首轮"要改的代码"=现有基线解析器 + 它的错输出;之后=**自己最好的那一版**(爬山,防多轮对话漂移)
    cur_parser, cur_output = _current_parser_evidence(code, year, spec, tables)
    best = None   # {"score","value","sig","src","err"}
    for r in range(1, max_rounds + 1):
        failure = ("现有解析器过不了双闸，请定位 bug 改对。" if r == 1 else
                   "上面是你目前**最好的一版**代码及其输出——**只针对性修还没到 1.00×锚 的那几维**"
                   "(偏小=漏行/跨页没拼、偏大=父子/合计重复计数)，**别把已经对的维度改坏**。")
        # prompt 走 Prompt Registry(templates/codegen.yaml)——流程图可显示、管理页可热编辑
        bm = build_messages("codegen", {
            "label": spec.label, "shape": _shape_str(spec), "spec_note": spec.spec_note,
            "categories": "/".join(spec.categories) or "(扁平列表)",
            "table_markers": "/".join(spec.table_markers),
            "section_anchors": "/".join(spec.section_anchors),
            "anchor": f"{anchor:,.0f}" if anchor else "(未取到，用合计行自校验)",
            "code": code, "year": year, "candidates": cand,
            "current_parser": cur_parser, "current_output": cur_output,
            "failure": failure, "mother_block": mother_block,
        })
        raw = chat(bm["messages"], role="codegen", model=resolve_model("codegen"), max_tokens=8000)
        src = _extract_code(raw)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(src)
        out_rb, sig, vres = None, {"confidence": None}, None
        try:
            out_rb = version_parse_fn_sandboxed(out_path)(code, year)   # 子进程隔离跑不可信 LLM 代码
            sig = field_plausibility(spec, out_rb or {}, anchors)
        except Exception:
            pass
        anchor_ok = sig.get("confidence") == "high" or sig.get("clean")
        if anchor_ok and out_rb:                                  # 闸1 过锚 → 闸2 复核
            vres = verify_field(field, code, year, out_rb, sig=sig, spec=spec)
            if vres.get("verdict") == "pass":
                log(f"  [第{r}轮] 双闸通过 ✓（过锚 + 复核 pass）→ 收下")
                return {"accepted": True, "rounds": r, "out_path": out_path,
                        "value": out_rb, "sig": sig}
        score = _anchor_score(spec, out_rb, anchor)
        if best is None or score > best["score"]:                 # 只留历史最好的一版
            best = {"score": score, "value": out_rb, "sig": sig, "src": src, "vres": vres}
        log(f"  [第{r}轮] 过锚={bool(anchor_ok)} 复核={(vres or {}).get('verdict')} 打分={score} 最好={best['score']}")
        # 爬山:下一轮以"目前最好的一版代码 + 它的输出"为基准去改(而非无限增长的对话)
        cur_parser = best["src"] or cur_parser
        cur_output = _output_summary(spec, best["value"], anchor)
        bv = best.get("vres") or {}   # 最好那版已过锚但复核挑刺(如某行取错年份列)→ 把疑点也喂回,否则 qwen 以为过锚就完事、看不到逐项错
        if bv.get("verdict") and bv.get("verdict") != "pass" and bv.get("suspects"):
            sus = "; ".join(f"[{s.get('field')}]{s.get('issue')}:{(s.get('reason') or '')[:70]}"
                            for s in bv["suspects"][:5])
            cur_output += f"\n※ 各维合计虽≈锚，但**复核仍挑出下列错，必须逐条修对否则不入库**：{sus}"

    if best and best.get("src"):                                  # 落最好的一版(不是最后一版),转人工
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(best["src"])
    log(f"  {max_rounds} 轮仍没过双闸(最好打分={best['score'] if best else None}) → 转人工")
    return {"accepted": False, "escalate": "human", "rounds": max_rounds, "out_path": out_path,
            "value": best["value"] if best else None, "sig": best["sig"] if best else None}


def repair(code: str, year: int, golden_entry: Dict, base_fn: Callable,
           out_path: str, catalog=None, fork_lo: float = 0.3, spec=None, log=print) -> Dict:
    """
    三岔修复决策（fork 优先，字段通用按 spec）：先用选择即验证挑最像的已认证母本，据分定路：
      母本 exact → 复用(不调LLM)；母本部分(≥lo) → fork；都很差 → 新建。
    """
    spec = spec or REVENUE
    from src.eval.parser_catalog import pick_mother
    mpath, mscore, mkey = pick_mother(code, year, golden_entry[spec.field], catalog, spec)

    if mscore >= 0.999:
        log(f"🔁 复用：已认证母本『{mkey}』对本报告 exact → 直接用，无需生成 LLM")
        return {"action": "reuse", "accepted": True, "parser": mpath, "score": mscore}

    if mpath and mscore >= fork_lo:
        log(f"🍴 fork：最像母本『{mkey}』(分{mscore}) → 在它基础上改")
        r = generate_parser(code, year, golden_entry, base_fn, out_path,
                            mother_path=mpath, spec=spec, log=log)
    else:
        log(f"🆕 新建：无合适母本(最高分{mscore}) → 从零写")
        r = generate_parser(code, year, golden_entry, base_fn, out_path, spec=spec, log=log)
    r["action"] = "fork" if (mpath and mscore >= fork_lo) else "new"
    return r
