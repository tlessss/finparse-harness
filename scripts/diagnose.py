"""
诊断工具 — 逐层分析每个解析器在哪一步失败

对一份 PDF，分 4 步输出每个解析器的诊断结果：
  1. 页面定位 — 是否找到目标页码
  2. 表格提取 — Camelot/pdfplumber 是否提取出表格
  3. 数据解析 — 引擎能否从表格中提取出数值/名称
  4. 分类输出 — 最终输出结构是否正确
"""

import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import yaml


def load_rule():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "src", "parser_rules", "industry_default.yaml")
    with open(path) as f:
        return yaml.safe_load(f)


def diagnose_revenue(pdf_path):
    """营收结构诊断"""
    from parsers.revenue_parser import RevenueParser
    rule = load_rule()

    print(f"\n{'='*60}")
    print(f"📊 RevenueParser 诊断")
    print(f"{'='*60}")

    # Step 1: 页面定位
    parser = RevenueParser(rule)
    pages = parser._get_pages(pdf_path)
    print(f"\n1️⃣  页面定位")
    print(f"   搜索关键词: {parser._keywords}")
    print(f"   命中页码: {pages}")

    # Step 2: Camelot 表格提取
    tables_c = parser._extract_tables_camelot(pdf_path)
    print(f"\n2️⃣  Camelot 提取")
    print(f"   找到 {len(tables_c)} 个匹配表")
    if tables_c:
        t = tables_c[0]
        df = t.df
        print(f"   首表: {t.shape[0]}行×{t.shape[1]}列")
        for i in range(min(6, len(df))):
            cells = [str(c).strip().replace('\n',' ')[:50] for c in df.iloc[i].tolist()]
            non_empty = {j: c for j, c in enumerate(cells) if c}
            print(f"   行{i}: {non_empty}")

    # Step 3: pdfplumber 提取（降级）
    tables_p = parser._extract_tables_pdfplumber(pdf_path)
    print(f"\n3️⃣  pdfplumber 降级")
    print(f"   找到 {len(tables_p)} 个匹配表")
    if tables_p:
        t = tables_p[0]
        print(f"   首表: {len(t)}行")
        for i, row in enumerate(t[:6]):
            cells = [c.strip()[:35] if c else "" for c in row]
            non_empty = {j: c for j, c in enumerate(cells) if c}
            if non_empty:
                print(f"   行{i}: {non_empty}")

    # Step 4: 分类输出
    print(f"\n4️⃣  分类结果")
    r = parser.parse(pdf_path)
    rev = r["revenue_breakdown"]
    if rev:
        for dim in ["segments", "industries", "regions"]:
            items = rev.get(dim, [])
            if items:
                total_pct = sum(i.get("ratio_pct", 0) or 0 for i in items)
                print(f"   {dim}: {len(items)}项 占比和={total_pct:.1f}%")
                for s in items[:3]:
                    print(f"     {s['name']}: {s.get('revenue_wan')} ({s.get('ratio_pct')})")
            else:
                print(f"   {dim}: 空")
    else:
        print(f"   状态: {r['status']}")


def diagnose_generic(parser_name, pdf_path, cls, section_key, rule):
    """通用诊断器"""
    section = rule.get(section_key, {})
    pages_str = section.get("pages", "")
    keywords = section.get("table_keywords", [])

    print(f"\n{'='*60}")
    print(f"📊 {parser_name} 诊断")
    print(f"{'='*60}")

    # Step 1: 页面定位
    print(f"\n1️⃣  页面定位")
    print(f"   配置页码: {pages_str}")

    # 搜索关键词
    import fitz
    doc = fitz.open(pdf_path)
    kw_pages = {}
    for kw in keywords:
        for pn in range(len(doc)):
            if kw in doc[pn].get_text("text"):
                kw_pages.setdefault(kw, []).append(pn + 1)
    doc.close()
    print(f"   关键词:")
    for kw, pgs in kw_pages.items():
        print(f"     「{kw}」→ 第{pgs[:5]}页")

    # Step 2: Camelot 提取
    print(f"\n2️⃣  Camelot 提取")
    import camelot
    page_nums = []
    for part in pages_str.split(","):
        p = part.strip()
        if "-" in p:
            s, e = p.split("-", 1)
            page_nums.extend(range(int(s), int(e) + 1))
        else:
            page_nums.append(int(p))

    for flavor in ["lattice", "stream"]:
        try:
            tables = camelot.read_pdf(pdf_path, pages=",".join(str(p) for p in page_nums[:3]), flavor=flavor)
            if tables:
                print(f"   {flavor}: {len(tables)} 个表")
                for i, t in enumerate(tables[:3]):
                    text = " ".join(str(c) for r in t.df.values for c in r)
                    print(f"   表{i+1}: {t.shape} '{text[:80]}'")
                break
        except Exception as e:
            print(f"   {flavor}: {e}")

    # Step 3: 解析结果
    print(f"\n3️⃣  解析结果")
    parser = cls(rule)
    start = time.time()
    r = parser.parse(pdf_path)
    elapsed = time.time() - start

    # 提取关键字段
    if parser_name == "RndParser":
        data = r.get("rnd_info")
        if data and data.get("rnd_detail"):
            print(f"   ✅ {len(data['rnd_detail'])}项明细, 合计={data['total_this']:.0f}")
            for d in data["rnd_detail"][:5]:
                print(f"     {d['name']}: {d['amount_this']:.0f}")
        else:
            print(f"   ❌ 空 ({r.get('status')})")
    elif parser_name == "EmployeeParser":
        data = r.get("employees")
        if data and data.get("total"):
            print(f"   ✅ total={data['total']}, comp={len(data.get('composition',[]))}类, edu={len(data.get('education',[]))}类")
        else:
            print(f"   ❌ 空 ({r.get('status')})")
    elif parser_name == "CostParser":
        data = r.get("cost_breakdown")
        if data:
            print(f"   ✅ {len(data)}项")
            for c in data:
                print(f"     {c['industry']}->{c['item']}: {c['amount_wan']}w {c['ratio_pct']}%")
        else:
            print(f"   ❌ 空 ({r.get('status')})")
    elif parser_name == "TopSupplierParser":
        tc = r.get("top_clients")
        ts = r.get("top_suppliers")
        tc_items = len(tc.get("items", [])) if tc else 0
        ts_items = len(ts.get("items", [])) if ts else 0
        print(f"   ✅ 客户{tc_items}家/供应商{ts_items}家" if tc_items or ts_items else f"   ❌ 空")
    print(f"   耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else "pdfs/多氟多-2025.pdf"
    print(f"\n🔍 诊断 PDF: {os.path.basename(pdf)}")

    rule = load_rule()

    # 逐个诊断
    diagnose_revenue(pdf)

    from parsers.rnd_parser import RndParser
    diagnose_generic("RndParser", pdf, RndParser, "rnd_section", rule)

    from parsers.employee_parser import EmployeeParser
    diagnose_generic("EmployeeParser", pdf, EmployeeParser, "employee_section", rule)

    from parsers.cost_parser import CostParser
    diagnose_generic("CostParser", pdf, CostParser, "cost_section", rule)

    from parsers.top_supplier_parser import TopSupplierParser
    diagnose_generic("TopSupplierParser", pdf, TopSupplierParser, "supplier_section", rule)
