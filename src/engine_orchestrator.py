"""
FinParseAI 编排器 — 一份 PDF 财报解析的"总入口/总指挥"
============================================================

这是整个解析流程的**主干**。外面(批处理脚本、API)拿到一份 PDF，就调 `FinParseAI().run(...)`，
由它把"抽表 → 选解析器 → 解析 6 个字段 → 组装 JSON → (可选)写库"串起来。

6 个目标字段：营收结构 / 研发 / 员工 / 成本 / 前五大客户 / 前五大供应商。

每个字段有两条路(谁先成功用谁)：
  1) **路由(选择即验证)** `_route_field`：去"已认证的专用解析器"里跑一遍，硬规则达标就用它(快又准)；
  2) **冷启动** 通用解析器：路由没命中时的兜底，靠启发式现解。

用法：
  from src.engine_orchestrator import FinParseAI
  result = FinParseAI().run("pdfs/多氟多-2025.pdf", stock_code="002407", report_year=2025)
"""

import time
import json
from typing import Dict, Optional
from pathlib import Path

import yaml

from src.parsers.selector import select_parser          # 通用解析器的"选型"(冷启动用)
# 每个字段的"规格"对象(字段名/占比键/校验类型等)，路由时要用
from src.eval.field_spec import REVENUE, COST, RND, EMPLOYEE, TOP_CLIENTS, TOP_SUPPLIERS
from src.database import find_stock, update_report_fields


