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

    def _resolve_unit_ratio(self, sel: dict) -> int:
        """表头/标题检测单位;规则里 unit_ratio_override 可强制(万元/千元自愈旋钮)。"""
        rule = load_rule("revenue") or {}
        ov = (rule.get("revenue_breakdown") or {}).get("unit_ratio_override")
        if ov in (1, 1000, 10000, 100000000):
            return int(ov)
        return detect_unit((sel.get("caption", "") or "") + " " + (sel.get("text", "") or ""))

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
              code: str = None, year: int = None, forced_sel: dict = None) -> Dict:
        """营收解析 = 选表解耦 + 认列切桶。
        选表逻辑不再内嵌本解析器：全权委托 select_table（① 向量召回 → ② 锚精判定表定金额列 →
        ③ 维度数闸）。本解析器只负责"给定选中表 → 结构化"（认列、切桶、溯源）。
        code/year 用于取锚做精判；缺省时 select_table 优雅退回纯语义召回（recall_only）。
        forced_sel: 选表自愈用——外部(LLM选表 agent)指定一张选中表，绕过 select_table 直接解析它。"""
        if not pre_scan and not forced_sel:
            return {"revenue_breakdown": None, "status": "no_table_found"}

        sel = forced_sel or select_table(pre_scan, code, year, "revenue")
        if not sel or not sel.get("table"):
            return {"revenue_breakdown": None, "status": "no_table_found"}

        table = sel["table"]
        unit_ratio = self._resolve_unit_ratio(sel)
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
            for c in row:                                   # 精确命中(原行为,无条件):"分行业"等
                if c and c.strip() in section_labels:
                    found = section_labels[c.strip()]
                    break
            if not found:
                # 带修饰前缀的切桶行,如"主营业务分行业""(2)分产品":以维度标记结尾即算。
                # 仅对**纯标签行(无金额)**放开,防"…分产品的收入"这类数据行被误判成切桶头。
                has_money = any(self._looks_like_money((c or "").replace(",", "").replace("，", "")) for c in row)
                if not has_money:
                    for c in row:
                        s = (c or "").strip()
                        for key, dim in section_labels.items():
                            if s != key and s.endswith(key) and 0 < len(s) - len(key) <= 6:
                                found = dim
                                break
                        if found:
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
        child_run = None   # "其中：X" 起的子拆分段:累计子项金额,≤父项预算(±2%)的都是子项(跳过,不计入顶层)
        has_markers = any(sections)     # 表内是否有维度切桶头(分行业/分产品/…)
        seen_marker = False             # 是否已进入第一个维度段
        # 口径前导行(营收=主营+其他):有维度头时,首个维度头之前的"其他业务收入"是口径前导、不是分产品,
        # 别塞进默认 segments 桶(否则 segments=其他业务+产品分项≈营收,复核判结构异常,苏宁002024)。主营/合计已由 is_total_row 挡。
        _PREAMBLE = ("其他业务收入", "其他业务")

        for row_idx, row in enumerate(table):
            sec = sections[row_idx]
            if sec:
                current = sec
                child_run = None            # 换维度 → 子拆分段结束
                seen_marker = True
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
            if has_markers and not seen_marker and name.replace(" ", "") in _PREAMBLE:
                continue                     # 口径前导"其他业务收入":有维度头时不当分项(见上)

            amount = self._parse_number(amount_raw) if amount_raw else None
            amt_yuan = convert_to_yuan(amount, unit_ratio) if amount is not None else None

            # 父子行去重:"其中：X" 标记上一顶层项的子拆分。个别PDF只在首个子项前标"其中：",
            # 后续兄弟(如"机器人与自动化/工业技术")不再带标记 → 靠金额累计续判:
            # 累计子项额 ≤ 父项额(±2%)就都算子项跳过;一旦超出父项预算,说明子拆分段结束、本行是新顶层项。
            # 否则父项(122M)+四个子项(共122M)会被重复计入 → 该维度和≈1.19×营收(美的000333)。
            if name.startswith("其中"):
                parent_amt = result[current][-1]["revenue_yuan"] if result[current] else None
                child_run = {"parent": parent_amt, "cum": amt_yuan or 0} if (parent_amt and amt_yuan is not None) else None
                continue
            if child_run and amt_yuan is not None:
                if child_run["cum"] + amt_yuan <= child_run["parent"] * 1.02:
                    child_run["cum"] += amt_yuan   # 仍在父项预算内 → 子项,不计入顶层
                    continue
                child_run = None                   # 超出父项预算 → 子拆分段结束,本行按新顶层项处理

            if amount is None:                   # 只收有金额的项(占比不再解析,下游用 金额/锚 算)
                continue

            item = {
                # 名称上限从 30 → 120:营收分项名常带括号列举(如"…产品(含CMP抛光垫、CMP抛光液、…)"),
                # 30 字会把长产品名截断 → 复核判 name_error(鼎龙股份300054)。120 足够容真名、又能挡住抽碎的整段。
                "name": name.split("  ")[0].strip()[:120],
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

        # 收尾:折叠**未带"其中："标记**的父子重复(如"中国大陆市场=东部+南部+西部+北部",苏宁002024)。
        for dim in result:
            keep = self._fold_nested(result[dim])
            if all(keep):
                continue
            old_to_new, ni = {}, 0
            for oi, k in enumerate(keep):
                if k:
                    old_to_new[oi] = ni
                    ni += 1
            result[dim] = [it for it, k in zip(result[dim], keep) if k]
            prefix, remapped = dim + "[", {}       # 重建该维度溯源索引(删掉子项的溯源)
            for key, val in provenance.items():
                if key.startswith(prefix):
                    idx_str, tail = key[len(prefix):].split("]", 1)
                    oi = int(idx_str)
                    if oi in old_to_new:
                        remapped[f"{dim}[{old_to_new[oi]}]{tail}"] = val
                else:
                    remapped[key] = val
            provenance = remapped

        return result, provenance

    @staticmethod
    def _fold_nested(items):
        """去未标记的父子重复:某项之后若干连续兄弟项之和≈该项(±1%,≥2项)→它们是它的明细拆分,
        **删掉父(聚合)行、留子(明细)行**——明细更完整,金额锚一样对得上,且复核要的是子区域/子产品明细。
        只在**精确相等(≤1%)且≥2子项**时折叠——巧合成立极罕见,对正常表零影响;
        专治没带"其中："标记的地区/产品嵌套(如'中国大陆市场=东部+南部+西部+北部',苏宁002024)。返回 keep 掩码。"""
        n = len(items)
        keep = [True] * n
        i = 0
        while i < n:
            p = items[i].get("revenue_yuan")
            if not p:
                i += 1
                continue
            cum, j, cnt, folded = 0.0, i + 1, 0, False
            while j < n:
                v = items[j].get("revenue_yuan")
                if v is None or cum + v > p * 1.01:
                    break
                cum += v
                cnt += 1
                j += 1
                if cnt >= 2 and abs(cum - p) <= p * 0.01:
                    keep[i] = False          # 删父聚合行,子明细行(i+1..i+cnt)保留
                    folded = True
                    break
            i = (i + 1 + cnt) if folded else (i + 1)
        return keep

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
