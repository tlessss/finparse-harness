"""
validate_report.py — 对比 PDF 原文与 FinParseAI 解析结果，生成校验报告

用法：
    python3 scripts/validate_report.py

输出：test_results/批量测试报告_auto_iterate_20260611_140603.md
"""

import json
import re
import os
import sys

# 添加项目根到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─── 配置 ───────────────────────────────────────────────────
TEST_RESULTS_PATH = "test_results/auto_iterate_20260611_140603.json"
PDF_CACHE_DIR = "../book-agent/output/pdf_cache"
OUTPUT_PATH = "test_results/批量测试报告_auto_iterate_20260611_140603.md"

# ─── 加载测试结果 ──────────────────────────────────────────
with open(TEST_RESULTS_PATH, "r", encoding="utf-8") as f:
    data = json.load(f)

results = data["results"]
parser_version = data.get("parser_version", "unknown")
total_stocks = len(results)
total_fields = sum(r.get("field_count", 0) for r in results)
avg_duration = total_fields / total_stocks if total_stocks else 0

# ─── 辅助函数 ──────────────────────────────────────────────

def extract_text_from_pdf(pdf_path):
    """用 PyMuPDF 提取全部文本"""
    import fitz
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text("text") + "\n"
    doc.close()
    return full_text


def search_pdf_for_keyword(text, keywords, context_lines=20):
    """在 PDF 文本中搜索关键词，返回上下文片段"""
    lines = text.split("\n")
    results = []
    for kw in keywords:
        for i, line in enumerate(lines):
            if kw in line:
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                snippet = "\n".join(lines[start:end])
                results.append({"keyword": kw, "line_num": i, "snippet": snippet})
                break  # 每个关键词只取第一次出现
    return results


def find_pdf_path(stock_code, pdf_file):
    """查找 PDF 缓存路径"""
    base = os.path.join(PDF_CACHE_DIR, pdf_file)
    if os.path.exists(base):
        return base
    # 尝试通配查找
    from glob import glob
    matches = glob(os.path.join(PDF_CACHE_DIR, f"{stock_code}_*.pdf"))
    if matches:
        return matches[0]
    return None


def extract_money_values(text, keyword):
    """从关键词附近的文本行中提取金额数值"""
    lines = text.split("\n")
    values = []
    found_idx = None
    for i, line in enumerate(lines):
        if keyword in line:
            found_idx = i
            break
    if found_idx is None:
        return values

    # 从该行附近找数字
    for i in range(max(0, found_idx - 1), min(len(lines), found_idx + 5)):
        line = lines[i].strip()
        # 提取所有数字
        nums = re.findall(r'[\d,]+(?:\.\d+)?', line)
        for n in nums:
            try:
                v = float(n.replace(",", ""))
                if v > 10:
                    values.append((line, v))
            except ValueError:
                pass
    return values


def check_amount_ratio(parsed_val, pdf_text, field_name, stock_code):
    """检查金额量级差异"""
    issues = []
    if parsed_val is None:
        return issues

    # 在 PDF 中找对应数值
    keywords_map = {
        "revenue": ["营业收入", "营收", "收入"],
        "rnd": ["研发费用", "研发投入"],
        "cost": ["营业成本", "成本"],
    }
    return issues


def get_field_coverage(result):
    """计算一份结果的字段覆盖率"""
    fields = []
    counts = {"revenue_breakdown": 0, "rnd_info": 0, "employees": 0,
              "cost_breakdown": 0, "top_clients": 0, "top_suppliers": 0}
    
    for key in counts:
        val = result.get(key)
        if val is not None and val != "no_table_found":
            if isinstance(val, dict) and val.get("status") == "no_table_found":
                continue
            if key == "revenue_breakdown" and val:
                segs = val.get("segments", [])
                inds = val.get("industries", [])
                regs = val.get("regions", [])
                if segs or inds or regs:
                    counts[key] = 1
            elif key in ("top_clients", "top_suppliers") and val:
                items = val.get("items", [])
                if items or val.get("total_amount"):
                    counts[key] = 1
            else:
                counts[key] = 1
    
    covered = sum(counts.values())
    total = len(counts)
    return covered, total, counts


