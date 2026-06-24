---
name: flow-visualizer
description: "Use when the user wants to understand or explain a system/codebase as an interactive flow diagram. Triggers: '把这个系统/架构/流程画出来', '可视化一下流程', '画个流程图/架构图', 'visualize the pipeline/architecture/data flow', 'make an interactive diagram', or any request to see how data/control moves through a project end-to-end."
---

# Flow Visualizer

Turn a system into a **single self-contained interactive HTML diagram**: layered nodes you can click to reveal each stage's file, responsibility, key points, and real numbers. Zero external dependencies — open the file in any browser.

The rendering is **data-driven with auto-layout**: you describe the system as `NODES` + `EDGES` + per-node `detail`; the engine computes coordinates and draws the SVG. You never hand-place rectangles.

## When to use

- User asks to understand/explain how a project works end-to-end.
- Onboarding material, architecture docs, an interview-ready walkthrough of one's own project.
- After building/refactoring a pipeline, to show before/after or the current shape.

If the user just wants a quick textual explanation, answer directly — only build the HTML when a visual is wanted.

## Procedure

### 1. Map the system (the real work)

Read the code, don't guess. Identify:

- **The linear stages** — the spine, from input to output (e.g. `数据来源 → 容器 → Agent → 解析 → 回写`). Each becomes a node with a `row` (vertical layer).
- **The key control-flow shape** — especially any *loop* (a tool-use/agent loop, a retry/self-heal cycle, a closure that writes back). Loops are the most important thing to make visible; represent them with same-row fan-out + a dashed back-edge.
- **Drill-downs** — a stage that expands into its own sub-chain (e.g. `parse_report → ParseOptimizer 内部`). Give it its own lower rows.
- **For each node**: the real `file` path, a one-line `what`, 1–3 `points`, and—when you can get them—**real numbers** (`data`). Numbers make it concrete and credible.

### 2. Gather real numbers (don't fabricate)

Run read-only commands to fill `data` fields: enumerate files, count rows, `SELECT COUNT(*)`. If you can't get a number, omit it rather than invent. Cite real file paths (`file_path:line` where useful).

### 3. Fill the data, render from the template

Copy `references/template.html`. Replace only the top `META / GROUPS / NODES / EDGES` block (everything below it is the engine — leave it alone). See `references/schema.md` for the exact field reference. Key rules:

- `row` = vertical layer (0 at top). Same `row` → nodes sit side by side, auto-centered.
- `group` → color + legend entry. One color per logical layer.
- Edge direction is inferred: `to` below `from` = down arrow; same row = horizontal; `to` above `from` = right-side loop-back (use `dashed: true` for closures/feedback).
- Keep labels short; put the prose in `detail`.

Save the result (e.g. `flow.html` at the project root, or under `docs/`). Tell the user to open it.

### 4. Offer to integrate (only if a frontend exists)

If the project has a web frontend, offer to also embed it as a page so it lives in the app, not just a loose file:

- **React/Vue/etc.**: embed the generated `<svg>` markup via the framework's raw-HTML escape hatch (e.g. React `dangerouslySetInnerHTML`), and reimplement the click→detail panel in the framework's state. Scope the CSS under a wrapper class so it doesn't leak.
- **Watch the static-serving trap**: if the backend has an SPA catch-all (`/{path:path}` → index.html), a loose `/flow.html` gets intercepted in production. A framework component (in the JS bundle) avoids this; a raw static file needs an explicit server route.

## Output conventions

- **One file, no CDN.** Inline SVG + inline JS. Must work offline by double-click.
- **Dark theme** by default (matches most dev tooling). Adjust if the user's app is light.
- **Honesty**: every `file` must be a real path; every `data` number must be measured, not guessed. A diagram that lies is worse than none.
- **Loops are the point.** If the system has an agent loop or a feedback cycle, make it the visual centerpiece — that's usually what the user most needs to understand.

## Files in this skill

- `references/template.html` — the engine + a tiny placeholder dataset. Copy, swap the data block, done.
- `references/schema.md` — field-by-field reference for `META / GROUPS / NODES / EDGES / detail`.
- `examples/agent-platform.html` — a complete worked example (an autonomous-agent data pipeline). Open it to see the target quality; read its data block to see how a real system maps to nodes/edges.
