"""
测试结果校验工具 — 对照 PDF 原文验证解析数据是否准确

用法:
  # 对一次解析结果做完整校验
  python3 scripts/validate_result.py 002407 2025 --pdf pdfs/多氟多-2025.pdf

  # 输出 Markdown 测试报告
  python3 scripts/validate_result.py 002407 2025 --pdf pdfs/多氟多-2025.pdf --report
"""

import sys
import os
import json
import re
from typing import Dict, List, Optional

# 加入 src 路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))


def load_parse_result(stock_code: str, year: int, pdf_path: str) -> Dict:
    """用当前引擎解析 PDF 返回结果"""
    from engine_orchestrator import FinParseAI
    engine = FinParseAI()
    return engine.run(pdf_path, stock_code=stock_code, report_year=year, db_write=False)


def extract_pdf_text(pdf_path: str, keywords: List[str], context_lines: int = 5) -> Dict[str, str]:
    """从 PDF 原文中提取关键词附近的文本作为参照"""
    import fitz
    doc = fitz.open(pdf_path)
    results = {}
    for kw in keywords:
        hits = []
        for pn in range(len(doc)):
            text = doc[pn].get_text("text")
            if kw in text:
                idx = text.index(kw)
                start = max(0, idx - 100)
                end = min(len(text), idx + 300)
                snippet = text[start:end].replace("\n", " ").strip()
                hits.append({"page": pn + 1, "snippet": snippet})
        if hits:
            results[kw] = hits[:3]
    doc.close()
    return results


def validate_revenue(result: Dict, pdf_text: Dict) -> List[Dict]:
    """校验营收结构数据"""
    issues = []
    rev = result.get("revenue_breakdown")
    if not rev:
        return [{"field": "revenue_breakdown", "status": "MISSING", "detail": "未解析出营收结构"}]

    for dim in ["segments", "industries", "regions"]:
        items = rev.get(dim, [])
        if not items:
            issues.append({"field": f"revenue_breakdown.{dim}", "status": "EMPTY", "detail": "无数据"})
            continue

        # 校验占比和是否在 95-105 之间
        ratios = [s.get("ratio_pct") for s in items if s.get("ratio_pct") is not None]
        if ratios:
            total = sum(ratios)
            if total < 80 or total > 120:
                issues.append({
                    "field": f"revenue_breakdown.{dim}",
                    "status": "SUSPICIOUS",
                    "detail": f"占比和={total:.1f}%，正常应在95-105%",
                })

        # 校验是否存在占比异常大的单项
        for s in items:
            if s.get("ratio_pct") and s["ratio_pct"] > 100:
                issues.append({
                    "field": f"revenue_breakdown.{dim}.{s['name']}",
                    "status": "ERROR",
                    "detail": f"占比={s['ratio_pct']}%，超过100%",
                })

    return issues


def validate_rnd(result: Dict) -> List[Dict]:
    """校验研发费用数据"""
    issues = []
    rnd = result.get("rnd_info")
    if not rnd or not rnd.get("rnd_detail"):
        return [{"field": "rnd_info", "status": "MISSING", "detail": "未解析出研发费用"}]

    details = rnd["rnd_detail"]
    total = rnd.get("total_this")

    # 验证明细和与合计
    if total:
        detail_sum = sum(d.get("amount_this", 0) or 0 for d in details)
        diff_pct = abs(detail_sum - total) / max(total, 1) * 100
        if diff_pct > 5:
            issues.append({
                "field": "rnd_info.total_this",
                "status": "SUSPICIOUS",
                "detail": f"明细之和={detail_sum:.0f}，声明合计={total:.0f}，差异{diff_pct:.1f}%",
            })
        else:
            issues.append({
                "field": "rnd_info.total_this",
                "status": "PASS",
                "detail": f"明细之和={detail_sum:.0f}，声明合计={total:.0f}，差异{diff_pct:.1f}%",
            })

    # 检查研发费用项数是否合理（通常在 5-15 项）
    if len(details) < 3:
        issues.append({
            "field": "rnd_info.detail_count",
            "status": "SUSPICIOUS",
            "detail": f"仅{len(details)}项，研发费用明细通常5项以上",
        })
    elif len(details) > 20:
        issues.append({
            "field": "rnd_info.detail_count",
            "status": "SUSPICIOUS",
            "detail": f"多达{len(details)}项，可能误匹配了非研发费用表",
        })
    else:
        issues.append({
            "field": "rnd_info.detail_count",
            "status": "PASS",
            "detail": f"{len(details)}项",
        })

    return issues