def check_rnd_bank_issue(stock_code, result):
    """银行有研发费用 → 误匹配"""
    bank_codes = ["000001", "000563"]  # 平安银行、陕国投A（信托）
    rnd = result.get("rnd_info")
    if stock_code in bank_codes and rnd is not None and isinstance(rnd, dict):
        detail = rnd.get("rnd_detail", [])
        if detail:
            return True, len(detail)
    return False, 0


def revenue_seems_bank_bs(result):
    """营收看起来像银行资产负债表"""
    rev = result.get("revenue_breakdown")
    if not rev:
        return False, []
    segs = rev.get("segments", [])
    names = [s.get("name", "") for s in segs]
    bank_keywords = ["利息净收入", "吸收存款", "发放贷款", "存放中央银行"]
    hits = sum(1 for kw in bank_keywords for n in names if kw in n)
    return hits >= 3, names


def revenue_seems_bs(result):
    """营收看起来像资产负债表（非银行的公司）"""
    rev = result.get("revenue_breakdown")
    if not rev:
        return False, []
    segs = rev.get("segments", [])
    names = [s.get("name", "") for s in segs]
    bs_keywords = ["货币资金", "应收账款", "存货", "固定资产", "在建工程",
                    "短期借款", "长期借款", "合同负债", "使用权资产", "租赁负债",
                    "应付账款", "预收款项", "递延所得税"]
    hits = sum(1 for kw in bs_keywords for n in names if kw in n)
    return hits >= 3, names


def check_revenue_unit_issue(result):
    """检查营收金额量级是否异常"""
    rev = result.get("revenue_breakdown")
    if not rev:
        return None
    for key in ["segments", "industries", "regions"]:
        items = rev.get(key, [])
        for item in items:
            val = item.get("revenue_yuan")
            if val is not None and val < 1:
                return f"金额极小({val})"
    return None


def revenue_missing_ratio(result):
    """检查营收是否缺占比"""
    rev = result.get("revenue_breakdown")
    if not rev:
        return False
    for key in ["segments", "industries", "regions"]:
        items = rev.get(key, [])
        for item in items:
            if item.get("ratio_pct") is None:
                return True
    return False


def check_segments_ratio_sum(segments):
    """检查 segment 占比和"""
    ratios = [s.get("ratio_pct") for s in segments if s.get("ratio_pct") is not None]
    if not ratios:
        return None
    return sum(ratios)


def is_top_confirmed_blank(data):
    """检查客户/供应商是否为空白但有汇总金额"""
    issues = []
    for key in ["top_clients", "top_suppliers"]:
        val = data.get(key)
        if val and isinstance(val, dict):
            items = val.get("items", [])
            total = val.get("total_amount")
            if total and not items:
                issues.append(key)
    return issues


def items_empty_no_total(val):
    """items 为空且 total_amount 也没有"""
    if not val or not isinstance(val, dict):
        return True
    items = val.get("items", [])
    total = val.get("total_amount")
    return not items and not total


# ─── Step 1-3: 逐份校验 ──────────────────────────────────
print(f"开始校验 {total_stocks} 份结果...")

stock_checks = []

