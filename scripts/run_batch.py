"""跑批 CLI — python scripts/run_batch.py [code1 code2 ...]

无参数则用 PDF 缓存里已有的报告(适合本地试跑)。真小批量(50-100)把 code 列表传进来。
只跑解析 + 填分诊队列，不自动改代码。
"""

import glob
import os
import sys

sys.path.insert(0, ".")

from src.config import Config
from src.batch_runner import run_batch
from src.eval.triage_queue import summary


def main():
    codes = sys.argv[1:]
    if not codes:
        pdfs = glob.glob(str(Config.PDF_CACHE_DIR / "*_2025*.pdf"))
        codes = sorted({os.path.basename(p).split("_")[0] for p in pdfs})
    print(f"批量跑 {len(codes)} 份: {codes}")
    st = run_batch(codes, 2025)
    keys = ("done", "total", "skipped", "errors", "fields_routed", "by_reason")
    print("最终:", {k: st.get(k) for k in keys})
    print("分诊队列:", summary())


if __name__ == "__main__":
    main()
