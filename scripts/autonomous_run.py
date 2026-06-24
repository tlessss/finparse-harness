"""
全自主跑批 — Phase 4 规模化批处理

对 2025 年全部财报 PDF 做无人值守解析，特性：
  - 多进程并行（绕过 GIL，CPU 满载）
  - 单份硬超时（SIGALRM）：卡死的 PDF 不拖垮整批（故障隔离）
  - 断点续跑：结果按行追加到 JSONL，重启自动跳过已完成
  - 硬规则闸门：正确率优先，红线不通过的归入死信队列，绝不当作"完成"
  - 死信队列：error / 硬规则红线 的难例单独归集，供 Phase 2 优化或人工兜底

安全：默认 **不写数据库**（db_write=False），只产出结果文件。
确认质量后再用 --db-write 单独入库（避免把未校验数据写进生产库）。

用法：
  python3 -m scripts.autonomous_run --limit 30                 # 抽样验证
  python3 -m scripts.autonomous_run --limit 0 --workers 6      # 全量 2025
  python3 -m scripts.autonomous_run --limit 0 --resume run_state/2025  # 续跑
"""

import os
import sys
import json
import time
import signal
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.config import Config

ALL_FIELDS = ["revenue_breakdown", "rnd_info", "employees",
              "cost_breakdown", "top_clients", "top_suppliers"]

# 每个 worker 进程内复用引擎（懒加载，跨任务持久）
_ENGINE = None


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from src.engine_orchestrator import FinParseAI
        _ENGINE = FinParseAI()
    return _ENGINE


def _parse_one(task: dict) -> dict:
    """worker 入口：解析一份 PDF + 硬规则 + 指纹。带硬超时，异常全部兜住。"""
    code, year, path, timeout_sec = task["stock_code"], task["year"], task["pdf_path"], task["timeout"]
    rec = {"stock_code": code, "year": year}
    start = time.time()

    def _on_timeout(signum, frame):
        raise TimeoutError(f"parse timeout >{timeout_sec}s")

    old = signal.signal(signal.SIGALRM, _on_timeout)
    signal.alarm(timeout_sec)
    try:
        from src.validators.hard_rules import check_hard_rules
        from src.parsers.layout_fingerprint import compute_fingerprint

        fp = compute_fingerprint(path)
        r = _get_engine().run(path, stock_code=code, report_year=year, db_write=False)
        hard = check_hard_rules(r)

        fc = r.get("field_count", 0)
        status = "clean" if hard["passed"] else "red"
        rec.update({
            "status": status,
            "field_count": fc,
            "present": {f: bool(r.get(f)) for f in ALL_FIELDS},
            "hard_passed": hard["passed"],
            "red_count": hard["red_count"],
            "warn_count": hard["warn_count"],
            "red_fields": hard["red_fields"],
            "violations": hard["violations"],
            "doc_type": fp.get("doc_type"),
            "fingerprint": fp.get("hash"),
            "net_pass": hard["passed"] and fc == 6,
            "duration_sec": round(time.time() - start, 1),
        })
    except TimeoutError as e:
        rec.update({"status": "timeout", "error": str(e), "field_count": 0, "net_pass": False})
    except Exception as e:
        rec.update({"status": "error", "error": f"{type(e).__name__}: {e}",
                    "field_count": 0, "net_pass": False})
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
    return rec


def discover(year: int, limit: int) -> list:
    cache = Config.PDF_CACHE_DIR
    seen, out = set(), []
    if not cache.exists():
        return out
    for f in sorted(cache.iterdir()):
        if f.suffix != ".pdf":
            continue
        parts = f.stem.split("_")
        if len(parts) < 2 or not (parts[1].isdigit() and len(parts[1]) == 4):
            continue
        code, yr = parts[0], int(parts[1])
        if yr != year or code in seen:
            continue
        seen.add(code)
        out.append({"stock_code": code, "year": yr, "pdf_path": str(f)})
    return out[:limit] if limit > 0 else out


def _load_done(jsonl_path: str) -> set:
    done = set()
    if os.path.exists(jsonl_path):
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["stock_code"])
                except Exception:
                    pass
    return done


