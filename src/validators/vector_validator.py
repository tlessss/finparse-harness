"""
向量校验 Agent — PRD §4.3

职责：
  1. 加载 quantification/rag_data 中存量向量库
  2. 对解析结果进行三重校验：
     a) 语义一致性：指标名称、字段释义匹配
     b) 数据逻辑勾稽：分项和=总值、占比逻辑
     c) 内容完整性：对照标准指标清单核核心字段无漏提
  3. 输出标准化异常报告（PRD §8.3 结构）

用法：
  from src.validators.vector_validator import VectorValidator
  validator = VectorValidator()
  report = validator.validate(parse_result)
"""

import json
import os
import numpy as np
from typing import Dict, List, Optional
from pathlib import Path
from sklearn.metrics.pairwise import cosine_similarity

from src.config import Config

# BGE embedding 模型（懒加载）
_bge_model = None
_bge_ready = False


def _init_bge():
    global _bge_model, _bge_ready
    if _bge_ready:
        return
    bge_path = str(Config.RAG_MODEL_DIR)
    if os.path.exists(bge_path):
        try:
            from sentence_transformers import SentenceTransformer
            _bge_model = SentenceTransformer(bge_path)
            _bge_ready = True
        except Exception as e:
            print(f"[vector_validator] BGE 加载失败: {e}")


def _embed(texts: list[str]) -> np.ndarray:
    """向量化文本"""
    _init_bge()
    if not _bge_ready or _bge_model is None:
        raise RuntimeError("BGE 模型未加载")
    texts_prepped = [f"为文本生成向量表示: {t}" for t in texts]
    return _bge_model.encode(texts_prepped, normalize_embeddings=True, show_progress_bar=False)


def _load_store(collection_name: str) -> Optional[dict]:
    """加载 rag_data 中的一个 collection"""
    meta_path = os.path.join(str(Config.RAG_DATA_DIR), f"{collection_name}_meta.json")
    vec_path = os.path.join(str(Config.RAG_DATA_DIR), f"{collection_name}_vec.npy")
    if not os.path.exists(meta_path) or not os.path.exists(vec_path):
        return None
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    vectors = np.load(vec_path)
    return {"chunks": meta["chunks"], "vectors": vectors, "ids": meta["ids"], "metadatas": meta.get("metadatas", [])}


def _query_store(store: dict, query_text: str, top_k: int = 5) -> list[dict]:
    """在加载的向量库中检索"""
    q_vec = _embed([query_text])
    scores = cosine_similarity(q_vec, store["vectors"])[0]
    top = np.argsort(scores)[-top_k:][::-1]
    results = []
    for idx in top:
        if scores[idx] > 0.3:
            results.append({
                "content": store["chunks"][idx][:200],
                "score": round(float(scores[idx]), 4),
            })
    return results


# ── 校验规则（内置） ──

_LOGIC_RULES = {
    "revenue_breakdown": [
        {"check": "segments_ratio_sum", "label": "分产品占比之和", "field": "segments"},
        {"check": "industries_ratio_sum", "label": "分行业占比之和", "field": "industries"},
        {"check": "regions_ratio_sum", "label": "分地区占比之和", "field": "regions"},
    ],
    "employees": [
        {"check": "composition_sum", "label": "专业构成人数之和 vs 总数", "field": "composition"},
    ],
    "rnd_info": [
        {"check": "rnd_detail_sum", "label": "研发费用明细之和 vs 合计", "field": "rnd_detail"},
    ],
}


