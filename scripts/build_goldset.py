"""
金标准集构建器 — Phase 0.1

把一次基线跑批的结果，转成"待人工标注"的金标准模板：
每个字段预填解析值 + 硬规则提示，人工只需把 labels.<field>.correct
从 null 改成 true（对）/ false（错），并可在 note 里写原因。

金标准集是纯自主的"裁判的裁判"：用它来评估校验器（见 eval_validator.py）
到底可不可信。覆盖建议：50~100 份，含制造/银行/券商/无研发等多版式。

用法：
  # 1) 先跑基线得到 records 文件
  python3 -m scripts.baseline_run --limit 80 --out test_results/baseline_80.json
  # 2) 生成待标注模板
  python3 -m scripts.build_goldset --from test_results/baseline_80.json --out goldset/goldset.json
  # 3) 人工编辑 goldset/goldset.json，把每个 correct: null 改成 true/false
"""

import os
import sys
import json
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

ALL_FIELDS = ["revenue_breakdown", "rnd_info", "employees",
              "cost_breakdown", "top_clients", "top_suppliers"]


def build(from_path: str, out_path: str, only_present: bool = False):
    with open(from_path, encoding="utf-8") as f:
        data = json.load(f)
    records = data.get("records", [])

    entries = []
    for r in records:
        if r.get("error"):
            continue
        present = r.get("present", {})
        red_fields = set(r.get("red_fields", []))
        labels = {}
        for fld in ALL_FIELDS:
            has = present.get(fld, False)
            if only_present and not has:
                continue
            labels[fld] = {
                "correct": None,                       # ← 人工填 true / false
                "present": has,
                "hard_rule": "red" if fld in red_fields else "ok/na",
                "note": "",
            }
        entries.append({
            "stock_code": r["stock_code"],
            "year": r.get("year"),
            "labels": labels,
        })

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "_instructions": "把每个 labels.<field>.correct 从 null 改为 true(对)/false(错)。"
                             "未标注(null)的字段在评估时会被跳过。",
            "source": from_path,
            "field_count": len(ALL_FIELDS),
            "entries": entries,
        }, f, ensure_ascii=False, indent=2)

    n_fields = sum(len(e["labels"]) for e in entries)
    print(f"✅ 已生成金标准模板: {out_path}")
    print(f"   样本 {len(entries)} 份，待标注字段 {n_fields} 个")
    print(f"   下一步: 人工编辑该文件，把 correct: null 改成 true/false")


def main():
    ap = argparse.ArgumentParser(description="构建金标准集模板")
    ap.add_argument("--from", dest="from_path", required=True, help="baseline 结果 json")
    ap.add_argument("--out", default="goldset/goldset.json")
    ap.add_argument("--only-present", action="store_true",
                    help="只为已解析出的字段建标注位（缺失字段不纳入）")
    args = ap.parse_args()
    build(args.from_path, args.out, only_present=args.only_present)


if __name__ == "__main__":
    main()
