"""从 PDF 缓存中提取原文参考数据，与解析结果对照。"""
import fitz
import re
import json
import os

PDF_CACHE = "../book-agent/output/pdf_cache"

# 10 份待校验的 PDF
pdfs = [
    ("000001", 2025, "000001_2025.pdf", "平安银行"),
    ("000002", 2025, "000002_2025_a953e1fae21e.pdf", "万科"),
    ("000066", 2025, "000066_2025.pdf", "长城电脑/中国长城"),
    ("000088", 2025, "000088_2025.pdf", "盐田港"),
    ("000333", 2023, "000333_2023_44fb1c3d7fdc.pdf", "美的集团"),
    ("000425", 2025, "000425_2025.pdf", "徐工机械"),
    ("000506", 2025, "000506_2025.pdf", "中润资源"),
    ("000550", 2025, "000550_2025.pdf", "江铃汽车"),
    ("000553", 2025, "000553_2025.pdf", "沙隆达/安道麦"),
    ("000563", 2025, "000563_2025.pdf", "陕国投"),
]


def search_text(doc, keywords, max_pages=5):
    """搜索关键词，返回匹配的页码列表"""
    results = {}
    for kw in keywords:
        pages = []
        for i in range(len(doc)):
            text = doc[i].get_text()
            if kw in text:
                pages.append(i)
        if pages:
            results[kw] = pages[:max_pages]
    return results


def get_page_text(doc, page_num):
    """获取指定页的文本"""
    return doc[page_num].get_text()


def extract_surrounding_text(doc, keyword, context_lines=30):
    """找到包含关键词的页面，返回周围的文本"""
    for kw in keyword:
        pages = doc.get_page_numbers(kw)
        if pages:
            page = pages[0]
            text = doc[page].get_text()
            # 找到关键词位置
            idx = text.find(kw)
            if idx >= 0:
                start = max(0, idx - context_lines * 20)
                end = min(len(text), idx + context_lines * 40)
                return text[start:end], page
    return None, -1


def extract_surrounding_text_multi(doc, keywords, context_chars=3000):
    """搜索多个关键词，返回每个关键词周围的上下文"""
    results = {}
    for kw in keywords:
        try:
            pages = doc.get_page_numbers(kw)
        except:
            pages = []
            for i in range(len(doc)):
                if kw in doc[i].get_text():
                    pages.append(i)
        if pages:
            page = pages[0]
            text = doc[page].get_text()
            idx = text.find(kw)
            if idx >= 0:
                start = max(0, idx - 800)
                end = min(len(text), idx + 2000)
                results[kw] = {
                    "page": page,
                    "context": text[start:end]
                }
    return results


def process_one_pdf(stock_code, year, pdf_name, company_name):
    pdf_path = os.path.join(PDF_CACHE, pdf_name)
    if not os.path.exists(pdf_path):
        print(f"[{stock_code}] PDF not found: {pdf_path}")
        return None
    
    print(f"\n{'='*80}")
    print(f"[{stock_code} {company_name} ({year})] 处理中...")
    print(f"PDF: {pdf_path}")
    
    doc = fitz.open(pdf_path)
    print(f"总页数: {len(doc)}")
    
    result = {
        "stock_code": stock_code,
        "company_name": company_name,
        "year": year,
        "pdf_file": pdf_name,
    }
    
    # ====== 营收结构 ======
    print(f"\n--- 营收结构 ---")
    rev_kw = ["营业收入构成", "主营业务分", "分产品", "分行业", "分地区"]
    rev_texts = extract_surrounding_text_multi(doc, rev_kw)
    result["revenue_keywords"] = {}
    for kw, info in rev_texts.items():
        result["revenue_keywords"][kw] = info
        page = info["page"]
        print(f"  「{kw}」→ 第{page}页 (上下文前100字符: {info['context'][:100].strip()})")
        # 打印完整上下文供参考
        print(f"    上下文:\n{info['context'][:1500]}")
    
    # ====== 研发费用 ======
    print(f"\n--- 研发费用 ---")
    rnd_kw = ["研发费用"]
    rnd_texts = extract_surrounding_text_multi(doc, rnd_kw)
    result["rnd_keywords"] = {}
    for kw, info in rnd_texts.items():
        result["rnd_keywords"][kw] = info
        page = info["page"]
        print(f"  「{kw}」→ 第{page}页")
        print(f"    上下文:\n{info['context'][:2000]}")
    
    # ====== 员工数据 ======
    print(f"\n--- 员工数据 ---")
    emp_kw = ["专业构成", "教育程度", "母公司在职员工", "在职员工"]
    emp_texts = extract_surrounding_text_multi(doc, emp_kw)
    result["employee_keywords"] = {}
    for kw, info in emp_texts.items():
        if info:
            result["employee_keywords"][kw] = info
            page = info["page"]
            print(f"  「{kw}」→ 第{page}页")
            print(f"    上下文:\n{info['context'][:1500]}")
    
    # ====== 成本构成 ======
    print(f"\n--- 成本构成 ---")
    cost_kw = ["占营业成本比重", "营业成本构成"]
    cost_texts = extract_surrounding_text_multi(doc, cost_kw)
    result["cost_keywords"] = {}
    for kw, info in cost_texts.items():
        if info:
            result["cost_keywords"][kw] = info
            page = info["page"]
            print(f"  「{kw}」→ 第{page}页")
            print(f"    上下文:\n{info['context'][:1500]}")
    
    # ====== 前五大客户 ======
    print(f"\n--- 前五大客户 ---")
    cli_kw = ["前五名客户", "主要销售客户"]
    cli_texts = extract_surrounding_text_multi(doc, cli_kw)
    result["client_keywords"] = {}
    for kw, info in cli_texts.items():
        if info:
            result["client_keywords"][kw] = info
            page = info["page"]
            print(f"  「{kw}」→ 第{page}页")
            print(f"    上下文:\n{info['context'][:1500]}")
    
    # ====== 前五大供应商 ======
    print(f"\n--- 前五大供应商 ---")
    sup_kw = ["前五名供应商", "主要供应商"]
    sup_texts = extract_surrounding_text_multi(doc, sup_kw)
    result["supplier_keywords"] = {}
    for kw, info in sup_texts.items():
        if info:
            result["supplier_keywords"][kw] = info
            page = info["page"]
            print(f"  「{kw}」→ 第{page}页")
            print(f"    上下文:\n{info['context'][:1500]}")
    
    doc.close()
    return result


def main():
    all_results = []
    for stock_code, year, pdf_name, company_name in pdfs:
        res = process_one_pdf(stock_code, year, pdf_name, company_name)
        if res:
            all_results.append(res)
    
    # 保存结果
    output_path = "scripts/pdf_ref_data.json"
    with open(output_path, "w", encoding="utf-8") as f:
        # 只保存前不能 json 化的部分
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n\n所有结果已保存到 {output_path}")
    return all_results


if __name__ == "__main__":
    main()
