"""批量从 PDF 提取关键参考数据用于校验。
逐页搜索关键词，输出详细上下文。"""
import fitz
import re
import os

PDF_CACHE = "../book-agent/output/pdf_cache"

# 10 份待校验的 PDF
pdfs = [
    ("000001", 2025, "000001_2025.pdf", "平安银行"),
    ("000002", 2025, "000002_2025_a953e1fae21e.pdf", "万科"),
    ("000066", 2025, "000066_2025.pdf", "中国长城"),
    ("000088", 2025, "000088_2025.pdf", "盐田港"),
    ("000333", 2023, "000333_2023_44fb1c3d7fdc.pdf", "美的集团"),
    ("000425", 2025, "000425_2025.pdf", "徐工机械"),
    ("000506", 2025, "000506_2025.pdf", "中润资源"),
    ("000550", 2025, "000550_2025.pdf", "江铃汽车"),
    ("000553", 2025, "000553_2025.pdf", "安道麦"),
    ("000563", 2025, "000563_2025.pdf", "陕国投"),
]

def extract_all(doc, keywords_dict, total_pages):
    """搜索多个关键词组，返回上下文"""
    results = {}
    for category, kws in keywords_dict.items():
        cat_results = {}
        for kw in kws:
            for i in range(total_pages):
                text = doc[i].get_text()
                if kw in text:
                    idx = text.find(kw)
                    start = max(0, idx - 300)
                    end = min(len(text), idx + 1500)
                    context = text[start:end]
                    if kw not in cat_results:
                        cat_results[kw] = []
                    cat_results[kw].append({
                        "page": i,
                        "context": context
                    })
                    break  # 只取第一个匹配
        if cat_results:
            results[category] = cat_results
    return results

# 定义所有搜索关键词
keywords_config = {
    "利息净收入": ["利息净收入"],
    "营业收入构成": ["营业收入构成"],
    "分产品": ["分产品"],
    "分行业": ["分行业"],
    "分地区": ["分地区"],
    "主营业务分": ["主营业务分"],
    "研发费用": ["研发费用"],
    "员工专业构成": ["专业构成"],
    "教育程度": ["教育程度"],
    "母公司在职员工": ["母公司在职员工"],
    "员工情况": ["员工情况"],
    "营业成本构成": ["营业成本构成"],
    "成本比重": ["占营业成本比重"],
    "前五名客户": ["前五名客户", "主要销售客户"],
    "前五名供应商": ["前五名供应商", "主要供应商"],
}

# 特别针对银行的营收搜索
bank_rev_kw = ["利息收入", "利息支出", "手续费及佣金", "非利息净收入"]

output_lines = []

def output(text):
    print(text)
    output_lines.append(text)

for stock_code, year, pdf_name, company in pdfs:
    pdf_path = os.path.join(PDF_CACHE, pdf_name)
    if not os.path.exists(pdf_path):
        output(f"\n{'='*70}")
        output(f"[{stock_code} {company} ({year})] PDF NOT FOUND: {pdf_path}")
        continue
    
    doc = fitz.open(pdf_path)
    total = len(doc)
    output(f"\n{'='*70}")
    output(f"[{stock_code} {company} ({year})] 共{total}页")
    
    # 搜索各字段
    for cat_name, kws in keywords_config.items():
        for kw in kws:
            for i in range(total):
                text = doc[i].get_text()
                if kw in text:
                    idx = text.find(kw)
                    start = max(0, idx - 200)
                    end = min(len(text), idx + 1500)
                    context = text[start:end]
                    output(f"\n--- {cat_name} ({kw}) 第{i}页 ---")
                    output(context)
                    break
    
    # 对于银行(000001)，额外搜索利息收入相关
    if stock_code == "000001":
        output(f"\n--- 银行专用: 利息收入/分部信息 ---")
        for i in range(total):
            text = doc[i].get_text()
            if "经营分部信息" in text or "分部信息" in text:
                output(f"\n=== 分部信息 第{i}页 ===")
                output(text[:3000])
    
    doc.close()

# 保存到文件
with open("scripts/pdf_reference_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(output_lines))

print("\n\n参考数据已保存到 scripts/pdf_reference_output.txt")
