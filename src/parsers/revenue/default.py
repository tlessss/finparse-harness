"""营收结构解析器 — 通用版（default）

适用于大多数 A 股上市公司的年报营收结构表。
使用 pdfplumber 提取表格，自动检测列分布。
"""

from typing import List, Dict, Optional
import pdfplumber
import fitz

from src.parsers.base import BaseParser
from src.parsers.infra.unit_detector import detect_unit, convert_to_yuan
from src.parsers.infra.table_scanner import is_total_row
from src.parsers.infra.header_columns import detect_columns_by_header
from src.parsers.infra.rule_loader import load_rule


class RevenueParser(BaseParser):
    name = "revenue_default"
    description = "通用营收结构解析器（适用于大多数A股年报）"

    def __init__(self, rule: Dict = None):
        super().__init__(rule or {})

    def _extra_exclude(self) -> set:
        """优化 Agent 可通过 rule['revenue_section']['extra_exclude_names'] 注入额外排除项。"""
        sec = (self.rule or {}).get("revenue_section", {}) or {}
        return set(sec.get("extra_exclude_names", []) or [])

    def _header_aliases(self) -> Optional[dict]:
        """加载规范驱动的表头别名（revenue.yaml）；缺失则返回 None 走旧统计法。"""
        rule = load_rule("revenue")
        if not rule:
            return None
        return rule.get("revenue_breakdown", {}).get("header_aliases")

    def _resolve_columns(self, table: list):
        """
        M1：表头驱动认列（含占比闸门）。
        - name/amount：表头优先，缺失回退统计法。
        - ratio：只有表头命中"占营业收入比重"类别名才取；否则置空，绝不拿毛利率顶替。
        """
        stat_name, stat_amount, stat_ratio = self._detect_columns(table)
        aliases = self._header_aliases()
        if not aliases:
            return stat_name, stat_amount, stat_ratio   # 无规则 → 旧逻辑
        hdr = detect_columns_by_header(table, aliases)
        name_col = hdr.get("name") if hdr.get("name") is not None else stat_name
        amount_col = hdr.get("revenue") if hdr.get("revenue") is not None else stat_amount
        ratio_col = hdr.get("ratio")    # 闸门：表头命中占比别名才取；否则置空
        return name_col, amount_col, ratio_col

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        candidate_pages = self._find_candidate_pages(pdf_path)
        if not candidate_pages:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        tables = self._extract_tables(pdf_path, candidate_pages)
        revenue_tables = self._filter_revenue_tables(tables)
        if not revenue_tables:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        unit_ratio = self._detect_unit_from_pdf(pdf_path, candidate_pages)
        best = self._select_best_table(revenue_tables)
        result, provenance = self._classify(best, unit_ratio=unit_ratio)
        return {"revenue_breakdown": result, "溯源": provenance, "status": "ok"}

    def _select_best_table(self, tables: list) -> list:
        """
        M2 A/B 表择优：在已按分数排序的候选表里，优先选 (A) 占比构成表，
        其次才退回最高分表（可能是 (B) 毛利率表）。
        —— 解决"挑错表"（如徐工挑到毛利率表）。
        """
        rule = load_rule("revenue")
        if not rule:
            return tables[0]
        rb = rule.get("revenue_breakdown", {})
        from src.parsers.infra.header_columns import classify_revenue_table
        comps = [t for t in tables if classify_revenue_table(t, rb) == "composition"]
        return comps[0] if comps else tables[0]

    # ── 以下方法完全来自原 revenue_parser.py ──

    def _find_candidate_pages(self, pdf_path: str) -> List[int]:
        """
        找页（规范驱动）：按信号强度打分取 top-N 页，不再按页码盲截断。
        有规则走 SectionLocator；无规则回退旧关键词逻辑。
        """
        rule = load_rule("revenue")
        fp = (rule or {}).get("revenue_breakdown", {}).get("find_page") if rule else None
        if fp:
            from src.parsers.infra.section_locator import rank_pages
            pages = rank_pages(
                pdf_path,
                strong=fp.get("strong", []),
                weak=fp.get("weak", []),
                prefer_section=fp.get("prefer_section"),
                min_page=fp.get("min_page", 1),
                top_n=fp.get("top_n", 12),
                window=fp.get("window", 1),
            )
            if pages:
                return pages
        return self._find_candidate_pages_legacy(pdf_path)

    def _find_candidate_pages_legacy(self, pdf_path: str) -> List[int]:
        _REVENUE_KEYWORDS = [
            "营业收入", "营收", "主营业务", "收入构成",
            "分产品", "分行业", "分地区", "收入与成本",
            "收入和成本", "经营情况",
        ]
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            return []

        candidates = set()
        for pn in range(len(doc)):
            text = doc[pn].get_text("text")
            for kw in _REVENUE_KEYWORDS:
                if kw in text:
                    candidates.add(pn + 1)
                    if pn > 0:
                        candidates.add(pn)
                    if pn < len(doc) - 1:
                        candidates.add(pn + 2)
                    break

        doc.close()
        candidates = {p for p in candidates if p >= 16}
        return sorted(candidates)[:15]

    def _extract_tables(self, pdf_path: str, pages: List[int]) -> list:
        """抽候选表。改用 find_tables 以保留每格坐标，旁挂到 _bbox_map 供溯源（M1）。
        返回值仍是 2D 字符串网格列表（不改下游筛选/认列逻辑）。"""
        self._bbox_map = {}   # id(grid) -> (page, cell_bbox)，靠对象身份关联，下游只重排不拷贝
        all_tables = []
        with pdfplumber.open(pdf_path) as pdf:
            for pn in pages:
                if pn - 1 >= len(pdf.pages):
                    continue
                for tbl in pdf.pages[pn - 1].find_tables():
                    try:
                        t = tbl.extract()
                    except Exception:
                        continue
                    if not t:
                        continue
                    text = " ".join(c.replace("\n", " ") for row in t for c in row if c)
                    if ("分产品" in text or "分行业" in text or "分地区" in text) and len(t) >= 5:
                        cell_bbox = []
                        for ri, row in enumerate(tbl.rows):
                            cells = getattr(row, "cells", None) or []
                            brow = [tuple(c) if c else None for c in cells]
                            # 对齐到与 t[ri] 同长
                            need = len(t[ri]) if ri < len(t) else len(brow)
                            cell_bbox.append([brow[ci] if ci < len(brow) else None for ci in range(need)])
                        self._bbox_map[id(t)] = (pn, cell_bbox)
                        all_tables.append(t)
        return all_tables

    def _filter_revenue_tables(self, tables: list) -> list:
        scored = []
        for t in tables:
            score = 0
            text = " ".join(c.replace("\n", " ") for row in t for c in row if c)
            nc, ac, rc = self._detect_columns(t)

            if rc is not None:
                score += 35
            if ac is not None:
                score += 25
            if "营业收入" in text:
                score += 20
            elif "营收" in text:
                score += 10
            if "同比" in text or "增减" in text:
                score += 10
            if "分产品" in text or "分行业" in text or "分地区" in text:
                score += 30
            rc2 = len(t)
            if 8 <= rc2 <= 30:
                score += 10
            elif rc2 > 30:
                score -= 10
            names = sum(1 for row in t for c in row if c and any(
                '\u4e00' <= ch <= '\u9fff' for ch in c) and len(c) >= 2)
            if names >= 5:
                score += 10
            if "员工" in text and "人数" in text:
                score -= 30
            if "供应商" in text:
                score -= 20
            if "研发" in text:
                score -= 20

            nc_check, ac_check, rc_check = self._detect_columns(t)
            if rc_check is not None:
                max_r = 0
                for row in t:
                    if rc_check < len(row):
                        v = row[rc_check]
                        if v and "%" in v:
                            try:
                                max_r = max(max_r, abs(float(v.replace("%","").replace(",","").strip())))
                            except ValueError:
                                pass
                if max_r > 100:
                    continue

            if score >= 40:
                scored.append((score, t))

        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored]

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

    def _classify(self, table: list, unit_ratio: int = 1) -> Dict:
        section_labels = {"分行业": "industries", "分产品": "segments", "分地区": "regions",
                          "分销售模式": "by_channel", "分销售渠道": "by_channel"}
        sections = []
        for row in table:
            found = None
            for c in row:
                if c and c.strip() in section_labels:
                    found = section_labels[c.strip()]
                    break
            sections.append(found)

        name_col, amount_col, ratio_col = self._resolve_columns(table)

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

            cells = [c.strip() if c else "" for c in row]

            non_empty = [c for c in cells if c]
            if len(non_empty) <= 2 and non_empty:
                merged = non_empty[0]
                parts = [p.strip() for p in merged.split(" ") if p.strip()]
                name = parts[0] if parts else ""
                amount_raw = ""
                ratio_raw = ""
                for p in parts[1:]:
                    if "%" in p:
                        if not ratio_raw:
                            ratio_raw = p
                    elif self._looks_like_money(p):
                        if not amount_raw:
                            amount_raw = p
            else:
                name = cells[name_col] if name_col is not None and name_col < len(cells) else ""
                amount_raw = cells[amount_col] if amount_col is not None and amount_col < len(cells) else ""
                if "%" in amount_raw:
                    ratio_raw = amount_raw
                    amount_raw = ""
                else:
                    ratio_raw = cells[ratio_col] if ratio_col is not None and ratio_col < len(cells) else ""

            if not name or name in ("项 目", "项目", "") or is_total_row(name):
                continue
            # 可配置排除项（供优化 Agent 在沙箱中调参；默认空）
            if name.strip() in self._extra_exclude():
                continue

            amount = self._parse_number(amount_raw) if amount_raw else None
            ratio = self._parse_ratio(ratio_raw) if ratio_raw else None

            if ratio is not None and (ratio < 0 or ratio > 100):
                ratio = None

            if ratio is None and amount is None:
                continue

            item = {
                "name": name.split("  ")[0].strip()[:30],
                "revenue_yuan": convert_to_yuan(amount, unit_ratio) if amount else None,
                "ratio_pct": ratio,
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
                    if ratio is not None:
                        rb = _bbox_at(row_idx, ratio_col)
                        if rb:
                            provenance[f"{base}.ratio_pct"] = {"page": page, "bbox": rb}

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

    @staticmethod
    def _detect_unit_from_pdf(pdf_path: str, pages: List[int]) -> int:
        try:
            doc = fitz.open(pdf_path)
            scan_pages = set(pages)
            for p in pages:
                scan_pages.add(p - 1)
                scan_pages.add(p + 1)
            for pn in sorted(scan_pages):
                if 1 <= pn <= len(doc):
                    text = doc[pn - 1].get_text("text")
                    ratio = detect_unit(text)
                    if ratio != 1:
                        doc.close()
                        return ratio
            doc.close()
        except Exception:
            pass
        return 1
