"""自愈产物固化 — 成功 healer 的可复用落盘(规则版本 / 抽表配置)。"""

from typing import Optional

from src.eval.extract_profiles import save_report_profile, save_fingerprint_profile


def consolidate_rule_delta(field_file: str, version_id: str, delta: dict,
                           note: str = "", meta: Optional[dict] = None) -> str:
    """L2 规则自愈成功 → 规则版本池(薄封装 rule_versions.save_version)。"""
    from src.parsers.infra.rule_versions import save_version
    key = {"revenue": "revenue_breakdown", "cost": "cost_breakdown"}.get(field_file, field_file)
    return save_version(field_file, version_id, {key: delta}, note=note, meta=meta)


def consolidate_extract_profile(code: str, year: int, page: int, profile_name: str,
                                settings: dict, note: str = "",
                                field: str = "revenue_breakdown") -> str:
    """L3 抽表自愈成功 → 报告级 + 版式级经验库,供 get_tables 读回补丁缓存。"""
    path = save_report_profile(code, year, page, profile_name, settings or {},
                               field=field, note=note)
    save_fingerprint_profile(code, year, page, profile_name, settings or {},
                             field=field, note=note)
    return path
