#!/usr/bin/env python3
"""从 docs/mock-verify-prompt-v2.md §三-B 同步 frontend/src/data/verifyPromptExamples.json。"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MD = ROOT / "docs" / "mock-verify-prompt-v2.md"
OUT = ROOT / "frontend" / "src" / "data" / "verifyPromptExamples.json"

META = [
    {
        "id": "TC-01",
        "title": "营收 pass",
        "scenario": "A 类四维完整、表对、逐项对",
        "expected_verdict": "pass",
        "expected_issue": None,
        "pipeline": "commit",
    },
    {
        "id": "TC-02",
        "title": "wrong_table",
        "scenario": "选中表=分地区销售情况表",
        "expected_verdict": "hold",
        "expected_issue": "wrong_table",
        "pipeline": "heal_select",
    },
]


def main() -> None:
    md = MD.read_text(encoding="utf-8")
    blocks = re.findall(
        r"### 3B\.\d+ TC-\d+.*?\n\n<details>.*?```json\n(\[\s*\{.*?\}\s*\])\n```",
        md,
        re.DOTALL,
    )
    expected_blocks = re.findall(
        r"\*\*期望 LLM 回复[^*]+\*\*\n\n```json\n(\{.*?\})\n```",
        md,
        re.DOTALL,
    )
    if len(blocks) != len(META) or len(expected_blocks) != len(META):
        raise SystemExit(
            f"parse mismatch: messages={len(blocks)} expected={len(expected_blocks)} meta={len(META)}"
        )

    examples = []
    for meta, block, exp in zip(META, blocks, expected_blocks):
        examples.append(
            {
                **meta,
                "version": "v2-mock",
                "source": "docs/mock-verify-prompt-v2.md §三-B",
                "messages": json.loads(block),
                "expected_reply": json.loads(exp),
            }
        )

    payload = {
        "schema_version": "verify_rendered_examples_v1",
        "agent_id": "verify",
        "note": "完整渲染 prompt（无占位符），设计稿 v2，未接入生产 build_verify_messages",
        "examples": examples,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(examples)} examples)")


if __name__ == "__main__":
    main()