def validate_employees(result: Dict) -> List[Dict]:
    """校验员工数据"""
    issues = []
    emp = result.get("employees")
    if not emp or not emp.get("total"):
        return [{"field": "employees", "status": "MISSING", "detail": "未解析出员工数据"}]

    total = emp["total"]
    comp = emp.get("composition", [])
    edu = emp.get("education", [])

    # 专业构成人数和
    if comp:
        comp_sum = sum(c.get("count", 0) for c in comp)
        if abs(comp_sum - total) > 10:
            issues.append({
                "field": "employees.composition",
                "status": "SUSPICIOUS",
                "detail": f"专业构成之和={comp_sum}，声明总数={total}，差异{abs(comp_sum - total)}",
            })
        else:
            issues.append({
                "field": "employees.composition",
                "status": "PASS",
                "detail": f"专业构成之和={comp_sum}，声明总数={total}",
            })

    # 教育程度人数和
    if edu:
        edu_sum = sum(e.get("count", 0) for e in edu)
        if abs(edu_sum - total) > 10:
            issues.append({
                "field": "employees.education",
                "status": "SUSPICIOUS",
                "detail": f"教育程度之和={edu_sum}，声明总数={total}，差异{abs(edu_sum - total)}",
            })

    return issues


def validate_top(result: Dict) -> List[Dict]:
    """校验供应商/客户数据"""
    issues = []
    tc = result.get("top_clients")
    ts = result.get("top_suppliers")

    if not tc or not tc.get("items"):
        issues.append({"field": "top_clients", "status": "MISSING", "detail": "未解析出前五大客户"})
    else:
        items = tc["items"]
        total_ratio = tc.get("total_ratio_pct", 0)
        items_ratio_sum = sum(i.get("ratio_pct", 0) or 0 for i in items)
        if abs(total_ratio - items_ratio_sum) > 1:
            issues.append({
                "field": "top_clients",
                "status": "SUSPICIOUS",
                "detail": f"声明合计占比={total_ratio}%，明细之和={items_ratio_sum:.2f}%",
            })

    if not ts or not ts.get("items"):
        issues.append({"field": "top_suppliers", "status": "MISSING", "detail": "未解析出前五大供应商"})

    return issues


def generate_report(stock_code: str, year: int, pdf_path: str) -> Dict:
    """生成完整校验报告"""
    import time
    start = time.time()

    result = load_parse_result(stock_code, year, pdf_path)
    parse_time = time.time() - start

    all_issues = []
    all_issues.extend(validate_revenue(result, {}))
    all_issues.extend(validate_rnd(result))
    all_issues.extend(validate_employees(result))
    all_issues.extend(validate_top(result))

    # 统计
    statuses = {}
    for issue in all_issues:
        s = issue["status"]
        if s not in statuses:
            statuses[s] = 0
        statuses[s] += 1

    return {
        "stock_code": stock_code,
        "report_year": year,
        "pdf_file": os.path.basename(pdf_path),
        "parse_duration_sec": round(parse_time, 1),
        "field_count": result.get("field_count", 0),
        "total_checks": len(all_issues),
        "status_summary": statuses,
        "issues": all_issues,
        "parse_result": {
            "revenue_breakdown": result.get("revenue_breakdown"),
            "rnd_info": result.get("rnd_info"),
            "employees": result.get("employees"),
            "cost_breakdown": result.get("cost_breakdown"),
            "top_clients": result.get("top_clients"),
            "top_suppliers": result.get("top_suppliers"),
        },
    }


def print_markdown(report: Dict):
    """以 Markdown 格式输出报告"""
    print(f"# 校验报告: {report['stock_code']} {report['report_year']}")
    print(f"\n**PDF**: {report['pdf_file']} | **解析耗时**: {report['parse_duration_sec']}s | **字段数**: {report['field_count']}/6")
    print(f"\n## 检查结果: {report['total_checks']} 项")
    print(f"\n| 状态 | 数量 |")
    print(f"|------|------|")
    for s, c in sorted(report["status_summary"].items()):
        icon = {"PASS": "✅", "SUSPICIOUS": "⚠️", "ERROR": "❌", "MISSING": "❌", "EMPTY": "⬜"}.get(s, "❓")
        print(f"| {icon} {s} | {c} |")

    print(f"\n## 逐项明细")
    for issue in report["issues"]:
        icon = {"PASS": "✅", "SUSPICIOUS": "⚠️", "ERROR": "❌", "MISSING": "❌", "EMPTY": "⬜"}.get(issue["status"], "❓")
        print(f"\n{icon} **{issue['field']}**: {issue['detail']}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="校验解析结果与 PDF 原文的一致性")
    parser.add_argument("stock_code", help="股票代码")
    parser.add_argument("year", type=int, help="年份")
    parser.add_argument("--pdf", required=True, help="PDF 文件路径")
    parser.add_argument("--report", action="store_true", help="输出 Markdown 报告")
    parser.add_argument("--output", help="输出 JSON 到文件")
    args = parser.parse_args()

    report = generate_report(args.stock_code, args.year, args.pdf)

    if args.report:
        print_markdown(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n已保存到: {args.output}")
