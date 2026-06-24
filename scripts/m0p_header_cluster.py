"""
M0′ 营收表头结构聚类 — "每版式一解析器" 的决定性测算

M0（文档指纹）已证明：文档级章节指纹不堪当版式 key，真实解析器数夹在 27～271。
M0′ 换一个**贴近"同一个营收解析器能不能复用"**的 key：营收表的**语义列结构**。

对每份 PDF：
  scan_pdf → filter_by_signature(,"revenue") 取最佳营收表
  → detect_columns_by_header 认出 {name/revenue/ratio/cost/gross} 各在第几列
  → 语义列结构签名 = 这些列按列序拼成 "name>revenue>ratio>cost"
  → classify_revenue_table 判 composition/margin

聚类口径（核心思想 docs/多agent编排设计.md §三/§5.1）：
  · 语义列结构相同 → 大概率同一个营收解析器能解 → 可复用/可 fork
  · 这才是真实"营收解析器复用率"，落在 27～271 间的那个真数

输出：
  · 找不到营收表的份数（这是抽表层问题，单列，不算进结构聚类）
  · 语义结构版式数 + 摊薄曲线（覆盖X%报告需多少种结构）
  · 原始表头签名（更细，看措辞抖动上界）
  · 可选 --baseline 关联净通过率

用法：
  python3 -m scripts.m0p_header_cluster --limit 60                      # 冒烟
  python3 -m scripts.m0p_header_cluster --baseline test_results/baseline_60.json --out test_results/m0p_full.json
"""

import os
import sys
import json
import time
import argparse
import re
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config
from src.parsers.infra.table_scanner import scan_pdf, filter_by_signature
from src.parsers.infra.header_columns import detect_columns_by_header, classify_revenue_table, _column_headers
from src.parsers.infra.rule_loader import load_rule

_SEM_ORDER = ["name", "revenue", "ratio", "cost", "gross"]


def discover_pdfs(limit: int = 0) -> list:
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
        if (code, yr) in seen:
            continue
        seen.add((code, yr))
        out.append({"stock_code": code, "year": yr, "pdf_path": str(f)})
    return out[:limit] if limit > 0 else out


def semantic_signature(cols: dict) -> str:
    """present 语义列按列序拼成结构签名，如 'name>revenue>ratio'。"""
    present = [(cols[s], s) for s in _SEM_ORDER if cols.get(s) is not None]
    present.sort()
    return ">".join(s for _, s in present) if present else "(无语义列)"


_NOISE = re.compile(r"[\s\d%,，.。():：（）\-—/]+")


def raw_header_signature(table: list) -> str:
    """各列表头文本去噪后按列序拼接，反映原始措辞（更细的 key）。"""
    hdrs = _column_headers(table, scan_rows=3)
    toks = []
    for c in sorted(hdrs):
        t = _NOISE.sub("", str(hdrs[c]))
        if t:
            toks.append(t)
    return "|".join(toks) if toks else "(空表头)"


def amortization_curve(sizes: list, total: int) -> dict:
    sizes = sorted(sizes, reverse=True)
    t = {50: None, 80: None, 90: None, 95: None}
    cu = 0
    for i, s in enumerate(sizes, 1):
        cu += s
        for k in t:
            if t[k] is None and cu / max(total, 1) >= k / 100:
                t[k] = i
    return {f"覆盖{k}%需结构数": t[k] for k in sorted(t)}


