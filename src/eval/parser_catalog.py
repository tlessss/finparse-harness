"""
母本目录 + 选母本（选择即验证，营收字段级）— fork 优先的基础

已认证的营收专用解析器登记在此。修复一份失败报告时，先用"选择即验证"在母本里
挑最像的（跑一遍对 golden 打分），据分决定 复用 / fork / 新建（见 code_generator.repair）。

注：这里用 golden 打分选母本——适用于"正在认证某失败报告"的构建场景。
生产运行时对无 golden 的新报告，选母本/路由用硬规则代理（见 `revenue_router.route_field`）。
"""

import json
import os
from typing import List, Dict, Tuple, Optional

from src.eval.table_cache import get_tables
from src.eval.sandbox_exec import version_parse_fn
from src.eval.revenue_score import score_field

# 认证清单（持久化，认证流程/前端可追加）。首次缺失则以种子写入。
_MANIFEST = "goldset/certified_parsers.json"
_SEED: List[Dict] = [
    {"key": "000425-工程机械占比构成表", "path": "src/parsers/versions/rev_000425_v1.py",
     "field": "revenue_breakdown"},
]
_DEFAULT_FIELD = "revenue_breakdown"


def load_certified() -> List[Dict]:
    """读认证清单；缺失则用种子初始化并落盘。"""
    if os.path.exists(_MANIFEST):
        return json.load(open(_MANIFEST, encoding="utf-8")).get("parsers", [])
    os.makedirs(os.path.dirname(_MANIFEST) or ".", exist_ok=True)
    json.dump({"parsers": _SEED}, open(_MANIFEST, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return list(_SEED)


def _save_manifest(parsers: List[Dict]) -> None:
    json.dump({"parsers": parsers}, open(_MANIFEST, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def _smoke_run(path: str, code: str, year: int) -> tuple:
    """认证前的**沙箱跑通闸**:在子进程里加载+跑这个解析器文件(隔离,超时),要求
      ① 不 import/scoping/run 报错 ② 输出是 dict ③ 至少有一个非空维度。
    任一不满足 → (False, 原因)。专治"parse_money 作用域 bug"这类文件本身就跑不起来、却被登记进认证库。
    用**子进程沙箱**(version_parse_fn_sandboxed):运行期错误(如 NameError)照样在 parse 时抛,能复现;又不拖垮主进程。"""
    try:
        from src.eval.sandbox_exec import version_parse_fn_sandboxed
        rb = version_parse_fn_sandboxed(path)(code, year)
    except Exception as e:
        return False, f"沙箱跑挂: {str(e)[:120]}"
    if not isinstance(rb, dict) or not rb:
        return False, "解析器无输出"
    if not any(isinstance(v, list) and v for v in rb.values()):
        return False, "解析器输出无有效维度(全空)"
    return True, ""


def certify(key: str, path: str, field: str = _DEFAULT_FIELD,
            fingerprints: Optional[List[str]] = None, table_doc: Optional[str] = None,
            smoke: Optional[tuple] = None) -> Dict:
    """登记一个认证解析器（去重）。记录字段 + **table_doc**（目标表去数字+标题文字骨架,表级向量匹配主键）
    + 版式指纹（老快路径,已弃用但字段保留）。
    smoke=(code, year)：**认证前先沙箱跑通这份报告**——文件本身跑不起来/解不出东西 → 拒绝登记,
    返回 {"certified": False, "reason": ...},不写库。防坏解析器(如 300014 的 parse_money 作用域 bug)混进认证库。
    返回 {"certified": True/False, ...}(旧调用忽略返回值不受影响)。"""
    if smoke:
        ok, why = _smoke_run(path, smoke[0], smoke[1])
        if not ok:
            return {"certified": False, "reason": why, "key": key, "path": path}
    cur = load_certified()
    fps = [f for f in (fingerprints or []) if f]
    for c in cur:
        if c["path"] == path:                      # 已在 → 并入指纹 / 补 table_doc
            c["fingerprints"] = sorted(set(c.get("fingerprints", [])) | set(fps))
            c.setdefault("field", field)
            if table_doc:
                c["table_doc"] = table_doc
            _save_manifest(cur)
            return {"certified": True, "key": key, "path": path, "updated": True}
    entry = {"key": key, "path": path, "field": field, "fingerprints": sorted(set(fps))}
    if table_doc:
        entry["table_doc"] = table_doc
    cur.append(entry)
    _save_manifest(cur)
    return {"certified": True, "key": key, "path": path, "updated": False}


def candidates_by_vector(field: str, report_doc: str, catalog: Optional[List[Dict]] = None,
                         top_k: int = 3, threshold: float = 0.5) -> List[Dict]:
    """**表+标题级向量匹配**（取代文档级指纹缩候选）：把待解析报告"目标表去数字+标题骨架"(report_doc)
    与每个认证解析器登记的 table_doc 算 BGE 余弦，取最像的 top_k。余弦只是**软先验**——
    最终仍靠 route_field 跑候选过金额锚验证。BGE 不可用 / 无 table_doc → 返回 []（回退指纹）。"""
    catalog = catalog if catalog is not None else load_certified()
    cands = [c for c in catalog if c.get("field", _DEFAULT_FIELD) == field and c.get("table_doc")]
    if not cands or not report_doc:
        return []
    try:
        from src.validators.vector_validator import _embed
        from sklearn.metrics.pairwise import cosine_similarity
        rv = _embed([report_doc])
        dv = _embed([c["table_doc"] for c in cands])
        sims = cosine_similarity(rv, dv)[0]
    except Exception:
        return []
    ranked = sorted(({**c, "vec_sim": round(float(s), 4)} for c, s in zip(cands, sims)),
                    key=lambda x: -x["vec_sim"])
    return [c for c in ranked if c["vec_sim"] >= threshold][:top_k]


def tag_fingerprint(path: str, fp: str) -> None:
    """给某认证解析器补一个它能处理的指纹（成功路由后自学，让索引越来越准）。"""
    if not fp:
        return
    cur = load_certified()
    for c in cur:
        if c["path"] == path and fp not in c.get("fingerprints", []):
            c["fingerprints"] = sorted(set(c.get("fingerprints", [])) | {fp})
            _save_manifest(cur)
            return


def candidates_for(field: str, fingerprint: str,
                   catalog: Optional[List[Dict]] = None) -> List[Dict]:
    """先按字段过滤，再指纹缩候选（召回导向：无匹配/指纹未知 → 该字段全跑兜底）。"""
    catalog = catalog if catalog is not None else load_certified()
    catalog = [c for c in catalog if c.get("field", _DEFAULT_FIELD) == field]
    if fingerprint:
        hit = [c for c in catalog if fingerprint in (c.get("fingerprints") or [])]
        if hit:
            return hit
    return catalog


# 向后兼容：模块级常量（首次 import 时加载/初始化）
CERTIFIED: List[Dict] = load_certified()


def pick_mother(code: str, year: int, golden_rb: Dict,
                catalog: List[Dict] = None, spec=None) -> Tuple:
    """选择即验证：跑该字段每个已认证解析器 → 对 golden 打分 → 返回 (最优path, 分, key)。"""
    from src.eval.field_spec import REVENUE
    spec = spec or REVENUE
    if catalog is None:
        catalog = [c for c in load_certified() if c.get("field", _DEFAULT_FIELD) == spec.field]
    if get_tables(code, year) is None:
        return (None, -1.0, None)
    best = (None, -1.0, None)
    for c in catalog:
        if not os.path.exists(c["path"]):
            continue
        try:
            rb = version_parse_fn(c["path"])(code, year)
            s = score_field(spec, rb, golden_rb)["score"]
        except Exception:
            s = -1.0
        if s > best[1]:
            best = (c["path"], s, c["key"])
    return best
