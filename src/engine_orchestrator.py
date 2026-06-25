"""
FinParseAI 编排器 — 统一解析入口

职责：
  1. 加载 YAML 规则配置
  2. 按顺序执行 6 个解析器
  3. 组装完整 JSON 输出
  4. 写入 financial_reports 数据库

用法：
  from src.engine_orchestrator import FinParseAI
  result = FinParseAI().run("pdfs/多氟多-2025.pdf", stock_code="002407", report_year=2025)
"""

import time
import json
from typing import Dict, Optional
from pathlib import Path

import yaml

from src.parsers.selector import select_parser
from src.database import find_stock, update_report_fields


class FinParseAI:
    """统一解析编排入口"""

    def __init__(self, rule_path: Optional[str] = None):
        if rule_path is None:
            rule_path = Path(__file__).parent.parent / "src" / "parser_rules" / "industry_default.yaml"
        with open(rule_path, "r", encoding="utf-8") as f:
            self.rule = yaml.safe_load(f)

    def _get_parser(self, field: str, pdf_path: str):
        """选择并实例化最适合的解析器"""
        cls = select_parser(field, pdf_path)
        return cls(self.rule)

    def _route_revenue(self, code, year, all_tables):
        """营收：选择即验证路由到认证专用解析器；命中返回结果，否则 None（回退冷启动）。"""
        if not code or not year:
            return None
        try:
            from src.eval.table_cache import put as cache_put
            from src.parsers.revenue_router import route_revenue
            cache_put(code, year, all_tables)        # 用引擎已抽好的表，route 不重扫
            rt = route_revenue(code, year)
            if rt.get("status") == "routed":
                prov = {}
                try:
                    from src.eval.provenance import attach_provenance
                    prov = attach_provenance(rt["result"], all_tables)   # 事后自动溯源
                except Exception:
                    prov = {}
                return {"revenue_breakdown": rt["result"], "溯源": prov,
                        "_parser": rt["parser_key"], "_routed": True}
        except Exception:
            return None                              # 路由出任何问题都安全回退
        return None

    def run(self, pdf_path: str, stock_code: str = None, report_year: int = None,
            company_name: str = None, db_write: bool = True, pre_scan: list = None) -> Dict:
        """执行一次完整的财报解析。pre_scan 可传入已抽好的表，避免重复扫描（注册表多候选共享）。"""
        start = time.time()

        # ── 全量表格一次扫描，共享给所有解析器（可由外部预先扫好传入）──
        if pre_scan is None:
            from src.parsers.infra.table_scanner import scan_pdf
            all_tables = scan_pdf(pdf_path)
        else:
            all_tables = pre_scan

        # ── 使用选择器动态选择解析器 ──  这里其实挺复杂。要找到一个最有可能的解析器
        rev_parser = self._get_parser("revenue_breakdown", pdf_path)
        rnd_parser = self._get_parser("rnd_info", pdf_path)
        emp_parser = self._get_parser("employees", pdf_path)
        cost_parser = self._get_parser("cost_breakdown", pdf_path)
        top_parser = self._get_parser("top_clients", pdf_path)

        # 营收：先选择即验证路由到认证专用解析器；没命中再用通用解析器冷启动
        rev_result = self._route_revenue(stock_code, report_year, all_tables)
        if rev_result is None:
            rev_result = rev_parser.parse(pdf_path, pre_scan=all_tables)
        rnd_result = rnd_parser.parse(pdf_path, pre_scan=all_tables)
        emp_result = emp_parser.parse(pdf_path, pre_scan=all_tables)
        cost_result = cost_parser.parse(pdf_path, pre_scan=all_tables)
        top_result = top_parser.parse(pdf_path, pre_scan=all_tables)

        duration = round(time.time() - start, 2)

        # ── 组装输出 ──
        output = {
            "stock_code": stock_code,
            "company_name": company_name,
            "report_year": report_year,
            "pdf_file": Path(pdf_path).name,
            "parse_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "parse_duration_sec": duration,
        }

        # 写入各字段（解析成功才写入）
        db_fields = {}
        statuses = []


        if rev_result.get("revenue_breakdown"):
            output["revenue_breakdown"] = rev_result["revenue_breakdown"]
            db_fields["revenue_breakdown"] = rev_result["revenue_breakdown"]
            output["revenue_source"] = ("routed:" + str(rev_result.get("_parser"))
                                        if rev_result.get("_routed") else "cold_start")
            statuses.append("rev_ok")
            # 透传溯源（M1）：供人工 review / 裁判对照原文
            if rev_result.get("溯源"):
                output.setdefault("溯源", {})["revenue_breakdown"] = rev_result["溯源"]
        else:
            statuses.append("rev_missing")

        if rnd_result.get("rnd_info"):
            output["rnd_info"] = rnd_result["rnd_info"]
            db_fields["rnd_info"] = rnd_result["rnd_info"]
            statuses.append("rnd_ok")
        else:
            statuses.append("rnd_missing")

        if emp_result.get("employees"):
            output["employees"] = emp_result["employees"]
            db_fields["employees"] = emp_result["employees"]
            statuses.append("emp_ok")
        else:
            statuses.append("emp_missing")

        if cost_result.get("cost_breakdown"):
            output["cost_breakdown"] = cost_result["cost_breakdown"]
            db_fields["cost_breakdown"] = cost_result["cost_breakdown"]
            statuses.append("cost_ok")
        else:
            statuses.append("cost_missing")

        if top_result.get("top_clients"):
            output["top_clients"] = top_result["top_clients"]
            db_fields["top_clients"] = top_result["top_clients"]
            statuses.append("client_ok")
        else:
            statuses.append("client_missing")

        if top_result.get("top_suppliers"):
            output["top_suppliers"] = top_result["top_suppliers"]
            db_fields["top_suppliers"] = top_result["top_suppliers"]
            statuses.append("supplier_ok")
        else:
            statuses.append("supplier_missing")

        output["parse_flags"] = {s.split("_")[0]: s.split("_")[1] for s in statuses}
        output["field_count"] = len([k for k in output if k in [
            "revenue_breakdown", "rnd_info", "employees",
            "cost_breakdown", "top_clients", "top_suppliers"
        ]])

        # ── 写入数据库 ──
        if db_write and stock_code:
            try:
                stock = find_stock(stock_code)
                if stock and db_fields:
                    # 查找对应的 financial_reports 记录
                    from src.database import get_conn
                    conn = get_conn()
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT id FROM financial_reports WHERE stock_code=%s AND report_year=%s AND report_quarter='annual' LIMIT 1",
                                (stock_code, report_year),
                            )
                            row = cur.fetchone()
                            if row:
                                report_id = row["id"]
                                db_fields["data_source"] = "hybrid"
                                db_fields["pdf_parsed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                                update_report_fields(report_id, db_fields)
                                output["report_id"] = report_id
                                output["db_write"] = "success"
                            else:
                                output["db_write"] = "report_not_found"
                    finally:
                        conn.close()
            except Exception as e:
                output["db_write"] = f"error: {e}"

        return output