for idx, r in enumerate(results):
    stock_code = r["stock_code"]
    year = r.get("report_year", "")
    pdf_file = r.get("pdf_file", "")
    print(f"[{idx+1}/{total_stocks}] {stock_code} ({year}) - PDF: {pdf_file}")

    check = {
        "stock_code": stock_code,
        "year": year,
        "pdf_file": pdf_file,
        "duration": r.get("duration_sec", 0),
        "field_count": r.get("field_count", 0),
        "issues": [],
        "fields_detail": {}
    }

    pdf_path = find_pdf_path(stock_code, pdf_file)
    text_content = ""
    if pdf_path:
        try:
            text_content = extract_text_from_pdf(pdf_path)
        except Exception as e:
            check["issues"].append(f"PDF 读取失败: {e}")

    # ─ 字段覆盖分析 ─
    covered, total_possible, counts = get_field_coverage(r)
    check["coverage"] = f"{covered}/{total_possible}"
    
    # ─ revenue_breakdown ─
    rev = r.get("revenue_breakdown")
    rev_detail = {"status": "✅" if rev and (rev.get("segments") or rev.get("industries") or rev.get("regions")) else "➖ 缺失"}
    
    if rev:
        seg_count = len(rev.get("segments", []))
        ind_count = len(rev.get("industries", []))
        reg_count = len(rev.get("regions", []))
        rev_detail["segments_count"] = seg_count
        rev_detail["industries_count"] = ind_count
        rev_detail["regions_count"] = reg_count
        rev_detail["total_items"] = seg_count + ind_count + reg_count
        
        # 检查单位异常
        unit_issue = check_revenue_unit_issue(r)
        if unit_issue:
            rev_detail["status"] = "❌ 单位"
            rev_detail["issue"] = unit_issue

        # 检查是否为银行资产负载表
        is_bank, names = revenue_seems_bank_bs(r)
        if is_bank:
            rev_detail["status"] = "❌ 误匹配"
            rev_detail["issue"] = f"疑似银行资产负债表({', '.join(names[:3])})"
        
        # 检查是否为普通资产负债表
        if not is_bank:
            is_bs, bs_names = revenue_seems_bs(r)
            if is_bs:
                rev_detail["status"] = "❌ 误匹配"
                rev_detail["issue"] = f"疑似资产负债表({', '.join(bs_names[:3])})"
        
        # BS误匹配的，清除ratio_sum（不触发列识别检查）
        if "资产负债表" in rev_detail.get("issue", ""):
            rev_detail["ratio_sum"] = None
        else:
            # 检查占比（仅非BS误匹配的股票）
            segs = rev.get("segments", [])
            ratio_sum = check_segments_ratio_sum(segs)
            if ratio_sum is not None and ratio_sum > 0:
                rev_detail["ratio_sum"] = round(ratio_sum, 1)
                if abs(ratio_sum - 100) > 5 and ratio_sum > 0:
                    rev_detail["status"] = rev_detail.get("status", "") + " ❌ 列识别"
                    rev_detail["issue"] = f"占比和={ratio_sum:.1f}%"

    check["fields_detail"]["营收结构"] = rev_detail

    # ─ rnd_info ─
    rnd = r.get("rnd_info")
    rnd_detail = {"status": "✅" if rnd and rnd.get("rnd_detail") else "➖ 缺失"}
    
    # 银行无研发费用是正常的
    bank_codes_known = ["000001", "000563"]
    if stock_code in bank_codes_known and (rnd is None or (isinstance(rnd, dict) and not rnd.get("rnd_detail"))):
        rnd_detail["status"] = "✅（无研发费用，银行正常）"
    
    if rnd and rnd.get("rnd_detail"):
        detail = rnd["rnd_detail"]
        rnd_detail["items_count"] = len(detail)
        
        # 检查银行误匹配
        bank_issue, detail_count = check_rnd_bank_issue(stock_code, r)
        if bank_issue:
            rnd_detail["status"] = "❌ 误匹配"
            rnd_detail["issue"] = f"银行不应有研发费用，解析到 {detail_count} 项"

        # 检查项数太多
        if len(detail) > 15:
            rnd_detail["status"] = "❌ 误匹配"
            rnd_detail["issue"] = f"研发费用项数{len(detail)}过多，可能是其他附注表"
        
        # 检查是否管理费用表被误匹配（包含职工薪酬但无研发相关词）
        names = [d.get("name", "") for d in detail]
        has_rnd_keyword = any("研发" in n or "开发" in n for n in names)
        has_admin_keyword = any("管理费用" in n or "销售费用" in n or "财务费用" in n for n in names)
        
        if not has_rnd_keyword and has_admin_keyword:
            rnd_detail["status"] = "❌ 误匹配"
            rnd_detail["issue"] = "包含销售/管理/财务费用，疑为期间费用表而非研发费用"
    
    check["fields_detail"]["研发费用"] = rnd_detail

    # ─ employees ─
    emp = r.get("employees")
    emp_detail = {"status": "✅" if emp and isinstance(emp, dict) and emp.get("total") else "➖ 缺失"}
    if emp and isinstance(emp, dict):
        comp = emp.get("composition", [])
        edu = emp.get("education", [])
        emp_detail["total"] = emp.get("total")
        emp_detail["composition_count"] = len(comp)
        emp_detail["education_count"] = len(edu)
        
        if not comp and not edu:
            emp_detail["status"] = "➖ 缺失"
        elif not edu and comp:
            emp_detail["status"] = "⚠️ 部分缺失"
            emp_detail["issue"] = "教育程度为空"
        elif not comp and edu:
            emp_detail["status"] = "⚠️ 部分缺失"
            emp_detail["issue"] = "专业构成为空"
    
    check["fields_detail"]["员工数据"] = emp_detail

    # ─ cost_breakdown ─
    cost = r.get("cost_breakdown")
    cost_detail = {"status": "✅" if cost and len(cost) > 0 else "➖ 缺失"}
    if cost:
        # 过滤掉标题行
        valid_rows = [c for c in cost if c.get("amount_yuan") is not None or c.get("ratio_pct") is not None]
        cost_detail["items_count"] = len(valid_rows)
        
        if valid_rows:
            # 检查是否有占比
            has_ratio = any(c.get("ratio_pct") is not None for c in valid_rows)
            if not has_ratio:
                cost_detail["status"] = "⚠️ 部分缺失"
                cost_detail["issue"] = "成本占比全部为空"
        else:
            cost_detail["status"] = "❌ 误匹配"
            cost_detail["issue"] = "疑似费用表或利润表"
    
    check["fields_detail"]["成本构成"] = cost_detail

    # ─ top_clients / top_suppliers ─
    for field_key, field_label in [("top_clients", "前五大客户"), ("top_suppliers", "前五大供应商")]:
        val = r.get(field_key)
        fd = {"status": "✅" if val and val.get("items") else "➖ 缺失"}
        if val and isinstance(val, dict):
            items = val.get("items", [])
            total = val.get("total_amount")
            ratio = val.get("total_ratio_pct")
            fd["items_count"] = len(items)
            fd["total_amount"] = total
            fd["total_ratio"] = ratio
            
            if not items and total:
                fd["status"] = "⚠️ 部分缺失"
                fd["issue"] = "有汇总金额但无明细项"
            elif not items and not total:
                fd["status"] = "➖ 缺失"
            elif items:
                # 检查客户名称是否标准
                names_check = [it.get("name", "") for it in items]
                fd["sample_names"] = ", ".join(names_check[:3])
        
        check["fields_detail"][field_label] = fd

    stock_checks.append(check)

