"""
调试工具 — 对单只股票逐层打印所有解析步骤

用法:
  python3 scripts/debug_stock.py 000002 2025
  python3 scripts/debug_stock.py 002407 2025 --local  # 用本地 pdfs/ 目录
"""

import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src"))

import yaml
import fitz
import camelot
import pdfplumber


def load_rule():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "parser_rules", "industry_default.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def find_pdf(stock_code: str, year: int, local: bool = False) -> str:
    if local:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "pdfs")
        for f in os.listdir(path):
            if f.endswith(".pdf") and stock_code in f:
                return os.path.join(path, f)
    cache = os.path.join(os.path.dirname(os.path.dirname(__file__)), "../book-agent/output/pdf_cache")
    for f in os.listdir(cache):
        if not f.endswith(".pdf"):
            continue
        parts = f.replace(".pdf", "").split("_")
        if len(parts) >= 2 and parts[0] == stock_code and parts[1] == str(year):
            return os.path.join(cache, f)
    return None


def print_step(title: str, obj, max_lines=20):
    """格式化打印调试步骤"""
    print(f"\n{'─'*60}")
    print(f"  🔍 {title}")
    print(f"{'─'*60}")
    if isinstance(obj, str):
        print(f"  {obj}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:max_lines]):
            print(f"  [{i}] {item}")
        if len(obj) > max_lines:
            print(f"  ... 还有 {len(obj) - max_lines} 行")
    elif isinstance(obj, dict):
        for k, v in list(obj.items())[:max_lines]:
            print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:120] if not isinstance(v, str) else v}")
    else:
        print(f"  {obj}")


