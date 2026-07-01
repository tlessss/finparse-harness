"""营收结构解析器 — 通用版（default）

适用于大多数 A 股上市公司的年报营收结构表。
使用 pdfplumber 提取表格，自动检测列分布。
"""

from typing import Dict, Optional

from src.parsers.base import BaseParser
from src.parsers.infra.unit_detector import detect_unit, convert_to_yuan
from src.parsers.infra.table_scanner import is_total_row
from src.parsers.infra.header_columns import detect_columns_by_header
from src.parsers.infra.rule_loader import load_rule
from src.parsers.infra.table_recall import select_table


class RevenueParser(BaseParser):
    name = "revenue_default"
    description = "通用营收结构解析器（适用于大多数A股年报）"

    def __init__(self, rule: Dict = None):
        super().__init__(rule or {})

    def _extra_exclude(self) -> set:
        """优化 Agent 可通过 rule['revenue_section']['extra_exclude_names'] 注入额外排除项。"""
        sec = (self.rule or {}).get("revenue_section", {}) or {}
        return set(sec.get("extra_exclude_names", []) or [])

    # 代码兜底的切桶标记（YAML 缺失/读不到时用）。正常以 revenue.yaml 的 dimensions 为准。
    _FALLBACK_DIMENSIONS = {
        "分行业": "industries", "按行业": "industries",
        "分产品": "segments", "按产品": "segments",
        "分地区": "regions", "按地区": "regions",
        "分销售模式": "by_channel", "分销售渠道": "by_channel",
        "销售模式": "by_channel", "按销售模式": "by_channel",
        "销售渠道": "by_channel", "按销售渠道": "by_channel",
    }

    def _header_aliases(self) -> Optional[dict]:
        """加载规范驱动的表头别名（revenue.yaml）；缺失则返回 None 走旧统计法。"""
        rule = load_rule("revenue")
        if not rule:
            return None
        return rule.get("revenue_breakdown", {}).get("header_aliases")

    def _section_labels(self) -> dict:
        """切桶标记：以 revenue.yaml 的 dimensions 为准，代码 _FALLBACK_DIMENSIONS 补缺/兜底。
        这是自愈"改规则"的落点——往 YAML 的 dimensions 加一条，这里立即生效，无需改代码。"""
        rule = load_rule("revenue")
        yaml_dims = (rule or {}).get("revenue_breakdown", {}).get("dimensions") or {}
        return {**self._FALLBACK_DIMENSIONS, **yaml_dims}    # YAML 覆盖/新增，代码兜底

    def _resolve_columns(self, table: list, amount_hint: Optional[int] = None):
        """
        营收表认列：**表头驱动为唯一主力**（读 revenue.yaml 的 header_aliases）。
        - name/amount/ratio 均以"表头别名命中的列"为准。
        - amount 表头没命中时，优先用选表解耦锚定的金额列 amount_hint（select_table 已用锚验过），
          再退极简兜底（第一个金额列）。name 兜底=第一个文字列。
        - ratio 闸门保持：表头命中"占营业收入比重"类别名才取，绝不拿毛利率顶替。
        """
        aliases = self._header_aliases()
        hdr = detect_columns_by_header(table, aliases) if aliases else {}
        name_col = hdr.get("name")
        amount_col = hdr.get("revenue")
        ratio_col = hdr.get("ratio")        # 闸门：表头命中占比别名才取
        if name_col is None:
            name_col = self._first_text_col(table)
        if amount_col is None:
            amount_col = amount_hint        # 锚定金额列兜底(比统计法可靠)
        if amount_col is None:
            amount_col = self._first_money_col(table, exclude={name_col, ratio_col})
        return name_col, amount_col, ratio_col

    @staticmethod
    def _first_text_col(table: list) -> int:
        """名称列兜底：第一个"多为文字"的列。找不到退 0。"""
        ncols = max((len(r) for r in table), default=0)
        for c in range(ncols):
            if sum(1 for row in table if c < len(row) and RevenueParser._is_text(row[c] or "")) >= 2:
                return c
        return 0

    @staticmethod
    def _first_money_col(table: list, exclude=None):
        """金额列兜底：第一个"多为金额"的列（跳过名称/占比列，避免撞列）。找不到退 None。"""
        skip = exclude if isinstance(exclude, (set, list, tuple)) else {exclude}
        ncols = max((len(r) for r in table), default=0)
        for c in range(ncols):
            if c in skip:
                continue
            if sum(1 for row in table if c < len(row) and RevenueParser._looks_like_money(row[c] or "")) >= 2:
                return c
        return None

    def parse(self, pdf_path: str = None, pre_scan: list = None,
              code: str = None, year: int = None) -> Dict:
        """营收解析 = 选表解耦 + 认列切桶。
        选表逻辑不再内嵌本解析器：全权委托 select_table（① 向量召回 → ② 锚精判定表定金额列 →
        ③ 维度数闸）。本解析器只负责"给定选中表 → 结构化"（认列、切桶、溯源）。
        code/year 用于取锚做精判；缺省时 select_table 优雅退回纯语义召回（recall_only）。"""
        if not pre_scan:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        sel = select_table(pre_scan, code, year, "revenue")
        if not sel or not sel.get("table"):
            return {"revenue_breakdown": None, "status": "no_table_found"}

        table = sel["table"]
        unit_ratio = detect_unit((sel.get("caption", "") or "") + " " + (sel.get("text", "") or ""))
        # 溯源：选中表自带的 (页码, cell_bbox)
        self._bbox_map = {id(table): (sel.get("page"), sel.get("cell_bbox"))}
        result, provenance = self._classify(
            table, unit_ratio=unit_ratio, amount_hint=sel.get("amount_col"))
        return {"revenue_breakdown": result, "溯源": provenance,
                "status": "ok", "_select_via": sel.get("via")}

    # ── 认列（内容统计法）：仅供调试台"内容法 vs 表头法"对照，不参与生产选列 ──

    def _detect_columns(self, table: list) -> tuple:
        num_cols = max(len(row) for row in table) if table else 0
        if num_cols == 0:
            return 0, None, None

        col_stats = {i: {"text": 0, "number": 0, "ratio": 0} for i in range(num_cols)}
        for row in table:
            for ci in range(len(row)):
                v = row[ci]
                if not v:
                    continue
                cv = v.replace("\n", " ").strip()
                if not cv:
                    continue
                if "%" in cv:
                    col_stats[ci]["ratio"] += 1
                elif self._looks_like_money(cv):
                    col_stats[ci]["number"] += 1
                elif self._is_text(cv):
                    col_stats[ci]["text"] += 1

        ratio_col = None
        for ci in range(num_cols):
            values = []
            for row in table:
                if ci < len(row):
                    v = row[ci]
                    if v and "%" in v:
                        try:
                            values.append(float(v.replace("%", "").replace(",", "").strip()))
                        except ValueError:
                            pass
            valid = [x for x in values if 0 <= x <= 100]
            if len(valid) >= 3:
                if ratio_col is None or len(valid) > len([v for row in table if ci < len(row) and row[ci] and "%" in row[ci]]):
                    ratio_col = ci

        amount_col = None
        for ci in range(num_cols):
            cnt = sum(1 for row in table if ci < len(row) and row[ci] and self._looks_like_money(row[ci]))
            if cnt >= 3:
                if amount_col is None or cnt > sum(1 for row in table if ci < len(row) and row[ci] and self._looks_like_money(row[ci])):
                    amount_col = ci

        if amount_col == ratio_col:
            amount_col = None
            for ci in range(num_cols):
                if ci != ratio_col:
                    cnt = sum(1 for row in table if ci < len(row) and row[ci] and self._looks_like_money(row[ci]))
                    if cnt >= 2:
                        amount_col = ci
                        break

        name_col = None
        for ci in range(num_cols):
            if ci == amount_col or ci == ratio_col:
                continue
            cnt = sum(1 for row in table if ci < len(row) and row[ci] and self._is_text(row[ci]))
            if cnt >= 3:
                name_col = ci
                break
        if name_col is None:
            candidates2 = sorted(col_stats.keys(), key=lambda i: col_stats[i]["text"], reverse=True)
            for c in candidates2:
                if c != amount_col and c != ratio_col:
                    name_col = c
                    break
            if name_col is None and candidates2:
                name_col = candidates2[0]

        return name_col, amount_col, ratio_col

    def _classify(self, table: list, unit_ratio: int = 1, amount_hint: Optional[int] = None) -> Dict:
        # 切桶标记从 revenue.yaml 的 dimensions 读(自愈"改规则"的旋钮);代码兜底防 YAML 缺失。
        section_labels = self._section_labels()
        sections = []
        for row in table:
            found = None
            for c in row:
                if c and c.strip() in section_labels:
                    found = section_labels[c.strip()]
                    break
            sections.append(found)

        name_col, amount_col, _ = self._resolve_columns(table, amount_hint=amount_hint)  # 占比列不再取(占比不解析)

        # 溯源：取该表的 (页码, 坐标网格)（_extract_tables 旁挂）
        page, cell_bbox = getattr(self, "_bbox_map", {}).get(id(table), (None, None))

        def _bbox_at(r, col):
            if cell_bbox is None or col is None or r >= len(cell_bbox):
                return None
            return cell_bbox[r][col] if col < len(cell_bbox[r]) else None

        result = {"segments": [], "industries": [], "regions": [], "by_channel": []}
        provenance = {}
        current = "segments"
        seen = {}

        for row_idx, row in enumerate(table):
            sec = sections[row_idx]
            if sec:
                current = sec
                if current not in seen:
                    seen[current] = set()
                continue
            if current not in seen:
                seen[current] = set()

            cells = [(c or "").replace("\n", "").strip() for c in row]

            non_empty = [c for c in cells if c]
            if len(non_empty) <= 2 and non_empty:
                merged = non_empty[0]
                parts = [p.strip() for p in merged.split(" ") if p.strip()]
                name = parts[0] if parts else ""
                amount_raw = next((p for p in parts[1:] if self._looks_like_money(p)), "")
            else:
                name = cells[name_col] if name_col is not None and name_col < len(cells) else ""
                amount_raw = cells[amount_col] if amount_col is not None and amount_col < len(cells) else ""
                if "%" in amount_raw:            # 金额列命中%值=取错到占比列 → 清空(占比不解析,由 金额/锚 算)
                    amount_raw = ""

            if not name or name in ("项 目", "项目", "") or is_total_row(name):
                continue
            if name.startswith("其中"):       # "其中：X" 是上一项的子拆分,不计入顶层(否则重复计数)
                continue
            # 可配置排除项（供优化 Agent 在沙箱中调参；默认空）
            if name.strip() in self._extra_exclude():
                continue

            amount = self._parse_number(amount_raw) if amount_raw else None
            if amount is None:                   # 只收有金额的项(占比不再解析,下游用 金额/锚 算)
                continue

            item = {
                "name": name.split("  ")[0].strip()[:30],
                "revenue_yuan": convert_to_yuan(amount, unit_ratio),
            }
            if name not in seen.get(current, set()):
                idx = len(result[current])
                result[current].append(item)
                seen[current].add(name)
                # 溯源：记录该项各数值来自哪一格
                if page is not None:
                    base = f"{current}[{idx}]"
                    nb = _bbox_at(row_idx, name_col)
                    if nb:
                        provenance[f"{base}.name"] = {"page": page, "bbox": nb}
                    if item["revenue_yuan"] is not None:
                        ab = _bbox_at(row_idx, amount_col)
                        if ab:
                            provenance[f"{base}.revenue_yuan"] = {"page": page, "bbox": ab}

        return result, provenance

    @staticmethod
    def _looks_like_money(s: str) -> bool:
        s = s.replace(",", "").replace("，", "").strip()
        if not s:
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False

    @staticmethod
    def _is_text(s: str) -> bool:
        if not s:
            return False
        return any('\u4e00' <= c <= '\u9fff' for c in s)

    @staticmethod
    def _parse_number(s: str):
        if not s:
            return None
        s = s.replace(",", "").replace("，", "").strip()
        try:
            return float(s)
        except ValueError:
            return None

    @staticmethod
    def _parse_ratio(s: str):
        if not s:
            return None
        s = s.replace("(", "-").replace("（", "-").replace(")", "").replace("）", "")
        s = s.replace("%", "").replace(",", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