def run(limit: int = 0, baseline: str = None, out_path: str = None):
    rule = load_rule("revenue") or {}
    rb = rule.get("revenue_breakdown", {})
    aliases = rb.get("header_aliases")
    if not aliases:
        print("❌ 读不到 revenue.yaml 的 header_aliases，无法认列。")
        return

    pdfs = discover_pdfs(limit=limit)
    print(f"🔍 缓存: {Config.PDF_CACHE_DIR}\n📋 待聚类: {len(pdfs)} 份\n")
    if not pdfs:
        return

    base_pass = {}
    if baseline and os.path.exists(baseline):
        for r in json.load(open(baseline, encoding="utf-8")).get("records", []):
            base_pass[r.get("stock_code")] = r.get("net_pass", False)
        print(f"🔗 关联基线: {len(base_pass)} 条\n")

    out_path = out_path or os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "test_results", f"m0p_header_{time.strftime('%Y%m%d_%H%M%S')}.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    records, t0 = [], time.time()
    for i, p in enumerate(pdfs, 1):
        rec = {"stock_code": p["stock_code"], "year": p["year"]}
        try:
            tables = scan_pdf(p["pdf_path"])
            ranked = filter_by_signature(tables, "revenue")
            if not ranked:
                rec.update({"has_table": False, "semantic_sig": None,
                            "table_type": None, "raw_sig": None})
            else:
                top = ranked[0]["table"]
                cols = detect_columns_by_header(top, aliases)
                rec.update({
                    "has_table": True,
                    "semantic_sig": semantic_signature(cols),
                    "table_type": classify_revenue_table(top, rb),
                    "raw_sig": raw_header_signature(top),
                    "n_cols": max((len(r) for r in top), default=0),
                    "page": ranked[0]["page"],
                })
        except Exception as e:
            rec.update({"has_table": None, "error": str(e)[:120],
                        "semantic_sig": None, "table_type": None, "raw_sig": None})
        records.append(rec)

        if i % 25 == 0 or i == len(pdfs):
            got = sum(1 for r in records if r.get("has_table"))
            sigs = len(set(r["semantic_sig"] for r in records if r.get("semantic_sig")))
            print(f"  [{i}/{len(pdfs)}] {time.time()-t0:.0f}s  有表={got}  结构数={sigs}")
            json.dump({"complete": i == len(pdfs), "records": records},
                      open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    summary = aggregate(records, base_pass)
    json.dump({"complete": True, "summary": summary, "records": records},
              open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print_summary(summary, out_path)


def aggregate(records: list, base_pass: dict) -> dict:
    total = len(records)
    errored = sum(1 for r in records if r.get("has_table") is None)
    no_table = sum(1 for r in records if r.get("has_table") is False)
    withtab = [r for r in records if r.get("has_table")]
    nt = len(withtab)

    by_sem = defaultdict(list)
    for r in withtab:
        by_sem[r["semantic_sig"]].append(r)
    sem_sizes = [len(v) for v in by_sem.values()]
    raw_sigs = set(r["raw_sig"] for r in withtab)

    top = sorted(by_sem.items(), key=lambda kv: -len(kv[1]))[:12]
    top_clusters = []
    for sig, members in top:
        codes = [m["stock_code"] for m in members]
        passes = [base_pass[c] for c in codes if c in base_pass]
        tt = Counter(m["table_type"] for m in members)
        top_clusters.append({
            "semantic_sig": sig, "size": len(members),
            "table_type分布": dict(tt),
            "sampled_pass": (f"{sum(passes)}/{len(passes)}" if passes else "-"),
        })

    return {
        "总份数": total,
        "抽表报错": errored,
        "找不到营收表": no_table,
        "有营收表": nt,
        "有表占比%": round(nt / max(total, 1) * 100, 1),
        "── 结构聚类(决定性) ──": "",
        "语义结构数": len(by_sem),
        "结构/有表比": round(len(by_sem) / max(nt, 1), 3),
        "单例结构数": sum(1 for s in sem_sizes if s == 1),
        "最大结构簇": max(sem_sizes) if sem_sizes else 0,
        "摊薄曲线": amortization_curve(sem_sizes, nt),
        "原始表头签名数": len(raw_sigs),
        "table_type分布": dict(Counter(r["table_type"] for r in withtab)),
        "top结构簇": top_clusters,
    }


def print_summary(s: dict, out_path: str):
    print(f"\n{'='*66}")
    print(f"📊 M0′ 营收表头结构聚类  —  {s['总份数']} 份")
    print(f"{'='*66}")
    print(f"  抽表报错        : {s['抽表报错']}")
    print(f"  找不到营收表    : {s['找不到营收表']}  ← 抽表层问题，非结构问题")
    print(f"  有营收表        : {s['有营收表']} ({s['有表占比%']}%)")
    print(f"  ── 结构聚类（决定性：≈ 需多少种营收解析器）──")
    print(f"  语义结构数      : {s['语义结构数']}   (结构/有表比 {s['结构/有表比']})")
    print(f"  单例结构        : {s['单例结构数']}   最大簇 {s['最大结构簇']} 份")
    print(f"  原始表头签名数  : {s['原始表头签名数']}  (更细，措辞抖动上界)")
    print(f"  table_type 分布 : {s['table_type分布']}")
    for k, v in s["摊薄曲线"].items():
        print(f"     {k}: {v}")
    print(f"  ── top 结构簇 ──")
    for c in s["top结构簇"]:
        print(f"     {c['size']:>4}份 通过={c['sampled_pass']:>5} {c['table_type分布']}  {c['semantic_sig']}")
    print(f"\n  结果文件: {out_path}")


def main():
    ap = argparse.ArgumentParser(description="M0′ 营收表头结构聚类")
    ap.add_argument("--limit", type=int, default=0, help="0=全量")
    ap.add_argument("--baseline", type=str, default=None)
    ap.add_argument("--out", type=str, default=None)
    args = ap.parse_args()
    run(limit=args.limit, baseline=args.baseline, out_path=args.out)


if __name__ == "__main__":
    main()