class VectorValidator:
    """向量校验 Agent"""

    def __init__(self):
        _init_bge()

    def validate(self, parse_result: Dict) -> Dict:
        """
        对一次解析结果进行完整三重校验。

        Args:
            parse_result: FinParseAI 管线输出的完整解析结果 dict

        Returns:
            {
                "validation_type": "vector",
                "passed": bool,
                "semantic_checks": [...],
                "logic_checks": [...],
                "completeness_checks": [...],
                "overall_score": float,
                "abnormal_reports": [...],
                "suggest_action": "pass" | "modify_parser" | "new_parser",
            }
        """
        stock_code = parse_result.get("stock_code", "")
        report_year = parse_result.get("report_year", "")

        reports = []
        checks_passed = 0
        checks_total = 0

        # ── 1. 语义一致性校验（向量检索） ──
        semantic_checks = self._check_semantic(parse_result, stock_code, report_year)
        for c in semantic_checks:
            checks_total += 1
            if c["passed"]:
                checks_passed += 1
            if not c["passed"]:
                reports.append(self._build_report(
                    abnormal_type="语义不一致",
                    field=c["field"],
                    detail=c["detail"],
                    similarity_score=c.get("similarity", 0),
                    suggest_action="modify_parser" if c.get("similarity", 0) > 0.6 else "new_parser",
                ))

        # ── 2. 数据逻辑勾稽校验 ──
        logic_checks = self._check_logic(parse_result)
        for c in logic_checks:
            checks_total += 1
            if c["passed"]:
                checks_passed += 1
            if not c["passed"]:
                reports.append(self._build_report(
                    abnormal_type="逻辑错误",
                    field=c["field"],
                    detail=c["detail"],
                    similarity_score=0,
                    suggest_action="modify_parser",
                ))

        # ── 3. 内容完整性校验 ──
        completeness_checks = self._check_completeness(parse_result)
        for c in completeness_checks:
            checks_total += 1
            if c["passed"]:
                checks_passed += 1
            if not c["passed"]:
                reports.append(self._build_report(
                    abnormal_type="漏提",
                    field=c["field"],
                    detail=c["detail"],
                    similarity_score=0,
                    suggest_action="modify_parser",
                ))

        overall_score = round(checks_passed / max(checks_total, 1), 4)

        # ── 决策 ──
        if reports:
            # 计算最低语义分
            min_semantic = min(
                (c.get("similarity", 1) for c in semantic_checks if not c["passed"]),
                default=1,
            )
            if min_semantic < 0.6:
                suggest = "new_parser"
            else:
                suggest = "modify_parser"
        else:
            suggest = "pass"

        return {
            "validation_type": "vector",
            "passed": len(reports) == 0,
            "overall_score": overall_score,
            "field_count": parse_result.get("field_count", 0),
            "checks": {"passed": checks_passed, "total": checks_total},
            "semantic_checks": semantic_checks,
            "logic_checks": logic_checks,
            "completeness_checks": completeness_checks,
            "abnormal_reports": reports,
            "suggest_action": suggest,
        }

    # ── 三重校验实现 ──

    def _check_semantic(self, result: Dict, stock_code: str, report_year: int) -> list:
        """语义一致性校验：用向量检索比对比标准样本"""
        checks = []
        collection = f"{stock_code}_{report_year}_annual"
        store = _load_store(collection)

        if store is None:
            # 无存量向量数据，跳过语义校验
            return [{"field": "vector_store", "passed": True, "detail": "无存量向量，跳过语义校验", "similarity": 1}]

        # 对每个有数据的字段做语义检索对比
        for field in ["revenue_breakdown", "rnd_info", "employees", "cost_breakdown"]:
            data = result.get(field)
            if not data:
                checks.append({"field": field, "passed": True, "detail": "字段为空，跳过", "similarity": 1})
                continue

            # 将数据转为文本用于检索
            query_text = self._field_to_query(field, data)
            matches = _query_store(store, query_text, top_k=3)

            if not matches:
                checks.append({"field": field, "passed": True, "detail": "向量库无匹配", "similarity": 0.5})
                continue

            max_score = matches[0]["score"]
            passed = max_score >= Config.SEMANTIC_SIMILARITY_THRESHOLD

            checks.append({
                "field": field,
                "passed": passed,
                "similarity": max_score,
                "top_match": matches[0]["content"] if matches else "",
                "detail": f"最高相似度 {max_score:.2f} (阈值 {Config.SEMANTIC_SIMILARITY_THRESHOLD})",
            })

        return checks

    def _check_logic(self, result: Dict) -> list:
        """数据逻辑勾稽校验：分项和 vs 总值"""
        checks = []

        # 营收结构占比和
        rev = result.get("revenue_breakdown")
        if rev:
            for rule in _LOGIC_RULES["revenue_breakdown"]:
                items = rev.get(rule["field"], [])
                if items:
                    ratios = [i.get("ratio_pct") for i in items if i.get("ratio_pct") is not None]
                    if ratios:
                        total = sum(ratios)
                        passed = 95 <= total <= 105
                        checks.append({
                            "field": f"revenue_breakdown.{rule['field']}",
                            "passed": passed,
                            "detail": f"{rule['label']}: {total:.2f}% ({'正常' if passed else '异常'})",
                        })

        # 员工构成人数和
        emp = result.get("employees")
        if emp and emp.get("composition"):
            comp_sum = sum(c["count"] for c in emp["composition"])
            total = emp.get("total")
            if total:
                passed = abs(comp_sum - total) <= 2
                checks.append({
                    "field": "employees.composition",
                    "passed": passed,
                    "detail": f"专业构成之和 {comp_sum} vs 声明总数 {total} ({'一致' if passed else '不一致'})",
                })

        # 研发费用明细和
        rnd = result.get("rnd_info")
        if rnd and rnd.get("rnd_detail"):
            detail_sum = sum(d.get("amount_this", 0) or 0 for d in rnd["rnd_detail"])
            total = rnd.get("total_this")
            if total:
                diff_pct = abs(detail_sum - total) / max(total, 1) * 100
                passed = diff_pct < 1
                checks.append({
                    "field": "rnd_info.rnd_detail",
                    "passed": passed,
                    "detail": f"明细之和 {detail_sum:.0f} vs 合计 {total:.0f} (差异 {diff_pct:.2f}%)",
                })

        return checks

    def _check_completeness(self, result: Dict) -> list:
        """内容完整性校验：期望的字段是否存在且有数据"""
        checks = []

        # 检查 6 个字段是否有值
        expected = ["revenue_breakdown", "rnd_info", "employees", "cost_breakdown", "top_clients", "top_suppliers"]
        for field in expected:
            data = result.get(field)
            passed = data is not None
            if isinstance(data, dict):
                passed = any(v for v in data.values() if isinstance(v, (list, dict)) and v)
            elif isinstance(data, list):
                passed = len(data) > 0
            checks.append({
                "field": field,
                "passed": passed,
                "detail": f"{'有数据' if passed else '缺失'}",
            })

        return checks

    # ── 工具方法 ──

    def _field_to_query(self, field: str, data) -> str:
        """将结构化字段转为文本查询"""
        if field == "revenue_breakdown" and isinstance(data, dict):
            parts = []
            for dim in ["segments", "industries", "regions"]:
                items = data.get(dim, [])
                if items:
                    names = [i.get("name", "") for i in items[:5]]
                    parts.append(f"{dim}: {' '.join(names)}")
            return "财报营收结构 " + " ".join(parts)
        elif field == "employees" and isinstance(data, dict):
            return f"员工数据 总数{data.get('total')}人 专业构成{data.get('composition', [])}"
        elif field == "rnd_info" and isinstance(data, dict):
            return f"研发费用 {data.get('total_this', '')}元"
        return f"{field}: {str(data)[:200]}"

    @staticmethod
    def _build_report(abnormal_type: str, field: str, detail: str,
                      similarity_score: float, suggest_action: str) -> Dict:
        return {
            "abnormal_type": abnormal_type,
            "abnormal_position": field,
            "error_detail": detail,
            "similarity_score": similarity_score,
            "suggest_action": suggest_action,
        }
