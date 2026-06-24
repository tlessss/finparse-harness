"""
M0 可行性测算 — "每版式一解析器 + 人在回路认证" 成不成立

只算指纹（取文本，快，不解析表格），回答命门问题：
  1289 份 PDF 聚成多少种版式？版式数 ≪ 报告数 才赚。
  · 摊薄曲线：覆盖 50%/80%/90% 报告各需多少个版式（= 需人工认证多少次）
  · 单例率：只出现 1 次的版式占比（无法摊薄的长尾）
  · 推演 5000+/年：按当前聚类形态外推新版式生成率

可选 --baseline test_results/baseline_60.json：按 stock_code 关联已跑的解析结果，
窥见各版式的现有解析器通过率（样本小，仅参考）。

用法：
  python3 -m scripts.m0_fingerprint_survey                 # 全量指纹聚类
  python3 -m scripts.m0_fingerprint_survey --limit 200     # 抽样
  python3 -m scripts.m0_fingerprint_survey --baseline test_results/baseline_60.json
"""

import os
import sys
import json
import time
import argparse
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.parsers.infra.layout_fingerprint import compute_fingerprint


def discover_pdfs(limit: int = 0) -> list:
    """扫缓存，按 (stock_code, year) 去重，返回全部年份。"""
    cache_dir = Config.PDF_CACHE_DIR
    if not cache_dir.exists():
        print(f"❌ PDF 缓存目录不存在: {cache_dir}")
        return []
    seen, out = set(), []
    for f in sorted(cache_dir.iterdir()):
        if f.suffix != ".pdf":
            continue
        parts = f.stem.split("_")
        if len(parts) < 2 or not (parts[1].isdigit() and len(parts[1]) == 4):
            continue
        code, yr = parts[0], int(parts[1])
        key = (code, yr)
        if key in seen:
            continue
        seen.add(key)
        out.append({"stock_code": code, "year": yr, "pdf_path": str(f)})
    return out[:limit] if limit > 0 else out


def amortization_curve(cluster_sizes: list, total: int) -> dict:
    """从大到小累加版式，看覆盖 X% 报告需要多少个版式。"""
    sizes = sorted(cluster_sizes, reverse=True)
    cum, marks, targets = 0, {}, {50: None, 80: None, 90: None, 95: None}
    for i, s in enumerate(sizes, 1):
        cum += s
        pct = cum / total * 100
        for t in targets:
            if targets[t] is None and pct >= t:
                targets[t] = i
    return {f"覆盖{t}%需版式数": targets[t] for t in sorted(targets)}


def run(limit: int = 0, baseline: str = None, out_path: str = None):
    pdfs = discover_pdfs(limit=limit)
    print(f"🔍 缓存: {Config.PDF_CACHE_DIR}")
    print(f"📋 待算指纹: {len(pdfs)} 份\n")
    if not pdfs:
        return

    # 可选：关联已有解析结果（按 stock_code）
    base_pass = {}
    if baseline and os.path.exists(baseline):
        bd = json.load(open(baseline, encoding="utf-8"))
        for r in bd.get("records", []):
            base_pass[r.get("stock_code")] = r.get("net_pass", False)
        print(f"🔗 关联基线 {baseline}: {len(base_pass)} 条解析结果\n")

    records, t0 = [], time.time()
    for i, p in enumerate(pdfs, 1):
        try:
            fp = compute_fingerprint(p["pdf_path"])
        except Exception as e:
            fp = {"hash": "error", "doc_type": "error", "signature": str(e),
                  "page_count": 0, "keyword_categories": []}
        records.append({
            "stock_code": p["stock_code"], "year": p["year"],
            "hash": fp.get("hash"), "doc_type": fp.get("doc_type"),
            "signature": fp.get("signature"), "page_count": fp.get("page_count"),
            "n_categories": len(fp.get("keyword_categories", [])),
        })
        if i % 50 == 0 or i == len(pdfs):
            print(f"  [{i}/{len(pdfs)}] {time.time()-t0:.0f}s  "
                  f"distinct_hash={len(set(r['hash'] for r in records))}")

    summary = aggregate(records, base_pass)
    out_path = out_path or os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "test_results", f"m0_fingerprint_{time.strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    json.dump({"summary": summary, "records": records},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print_summary(summary, out_path, len(records))


def aggregate(records: list, base_pass: dict) -> dict:
    total = len(records)
    by_hash = defaultdict(list)
    for r in records:
        by_hash[r["hash"]].append(r)
    sizes = [len(v) for v in by_hash.values()]
    singletons = sum(1 for s in sizes if s == 1)

    # 最大的若干版式簇
    top = sorted(by_hash.items(), key=lambda kv: -len(kv[1]))[:15]
    top_clusters = []
    for h, members in top:
        codes = [m["stock_code"] for m in members]
        passes = [base_pass[c] for c in codes if c in base_pass]
        top_clusters.append({
            "hash": h, "size": len(members),
            "doc_type": members[0]["doc_type"],
            "signature": members[0]["signature"],
            "sampled_pass": (f"{sum(passes)}/{len(passes)}" if passes else "-"),
        })

    return {
        "总报告数": total,
        "不同版式数(hash)": len(by_hash),
        "不同签名数(signature)": len(set(r["signature"] for r in records)),
        "版式/报告比": round(len(by_hash) / max(total, 1), 3),
        "单例版式数": singletons,
        "单例占比%": round(singletons / max(len(by_hash), 1) * 100, 1),
        "最大簇覆盖": max(sizes) if sizes else 0,
        "doc_type分布": dict(Counter(r["doc_type"] for r in records)),
        "摊薄曲线": amortization_curve(sizes, total),
        "top版式簇": top_clusters,
    }


def print_summary(s: dict, out_path: str, total: int):
    print(f"\n{'='*64}")
    print(f"📊 M0 版式指纹测算  —  {s['总报告数']} 份报告")
    print(f"{'='*64}")
    print(f"  不同版式数(hash)   : {s['不同版式数(hash)']}")
    print(f"  版式/报告比        : {s['版式/报告比']}   (越小越赚)")
    print(f"  单例版式           : {s['单例版式数']} ({s['单例占比%']}%)  ← 无法摊薄的长尾")
    print(f"  最大簇覆盖          : {s['最大簇覆盖']} 份")
    print(f"  doc_type 分布       : {s['doc_type分布']}")
    print(f"  ── 摊薄曲线（认证N次覆盖X%报告）──")
    for k, v in s["摊薄曲线"].items():
        print(f"     {k}: {v}")
    print(f"  ── 最大的版式簇 ──")
    for c in s["top版式簇"][:10]:
        print(f"     {c['size']:>4}份 [{c['doc_type']}] 通过={c['sampled_pass']}  {c['signature'][:60]}")
    print(f"\n  结果文件: {out_path}")


def main():
    ap = argparse.ArgumentParser(description="M0 版式指纹可行性测算")
    ap.add_argument("--limit", type=int, default=0, help="0=全量")
    ap.add_argument("--baseline", type=str, default=None, help="关联已跑解析结果(json)")
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()
    run(limit=args.limit, baseline=args.baseline, out_path=args.out)


if __name__ == "__main__":
    main()
