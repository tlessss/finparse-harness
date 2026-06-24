"""
生成专用解析器智能体 (M5 自动化) — LLM 写代码 → 沙箱跑 → 闸判 → 失败重试

闭环（构建期，自进化的心脏）：
  失败案例的表 + v0错在哪(不给golden真值,防硬编码) → LLM 写 parse(tables)
    → 沙箱在缓存表上跑 → 打分器 vs golden → 版本闸 accept_candidate
    → accept 则收；reject 则把"分数+哪错了"回灌，重试 ≤K 轮

正确性与模型强弱解耦：弱模型(DeepSeek)写错→闸 reject→多迭代，不会错填。

用法：
  from src.agents.code_generator import generate_parser
  r = generate_parser("000425", 2025, golden_entry, base_fn, "src/parsers/versions/rev_000425_auto.py")
"""

import re
from typing import Dict, Callable

from src.agents.llm_client import chat
from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import version_parse_fn
from src.eval.run_eval import eval_version, accept_candidate
from src.parsers.infra.table_scanner import filter_by_signature

_CONTRACT = '''你要写一个 A 股年报"营收分项"专用解析器。严格实现契约：

def parse(tables, context=None) -> dict:
    # tables: 列表，每项 = {"page":页码, "table":二维数组(每格是字符串或None),
    #                      "text":整表文字, "section":章节, "cell_bbox":..., "table_bbox":...}
    # 返回: {"industries":[{"name":str,"revenue_yuan":float,"ratio_pct":float}, ...],
    #        "segments":[...], "regions":[...]}   # 只填能确定的维度
    ...

要点（很重要）：
- 营收分项表里，行用"分行业/分产品/分地区"标记切桶(industries/segments/regions)。
- **陷阱**：常有两张像的表——①毛利率表(列是营业收入/营业成本/毛利率)②占比构成表(列是金额/占营业收入比重)。
  你要的是②。判别法：在某个维度桶内，占比列的各行%求和≈100；毛利率列求和远不到100；"同比增减"列正负混合也不到100。
  取"桶内%求和≈100 的最左列"当占比列；金额列=占比列左侧最近的大额数字列。
- 取当年(最左)那一组金额/占比，不要去年。跳过合计/小计行。
- 可用工具(可 import)：from src.parsers.infra.table_scanner import parse_money, parse_ratio, is_total_row
- 只输出 Python 代码，不要解释。'''


def _render_candidates(tables, n=3) -> str:
    ranked = filter_by_signature(tables, "revenue")[:n]
    out = [f"营收候选表 {len(ranked)} 张（仅供你了解结构，运行时你拿到的是全部 tables）："]
    for i, c in enumerate(ranked):
        out.append(f"\n候选{i} 页{c['page']}:")
        for row in c["table"][:14]:
            out.append("  " + " | ".join((x or "").replace("\n", " ").strip()[:16] for x in row))
    return "\n".join(out)


def _extract_code(raw: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", raw, re.DOTALL)
    return (m.group(1) if m else raw).strip()


def _feedback(rep: Dict, out_rb: Dict) -> str:
    """有诊断性的负反馈（给结构知识，不泄露 golden 真值）。"""
    n_out = sum(len(v) for v in (out_rb or {}).values())
    lines = [f"你的解析器得分 {rep['score']}（需无漏行/错值才通过）。"]
    if rep.get("error"):
        lines.append(f"运行报错：{rep['error']}")
    elif n_out == 0:
        lines.append(
            "你的输出**完全为空**，八成是选表失败。最常见的坑："
            "❶ 不要用『整列%求和≈100』判占比列！营收构成表里同一列把 "
            "分行业(~100)+分产品(~100)+分地区(~100) 叠成 ~300，落不进 [95,105]，于是你一张表都没选中。"
            "正确做法：先按『分行业/分产品/分地区』标记把数据行切成维度桶，再**在每个桶内**对%求和≈100 来认占比列。"
            "❷ 注意 is_total_row 的参数是『行名字符串』不是整行列表。"
            "❸ 选表时取『桶内%求和最接近100』的列(用最小偏差,不是最大)。")
    else:
        probs = "; ".join(f"[{m.get('dim')}]{m.get('name')}:{m.get('issue')}"
                          for m in (rep.get("mismatches") or [])[:8])
        lines.append(f"问题：{probs}。")
        # 整维度漏失的诊断（通用结构知识）
        miss = [d for d in ("industries", "segments", "regions")
                if not (out_rb or {}).get(d)
                and any(m.get("dim") == d for m in (rep.get("mismatches") or []))]
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
                    max_rounds: int = 8, log=print) -> Dict:
    """终点=完全正确(exact)。没到 exact 就继续想办法；到上限仍不 exact → 转人工。
    绝不在半成品(部分正确)上停下。base_fn 仅作安全护栏(不退步)，不是 stop 条件。"""
    tables = get_tables(code, year)
    if tables is None:
        return {"accepted": False, "error": "无缓存表"}

    failure = ("v0(现有解析器)在这份上得分 0：它选错了表(把毛利率当占比)、"
               "且分项漏行。请写一个能正确选占比构成表、认对列、提全所有分项的解析器。")
    messages = [
        {"role": "system", "content": "你是资深 Python 工程师，精通解析中文财报表格。"},
        {"role": "user", "content": f"{_CONTRACT}\n\n股票 {code} {year}。\n{_render_candidates(tables)}\n\n{failure}"},
    ]

    for r in range(1, max_rounds + 1):
        raw = chat(messages, role="codegen")
        src = _extract_code(raw)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(src)

        try:
            v1_fn = version_parse_fn(out_path)
            out_rb = v1_fn(code, year)
            ev = eval_version(v1_fn, [golden_entry])
            safety = accept_candidate(base_fn, v1_fn, [golden_entry])  # 仅护栏：是否比v0退步
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
                     {"role": "user", "content": _feedback(rep, out_rb)}]

    # 想尽 max_rounds 仍没到 exact → 不留半成品，转人工
    return {"accepted": False, "escalate": "human", "rounds": max_rounds,
            "best_score": score, "out_path": out_path}