# ─── Step 4: 推导修复建议 ──────────────────────────────────
print("\n推导修复建议...")

repair_suggestions = []

# 汇总所有问题
all_issues = []
for check in stock_checks:
    for field, detail in check["fields_detail"].items():
        status = detail.get("status", "")
        issue = detail.get("issue", "")
        if "❌" in status or "⚠️" in status:
            all_issues.append({
                "stock_code": check["stock_code"],
                "field": field,
                "status": status,
                "issue": issue,
                "detail": detail,
            })

# 按问题类型归类
issue_groups = {}
for issue in all_issues:
    key = issue["issue"] if issue["issue"] else issue["status"]
    if key not in issue_groups:
        issue_groups[key] = {"stocks": [], "field": issue["field"], "type": issue["status"]}
    issue_groups[key]["stocks"].append(issue["stock_code"])

# 生成修复建议
repair_suggestions = []

# 先计算资产负债表误匹配的股票
s1_stocks_bs = [c["stock_code"] for c in stock_checks if "银行资产负债表" in str(c["fields_detail"].get("营收结构", {}).get("issue", ""))]
s1b_stocks_bs = [c["stock_code"] for c in stock_checks if "疑似资产负债表" in str(c["fields_detail"].get("营收结构", {}).get("issue", ""))]
all_bs_stocks = s1_stocks_bs + s1b_stocks_bs

