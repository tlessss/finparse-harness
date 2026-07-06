#!/usr/bin/env python3
"""对比 pipeline.py 关键符号与 PipelineFlow.tsx 节点 id，辅助更新 /console/flow。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PIPELINE = ROOT / "src" / "pipeline.py"
FLOW_TSX = ROOT / "frontend" / "src" / "app" / "console" / "PipelineFlow.tsx"

# pipeline 里应在流程图中有对应分支的符号
EXPECTED_SYMBOLS = {
    "run_field": "scan",
    "route_field": "d_route",
    "_parse_versioned": "cold",
    "field_plausibility": "d_anchor",
    "verify_field": "verify",
    "_heal_and_verify": "heal",
    "heal_select": "heal",
    "_rule_heal_and_verify": "rule",
    "_nongreen_llm": "diag",
    "prepare_judge_diagnose": "diag",
    "_auto_commit": "t_commit",
}

# 流程图应存在的核心节点 id
CORE_NODE_IDS = {
    "scan", "d_route", "cold", "d_anchor", "verify", "d_verify",
    "heal", "d_heal", "rule", "d_gate", "verifyT", "d_verifyT",
    "diag", "t_commit", "t_commitH", "t_human", "t_humanT",
    "t_nosuch", "t_diag", "no_input", "no_data", "no_anchor",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_node_ids(tsx: str) -> set[str]:
    ids: set[str] = set()
    for m in re.finditer(r'\{\s*id:\s*"([a-zA-Z0-9_]+)"', tsx):
        ids.add(m.group(1))
    return ids


def extract_pipeline_funcs(py: str) -> set[str]:
    return set(re.findall(r"^def ([a-zA-Z_][a-zA-Z0-9_]*)", py, re.M))


def main() -> int:
    if not PIPELINE.is_file():
        print(f"ERROR: missing {PIPELINE}", file=sys.stderr)
        return 1
    if not FLOW_TSX.is_file():
        print(f"ERROR: missing {FLOW_TSX}", file=sys.stderr)
        return 1

    py = read(PIPELINE)
    tsx = read(FLOW_TSX)
    node_ids = extract_node_ids(tsx)
    funcs = extract_pipeline_funcs(py)

    print("=== PipelineFlow node ids ===")
    print(f"count: {len(node_ids)}")
    print(" ".join(sorted(node_ids)))

    missing_core = CORE_NODE_IDS - node_ids
    if missing_core:
        print("\n=== MISSING core nodes in TSX ===")
        for x in sorted(missing_core):
            print(f"  - {x}")

    print("\n=== Symbol → node mapping check ===")
    for sym, node in sorted(EXPECTED_SYMBOLS.items()):
        in_py = sym in funcs or sym in py
        in_flow = node in node_ids
        flag = "OK" if (in_py and in_flow) else "WARN"
        print(f"  [{flag}] {sym} → {node}  (in_py={in_py}, in_flow={in_flow})")

    print("\n=== Key pipeline functions present ===")
    for f in sorted(funcs):
        if f.startswith("_") or f in ("run_field", "heal_select", "field_chain"):
            print(f"  {f}")

    print("\nHint: 更新流程图请读 .cursor/skills/update-pipeline-flow/SKILL.md")
    return 0 if not missing_core else 1


if __name__ == "__main__":
    sys.exit(main())
