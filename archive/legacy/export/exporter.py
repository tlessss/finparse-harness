"""
数据归档与导出模块 — PRD §4.7

职责：
  1. 解析结果导出为 JSON / CSV / Excel
  2. 条件检索与历史查询 API
  3. 解析器版本记录留存

端点注册在 api.py 中。
"""

import json
import csv
import io
from typing import Dict, List, Optional
from datetime import datetime

from src.database import get_conn


def export_json(stock_code: str = None, year: int = None, report_id: int = None,
                limit: int = 10) -> List[Dict]:
    """导出解析结果为 JSON 格式"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            where = ["1=1"]
            params = []
            if stock_code:
                where.append("fr.stock_code = %s")
                params.append(stock_code)
            if year:
                where.append("fr.report_year = %s")
                params.append(year)
            if report_id:
                where.append("fr.id = %s")
                params.append(report_id)

            cur.execute(
                f"""SELECT fr.id, fr.stock_code, fr.company_name, fr.report_year,
                           fr.report_quarter, fr.data_source, fr.pdf_parsed_at,
                           fr.revenue_breakdown, fr.cost_breakdown,
                           fr.employees, fr.rnd_info,
                           fr.top_clients, fr.top_suppliers,
                           fr.quality_score, fr.quality_flags
                    FROM financial_reports fr
                    WHERE {' AND '.join(where)}
                    ORDER BY fr.report_year DESC, fr.stock_code
                    LIMIT %s""",
                params + [limit],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    result = []
    for row in rows:
        item = {
            "id": row["id"],
            "stock_code": row["stock_code"],
            "company_name": row["company_name"],
            "report_year": row["report_year"],
            "data_source": row.get("data_source"),
            "pdf_parsed_at": str(row.get("pdf_parsed_at") or ""),
        }
        # 展开 JSON 字段
        for f in ["revenue_breakdown", "cost_breakdown", "employees",
                   "rnd_info", "top_clients", "top_suppliers"]:
            val = row.get(f)
            if val:
                if isinstance(val, str):
                    try:
                        val = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        pass
                item[f] = val
            else:
                item[f] = None

        # 质量评分
        if row.get("quality_score") is not None:
            item["quality_score"] = float(row["quality_score"])
        if row.get("quality_flags"):
            val = row["quality_flags"]
            if isinstance(val, str):
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
            item["quality_flags"] = val

        result.append(item)

    return result


def export_csv(stock_code: str = None, year: int = None, limit: int = 100) -> str:
    """导出解析结果为 CSV 格式"""
    data = export_json(stock_code=stock_code, year=year, limit=limit)

    output = io.StringIO()
    writer = csv.writer(output)

    # 表头
    headers = ["id", "stock_code", "company_name", "report_year",
               "data_source", "has_revenue_breakdown", "has_cost_breakdown",
               "has_employees", "has_rnd_info", "has_top_clients",
               "has_top_suppliers", "quality_score", "pdf_parsed_at"]
    writer.writerow(headers)

    for item in data:
        writer.writerow([
            item["id"],
            item["stock_code"],
            item["company_name"],
            item["report_year"],
            item.get("data_source", ""),
            "yes" if item.get("revenue_breakdown") else "no",
            "yes" if item.get("cost_breakdown") else "no",
            "yes" if item.get("employees") else "no",
            "yes" if item.get("rnd_info") else "no",
            "yes" if item.get("top_clients") else "no",
            "yes" if item.get("top_suppliers") else "no",
            item.get("quality_score", ""),
            item.get("pdf_parsed_at", ""),
        ])

    return output.getvalue()


def get_parser_version_history(limit: int = 20) -> List[Dict]:
    """获取解析器版本变更记录（来自 rule_loader 的 history 或文件修改记录）"""
    import os
    from pathlib import Path

    rules_dir = Path(__file__).parent.parent / "src" / "parser_rules"
    versions = []
    if rules_dir.exists():
        for f in sorted(rules_dir.glob("*.yaml"), key=os.path.getmtime, reverse=True)[:limit]:
            versions.append({
                "filename": f.name,
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
            })
    return versions