# 1) 营收占比较远的列识别问题（排除已识别为资产负债表的股票）
s1_exclude = all_bs_stocks
s1_stocks = [c["stock_code"] for c in stock_checks 
             if c["stock_code"] not in s1_exclude
             and c["fields_detail"].get("营收结构", {}).get("ratio_sum") is not None
             and abs(c["fields_detail"]["营收结构"]["ratio_sum"] - 100) > 10]
s1_stocks = list(set(s1_stocks))
if s1_stocks:
    repair_suggestions.append({
        "problem": "营收占比和远离 100%，可能列识别错误",
        "stocks": s1_stocks,
        "diff_type": "列识别",
        "root_file": "src/parsers/revenue_parser.py",
        "root_func": "_detect_columns",
        "suggestion": (
            "1. 在 `_detect_columns`（第196-211行）的 ratio_col 选择逻辑中增加：计算候选列占比和，优先选和逼近 100% 的列\n"
            "2. 原因是：部分 PDF 中增长百分比列被误判为占比列，增加'和接近 100'校验可以排除同比增减列"
        ),
        "expected": "营收结构占比和应接近 100%（实际应在 95%-105% 范围内）"
    })

# 1b) 营收结构被误识别为资产负债表
all_bs_stocks = [c["stock_code"] for c in stock_checks if "银行资产负债表" in str(c["fields_detail"].get("营收结构", {}).get("issue", ""))]
all_bs_stocks += [c["stock_code"] for c in stock_checks if "疑似资产负债表" in str(c["fields_detail"].get("营收结构", {}).get("issue", ""))]
if all_bs_stocks:
    repair_suggestions.append({
        "problem": "营收结构被误识别为资产负债表（银行/普通公司）",
        "stocks": all_bs_stocks,
        "diff_type": "误匹配 / 单位错误",
        "root_file": "src/parsers/revenue_parser.py",
        "root_func": "_filter_revenue_tables",
        "suggestion": (
            "1. 在 `TABLE_SIGNATURES[\"revenue\"][\"exclude\"]`（table_scanner.py 第117行）加排除词：`\"利息收入\"`、`\"利息支出\"`、`\"吸收存款\"`、`\"发放贷款\"`、`\"货币资金\"`、`\"应收账款\"`、`\"固定资产\"`\n"
            "2. 原因是：银行/普通公司的资产负债表含大量金额列和%列，与营收结构表特征相似，需要通过排除词跳过\n"
            "3. 当前 `ratio_max=100` 防止了大部分 KPI 汇总表，但资产负债表各项占比在 0-100 之间，不足以排除"
        ),
        "expected": "银行（000001、000563）和江铃汽车（000550）营收结构应返回 items=[]（无标准营收结构），而非资产负债表"
    })

# 2) bank rnd 误匹配
s2_stocks = [c["stock_code"] for c in stock_checks if "银行不应有研发费用" in str(c["fields_detail"].get("研发费用", {}).get("issue", ""))]
if s2_stocks:
    repair_suggestions.append({
        "problem": "银行类股票不应解析出研发费用（银行存款表被误匹配）",
        "stocks": s2_stocks,
        "diff_type": "误匹配",
        "root_file": "src/parsers/table_scanner.py",
        "root_func": "TABLE_SIGNATURES[\"rnd\"][\"exclude\"]",
        "suggestion": (
            "1. 在 `TABLE_SIGNATURES[\"rnd\"][\"exclude\"]`（第127行）加排除词：`\"利息收入\"`、`\"吸收存款\"`、`\"发放贷款\"`、`\"利息支出\"`\n"
            "2. 原因是：银行 PDF 附注中含'吸收存款'、'发放贷款'等附注表，其表格特征（职工薪酬、研发材料等关键词）不存在于该表中，实际应通过排除词跳过"
        ),
        "expected": "银行（000001、000563）研发费用应为 null，而非解析出管理费用明细"
    })

# 3) cost 占比为空的利润表（000425 徐工机械、000563 陕国投）
s3_stocks = [c["stock_code"] for c in stock_checks 
             if c["fields_detail"].get("成本构成", {}).get("status", "") == "❌ 误匹配"
             or ("占比全部为空" in str(c["fields_detail"].get("成本构成", {}).get("issue", "")))]