def debug_parser(parser_name: str, pdf_path: str, section_key: str, parser_cls, rule):
    section = rule.get(section_key, {})
    pages_str = section.get("pages", "")
    keywords = section.get("table_keywords", [])

    print(f"\n{'='*70}")
    print(f"  📊 {parser_name}")
    print(f"{'='*70}")

    # Step 1: 页面定位
    print(f"\n{'─'*50}")
    print(f"  STEP 1: 页面定位")
    print(f"{'─'*50}")
    print(f"  YAML配置页码: {pages_str}")
    print(f"  YAML搜索关键词: {keywords}")

    doc = fitz.open(pdf_path)
    for kw in keywords:
        pages = []
        for pn in range(len(doc)):
            if kw in doc[pn].get_text("text"):
                pages.append(pn + 1)
        print(f"  搜索「{kw}」→ 命中第 {pages[:5]} 页")
    doc.close()

    # Step 2: 表格提取
    print(f"\n{'─'*50}")
    print(f"  STEP 2: 表格提取")
    print(f"{'─'*50}")

    # 解析页码范围
    page_nums = []
    for part in pages_str.split(","):
        p = part.strip()
        if "-" in p:
            s, e = p.split("-", 1)
            page_nums.extend(range(int(s), int(e) + 1))
        else:
            page_nums.append(int(p))
    print(f"  解析页码范围: {page_nums[:5]}...")

    # Camelot
    for flavor in ["lattice", "stream"]:
        try:
            tables = camelot.read_pdf(pdf_path, pages=",".join(str(p) for p in page_nums[:5]), flavor=flavor)
            if tables:
                print(f"  Camelot ({flavor}): {len(tables)} 个表格")
                for i, t in enumerate(tables[:3]):
                    text = " ".join(str(c) for r in t.df.values for c in r)[:150]
                    print(f"    表{i+1}: {t.shape[0]}行×{t.shape[1]}列 | '{text}'")
                break
        except Exception as e:
            print(f"  Camelot ({flavor}): 错误 - {e}")

    # pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        for pn in page_nums[:5]:
            if pn - 1 >= len(pdf.pages):
                continue
            tables = pdf.pages[pn - 1].extract_tables()
            if tables:
                print(f"  pdfplumber 第{pn}页: {len(tables)} 个表")
                for i, t in enumerate(tables[:2]):
                    text = " ".join(c for r in t for c in r if c)[:150]
                    print(f"    表{i+1}: {len(t)}行 | '{text}'")
                break

    # Step 3: 引擎解析中间结果
    print(f"\n{'─'*50}")
    print(f"  STEP 3: 解析器执行")
    print(f"{'─'*50}")
    
    parser = parser_cls(rule)
    try:
        start = time.time()
        result = parser.parse(pdf_path)
        elapsed = time.time() - start
        print(f"  耗时: {elapsed:.2f}s")

        # 提取关键字段展示
        if parser_name == "营收结构":
            rev = result.get("revenue_breakdown")
            if rev:
                for dim in ["segments", "industries", "regions"]:
                    items = rev.get(dim, [])
                    if items:
                        total_pct = sum(i.get("ratio_pct", 0) or 0 for i in items)
                        print(f"  {dim}: {len(items)}项 占比和={total_pct:.1f}%")
                        for s in items[:5]:
                            print(f"    {s['name']}: {s.get('revenue_wan')}万元 ({s.get('ratio_pct')}%)")
                    else:
                        print(f"  {dim}: 空")
            else:
                print(f"  状态: {result.get('status')}")
        elif parser_name == "研发费用":
            rnd = result.get("rnd_info")
            if rnd and rnd.get("rnd_detail"):
                print(f"  明细: {len(rnd['rnd_detail'])}项")
                for d in rnd["rnd_detail"][:5]:
                    print(f"    {d['name']}: {d['amount_this']:.0f}")
                print(f"  合计: {rnd['total_this']:.0f}")
            else:
                print(f"  状态: {result.get('status')}")
        elif parser_name == "员工数据":
            emp = result.get("employees")
            if emp and emp.get("total"):
                print(f"  total={emp['total']}")
                print(f"  专业构成: {len(emp.get('composition',[]))}类")
                for c in emp.get("composition", [])[:5]:
                    print(f"    {c['type']}: {c['count']}人")
            else:
                print(f"  状态: {result.get('status')}")
        elif parser_name == "成本构成":
            cost = result.get("cost_breakdown")
            if cost:
                print(f"  {len(cost)}项")
                for c in cost[:3]:
                    print(f"    {c['industry']}→{c['item']}: {c['amount_wan']}w {c['ratio_pct']}%")
            else:
                print(f"  状态: {result.get('status')}")
        elif parser_name == "供应商/客户":
            tc = result.get("top_clients")
            ts = result.get("top_suppliers")
            tc_n = len(tc.get("items", [])) if tc else 0
            ts_n = len(ts.get("items", [])) if ts else 0
            if tc_n:
                print(f"  客户: {tc_n}家 合计占比={tc.get('total_ratio_pct')}%")
                for item in tc["items"][:3]:
                    print(f"    第{item['rank']}名: {item['name']} {item.get('amount_wan')}w {item['ratio_pct']}%")
            else:
                print(f"  客户: 空")
            if ts_n:
                print(f"  供应商: {ts_n}家 合计占比={ts.get('total_ratio_pct')}%")
                for item in ts["items"][:3]:
                    print(f"    第{item['rank']}名: {item['name']} {item.get('amount_wan')}w {item['ratio_pct']}%")
            else:
                print(f"  供应商: 空")
    except Exception as e:
        import traceback
        print(f"  错误: {e}")
        traceback.print_exc()

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser(description="调试单只股票的解析流程")
    parser.add_argument("stock_code", help="股票代码，如 000002")
    parser.add_argument("year", type=int, help="年份，如 2025")
    parser.add_argument("--local", action="store_true", help="使用本地 pdfs/ 目录")
    args = parser.parse_args()

    pdf_path = find_pdf(args.stock_code, args.year, local=args.local)
    if not pdf_path:
        print(f"❌ 未找到 {args.stock_code}_{args.year}.pdf")
        sys.exit(1)

    print(f"\n{'#'*70}")
    print(f"#  调试: {args.stock_code} {args.year}")
    print(f"#  PDF: {pdf_path}")
    print(f"{'#'*70}")

    rule = load_rule()

    from parsers.revenue.default import RevenueParser
    from parsers.rnd.default import RndParser
    from parsers.employee.default import EmployeeParser
    from parsers.cost.default import CostParser
    from parsers.top_supplier.default import TopSupplierParser

    results = {}
    results["revenue"] = debug_parser("营收结构", pdf_path, "revenue_section", RevenueParser, rule)
    results["rnd"] = debug_parser("研发费用", pdf_path, "rnd_section", RndParser, rule)
    results["employee"] = debug_parser("员工数据", pdf_path, "employee_section", EmployeeParser, rule)
    results["cost"] = debug_parser("成本构成", pdf_path, "cost_section", CostParser, rule)
    results["supplier"] = debug_parser("供应商/客户", pdf_path, "supplier_section", TopSupplierParser, rule)

    # 输出摘要
    print(f"\n{'='*70}")
    print(f"  摘要")
    print(f"{'='*70}")
    for name, key in [("营收", "revenue"), ("研发", "rnd"), ("员工", "employee"), ("成本", "cost"), ("客户/供应商", "supplier")]:
        r = results[key]
        if r and r.get("status") == "ok" or (key == "revenue" and r.get("revenue_breakdown")):
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name}")


if __name__ == "__main__":
    main()
