"""
RevenueParser — 基于表格内容特征识别

不再依赖关键词搜索页码和固定列索引，改为：
  1. 用 PyMuPDF 全文搜索宽泛关键词找到候选页码
  2. 对候选页用 pdfplumber 提取表格
  3. 通过表格内容特征识别营收结构表
  4. 自动检测列分布提取数据
"""

from typing import List, Dict, Optional
import pdfplumber
import fitz
from src.parsers.unit_detector import detect_unit, convert_to_yuan

# 宽泛的营收相关关键词
_REVENUE_KEYWORDS = [
    "营业收入", "营收", "主营业务", "收入构成",
    "分产品", "分行业", "分地区", "收入与成本",
    "收入和成本", "经营情况",
]


class RevenueParser:
    def __init__(self, rule: Dict):
        self.rule = rule

    def parse(self, pdf_path: str, pre_scan: list = None) -> Dict:
        candidate_pages = self._find_candidate_pages(pdf_path)
        if not candidate_pages:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        tables = self._extract_tables(pdf_path, candidate_pages)
        revenue_tables = self._filter_revenue_tables(tables)
        if not revenue_tables:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        # 检测单位
        unit_ratio = self._detect_unit_from_pdf(pdf_path, candidate_pages)

        best = revenue_tables[0]
        result = self._classify(best, unit_ratio=unit_ratio)
        return {"revenue_breakdown": result, "status": "ok"}

    def _detect_unit_from_pdf(self, pdf_path: str, pages: List[int]) -> int:
        """从 PDF 原文中检测金额单位"""
        try:
            doc = fitz.open(pdf_path)
            # 扫描候选页及其前后页
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

    def _find_candidate_pages(self, pdf_path: str) -> List[int]:
        """用 PyMuPDF 快速搜索关键词，找到候选页码"""
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
                        candidates.add(pn)      # 前页（跨页表）
                    if pn < len(doc) - 1:
                        candidates.add(pn + 2)  # 后页
                    break

        doc.close()
        candidates = {p for p in candidates if p >= 16}
        return sorted(candidates)[:15]

    def _extract_tables(self, pdf_path: str, pages: List[int]) -> list:
        """提取候选页的表格"""
        all_tables = []
        with pdfplumber.open(pdf_path) as pdf:
            for pn in pages:
                if pn - 1 >= len(pdf.pages):
                    continue
                for t in pdf.pages[pn - 1].extract_tables():
                    if len(t) >= 5:
                        all_tables.append(t)
        return all_tables

    def _filter_revenue_tables(self, tables: list) -> list:
        """通过内容特征筛选营收结构表"""
        scored = []
        for t in tables:
            score = 0
            text = " ".join(c.replace("\n", " ") for row in t for c in row if c)
            name_col, amount_col, ratio_col = self._detect_columns(t)

            # 特征1: 有占比列（核心特征）
            if ratio_col is not None:
                score += 35

            # 特征2: 有金额列
            if amount_col is not None:
                score += 25

            # 特征3: 包含"营业收入"或"营收"
            if "营业收入" in text:
                score += 20
            elif "营收" in text:
                score += 10

            # 特征4: 有同比/增减
            if "同比" in text or "增减" in text:
                score += 10

            # 特征5: 有节标题（分产品/行业/地区）— 高权重
            if "分产品" in text or "分行业" in text or "分地区" in text:
                score += 30

            # 特征6: 行数适中
            rc = len(t)
            if 8 <= rc <= 30:
                score += 10
            elif rc > 30:
                score -= 10

            # 特征7: 多个中文名称
            names = sum(1 for row in t for c in row if c and any(
                '\u4e00' <= ch <= '\u9fff' for ch in c) and len(c) >= 2)
            if names >= 5:
                score += 10

            # 排除其它表格类型
            if "员工" in text and "人数" in text:
                score -= 30
            if "供应商" in text:
                score -= 20
            if "研发" in text:
                score -= 20

            # 占比最大值校验：排除 KPI 汇总表
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
                    continue  # 占比超过 100% 直接排除

            if score >= 40:
                scored.append((score, t))

        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored]

    def _detect_columns(self, table: list) -> tuple:
        """自动检测名称列、金额列、占比列"""
        num_cols = max(len(row) for row in table) if table else 0
        if num_cols == 0:
            return 0, None, None

        # 统计各列内容类型
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

        # 占比列：找数值在 0-100 之间的列
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

        # 金额列
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

        # 名称列
        name_col = None
        for ci in range(num_cols):
            if ci == amount_col or ci == ratio_col:
                continue
            cnt = sum(1 for row in table if ci < len(row) and row[ci] and self._is_text(row[ci]))
            if cnt >= 3:
                name_col = ci
                break
        if name_col is None:
            candidates = sorted(col_stats.keys(), key=lambda i: col_stats[i]["text"], reverse=True)
            for c in candidates:
                if c != amount_col and c != ratio_col:
                    name_col = c
                    break
            if name_col is None and candidates:
                name_col = candidates[0]

        return name_col, amount_col, ratio_col

    def _classify(self, table: list, unit_ratio: int = 1) -> Dict:
        """按内容分类"""
        section_labels = {"分行业": "industries", "分产品": "segments", "分地区": "regions"}
        sections = []
        for row in table:
            found = None
            for c in row:
                if c and c.strip() in section_labels:
                    found = section_labels[c.strip()]
                    break
            sections.append(found)

        name_col, amount_col, ratio_col = self._detect_columns(table)

        result = {"segments": [], "industries": [], "regions": []}
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

            name = cells[name_col] if name_col is not None and name_col < len(cells) else ""
            if not name or len(name) <= 1:
                for c in cells:
                    if c and self._is_text(c) and len(c) >= 2:
                        name = c[:30]
                        break
                if not name or len(name) <= 1:
                    continue

            if name in ("项 目", "项目", "营业收入合计", "营业收入总额", "合计"):
                continue
            if any(kw in name for kw in ["年", "金额", "占比", "增减"]):
                if any(c.isdigit() for c in name):
                    continue

            amount_raw = cells[amount_col] if amount_col is not None and amount_col < len(cells) else ""
            ratio_raw = cells[ratio_col] if ratio_col is not None and ratio_col < len(cells) else ""

            amount = self._parse_number(amount_raw) if amount_raw else None
            ratio = self._parse_ratio(ratio_raw) if ratio_raw else None

            if "%" in amount_raw and ratio is None:
                ratio = self._parse_ratio(amount_raw)
                amount = None

            if ratio is None and amount is None:
                continue

            # 占比合理性校验：0-100 且非负
            if ratio is not None and (ratio < 0 or ratio > 100):
                ratio = None

            item = {
                "name": name.split("  ")[0].strip()[:30],
                "revenue_yuan": convert_to_yuan(amount, unit_ratio) if amount else None,
                "ratio_pct": ratio,
            }
            if name not in seen.get(current, set()):
                result[current].append(item)
                seen[current].add(name)

        return result

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