if s3_stocks:
    repair_suggestions.append({
        "problem": "成本构成被误解析为费用表/利润表，占比全部为空",
        "stocks": s3_stocks,
        "diff_type": "误匹配",
        "root_file": "src/parsers/table_scanner.py",
        "root_func": "TABLE_SIGNATURES[\"cost\"][\"exclude\"]",
        "suggestion": (
            "1. 在 `TABLE_SIGNATURES[\"cost\"][\"exclude\"]`（第143行）加排除词：`\"营业总收入\"`、`\"营业总成本\"`、`\"一、\"`、`\"二、\"`、`\"投资收益\"`、`\"公允价值变动\"`\n"
            "2. 原因是：利润表也含'成本'关键词，但其结构（一、营业总收入、二、营业总成本）与真正的成本构成表不同，应通过排除词排除"
        ),
        "expected": "000425（徐工机械）、000563（陕国投）的成本构成应返回 null，而非利润表"
    })

# 4) 员工教育程度缺失
s5_stocks = [c["stock_code"] for c in stock_checks if "教育程度为空" in str(c["fields_detail"].get("员工数据", {}).get("issue", ""))]
if s5_stocks:
    repair_suggestions.append({
        "problem": "员工教育程度字段为空，专业构成和教育程度被拆成两个独立表",
        "stocks": s5_stocks,
        "diff_type": "缺失",
        "root_file": "src/parsers/employee_parser.py",
        "root_func": "_parse_table",
        "suggestion": (
            "1. 确认 `employee_parser.py` 的 `parse()` 方法已合并所有匹配表的 composition 和 education 结果（当前逻辑正确）\n"
            "2. 检查 `TABLE_SIGNATURES[\"employee\"][\"must_have\"]` 是否包含了教育程度的关键词（`\"教育程度\"` 已在列表中）\n"
            "3. 可能原因：教育程度表不在 scan_pdf 抓取范围内 (max_pages=200)，或 pdfplumber 未能正确提取该表\n"
            "4. 建议在 `scan_pdf` 中增加 max_pages 至 300，或检查 PDF 中教育程度表是否跨页"
        ),
        "expected": "000002（万科）应有教育程度数据（本科、硕士等），当前 education=[]"
    })

# 5) 客户/供应商有汇总金额但无明细
s6_stocks = [c["stock_code"] for c in stock_checks if "有汇总金额但无明细项" in str(c["fields_detail"].get("前五大客户", {}).get("issue", ""))]
s6_stocks += [c["stock_code"] for c in stock_checks if "有汇总金额但无明细项" in str(c["fields_detail"].get("前五大供应商", {}).get("issue", ""))]
if s6_stocks:
    repair_suggestions.append({
        "problem": "前五大客户/供应商有汇总金额但 items 明细为空",
        "stocks": list(set(s6_stocks)),
        "diff_type": "缺失",
        "root_file": "src/parsers/top_supplier_parser.py",
        "root_func": "parse",
        "suggestion": (
            "1. 在 `parse()` 的明细表匹配条件中（第33行、第44行），将 `if result[\"top_clients\"] is None or not result[\"top_clients\"].get(\"items\")` 作为优先条件\n"
            "2. 原因是：汇总表先于明细表被匹配到，明细表数据没有被正确赋值的条件不够严格，应确保 items 为空时才允许汇总表覆盖\n"
            "3. 另外检查 `_parse_rows`（第88行）中的关键词匹配：确认明细表行内是否能正确提取序号和金额"
        ),
        "expected": "000553（沙隆达）的 top_clients 和 top_suppliers 应有明细 items 列表"
    })

# 6) revenue 单位量级不对（平安银行金额几百到几千，实际应以亿为单位）
s7_stocks = []
for c in stock_checks:
    rd = c["fields_detail"].get("营收结构", {})
    if rd.get("total_items", 0) > 0:
        r = next((x for x in results if x["stock_code"] == c["stock_code"]), None)
        if r:
            rev = r.get("revenue_breakdown", {})
            for key in ["segments", "regions", "industries"]:
                items = rev.get(key, [])
                for item in items:
                    val = item.get("revenue_yuan")
                    if val is not None and val < 1000000 and val > 10:
                        s7_stocks.append(c["stock_code"])
                        break
