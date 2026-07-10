"""超级管家 (Tier 2) —— 在"确定性路由/双闸用尽或两锚分歧"处加判断。
定位:**管家提议、双锚 enforce**——金额锚 + 复核仍是正确性执行者,管家绝不入库锚拒的数据,只在
      歧义/元决策/治理处补判断。

本文件先实现 **A·两锚打架的二次裁决**:
  金额锚已过、但弱模型(DeepSeek)复核 hold —— 这是两个真值锚分歧的信号。管家用**强模型(qwen)重判**:
    · 强模型 pass = **假 hold**(弱模型固执/幻觉)→ decision=commit(仍是"金额锚 + 强模型复核"双过,没绕闸)
    · 强模型 hold = **真 hold**(数据真有问题)→ decision=real_hold + 强模型病因(供路由 healer / 带因交人工)
  只在"过锚但复核 hold"的少数触发,成本可控。
B(链尽兜底编排)、C(批量复盘驱动路线图)后续加。
"""

from typing import Dict, List

STEWARD_MODEL = "qwen3-coder-plus"   # 管家二次裁决用的强模型(DashScope;provider 由 llm_client 按名路由)


def steward_adjudicate(code: str, year: int, field: str, value, sig: Dict, spec,
                       source_override: str = None, extra_note: str = None) -> Dict:
    """A·二次裁决。前提:金额锚已过(调用方在过锚但复核 hold 出口触发)。返回:
      {decision: "commit" | "real_hold" | "keep_hold", ...}
    commit=假 hold 应入库;real_hold=真有问题(带 cause);keep_hold=强模型没能给出结论(维持原 hold)。
    source_override/extra_note:healer 路径(如 L3 重抽)要让强模型看**修好后那张表**,而不是原抽残表。"""
    from src.agents.llm_judge import verify_field
    try:
        v = verify_field(field, code, year, value, sig=sig, spec=spec, model=STEWARD_MODEL,
                         source_override=source_override, extra_note=extra_note, debug=False)
    except Exception as e:
        return {"decision": "keep_hold", "reason": f"管家强模型调用失败: {str(e)[:80]}", "model": STEWARD_MODEL}
    strong = v.get("verdict")
    if strong == "pass":
        return {"decision": "commit", "strong_verdict": "pass",
                "strong_summary": v.get("summary"), "model": STEWARD_MODEL}
    if strong == "hold":
        sus = v.get("suspects") or []
        cause = "; ".join(f"{s.get('field')}:{s.get('issue')}" for s in sus[:4]) or (v.get("summary") or "")
        return {"decision": "real_hold", "strong_verdict": "hold", "cause": cause,
                "strong_suspects": sus, "strong_summary": v.get("summary"), "model": STEWARD_MODEL}
    # unknown / 其它 → 不敢放行,维持原 hold
    return {"decision": "keep_hold", "strong_verdict": strong,
            "strong_summary": v.get("summary"), "model": STEWARD_MODEL}


def steward_diagnose(code: str, year: int, field: str = "revenue_breakdown") -> Dict:
    """管家·单案深度诊断(复制"我能找到真根因"的能力):
      收集分层证据档案(steward_probes) → 强模型自顶向下分层归因 → 结构化根因 + 处方。
    这是管家的"大脑":A(二次裁决)是它的一个反射,C(批量复盘)是批量用它 + 聚类根因驱动路线图。"""
    import json
    from src.agents.steward_probes import collect_dossier
    from src.prompts.registry import build_messages
    from src.agents.llm_client import chat
    from src.agents.llm_judge import _extract_json
    dossier = collect_dossier(code, year, field)
    bm = build_messages("steward_diagnose", {
        "code": code, "dossier": json.dumps(dossier, ensure_ascii=False, indent=1, default=str)})
    try:
        raw = chat(bm["messages"], role="judge", temperature=0, model=STEWARD_MODEL)
        diag = _extract_json(raw)
    except Exception as e:
        diag = {"error": str(e)[:100]}
    diag["dossier"] = dossier
    return diag


def steward_review(year: int = 2025, field: str = "revenue_breakdown",
                   codes: List[str] = None, log=print) -> Dict:
    """C·批量复盘(治理/自驱路线图):对失败尾巴每家跑 steward_diagnose → 聚类根因 →
    强模型出"系统体检 + 路线图"(最大桶、造/强化哪个 healer、下一步)。这是"把我人肉逐家分析变全批自动"。
    codes 缺省 = 读进度文件里 verify_hold/non_green 的失败尾巴。"""
    import json
    from collections import Counter
    from src.prompts.registry import build_messages
    from src.agents.llm_client import chat
    from src.agents.llm_judge import _extract_json
    if codes is None:
        from src.pipeline import load_progress
        prog = load_progress() or {}
        codes = [d.get("code") for d in prog.get("done", [])
                 if d.get("outcome") in ("verify_hold", "non_green")]
    diags: List[Dict] = []
    for c in codes:
        d = steward_diagnose(c, year, field)
        row = {"code": c, "layer": d.get("failure_layer"), "root_cause": d.get("root_cause"),
               "why_no_heal": d.get("why_no_heal"), "prescription": d.get("prescription"),
               "fixable_now": d.get("fixable_now")}
        diags.append(row)
        log(f"  {c}: [{row['layer']}] {row['root_cause']}")
    buckets = dict(Counter(x["layer"] for x in diags))
    bm = build_messages("steward_review", {
        "n": len(diags), "buckets": json.dumps(buckets, ensure_ascii=False),
        "diagnoses": json.dumps(diags, ensure_ascii=False, indent=1)})
    try:
        raw = chat(bm["messages"], role="judge", temperature=0, model=STEWARD_MODEL)
        roadmap = _extract_json(raw)
    except Exception as e:
        roadmap = {"error": str(e)[:100]}
    result = {"n_failures": len(diags), "buckets": buckets, "diagnoses": diags, "roadmap": roadmap}
    try:                                              # 持久化,供工作台读
        import os
        os.makedirs("goldset", exist_ok=True)
        json.dump(result, open("goldset/steward_review.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1, default=str)
    except Exception:
        pass
    return result
