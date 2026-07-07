# Archive

已从主链移除的代码，**不参与**生产部署与常规 `pytest`。

恢复某文件前请先查 [`docs/遗留代码清理计划.md`](../docs/遗留代码清理计划.md)。

| 子目录 | 内容 |
|--------|------|
| `legacy/` | `registry`、`heal_pipeline`、`workflow`、`iteration`、`sandbox`、`experience_db` |
| `legacy/review/` | 旧 `ReviewManager` |
| `legacy/export/` | 旧 `export/exporter.py` |
| `m1-unwired/` | `page_locator`、`section_locator`、`finders/`（M1 未接线） |
| `scripts/` | `diagnose.py`、`extract_pdf_ref*.py`、`auto_iterate.py` |
| `demo-parsers/` | fork/codegen 演示产出解析器 |