class FinParseAI:
    """统一解析编排入口。一个实例可复用，反复 run() 不同 PDF。"""

    def __init__(self, rule_path: Optional[str] = None):
        # 加载 YAML 规则(关键词/页码/列映射等)。不传路径就用默认的 industry_default.yaml。
        if rule_path is None:
            rule_path = Path(__file__).parent.parent / "src" / "parser_rules" / "industry_default.yaml"
        with open(rule_path, "r", encoding="utf-8") as f:
            self.rule = yaml.safe_load(f)

    def _get_parser(self, field: str, pdf_path: str):
        """给某字段挑一个**通用解析器类**并实例化(冷启动用)。
        select_parser 会扫该字段目录下所有解析器、按 can_handle() 评分选最高的。"""
        cls = select_parser(field, pdf_path)
        return cls(self.rule)          # 把 YAML 规则注入解析器实例

    def _route_field(self, spec, code, year, all_tables):
        """
        选择即验证路由(一个字段)：去"已认证专用解析器"里跑，硬规则达标就用它。

        ── 入参格式 ──
        spec : FieldSpec 对象(来自 src/eval/field_spec.py，如 REVENUE/RND/...)。关键属性：
               .field(顶层字段名如"revenue_breakdown") .cls(判据类 A/B/C) .ratio_key .amount_key
               .total_key .detail_key .dims(维度元组)
        code : str  股票代码，如 "002407"
        year : int  年份，如 2025
        all_tables : list[dict]  引擎已抽好的表(scan_pdf 的输出)，每个元素：
               {
                 "page": int,                          # 页码(从1)
                 "table": [[str|None, ...], ...],       # 二维单元格(字符串网格)
                 "text": str,                           # 整表拼成的文本(便于关键词匹配)
                 "section": str,                        # 章节标签 "fuzhu"/"management"/"other"
                 "cell_bbox": [[(x0,y0,x1,y1)|None, ...], ...],  # 与 table 同形状的坐标
                 "table_bbox": (x0,y0,x1,y1) | None,
               }

        ── 返回 ──
        命中 → {字段名: 值, "溯源": {...}, "_parser": 哪个解析器, "_routed": True}
        没命中/出任何错 → None(让上层回退冷启动)。
        """
        if not code or not year:
            return None                              # 没有代码/年份无法查注册表 → 直接回退
        try:
            from src.eval.table_cache import put as cache_put
            from src.parsers.revenue_router import route_field
            from src.eval.provenance import attach_provenance
            # ① 把已抽好的表放进共享缓存(键=code+year)，这样 route_field 直接读、不重扫 PDF
            cache_put(code, year, all_tables)
            # ② 真正的选择即验证：跑候选专用解析器 → 硬规则选优(细节见 revenue_router.route_field)
            rt = route_field(spec, code, year)
            if rt.get("status") == "routed":         # 有专用解析器解干净了
                result = rt["result"]
                # 有的结果是 {字段名: 值, ...} 包了一层，这里解包出"字段值"本身
                field_value = (result[spec.field]
                               if isinstance(result, dict) and spec.field in result else result)
                # ③ 事后补溯源：把每个数值反查回 PDF 的 (页码, 单元格bbox)，供人审/裁判对照原文
                try:
                    prov = attach_provenance(field_value, all_tables, spec)
                except Exception:
                    prov = {}                        # 补溯源失败不影响主结果
                return {spec.field: field_value, "溯源": prov,
                        "_parser": rt["parser_key"], "_routed": True}
        except Exception:
            return None                              # 路由是优化项，出任何问题都安全回退冷启动
        return None                                  # status != routed(没命中) → 回退

    def run(self, pdf_path: str, stock_code: str = None, report_year: int = None,
            company_name: str = None, db_write: bool = True, pre_scan: list = None,
            on_stage=None) -> Dict:
        """
        执行一次完整的财报解析(主流程)。

        pre_scan : 可传入已抽好的表，避免重复扫描(批处理/多候选共享同一次抽表)。
        db_write : 是否把结果写进 financial_reports 表。
        """
        start = time.time()

        # ── 第 1 步：抽表 ── 把整份 PDF 的所有表格抽出来(贵，只做一次，6 个解析器共用)
        if pre_scan is None:
            from src.parsers.infra.table_scanner import scan_pdf
            all_tables = scan_pdf(pdf_path)
        else:
            all_tables = pre_scan                    # 外面已经抽好了，直接用

        # ── 第 2 步：为每个字段准备"冷启动通用解析器"(路由没命中时才真正用到) ──
        rev_parser = self._get_parser("revenue_breakdown", pdf_path)
        rnd_parser = self._get_parser("rnd_info", pdf_path)
        emp_parser = self._get_parser("employees", pdf_path)
        cost_parser = self._get_parser("cost_breakdown", pdf_path)
        top_parser = self._get_parser("top_clients", pdf_path)   # 客户+供应商共用一个解析器

        # ── 第 3 步：逐字段解析 ── 每个字段都"先路由、没命中再冷启动"
        _stage = on_stage or (lambda *_: None)        # 阶段回调：上报"正在解析哪个字段"
        # 营收
        _stage("营收")
        rev_result = self._route_field(REVENUE, stock_code, report_year, all_tables)
        if rev_result is None:
            # 冷启动营收:把 code/year 透传给解析器,让选表解耦(select_table)能取锚做精判
            rev_result = rev_parser.parse(pdf_path, pre_scan=all_tables,
                                          code=stock_code, year=report_year)
        # 研发
        _stage("研发")
        rnd_result = self._route_field(RND, stock_code, report_year, all_tables)
        if rnd_result is None:
            rnd_result = rnd_parser.parse(pdf_path, pre_scan=all_tables)
        # 员工
        _stage("员工")
        emp_result = self._route_field(EMPLOYEE, stock_code, report_year, all_tables)
        if emp_result is None:
            emp_result = emp_parser.parse(pdf_path, pre_scan=all_tables)
        # 成本
        _stage("成本")
        cost_result = self._route_field(COST, stock_code, report_year, all_tables)
        if cost_result is None:
            cost_result = cost_parser.parse(pdf_path, pre_scan=all_tables)
        # 客户/供应商：top_parser 一次解出双字段做"基底"；各自路由若命中则覆盖对应字段
        _stage("客户/供应商")
        top_result = top_parser.parse(pdf_path, pre_scan=all_tables)
        tc_routed = self._route_field(TOP_CLIENTS, stock_code, report_year, all_tables)
        ts_routed = self._route_field(TOP_SUPPLIERS, stock_code, report_year, all_tables)
        client_result = tc_routed or top_result      # 路由命中用路由的，否则用基底
        supplier_result = ts_routed or top_result

        duration = round(time.time() - start, 2)

        # ── 第 4 步：组装统一输出 JSON ── 先放元信息
        output = {
            "stock_code": stock_code,
            "company_name": company_name,
            "report_year": report_year,
            "pdf_file": Path(pdf_path).name,
            "parse_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "parse_duration_sec": duration,
        }

        # 逐字段写入：解析出来了才写(没解出来记一个 *_missing 标记)
        db_fields = {}        # 要写库的字段
        statuses = []         # 每个字段的状态(rev_ok / rev_missing ...)，最后汇成 parse_flags

        # 营收：写值 + 标来源(routed 还是 cold_start) + 透传溯源
        if rev_result.get("revenue_breakdown"):
            output["revenue_breakdown"] = rev_result["revenue_breakdown"]
            db_fields["revenue_breakdown"] = rev_result["revenue_breakdown"]
            output["revenue_source"] = ("routed:" + str(rev_result.get("_parser"))
                                        if rev_result.get("_routed") else "cold_start")
            statuses.append("rev_ok")
            if rev_result.get("溯源"):
                output.setdefault("溯源", {})["revenue_breakdown"] = rev_result["溯源"]
        else:
            statuses.append("rev_missing")

        # 研发
        if rnd_result.get("rnd_info"):
            output["rnd_info"] = rnd_result["rnd_info"]
            db_fields["rnd_info"] = rnd_result["rnd_info"]
            if rnd_result.get("溯源"):
                output.setdefault("溯源", {})["rnd_info"] = rnd_result["溯源"]
            statuses.append("rnd_ok")
        else:
            statuses.append("rnd_missing")

        # 员工
        if emp_result.get("employees"):
            output["employees"] = emp_result["employees"]
            db_fields["employees"] = emp_result["employees"]
            if emp_result.get("溯源"):
                output.setdefault("溯源", {})["employees"] = emp_result["溯源"]
            statuses.append("emp_ok")
        else:
            statuses.append("emp_missing")

        # 成本
        if cost_result.get("cost_breakdown"):
            output["cost_breakdown"] = cost_result["cost_breakdown"]
            db_fields["cost_breakdown"] = cost_result["cost_breakdown"]
            if cost_result.get("溯源"):
                output.setdefault("溯源", {})["cost_breakdown"] = cost_result["溯源"]
            statuses.append("cost_ok")
        else:
            statuses.append("cost_missing")

        # 前五大客户
        if client_result.get("top_clients"):
            output["top_clients"] = client_result["top_clients"]
            db_fields["top_clients"] = client_result["top_clients"]
            if client_result.get("溯源"):
                output.setdefault("溯源", {})["top_clients"] = client_result["溯源"]
            statuses.append("client_ok")
        else:
            statuses.append("client_missing")

        # 前五大供应商
        if supplier_result.get("top_suppliers"):
            output["top_suppliers"] = supplier_result["top_suppliers"]
            db_fields["top_suppliers"] = supplier_result["top_suppliers"]
            if supplier_result.get("溯源"):
                output.setdefault("溯源", {})["top_suppliers"] = supplier_result["溯源"]
            statuses.append("supplier_ok")
        else:
            statuses.append("supplier_missing")

        # ── 质检关：入库裁决权移交 LLM，锚不再当"终审开关" ──
        #   认证路由=认证背书→当场写库；冷启动=无背书→**一律不当场写**：
        #     锚过(high)也不算过,要过异步"复核 agent"审锚的盲区(见 triage._verify_green);
        #     无锚(unknown)要人核验;锚判错(low)本就不写。数据仍留 output 供分诊层复核后再入库。
        #   —— 堵住旧的"某维度和≈锚就静默入库"漏洞(任一维度对上,其余维度错了也曾一起写库)。
        from src.parsers.revenue_router import field_plausibility
        from src.eval.anchors import get_anchors
        _anchors = get_anchors(stock_code, report_year) if stock_code else {}
        _src = {"revenue_breakdown": rev_result, "rnd_info": rnd_result, "employees": emp_result,
                "cost_breakdown": cost_result, "top_clients": tc_routed, "top_suppliers": ts_routed}
        _specs = {"revenue_breakdown": REVENUE, "cost_breakdown": COST, "rnd_info": RND,
                  "employees": EMPLOYEE, "top_clients": TOP_CLIENTS, "top_suppliers": TOP_SUPPLIERS}
        output["signals"] = {}
        for _f, _spec in _specs.items():
            _val = output.get(_f)
            if not _val:
                continue
            _routed = bool((_src.get(_f) or {}).get("_routed"))
            try:
                _sig = field_plausibility(_spec, _val, _anchors)
            except Exception:
                _sig = {}
            _conf = _sig.get("confidence")
            _trust = _routed                              # 只有认证路由当场写库；冷启动全交复核层
            # 冷启动待办:锚过→待复核 agent；其余→待人核验。给分诊层/控制台一个明确的交接标记。
            _pending = None if _routed else ("verify" if _conf == "high" else "review")
            output["signals"][_f] = {
                "source": "routed" if _routed else "cold_start",
                "confidence": _conf, "clean": _sig.get("clean"),
                "anchored": _sig.get("anchored"), "anchor": _sig.get("anchor"),
                "written": bool(_trust), "pending": _pending,
            }
            if _f in db_fields and not _trust:
                del db_fields[_f]        # 冷启动一律不当场写(宁缺毋滥,正确率优先;等复核层裁决)

        # parse_flags 形如 {rev: ok, rnd: missing, ...}；field_count = 成功几个字段
        output["parse_flags"] = {s.split("_")[0]: s.split("_")[1] for s in statuses}
        output["field_count"] = len([k for k in output if k in [
            "revenue_breakdown", "rnd_info", "employees",
            "cost_breakdown", "top_clients", "top_suppliers"
        ]])

        # ── 第 5 步：写数据库(可选) ── 找到对应的 financial_reports 记录，更新这几个字段
        if db_write and stock_code:
            try:
                stock = find_stock(stock_code)
                if stock and db_fields:
                    from src.database import get_conn, reports_table
                    conn = get_conn()
                    try:
                        with conn.cursor() as cur:
                            # 按 股票代码+年份+年报 定位记录行（走 reports_table 开关，与写入同表）
                            cur.execute(
                                f"SELECT id FROM `{reports_table()}` WHERE stock_code=%s AND report_year=%s AND report_quarter='annual' LIMIT 1",
                                (stock_code, report_year),
                            )
                            row = cur.fetchone()
                            if row:
                                report_id = row["id"]
                                db_fields["data_source"] = "hybrid"     # 标记数据来自 PDF 解析
                                db_fields["pdf_parsed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                                update_report_fields(report_id, db_fields)
                                output["report_id"] = report_id
                                output["db_write"] = "success"
                            else:
                                output["db_write"] = "report_not_found"
                    finally:
                        conn.close()
            except Exception as e:
                output["db_write"] = f"error: {e}"        # 写库失败不影响解析结果返回

        return output