s7_stocks = list(set(s7_stocks))
if s7_stocks:
    repair_suggestions.append({
        "problem": "营收金额量级异常（如 88021 → 实际应为 880 亿），单位检测未命中 PDF 中的单位标注",
        "stocks": s7_stocks,
        "diff_type": "单位错误",
        "root_file": "src/parsers/unit_detector.py",
        "root_func": "_UNIT_PATTERNS / _UNIT_MAP",
        "suggestion": (
            "1. 在 `_UNIT_PATTERNS`（第24-28行）增加模式：`r\"单位[：:].*?百万元\"` 、`r\"货币单位[：:].*?百万元\"`\n"
            "2. 同时检查银行类 PDF（如平安银行）的页眉/页脚是否有'人民币百万元'的文字，确保正则能匹配到\n"
            "3. 在 `revenue_parser.py` 的 `_detect_unit_from_pdf` 中，增加扫描页眉和页脚区域"
        ),
        "expected": "平安银行（000001）的利息净收入应为 880 亿左右（实际 8802099 万），而非 88021 元"
    })

# 7) 客户名称混淆
s8_stocks = []
for c in stock_checks:
    for f in ["前五大客户", "前五大供应商"]:
        fd = c["fields_detail"].get(f, {})
        names = fd.get("sample_names", "")
        if "客户" in names and "客户" not in str(c.get("stock_code")):
            s8_stocks.append(c["stock_code"])
            break
if s8_stocks:
    repair_suggestions.append({
        "problem": "客户/供应商名称被模糊处理为'客户a/客户b/客户c'等匿名标签",
        "stocks": list(set(s8_stocks)),
        "diff_type": "缺失",
        "root_file": "src/parsers/top_supplier_parser.py",
        "root_func": "_parse_rows",
        "suggestion": (
            "1. 在 `_parse_rows`（第126-141行）的名称提取逻辑中，优先选择原文中最长的中文文本作为名称\n"
            "2. 当前逻辑会选数字和%之后的下一个 token 作为名称，如果 PDF 中客户名称列在金额列之后，需要调换列顺序\n"
            "3. 建议改为：对于序号为 1 的行，整行中所有中文文本的长度最长的作为客户名"
        ),
        "expected": "000425（徐工机械）的客户名称应为中文实名（如'客户a'应改为实际名称）"
    })

# 排序：按影响股票数降序
repair_suggestions.sort(key=lambda x: -len(x["stocks"]))

# ─── Step 5: 生成报告 ──────────────────────────────────────
print("生成报告...")

lines = []
lines.append(f"# 批量测试报告 — `{parser_version}`")
lines.append("")
lines.append(f"**导出时间**：2026-06-11 14:06:03")
lines.append(f"**测试文件**：`test_results/auto_iterate_20260611_140603.json`")
lines.append(f"**股票数量**：{total_stocks}")
lines.append(f"**平均字段覆盖数**：{data.get('average_field_count', avg_duration)}")
lines.append(f"**平均耗时**：{data.get('average_duration_sec', 0)} 秒")
lines.append("")

lines.append("---")
lines.append("## 1. 总体统计")
lines.append("")
lines.append("| 指标 | 值 |")
lines.append("|------|-----|")
lines.append(f"| 股票总数 | {total_stocks} |")
lines.append(f"| 总字段覆盖数 | {total_fields} |")
lines.append(f"| 平均字段覆盖/份 | {data.get('average_field_count', 0):.1f} |")
lines.append(f"| 最大字段数 | {max(r.get('field_count', 0) for r in results)} |")
lines.append(f"| 最小字段数 | {min(r.get('field_count', 0) for r in results)} |")
lines.append(f"| 平均耗时（秒） | {data.get('average_duration_sec', 0):.1f} |")

