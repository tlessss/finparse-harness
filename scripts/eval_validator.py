"""
校验器评估 — Phase 0.2

用人工金标准集评估"校验器到底可不可信"。
纯自主模式下没有人复核，系统完全信任校验器当裁判 —— 所以必须先量化它。

核心关注：**错误检出召回率（error recall）**。
  把"实际是错的"当正类：
    TP = 实际错 且 判错      （成功拦截垃圾数据）
    FN = 实际错 但 判对      ★ 最危险：垃圾数据被放进库
    FP = 实际对 但 判错      （过度拦截，浪费迭代）
    TN = 实际对 且 判对
  error_recall    = TP/(TP+FN)   ← Phase 0 验收线 ≥ 0.95
  error_precision = TP/(TP+FP)

支持的校验器（--validator）：
  hard  — 关键字段勾稽硬规则（无外部依赖，可立即跑；仅覆盖营收/研发/员工）
  llm   — scripts.auto_iterate._ai_validate（LLM+向量，需 API 与向量库）

用法：
  python3 -m scripts.eval_validator --gold goldset/goldset.json --validator hard
"""

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.validators.hard_rules import check_hard_rules

# 硬规则只覆盖这三个字段（其余字段它"无意见"，评估时跳过）
HARD_COVERED = {"revenue_breakdown", "rnd_info", "employees"}


def _predict_hard(parse_result: dict) -> dict:
    """返回 {field: predicted_correct(bool)}，仅含硬规则覆盖字段。"""
    report = check_hard_rules(parse_result)
    red_fields = set(report["red_fields"])
    return {f: (f not in red_fields) for f in HARD_COVERED}


def _reparse(stock_code: str, year: int):
    """按需重解析一份 PDF（评估 llm/hard 都需要解析结果）。"""
    from src.config import Config
    from src.engine_orchestrator import FinParseAI
    cache = Config.PDF_CACHE_DIR
    path = None
    for f in cache.iterdir():
        if f.suffix != ".pdf":
            continue
        parts = f.stem.split("_")
        if len(parts) >= 2 and parts[0] == stock_code and parts[1] == str(year):
            path = str(f)
            break
    if not path:
        return None
    return FinParseAI().run(path, stock_code=stock_code, report_year=year, db_write=False), path


def evaluate(gold_path: str, validator: str):
    with open(gold_path, encoding="utf-8") as f:
        gold = json.load(f)
    entries = gold.get("entries", [])

    # 统计：整体 + 分字段
    tp = fn = fp = tn = 0
    skipped = 0
    per_field = {}

    for e in entries:
        labels = e.get("labels", {})
        # 是否有已标注字段
        labeled = {fld: v for fld, v in labels.items() if v.get("correct") in (True, False)}
        if not labeled:
            continue

        reparse = _reparse(e["stock_code"], e.get("year"))
        if not reparse:
            print(f"  ⚠️ 跳过 {e['stock_code']}：找不到 PDF")
            continue
        parse_result, _ = reparse

        if validator == "hard":
            preds = _predict_hard(parse_result)
        elif validator == "llm":
            raise SystemExit(
                "llm 校验器依赖的 auto_iterate 已迁入 archive/scripts/；请用 --validator hard")
        else:
            raise SystemExit(f"未知 validator: {validator}")

        for fld, lab in labeled.items():
            if fld not in preds:   # 该校验器对此字段无意见
                skipped += 1
                continue
            actual_correct = lab["correct"]
            pred_correct = preds[fld]
            pf = per_field.setdefault(fld, {"tp": 0, "fn": 0, "fp": 0, "tn": 0})
            if not actual_correct and not pred_correct:
                tp += 1; pf["tp"] += 1
            elif not actual_correct and pred_correct:
                fn += 1; pf["fn"] += 1
            elif actual_correct and not pred_correct:
                fp += 1; pf["fp"] += 1
            else:
                tn += 1; pf["tn"] += 1

    _report(validator, tp, fn, fp, tn, skipped, per_field)


def _safe_div(a, b):
    return round(a / b, 4) if b else None


def _report(validator, tp, fn, fp, tn, skipped, per_field):
    total = tp + fn + fp + tn
    print(f"\n{'='*60}")
    print(f"📐 校验器评估: {validator}   (已评估字段判定 {total} 个, 跳过 {skipped})")
    print(f"{'='*60}")
    if total == 0:
        print("  ⚠️ 没有可评估的标注数据。请先在金标准集里把 correct 填成 true/false。")
        return
    print(f"  混淆矩阵 (正类=错误):")
    print(f"    TP(拦住错)={tp}  FN(漏放错)={fn}  FP(误拦对)={fp}  TN(放过对)={tn}")
    er = _safe_div(tp, tp + fn)
    ep = _safe_div(tp, tp + fp)
    print(f"  ★ error_recall    = {er}   (验收线 ≥ 0.95；FN 越少越好)")
    print(f"    error_precision = {ep}")
    print(f"    accuracy        = {_safe_div(tp + tn, total)}")
    if er is not None and er < 0.95:
        print(f"  ❌ 召回不达标：有 {fn} 个错误数据会被放进库，纯自主不可接受。")
    elif er is not None:
        print(f"  ✅ 召回达标。")
    print(f"  ── 分字段 ──")
    for fld, c in per_field.items():
        r = _safe_div(c["tp"], c["tp"] + c["fn"])
        print(f"     {fld:20s} recall={r}  TP={c['tp']} FN={c['fn']} FP={c['fp']} TN={c['tn']}")


def main():
    ap = argparse.ArgumentParser(description="校验器评估")
    ap.add_argument("--gold", required=True, help="已标注的金标准集 json")
    ap.add_argument("--validator", default="hard", choices=["hard", "llm"])
    args = ap.parse_args()
    evaluate(args.gold, args.validator)


if __name__ == "__main__":
    main()