def run(year=2025, limit=0, workers=6, timeout=180, state_dir=None):
    if state_dir is None:
        state_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                 "run_state", str(year))
    os.makedirs(state_dir, exist_ok=True)
    jsonl = os.path.join(state_dir, "results.jsonl")
    deadletter = os.path.join(state_dir, "deadletter.jsonl")

    all_tasks = discover(year, limit)
    done = _load_done(jsonl)
    todo = [t for t in all_tasks if t["stock_code"] not in done]
    for t in todo:
        t["timeout"] = timeout

    print(f"🚀 全自主跑批 {year} | 总 {len(all_tasks)} | 已完成 {len(done)} | 待跑 {len(todo)} "
          f"| workers={workers} timeout={timeout}s")
    if not todo:
        print("🎉 全部已完成")
        _summarize(jsonl)
        return

    t0 = time.time()
    counts = {"clean": 0, "red": 0, "error": 0, "timeout": 0}
    n_done = 0
    fout = open(jsonl, "a", encoding="utf-8")
    fdead = open(deadletter, "a", encoding="utf-8")
    try:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_parse_one, t): t for t in todo}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    rec = fut.result()
                except Exception as e:
                    rec = {"stock_code": t["stock_code"], "year": t["year"],
                           "status": "error", "error": f"future: {e}", "net_pass": False}
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n"); fout.flush()
                st = rec.get("status", "error")
                counts[st] = counts.get(st, 0) + 1
                if st in ("red", "error", "timeout"):
                    fdead.write(json.dumps(rec, ensure_ascii=False) + "\n"); fdead.flush()
                n_done += 1
                if n_done % 10 == 0 or n_done == len(todo):
                    el = time.time() - t0
                    rate = n_done / el if el else 0
                    eta = (len(todo) - n_done) / rate if rate else 0
                    print(f"  [{n_done}/{len(todo)}] clean={counts['clean']} red={counts['red']} "
                          f"err={counts['error']} to={counts['timeout']} "
                          f"| {rate:.2f}/s ETA {eta/60:.1f}min")
    finally:
        fout.close(); fdead.close()

    print(f"\n✅ 本轮完成 {n_done} 份，耗时 {(time.time()-t0)/60:.1f}min")
    _summarize(jsonl)


def _summarize(jsonl: str):
    recs = []
    with open(jsonl, encoding="utf-8") as f:
        for line in f:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    n = len(recs)
    if not n:
        return
    clean = sum(1 for r in recs if r.get("status") == "clean")
    netp = sum(1 for r in recs if r.get("net_pass"))
    red = sum(1 for r in recs if r.get("status") == "red")
    err = sum(1 for r in recs if r.get("status") in ("error", "timeout"))
    print(f"\n{'='*60}\n📊 累计汇总  共 {n} 份\n{'='*60}")
    print(f"  硬规则清洁(clean) : {clean}  ({clean/n*100:.1f}%)")
    print(f"  ★ 净通过(6/6+清洁) : {netp}  ({netp/n*100:.1f}%)")
    print(f"  死信-红线(red)    : {red}")
    print(f"  死信-报错/超时    : {err}")
    # 字段覆盖
    print(f"  ── 字段覆盖 ──")
    for fld in ALL_FIELDS:
        c = sum(1 for r in recs if r.get("present", {}).get(fld))
        print(f"     {fld:20s} {c}/{n} ({c/n*100:.0f}%)")
    # doc_type 分布
    dt = {}
    for r in recs:
        dt[r.get("doc_type", "?")] = dt.get(r.get("doc_type", "?"), 0) + 1
    print(f"  doc_type: {dt}")


def main():
    ap = argparse.ArgumentParser(description="全自主跑批")
    ap.add_argument("--year", type=int, default=2025)
    ap.add_argument("--limit", type=int, default=0, help="0=全量")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--timeout", type=int, default=180, help="单份解析硬超时(秒)")
    ap.add_argument("--resume", type=str, default=None, help="状态目录(续跑)")
    args = ap.parse_args()
    run(year=args.year, limit=args.limit, workers=args.workers,
        timeout=args.timeout, state_dir=args.resume)


if __name__ == "__main__":
    main()
