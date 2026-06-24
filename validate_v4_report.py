#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
校验 V4 解析结果（含修复建议），生成批量测试报告。
根据 .cursor/skills/validate-parse-report/SKILL.md 的工作流执行。
"""

import json
import re
import os
import sys
from collections import defaultdict
import fitz  # PyMuPDF

# ── 配置 ──────────────────────────────────────────────────────────
TEST_RESULTS_PATH = "test_results/batch_test_results_v4_fixes.json"
PDF_CACHE_DIR = "../book-agent/output/pdf_cache"
OUTPUT_PATH = "test_results/批量测试报告_v4_含修复建议.md"

# 搜索关键词（对应 SKILL.md Step 2）
REVENUE_KEYWORDS = ["营业收入构成", "主营业务分产品", "分产品", "分行业"]
RND_KEYWORDS = ["研发费用"]
EMPLOYEE_KEYWORDS = ["专业构成", "教育程度", "在职员工"]
COST_KEYWORDS = ["占营业成本比重", "营业成本构成", "主营业务分行业"]
CLIENT_KEYWORDS = ["前五名客户", "主要销售客户"]
SUPPLIER_KEYWORDS = ["前五名供应商", "主要供应商"]


# ── 辅助函数 ──────────────────────────────────────────────────────

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def normalize_num(text):
    """从文本中提取数值（去掉逗号、括号负号）"""
    if text is None:
        return None
    text = str(text).replace(",", "").strip()
    m = re.search(r"-?[\d.]+", text.replace("(", "-").replace(")", "").replace("（", "-").replace("）", ""))
    if m:
        return float(m.group())
    return None


def ratio_check(val):
    """占比是否合理（0-100之间）"""
    if val is None:
        return True
    return 0 <= val <= 100


def extract_pdf_text(pdf_path, keywords, max_pages=300):
    """从 PDF 中提取包含关键词的附近文本"""
    doc = fitz.open(pdf_path)
    results = []
    for page_num in range(min(len(doc), max_pages)):
        page = doc[page_num]
        text = page.get_text("text")
        for kw in keywords:
            if kw in text:
                # 找到关键词位置，提取前后上下文
                lines = text.split("\n")
                for i, line in enumerate(lines):
                    if kw in line:
                        start = max(0, i - 5)
                        end = min(len(lines), i + 30)
                        snippet = "\n".join(lines[start:end])
                        results.append({
                            "page": page_num + 1,
                            "keyword": kw,
                            "snippet": snippet,
                            "lines": lines[start:end]
                        })
    doc.close()
    return results


def extract_ground_truth_from_snippets(snippets, field_type):
    """从 PDF 文本片段中提取标准数值（人工辅助，尽量自动化）"""
    if not snippets:
        return None
    all_lines = []
    for s in snippets:
        all_lines.extend(s["lines"])
    return all_lines


def get_pdf_path(stock_code, pdf_file):
    """查找 PDF 缓存文件"""
    for f in os.listdir(PDF_CACHE_DIR):
        if f == pdf_file:
            return os.path.join(PDF_CACHE_DIR, f)
        if f.startswith(stock_code + "_"):
            return os.path.join(PDF_CACHE_DIR, f)
    # 模糊查找
    for f in os.listdir(PDF_CACHE_DIR):
        if f.startswith(stock_code):
            return os.path.join(PDF_CACHE_DIR, f)
    return None


# ── 差异分析函数 ──────────────────────────────────────────────────

def compare_value(parsed, ground, field_name, tolerance=0.01):
    """
    对比单个数值，返回（标记, 说明）
    """
    if parsed is None and ground is None:
        return ("✅", "均为空，一致")
    if parsed is None and ground is not None:
        return ("➖ 缺失", f"解析缺失，PDF 原文为 {ground}")
    if parsed is not None and ground is None:
        return ("➕ 多余", f"解析多出 {parsed}，PDF 未找到对应项")

    if ground == 0 and parsed == 0:
        return ("✅", "均为 0，一致")

    if ground != 0:
        ratio = parsed / ground
        if 0.99 < ratio < 1.01:
            return ("✅", f"解析={parsed:.2f}, PDF={ground:.2f}, 匹配")
        elif 0.9 < ratio < 1.1:
            return ("✅ 近似", f"解析={parsed:.2f}, PDF={ground:.2f}, 差异{abs(1-ratio)*100:.1f}%")
        elif abs(ratio) > 100 or abs(ratio) < 0.01:
            # 单位问题
            if abs(ratio) > 1000:
                factor = ratio / 10000 if abs(ratio / 10000 - 1) < 0.01 else ratio / 1000000 if abs(ratio / 1000000 - 1) < 0.01 else None
                return ("❌ 单位", f"解析={parsed:.2f}, PDF≈{ground:.2f}, 比值≈{ratio:.1f}x")
            elif abs(ratio) < 0.001:
                return ("❌ 单位", f"解析={parsed:.2f}, PDF≈{ground:.2f}, 比值≈{ratio:.6f}x（可能缺少万/百万倍率）")
            else:
                return ("❌ 数值", f"解析={parsed:.2f}, PDF={ground:.2f}, 差异{(parsed-ground)/ground*100:.1f}%")
        else:
            return ("❌ 数值", f"解析={parsed:.2f}, PDF={ground:.2f}, 差异{(parsed-ground)/ground*100:.1f}%")
    
    return ("❌ 数值", f"解析={parsed}, PDF原文={ground}")


def check_unit_scale(parsed_val, pdf_val):
    """检查是否是单位量级问题"""
    if parsed_val is None or pdf_val is None or pdf_val == 0:
        return None
    ratio = parsed_val / pdf_val
    # 常见的倍率问题
    scales = {
        1000000: "解析少了 100 万倍（÷1000000）",
        10000: "解析少了 1 万倍（÷10000）",
        1000: "解析少了 1000 倍（÷1000）",
        100: "解析少了 100 倍（÷100）",
        0.000001: "解析多了 100 万倍",
        0.0001: "解析多了 1 万倍",
        0.001: "解析多了 1000 倍",
        0.01: "解析多了 100 倍",
    }
    for factor, desc in scales.items():
        if abs(ratio / factor - 1) < 0.05:
            return desc
    return None


def check_ratio_column(ratios):
    """检查占比列是否正确"""
    if not ratios:
        return None
    valid = [r for r in ratios if r is not None]
    if not valid:
        return None
    total = sum(valid)
    if 98 < total < 102:
        return None  # 正常
    elif total > 0:
        return f"占比和={total:.1f}%"
    return None


# ── 主校验逻辑 ──────────────────────────────────────────────────

def validate_stock(result):
    """对单只股票进行校验，返回差异列表"""
    stock_code = result["stock_code"]
    pdf_file = result.get("pdf_file", "")
    report_year = result.get("report_year", "")
    differences = []  # (字段, 子字段, 标记, 说明, pdf原文片段)

    pdf_path = get_pdf_path(stock_code, pdf_file)
    if not pdf_path or not os.path.exists(pdf_path):
        differences.append(("文件", stock_code, "⚠️ PDF 未找到", f"查找路径: {PDF_CACHE_DIR}/{pdf_file}", ""))
        return differences, None

    # 1. 营收结构
    rev = result.get("revenue_breakdown", {})
    if rev:
        snippets = extract_pdf_text(pdf_path, REVENUE_KEYWORDS)
        segments = rev.get("segments", [])
        industries = rev.get("industries", [])
        regions = rev.get("regions", [])
        
        # 检查是否误匹配了资产负债表（平安银行/江铃汽车等）
        balance_sheet_keywords = ["货币资金", "应收账款", "存货", "长期股权投资", "固定资产",
                                   "在建工程", "短期借款", "长期借款", "合同负债", "租赁负债",
                                   "吸收存款", "发放贷款", "利息收入", "利息支出"]
        if segments:
            seg_names = [s.get("name", "") for s in segments]
            bs_hits = sum(1 for n in seg_names for kw in balance_sheet_keywords if kw in n)
            if bs_hits >= 3:
                differences.append(("营收结构", "产品/业务", "❌ 误匹配", 
                    f"解析到资产负债表/银行利息表（{len(segments)} 项含资产负债表科目），非营收结构", ""))
            else:
                ratios = [s.get("ratio_pct") for s in segments]
                issue = check_ratio_column(ratios)
                if issue:
                    differences.append(("营收结构", "产品/业务", "❌ 列识别", 
                        f"占比检查: {issue}", snippets[0]["snippet"][:200] if snippets else ""))
                if snippets:
                    differences.append(("营收结构", "产品/业务", "✅", 
                        f"解析到 {len(segments)} 项", snippets[0]["snippet"][:200]))
                else:
                    differences.append(("营收结构", "产品/业务", "✅", f"解析到 {len(segments)} 项", ""))
        else:
            differences.append(("营收结构", "产品/业务", "✅", "segments 为空（可能该股票无此维度）", ""))
        
        # 检查 industries
        if industries:
            differences.append(("营收结构", "行业", "✅", f"解析到 {len(industries)} 项", ""))
        
        # 检查 regions
        if regions:
            ratios_r = [r.get("ratio_pct") for r in regions if r.get("ratio_pct") is not None]
            issue_r = check_ratio_column(ratios_r)
            if issue_r:
                differences.append(("营收结构", "地区", "❌ 列识别", 
                    f"占比检查: {issue_r}", ""))
            else:
                differences.append(("营收结构", "地区", "✅", f"解析到 {len(regions)} 项", ""))
    else:
        differences.append(("营收结构", "数据", "➖ 缺失", "解析结果为空", ""))

    # 2. 研发费用
    rnd = result.get("rnd_info")
    # 银行/金融类代码
    bank_codes = ["000001", "000563"]
    is_bank = stock_code in bank_codes
    
    if rnd and rnd.get("rnd_detail"):
        snippets = extract_pdf_text(pdf_path, RND_KEYWORDS)
        items = rnd["rnd_detail"]
        
        # 检查研发明细是否实际为其他表（递延所得税、管理费用等）
        non_rnd_keywords_in_items = ["资产减值准备", "递延收益", "可抵扣亏损", "内部交易未实现利润",
                                      "金融资产公允价值", "应付职工薪酬", "存货可抵减"]
        rnd_item_names = [i.get("name", "") for i in items]
        non_rnd_hits = sum(1 for n in rnd_item_names for kw in non_rnd_keywords_in_items if kw in n)
        
        if is_bank:
            differences.append(("研发费用", "数据", "❌ 误匹配", 
                f"银行类公司不应有研发费用，但解析到 {len(items)} 项（可能是管理费用表被误匹配）", snippets[0]["snippet"][:200] if snippets else ""))
        elif non_rnd_hits >= 2:
            differences.append(("研发费用", "明细", "❌ 误匹配", 
                f"研发明细包含 {non_rnd_hits} 项非研发关键词（如递延所得税、资产减值等），可能是其他附注表误匹配", snippets[0]["snippet"][:200] if snippets else ""))
        elif len(items) > 15:
            differences.append(("研发费用", "明细", "❌ 误匹配", 
                f"研发明细项数={len(items)}（>15）, 可能匹配到了其他附注表", snippets[0]["snippet"][:200] if snippets else ""))
        elif snippets:
            differences.append(("研发费用", "数据", "✅", f"解析到 {len(items)} 项研发明细", snippets[0]["snippet"][:200]))
        else:
            differences.append(("研发费用", "数据", "✅", f"解析到 {len(items)} 项", ""))
    elif rnd is None:
        if is_bank:
            differences.append(("研发费用", "数据", "✅", "银行为空，正确", ""))
        else:
            snippets = extract_pdf_text(pdf_path, RND_KEYWORDS)
            if snippets:
                differences.append(("研发费用", "数据", "➖ 缺失", "PDF 中有「研发费用」但解析为空", snippets[0]["snippet"][:200]))
            else:
                differences.append(("研发费用", "数据", "✅", "PDF 中无研发费用，解析为空", ""))
    else:
        differences.append(("研发费用", "数据", "✅", "rnd_info 为空", ""))

    # 3. 员工数据
    emp = result.get("employees")
    if emp:
        snippets = extract_pdf_text(pdf_path, EMPLOYEE_KEYWORDS)
        comp = emp.get("composition", [])
        edu = emp.get("education", [])
        total = emp.get("total")
        
        if total and snippets:
            differences.append(("员工数据", "总人数", "✅", f"总人数={total}", snippets[0]["snippet"][:200]))
        elif total is None and snippets:
            differences.append(("员工数据", "总人数", "➖ 缺失", "PDF 有员工信息但解析总人数为空", snippets[0]["snippet"][:200]))
        elif snippets:
            differences.append(("员工数据", "总人数", "✅", f"总人数={total}" if total else "总人数为空", ""))
        
        if comp:
            total_comp = sum(c.get("count", 0) for c in comp)
            differences.append(("员工数据", "专业构成", "✅", f"共 {len(comp)} 类，合计 {total_comp} 人", ""))
        elif snippets:
            differences.append(("员工数据", "专业构成", "➖ 缺失", "PDF 有专业构成但解析为空", snippets[0]["snippet"][:200]))
        
        if edu:
            total_edu = sum(c.get("count", 0) for c in edu)
            differences.append(("员工数据", "教育程度", "✅", f"共 {len(edu)} 类，合计 {total_edu} 人", ""))
        elif snippets:
            differences.append(("员工数据", "教育程度", "➖ 缺失", "PDF 有教育程度但解析为空", snippets[0]["snippet"][:200]))
    else:
        snippets = extract_pdf_text(pdf_path, EMPLOYEE_KEYWORDS)
        if snippets:
            differences.append(("员工数据", "数据", "➖ 缺失", "PDF 有员工信息但解析为空", snippets[0]["snippet"][:200]))

    # 4. 成本构成
    cost = result.get("cost_breakdown")
    if cost:
        snippets = extract_pdf_text(pdf_path, COST_KEYWORDS)
        # 检查是否是利润表而非成本构成
        income_keywords = ["营业总收入", "营业总成本", "营业收入", "营业成本"]
        cost_has_income = False
        for c in cost:
            ind = c.get("industry", "")
            for kw in income_keywords:
                if kw in ind:
                    cost_has_income = True
                    break
        if cost_has_income:
            differences.append(("成本构成", "数据", "❌ 误匹配", 
                "解析到的是利润表/损益表而非成本构成表", snippets[0]["snippet"][:200] if snippets else ""))
        elif snippets:
            ratios_c = [c.get("ratio_pct") for c in cost if c.get("ratio_pct") is not None]
            issue_c = check_ratio_column(ratios_c)
            if issue_c:
                differences.append(("成本构成", "数据", "❌ 列识别", f"占比检查: {issue_c}", ""))
            else:
                differences.append(("成本构成", "数据", "✅", f"解析到 {len(cost)} 项成本", snippets[0]["snippet"][:200]))
        else:
            differences.append(("成本构成", "数据", "⚠️ 参考", f"解析到 {len(cost)} 项但 PDF 中未找到成本关键字", ""))
    else:
        snippets = extract_pdf_text(pdf_path, COST_KEYWORDS)
        if snippets:
            differences.append(("成本构成", "数据", "➖ 缺失", "PDF 有成本构成但解析为空", snippets[0]["snippet"][:200]))

    # 5. 前五大客户
    clients = result.get("top_clients")
    if clients and clients.get("items"):
        snippets = extract_pdf_text(pdf_path, CLIENT_KEYWORDS)
        if snippets:
            diff.append("见下方") if "diff" in dir() else None  # noop
        total_amount = clients.get("total_amount")
        total_ratio = clients.get("total_ratio_pct")
        items = clients["items"]
        amount_ok = total_amount is not None
        ratio_ok = total_ratio is not None and 0 < total_ratio <= 100
        if amount_ok and ratio_ok:
            differences.append(("前五大客户", "汇总", "✅", 
                f"共 {len(items)} 家, 合计金额={total_amount:.2f}, 占比={total_ratio:.2f}%", ""))
        elif amount_ok:
            differences.append(("前五大客户", "汇总", "✅", f"共 {len(items)} 家, 合计金额={total_amount:.2f}", ""))
        else:
            differences.append(("前五大客户", "汇总", "⚠️", f"共 {len(items)} 家但缺少汇总金额", ""))
    elif clients and not clients.get("items"):
        snippets = extract_pdf_text(pdf_path, CLIENT_KEYWORDS)
        if snippets:
            if clients.get("total_amount"):
                differences.append(("前五大客户", "明细", "➖ 缺失", 
                    "PDF 有客户明细但 items 为空（仅有汇总金额）", snippets[0]["snippet"][:200]))
            else:
                differences.append(("前五大客户", "明细", "➖ 缺失", 
                    "PDF 有客户信息但解析 items 为空", snippets[0]["snippet"][:200]))
    else:
        snippets = extract_pdf_text(pdf_path, CLIENT_KEYWORDS)
        if snippets:
            differences.append(("前五大客户", "数据", "➖ 缺失", "PDF 有前五名客户但解析为空", snippets[0]["snippet"][:200]))
        else:
            differences.append(("前五大客户", "数据", "✅", "PDF 中无客户数据，解析为空", ""))

    # 6. 前五大供应商
    suppliers = result.get("top_suppliers")
    if suppliers and suppliers.get("items"):
        snippets = extract_pdf_text(pdf_path, SUPPLIER_KEYWORDS)
        total_amount = suppliers.get("total_amount")
        items = suppliers["items"]
        differences.append(("前五大供应商", "汇总", "✅", 
            f"共 {len(items)} 家, 合计金额={total_amount:.2f}", ""))
    elif suppliers and not suppliers.get("items"):
        snippets = extract_pdf_text(pdf_path, SUPPLIER_KEYWORDS)
        if snippets:
            if suppliers.get("total_amount"):
                differences.append(("前五大供应商", "明细", "➖ 缺失", 
                    "PDF 有供应商明细但 items 为空（仅有汇总金额）", snippets[0]["snippet"][:200]))
            else:
                differences.append(("前五大供应商", "明细", "➖ 缺失", 
                    "PDF 有供应商信息但解析 items 为空", snippets[0]["snippet"][:200]))
    else:
        snippets = extract_pdf_text(pdf_path, SUPPLIER_KEYWORDS)
        if snippets:
            differences.append(("前五大供应商", "数据", "➖ 缺失", "PDF 有前五名供应商但解析为空", snippets[0]["snippet"][:200]))
        else:
            differences.append(("前五大供应商", "数据", "✅", "PDF 中无供应商数据，解析为空", ""))

    return differences, pdf_path


# ── 修复建议推导（Step 4 映射表） ──────────────────────────────

FIX_SUGGESTIONS = {
    "单位": {
        "pattern": "❌ 单位",
        "files": ["unit_detector.py"],
        "root_cause": "`unit_detector.py` 没检测到 PDF 中的单位标注",
        "function": "`_UNIT_MAP` / `_UNIT_PATTERNS`",
        "suggestion": "增加单位模式或关键词，确保 PDF 页眉/页脚中的「人民币百万元」能被正则匹配到",
        "code_hint": "在 `unit_detector.py` 的 `_UNIT_PATTERNS` 中添加 '百万'/'百万元'/'亿元' 等模式"
    },
    "列识别": {
        "pattern": "❌ 列识别",
        "files": ["revenue_parser.py", "cost_parser.py"],
        "root_cause": "`_detect_columns` 选错了 `ratio_col`",
        "function": "`_detect_columns`",
        "suggestion": "调整 `ratio_col` 选择逻辑：优先选 `%` 值完全在 1-100 且和接近 100 的列",
        "code_hint": "在 `_detect_columns` 中增加: `if abs(sum(values) - 100) < 5: return col`"
    },
    "误匹配": {
        "pattern": "❌ 误匹配",
        "files": ["table_scanner.py", "revenue_parser.py"],
        "root_cause": "表类型识别错误",
        "function": "`TABLE_SIGNATURES`",
        "suggestion": "加排除词或缩小匹配范围",
        "code_hint": ""
    },
    "缺失": {
        "pattern": "➖ 缺失",
        "files": ["table_scanner.py", "employee_parser.py", "top_supplier_parser.py"],
        "root_cause": "匹配条件不够或扫描范围不足",
        "function": "`scan_pdf` / `parse()`",
        "suggestion": "",
        "code_hint": ""
    }
}


def derive_fix_suggestions(all_results):
    """对所有差异推导修复建议，按影响股票数排序"""
    # 统计每种问题影响的股票
    issue_stocks = defaultdict(set)
    
    for stock_code, differences in all_results:
        for field, subfield, tag, desc, _ in differences:
            if tag.startswith("❌") or tag.startswith("➖"):
                # 根据 desc 内容更精细地区分问题
                if "资产负债表" in desc or "利息" in desc:
                    key_type = "revenue_bs_mismatch"
                elif "利润表" in desc or "损益表" in desc:
                    key_type = "cost_income_mismatch"
                elif "误匹配" in tag and "研发" in field:
                    key_type = "rnd_mismatch"
                elif "误匹配" in tag:
                    key_type = "mismatch"
                elif "占比" in tag or "列识别" in tag or "占比和" in desc:
                    key_type = "ratio_col"
                elif "单位" in tag or "倍" in desc.lower():
                    key_type = "unit"
                elif "缺失" in tag and "研发" in field:
                    key_type = "rnd_missing"
                elif "缺失" in tag and ("前五大客户" in field or "前五大供应商" in field):
                    key_type = "client_supplier_missing"
                elif "缺失" in tag and "员工" in field:
                    key_type = "employee_missing"
                elif "缺失" in tag and "成本" in field:
                    key_type = "cost_missing"
                else:
                    key_type = "missing_other"
                key = f"{key_type}|{tag}|{field}|{desc[:80]}"
                issue_stocks[key].add(stock_code)
    
    # 定义每个 key_type 对应的修复建议
    fix_definitions = {
        "rnd_mismatch": {
            "files": ["table_scanner.py"],
            "root_cause": "研发费用匹配到了其他附注表（如递延所得税资产明细、管理费用明细）",
            "function": "`TABLE_SIGNATURES['rnd']` 的 `exclude` / `must_have`",
            "suggestion": "在 `TABLE_SIGNATURES['rnd']['exclude']` 中添加排除词：`'资产减值准备'`、`'递延收益'`、`'可抵扣亏损'`、`'内部交易未实现利润'`、`'金融资产公允价值'`",
            "code_hint": "添加这些非研发关键词到排除列表；或增加 `must_have` 条件要求表中必须含 `'研发费用'` 关键词"
        },
        "revenue_bs_mismatch": {
            "files": ["table_scanner.py"],
            "root_cause": "`table_scanner.py` 的营收结构中匹配到了资产负债表或银行利息收支表",
            "function": "`TABLE_SIGNATURES['revenue']` 的 `exclude`",
            "suggestion": "在 `TABLE_SIGNATURES['revenue']` 的 `exclude` 中添加排除词：`'货币资金'`、`'应收账款'`、`'存货'`、`'固定资产'`、`'吸收存款'`、`'发放贷款'`、`'利息收入'`、`'利息支出'`",
            "code_hint": "在 `TABLE_SIGNATURES['revenue']['exclude']` 中添加这些关键词，确保不匹配资产负债表和银行利息表"
        },
        "cost_income_mismatch": {
            "files": ["table_scanner.py", "cost_parser.py"],
            "root_cause": "成本构成匹配到了利润表/损益表而非营业成本构成表",
            "function": "`TABLE_SIGNATURES['cost']` 的 `exclude`",
            "suggestion": "在成本构成的排除词中添加 `'营业总收入'`、`'营业总成本'`、`'投资收益'`、`'公允价值变动'`",
            "code_hint": "在 `TABLE_SIGNATURES['cost']['exclude']` 中添加利润表项目关键词"
        },
        "ratio_col": {
            "files": ["revenue_parser.py", "cost_parser.py"],
            "root_cause": "`_detect_columns` 选错了 `ratio_col`，选中了同比增减列或金额列",
            "function": "`_detect_columns`",
            "suggestion": "调整 `ratio_col` 选择逻辑：优先选 `%` 值完全在 1-100 且和接近 100 的列；排除同比增减列（-100 到 200 范围的单列）",
            "code_hint": "在 `_detect_columns` 中增加校验: `if abs(sum(values) - 100) < 5: return col_idx`；且排除全列值都在 -100~200 范围的列"
        },
        "unit": {
            "files": ["unit_detector.py"],
            "root_cause": "`unit_detector.py` 没检测到 PDF 中的单位标注",
            "function": "`_UNIT_MAP` / `_UNIT_PATTERNS`",
            "suggestion": "增加单位模式或关键词，确保 PDF 页眉/页脚中的「人民币百万元」能被正则匹配到",
            "code_hint": "在 `unit_detector.py` 的 `_UNIT_PATTERNS` 中添加 '百万'/'百万元'/'亿元' 等模式"
        },
        "rnd_missing": {
            "files": ["table_scanner.py", "rnd_parser.py"],
            "root_cause": "研发费用表匹配条件不够或扫描范围不足（max_pages 默认 80 页不够）",
            "function": "`scan_pdf` 的 `max_pages` / `TABLE_SIGNATURES['rnd']`",
            "suggestion": "增大 `scan_pdf` 的 `max_pages`（如 200），或在 `TABLE_SIGNATURES['rnd']` 中放宽匹配条件",
            "code_hint": "在 `table_scanner.py` 中将默认 `max_pages` 从 80 改为 200"
        },
        "client_supplier_missing": {
            "files": ["top_supplier_parser.py"],
            "root_cause": "客户/供应商汇总表先于明细表被匹配，明细表没有赋值",
            "function": "`parse()`",
            "suggestion": "明细表匹配条件改为 `result['top_clients'] is None or not result['top_clients'].get('items')`",
            "code_hint": "在 `parse()` 中，匹配到明细表后检查 `items` 是否已存在，避免汇总表覆盖明细"
        },
        "employee_missing": {
            "files": ["employee_parser.py"],
            "root_cause": "专业构成和教育程度被 pdfplumber 拆成两个独立表，或 PDF 格式特殊",
            "function": "`parse()`",
            "suggestion": "合并所有匹配表的 `composition` 和 `education` 结果；对总人数找关键词 `'在职员工'` 所在行",
            "code_hint": "在 `parse()` 中: `for table in matched_tables: composition.extend(table.get('composition',[]))`"
        },
        "cost_missing": {
            "files": ["table_scanner.py", "cost_parser.py"],
            "root_cause": "成本构成表的扫描范围不够或关键词不匹配",
            "function": "`scan_pdf` 的 `max_pages` / `TABLE_SIGNATURES['cost']` 的 `keywords`",
            "suggestion": "增大 `scan_pdf` 的 `max_pages`（如 200），或在成本表关键词中增加 `'营业成本'`",
            "code_hint": "在 `table_scanner.py` 中将默认 `max_pages` 从 80 改为 200；在 `TABLE_SIGNATURES['cost']['keywords']` 中添加 `'营业成本'`"
        },
    }
    
    # 生成建议列表
    suggestions = []
    for issue_key, stocks in sorted(issue_stocks.items(), key=lambda x: -len(x[1])):
        key_type = issue_key.split("|")[0]
        tag = issue_key.split("|")[1]
        field = issue_key.split("|")[2]
        desc = issue_key.split("|")[3]
        
        fix = fix_definitions.get(key_type, {
            "files": ["待定位"],
            "root_cause": "需要人工复查",
            "function": "N/A",
            "suggestion": "N/A",
            "code_hint": ""
        })
        
        suggestions.append({
            "issue": f"{tag} | {field} | {desc}",
            "stocks": sorted(stocks),
            "count": len(stocks),
            "tag": tag,
            "field": field,
            "fix": fix,
            "key_type": key_type
        })
    
    return suggestions


# ── 报告生成 ──────────────────────────────────────────────────────

def generate_report(data, all_diffs, suggestions):
    """生成最终 Markdown 报告"""
    lines = []
    total = data["total"]
    version = data.get("parser_version", "unknown")
    
    lines.append(f"# 批量测试报告 V4（含修复建议）\n")
    lines.append(f"**解析器版本**: {version}")
    lines.append(f"**股票数量**: {total}")
    lines.append(f"**平均字段数**: {data.get('average_field_count', 'N/A')}")
    lines.append(f"**平均耗时**: {data.get('average_duration_sec', 'N/A')} 秒")
    lines.append(f"**生成时间**: {data.get('export_time', 'N/A')}\n")
    
    # 总体统计
    lines.append("---\n")
    lines.append("## 总体统计\n")
    
    field_stats = defaultdict(lambda: {"ok": 0, "warn": 0, "error": 0, "missing": 0, "total": 0})
    for stock_code, diffs in all_diffs:
        for field, subfield, tag, desc, _ in diffs:
            field_stats[f"{field}|{subfield}"]["total"] += 1
            if tag == "✅" or tag == "✅ 近似":
                field_stats[f"{field}|{subfield}"]["ok"] += 1
            elif tag.startswith("⚠️"):
                field_stats[f"{field}|{subfield}"]["warn"] += 1
            elif tag.startswith("❌"):
                field_stats[f"{field}|{subfield}"]["error"] += 1
            elif tag.startswith("➖"):
                field_stats[f"{field}|{subfield}"]["missing"] += 1
    
    lines.append("| 字段 | ✅ 正确 | ⚠️ 警告 | ❌ 错误 | ➖ 缺失 | 总份数 | 正确率 |")
    lines.append("|------|--------|--------|--------|--------|--------|--------|")
    for key, stats in sorted(field_stats.items()):
        ok = stats["ok"]
        err = stats["error"]
        warn = stats["warn"]
        miss = stats["missing"]
        tot = stats["total"]
        rate = ok / tot * 100 if tot > 0 else 0
        lines.append(f"| {key} | {ok} | {warn} | {err} | {miss} | {tot} | {rate:.1f}% |")
    
    lines.append("")
    
    # 逐份校验
    lines.append("---\n")
    lines.append("## 逐份校验\n")
    
    for i, result in enumerate(data["results"]):
        stock_code = result["stock_code"]
        year = result.get("report_year", "")
        dur = result.get("duration_sec", 0)
        field_count = result.get("field_count", 0)
        diffs = all_diffs[i][1]
        
        lines.append(f"### [{i+1}/{total}] {stock_code} ({year}) — 耗时 {dur}s, 字段数 {field_count}\n")
        
        lines.append("| 字段 | 子字段 | 标记 | 说明 |")
        lines.append("|------|--------|------|------|")
        for field, subfield, tag, desc, snippet in diffs:
            short_desc = desc[:120] if len(desc) > 120 else desc
            lines.append(f"| {field} | {subfield} | {tag} | {short_desc} |")
        lines.append("")
    
    # 修复建议
    lines.append("---\n")
    lines.append("## 修复建议\n")
    
    lines.append("按影响股票数从多到少排列：\n")
    
    # 聚合相同根因的建议（按 key_type 聚合）
    root_cause_groups = defaultdict(lambda: {"stocks": set(), "items": [], "fix": None, "key_type": ""})
    for sug in suggestions:
        kt = sug["key_type"]
        root_cause_groups[kt]["stocks"].update(sug["stocks"])
        root_cause_groups[kt]["items"].append(sug)
        root_cause_groups[kt]["fix"] = sug["fix"]
        root_cause_groups[kt]["key_type"] = kt
    
    sorted_groups = sorted(root_cause_groups.items(), key=lambda x: -len(x[1]["stocks"]))
    
    for idx, (kt, group) in enumerate(sorted_groups, 1):
        fix = group["fix"]
        stocks = sorted(group["stocks"])
        first_item = group["items"][0]
        
        # 计算总差异数
        total_diffs = len(group["items"])
        
        lines.append(f"### 问题 {idx}：{fix['root_cause']}\n")
        lines.append(f"**影响股票**（{len(stocks)} 只）：{', '.join(stocks)}\n")
        lines.append(f"**差异类型**：{first_item['tag']}\n")
        lines.append(f"**根因文件**：`{'`, `'.join(fix['files'])}`\n")
        lines.append(f"**根因函数**：{fix['function']}\n")
        lines.append(f"**根因分析**：{fix['root_cause']}\n")
        lines.append(f"**建议修改**：\n")
        lines.append(f"1. {fix['suggestion']}\n")
        if fix.get("code_hint"):
            lines.append(f"2. 代码提示：{fix['code_hint']}\n")
        lines.append(f"\n**受影响的具体情况**（共 {total_diffs} 条差异）：\n")
        for item in group["items"]:
            for s in item["stocks"]:
                lines.append(f"- {s}: {item['issue']}\n")
        lines.append("")
    
    # 改进优先级
    lines.append("---\n")
    lines.append("## 改进优先级\n")
    lines.append("按影响股票数从多到少排列：\n\n")
    lines.append("| 优先级 | 问题描述 | 影响股票数 | 建议的修复方向 |")
    lines.append("|--------|---------|-----------|---------------|")
    for idx, (kt, group) in enumerate(sorted_groups, 1):
        fix = group["fix"]
        first_item = group["items"][0]
        short_desc = fix['root_cause'][:50]
        lines.append(f"| P{idx} | {short_desc} | {len(group['stocks'])} 只 | {fix['suggestion'][:50]} |")
    
    lines.append("")
    lines.append("---")
    lines.append("*报告由 validate_v4_report.py 自动生成*")
    
    return "\n".join(lines)


# ── 主流程 ──────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("FinParseAI 解析结果校验 — V4（含修复建议）")
    print("=" * 60)
    
    # Step 1: 加载测试数据
    print("\n[Step 1] 加载测试结果...")
    data = load_json(TEST_RESULTS_PATH)
    results = data["results"]
    print(f"  共 {len(results)} 份股票数据")
    
    # Step 2-3: 逐份校验
    print("\n[Step 2-3] 逐份校验...")
    all_diffs = []
    
    for i, result in enumerate(results):
        stock_code = result["stock_code"]
        print(f"  [{i+1}/{len(results)}] {stock_code}...", end=" ", flush=True)
        
        diffs, pdf_path = validate_stock(result)
        all_diffs.append((stock_code, diffs))
        
        error_count = sum(1 for d in diffs if d[2].startswith("❌"))
        missing_count = sum(1 for d in diffs if d[2].startswith("➖"))
        ok_count = sum(1 for d in diffs if d[2].startswith("✅"))
        
        print(f" ✅{ok_count} ❌{error_count} ➖{missing_count} 差异共{len(diffs)}项")
    
    # Step 4: 推导修复建议
    print("\n[Step 4] 推导修复建议...")
    suggestions = derive_fix_suggestions(all_diffs)
    print(f"  共 {len(suggestions)} 条差异化建议，聚合为 {len(set(s['fix']['root_cause'] for s in suggestions))} 类根因")
    
    # Step 5: 生成报告
    print("\n[Step 5] 生成报告...")
    report = generate_report(data, all_diffs, suggestions)
    
    # 写入文件
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"  报告已保存到: {OUTPUT_PATH}")
    print(f"  报告长度: {len(report)} 字符")
    
    print("\n✅ 校验完成！")


if __name__ == "__main__":
    main()