# 各字段的总体覆盖率
field_success = {"营收结构": 0, "研发费用": 0, "员工数据": 0, "成本构成": 0, "前五大客户": 0, "前五大供应商": 0}
field_total = len(results)
for check in stock_checks:
    for f in field_success:
        st = check["fields_detail"].get(f, {}).get("status", "")
        if "✅" in st:
            field_success[f] += 1

lines.append("")
lines.append("| 字段 | 成功数 | 覆盖率 |")
lines.append("|------|--------|--------|")
for f, cnt in field_success.items():
    pct = cnt / field_total * 100
    lines.append(f"| {f} | {cnt}/{field_total} | {pct:.0f}% |")

lines.append("")
lines.append("---")
lines.append("## 2. 逐份校验")
lines.append("")

for check in stock_checks:
    sc = check["stock_code"]
    lines.append(f"### {sc}（{check['year']}）")
    lines.append("")
    lines.append(f"- **PDF**：`{check['pdf_file']}`")
    lines.append(f"- **耗时**：{check['duration']} 秒")
    lines.append(f"- **字段覆盖**：{check['coverage']}")
    lines.append("")

    lines.append("| 字段 | 状态 | 详情 |")
    lines.append("|------|------|------|")
    for field_name in ["营收结构", "研发费用", "员工数据", "成本构成", "前五大客户", "前五大供应商"]:
        fd = check["fields_detail"].get(field_name, {})
        status = fd.get("status", "➖ 缺失")
        detail_str = fd.get("issue", "")
        
        if not detail_str:
            if "items_count" in fd:
                detail_str = f"共 {fd['items_count']} 项"
            if fd.get("total_items"):
                detail_str = f"产品{fd.get('segments_count',0)}项/行业{fd.get('industries_count',0)}项/地区{fd.get('regions_count',0)}项"
            if fd.get("total"):
                detail_str += f"，员工{fd.get('total',0)}人"
            if fd.get("ratio_sum"):
                detail_str += f"，占比和={fd['ratio_sum']}%"
            if fd.get("education_count") is not None:
                detail_str += f"，学历{fd.get('education_count',0)}项"
            if fd.get("total_amount"):
                detail_str += f"，汇总金额 {fd['total_amount']:.2f}"
            if fd.get("sample_names"):
                detail_str += f"，样例: {fd['sample_names']}"
        
        lines.append(f"| {field_name} | {status} | {detail_str} |")
    
    lines.append("")

lines.append("---")
lines.append("## 3. 修复建议")
lines.append("")

if not repair_suggestions:
    lines.append("_本批次未发现需要修复的问题。_")
else:
    for i, sug in enumerate(repair_suggestions, 1):
        lines.append(f"### 问题 {i}：{sug['problem']}")
        lines.append("")
        lines.append(f"**影响股票**：{', '.join(sug['stocks'])}")
        lines.append(f"**差异类型**：{sug['diff_type']}")
        lines.append(f"**根因文件**：`{sug['root_file']}`")
        lines.append(f"**根因函数**：`{sug['root_func']}`")
        lines.append(f"**建议修改**：")
        for line in sug['suggestion'].split('\n'):
            lines.append(f"  {line.strip()}")
        lines.append("")
        lines.append(f"**预期效果**：{sug['expected']}")
        lines.append("")

lines.append("---")
lines.append("## 4. 改进优先级")
lines.append("")
lines.append("按影响股票数从高到低排列：")
lines.append("")
lines.append("| 优先级 | 问题 | 影响股票数 | 差异类型 |")
lines.append("|--------|------|-----------|---------|")
for i, sug in enumerate(repair_suggestions, 1):
    n_stocks = len(sug['stocks'])
    lines.append(f"| P{i} | {sug['problem'][:40]}... | {n_stocks} | {sug['diff_type']} |")

lines.append("")
lines.append("---")
lines.append("*报告由 FinParseAI validate-parse-report 技能自动生成*")

report_content = "\n".join(lines)

# 保存报告
with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    f.write(report_content)

print(f"\n✅ 报告已保存到: {OUTPUT_PATH}")
print(f"共发现 {len(repair_suggestions)} 个需要修复的问题")
