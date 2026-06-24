"""
测试 base_engine.py 的解析流程（无需 MinerU/Camelot 环境安装）

用 PyMuPDF + pdfplumber 代替 MinerU + Camelot，
验证 keyword_mapping / regex / unit_convert 的整个管线。
"""
import os
import json
import yaml


def run_parse_test():
    rule_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "src", "parser_rules", "industry_default.yaml"
    )
    pdf_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "pdfs", "多氟多-2025.pdf"
    )

    # ── 加载规则 ──
    with open(rule_path, "r", encoding="utf-8") as f:
        rule = yaml.safe_load(f)

    keyword_mapping = rule.get("keyword_mapping", {})
    regex_rules = rule.get("regex_rules", [])
    unit_convert = rule.get("unit_convert", {})
    table_area = rule.get("table_area", {})

    print(f"{'='*60}")
    print(f"PDF: {os.path.basename(pdf_path)}")
    print(f"规则: {rule.get('version', 'N/A')}")
    print(f"关键词: {list(keyword_mapping.keys())}")
    print(f"正则字段: {[r['field'] for r in regex_rules]}")
    print(f"{'='*60}\n")

    # ── Step 1: 用 fitz 取代 MinerU 做文本提取 ──
    import fitz
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    text_blocks = []
    for page_num in range(total_pages):
        page = doc[page_num]
        blocks = page.get_text("blocks")
        for b in blocks:
            txt = b[4].strip()
            if txt:
                text_blocks.append({
                    "page": page_num + 1,
                    "text": txt,
                })
    doc.close()
    print(f"📖 PDF 总页数: {total_pages}")
    print(f"📄 提取文本块数: {len(text_blocks)}\n")

    # ── Step 2: 用 pdfplumber 取代 Camelot 做表格提取 ──
    import pdfplumber
    pdf = pdfplumber.open(pdf_path)
    table_count = 0
    for i in range(min(total_pages, 20)):
        page = pdf.pages[i]
        tables = page.extract_tables()
        table_count += len(tables)
    pdf.close()
    print(f"📊 前20页表格数: {table_count}\n")

    # ── Step 3: 关键词抓取 ──
    result_raw = {}
    for block in text_blocks:
        text = block["text"]
        for std_key, alias_list in keyword_mapping.items():
            for alias in alias_list:
                if alias in text:
                    if std_key not in result_raw:
                        result_raw[std_key] = text
    print(f"{'─'*60}")
    print(f"🔍 关键词匹配结果（原文段落摘要）:")
    print(f"{'─'*60}")
    for field, text in sorted(result_raw.items()):
        preview = text[:150].replace("\n", " ")
        print(f"  [{field}] → {preview}...")
    print()

    # ── Step 4: 正则清洗 ──
    import re
    result_clean = dict(result_raw)
    for rrule in regex_rules:
        field = rrule["field"]
        pattern = rrule["pattern"]
        if field in result_clean:
            matches = re.findall(pattern, result_clean[field])
            if matches:
                result_clean[field] = matches[0]
    print(f"{'─'*60}")
    print(f"🧹 正则清洗后（提取纯数值）:")
    print(f"{'─'*60}")
    for field, val in sorted(result_clean.items()):
        print(f"  [{field}] → {val}")
    print()

    # ── Step 5: 单位转换 ──
    multiple = unit_convert.get("multiple", 1)
    for field in list(result_clean.keys()):
        try:
            val = result_clean[field]
            # 去逗号后转 float
            cleaned = val.replace(",", "").replace("，", "")
            num_val = float(cleaned)
            result_clean[field] = round(num_val * multiple, 2)
        except (ValueError, TypeError, AttributeError):
            pass  # 保留原值
    print(f"{'─'*60}")
    print(f"📐 单位换算后（倍数={multiple}）:")
    print(f"{'─'*60}")
    print(json.dumps(result_clean, ensure_ascii=False, indent=2, default=str))
    print()

    # ── 结论 ──
    found = list(result_raw.keys())
    missing = [k for k in keyword_mapping.keys() if k not in result_raw]
    print(f"{'='*60}")
    print(f"✅ 命中字段: {found}")
    if missing:
        print(f"⚠️  未命中: {missing} → 别名与 PDF 中实际文字不匹配，需要 AI 更新 keyword_mapping")
    print(f"{'='*60}")


if __name__ == "__main__":
    run_parse_test()
