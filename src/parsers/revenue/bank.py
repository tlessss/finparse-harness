"""营收结构解析器 — 银行版（bank）

适用于平安银行等商业银行的营收表。
银行营收表不以"分产品/分行业/分地区"组织，
而是以"利息净收入"、"非利息净收入"等科目列示。
"""

from typing import List, Dict
import pdfplumber
import fitz

from src.parsers.base import BaseParser
from src.parsers.infra.unit_detector import convert_to_yuan
from src.parsers.infra.table_scanner import is_total_row


class BankRevenueParser(BaseParser):
    name = "revenue_bank"
    description = "银行营收结构解析器（适用于商业银行年报）"

    def __init__(self, rule: Dict = None):
        super().__init__(rule or {})

    @classmethod
    def can_handle(cls, pdf_path: str, hint: str = "") -> float:
        """
        检测 PDF 是否为银行年报。
        只用银行特有的"非利息净收入"作判据（普通公司为 0），
        不用"吸收存款/发放贷款"——这些科目在任何公司的合并现金流量表都会出现，
        会把普通公司误判成银行。
        """
        import fitz
        try:
            doc = fitz.open(pdf_path)
            hits = 0
            for pn in range(min(80, len(doc))):
                text = doc[pn].get_text("text")
                hits += text.count("非利息净收入")
                if hits >= 2:
                    doc.close()
                    return 0.9
            doc.close()
        except Exception:
            pass
        return 0.3

    # ── 公开方法 ──

    def parse(self, pdf_path: str, pre_scan: list = None,
              code: str = None, year: int = None) -> Dict:
        # code/year 仅为与通用营收解析器签名一致(银行版自走版式化抽表,暂不接选表解耦)
        candidate_pages = self._find_candidate_pages(pdf_path)
        if not candidate_pages:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        tables = self._extract_tables(pdf_path, candidate_pages)
        bank_tables = self._filter_bank_revenue_tables(tables)
        if not bank_tables:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        unit_ratio = self._detect_unit_from_pdf(pdf_path, candidate_pages)
        best = bank_tables[0]
        result = self._classify(best, unit_ratio=unit_ratio)
        return {"revenue_breakdown": result, "status": "ok"}

    # ── 页面发现 ──

    def _find_candidate_pages(self, pdf_path: str) -> List[int]:
        _BANK_KEYWORDS = [
            "营业收入构成", "项目 2025年 2024年",
            "利息净收入 非利息净收入",
        ]
        try:
            doc = fitz.open(pdf_path)
        except Exception:
            return []

        candidates = set()
        for pn in range(len(doc)):
            text = doc[pn].get_text("text")
            for kw in _BANK_KEYWORDS:
                if kw in text:
                    candidates.add(pn + 1)
                    if pn > 0:
                        candidates.add(pn)
                    if pn < len(doc) - 1:
                        candidates.add(pn + 2)
                    break

        # 如果没找到精准词，降级扫描含"营业收入构成"相关短语的页面
        if not candidates:
            for pn in range(len(doc)):
                text = doc[pn].get_text("text")
                # 检查是否有营业收入 + 利息净收入在一页上
                if "营业收入" in text and "利息净收入" in text:
                    candidates.add(pn + 1)
                    if pn > 0:
                        candidates.add(pn)
                    if pn < len(doc) - 1:
                        candidates.add(pn + 2)
                    break

        doc.close()
        candidates = {p for p in candidates if p >= 16}
        return sorted(candidates)[:15]

    # ── 表格提取与筛选 ──

    def _extract_tables(self, pdf_path: str, pages: List[int]) -> list:
        all_tables = []
        with pdfplumber.open(pdf_path) as pdf:
            for pn in pages:
                if pn - 1 >= len(pdf.pages):
                    continue
                for t in pdf.pages[pn - 1].extract_tables():
                    all_tables.append(t)
        return all_tables

    def _filter_bank_revenue_tables(self, tables: list) -> list:
        """筛选银行营收表：含营业收入及利息净收入等科目"""
        scored = []
        for t in tables:
            text = " ".join(c.replace("\n", " ") for row in t for c in row if c)

            # 必须包含营业收入相关词
            if "营业收入" not in text and "收入构成" not in text:
                continue

            # 加分特征
            score = 0
            if "利息净收入" in text:
                score += 40
            if "非利息净收入" in text:
                score += 30
            if "同比" in text or "增减" in text:
                score += 10
            if "占比" in text or "%" in text:
                score += 10

            # 排除非营收表
            if "员工" in text and "人数" in text:
                score -= 30
            if "研发" in text:
                score -= 20
            if "总负债" in text and "总资产" in text:
                score -= 30

            # 行数合理
            rc = len(t)
            if 5 <= rc <= 40:
                score += 10
            elif rc > 40:
                score -= 10

            if score >= 40:
                scored.append((score, t))

        scored.sort(key=lambda x: -x[0])
        return [t for _, t in scored]

    # ── 列检测 ──

    def _detect_columns(self, table: list) -> tuple:
        """检测名称列、金额列、占比列"""
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
                if ratio_col is None or len(valid) > len(valid):
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

    # ── 分类与提取 ──

    def _classify(self, table: list, unit_ratio: int = 1) -> Dict:
        name_col, amount_col, ratio_col = self._detect_columns(table)

        result = {"segments": [], "industries": [], "regions": []}
        seen = set()

        for row in table:
            cells = [c.strip() if c else "" for c in row]

            # 尝试提取名称
            name = cells[name_col] if name_col is not None and name_col < len(cells) else ""
            if not name or len(name) <= 1:
                for c in cells:
                    if c and self._is_text(c) and len(c) >= 2:
                        name = c[:30]
                        break
                if not name or len(name) <= 1:
                    continue

            # 跳过表头行 / 合计行 / 无效行
            if name in ("项 目", "项目") or is_total_row(name):
                continue
            if any(kw in name for kw in ["年", "金额", "占比", "增减"]):
                if any(c.isdigit() for c in name):
                    continue

            # 提取金额和占比
            amount_raw = cells[amount_col] if amount_col is not None and amount_col < len(cells) else ""
            ratio_raw = cells[ratio_col] if ratio_col is not None and ratio_col < len(cells) else ""

            amount = self._parse_number(amount_raw) if amount_raw else None
            ratio = self._parse_ratio(ratio_raw) if ratio_raw else None

            if "%" in amount_raw and ratio is None:
                ratio = self._parse_ratio(amount_raw)
                amount = None

            if ratio is None and amount is None:
                continue

            if ratio is not None and (ratio < 0 or ratio > 100):
                ratio = None

            item = {
                "name": name.split("  ")[0].strip()[:30],
                "revenue_yuan": convert_to_yuan(amount, unit_ratio) if amount else None,
                "ratio_pct": ratio,
            }
            if name not in seen:
                result["segments"].append(item)
                seen.add(name)

        return result

    def _detect_unit_from_pdf(self, pdf_path: str, pages: List[int]) -> int:
        try:
            doc = fitz.open(pdf_path)
            scan_pages = set(pages)
            for p in pages:
                scan_pages.add(p - 1)
                scan_pages.add(p + 1)
            for pn in range(1, 11):
                scan_pages.add(pn)
            for pn in sorted(scan_pages):
                if 1 <= pn <= len(doc):
                    text = doc[pn - 1].get_text("text")
                    ratio = self._detect_unit_custom(text)
                    if ratio != 1:
                        doc.close()
                        return ratio
            doc.close()
        except Exception:
            pass
        return 1

    @staticmethod
    def _detect_unit_custom(text: str) -> int:
        """自定义单位检测，规避 detect_unit 中 '元' 作为子串误匹配的问题"""
        import re
        _PATTERNS = [
            r"[（(]货币单位[：:]\s*人民币?(\S+?)[)）]",
            r"[（(]单位[：:]\s*人民币?(\S+?)[)）]",
            r"单位[：:]\s*人民币?(\S+)",
        ]
        _UNIT_RATIOS = [
            ("人民币百万元", 1000000),
            ("人民币亿元", 100000000),
            ("人民币万元", 10000),
            ("人民币千元", 1000),
            ("百万元", 1000000),
            ("亿元", 100000000),
            ("千万元", 10000000),
            ("万元", 10000),
            ("十万元", 100000),
            ("千元", 1000),
            ("人民币元", 1),
            ("元", 1),
        ]
        for pat in _PATTERNS:
            m = re.search(pat, text)
            if m:
                unit_text = m.group(1).strip()
                for key, ratio in _UNIT_RATIOS:
                    if key in unit_text or unit_text in key:
                        return ratio
        return 1

    # ── 辅助方法 ──

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
