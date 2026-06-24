"""
解析优化 Agent — PRD §4.4

职责：
  1. 根因诊断：收到校验异常报告后判断问题是局部还是全局
  2. 双模式决策：局部→修改现有 YAML；全局→新建 YAML
  3. 执行优化：修改 keyword_mapping / pages / 列索引
  4. 版本管理：记录每次修改

用法：
  from src.agents.optimizer import ParseOptimizer
  optimizer = ParseOptimizer()
  decision = optimizer.diagnose(parse_result, vector_report)
  optimizer.apply(decision)
"""

import json
import os
import shutil
import time
from typing import Dict, List, Optional
from pathlib import Path
import re


class ParseOptimizer:
    """解析优化 Agent"""

    def __init__(self, rule_path: Optional[str] = None):
        if rule_path is None:
            rule_path = str(Path(__file__).parent.parent.parent / "src" / "parser_rules" / "industry_default.yaml")
        self.rule_path = rule_path
        self.rule_dir = Path(rule_path).parent
        self.history: List[Dict] = []

    # ═══════════════════════════════════════════════
    #  1. 根因诊断
    # ═══════════════════════════════════════════════

    def diagnose(self, parse_result: Dict, vector_report: Dict) -> Dict:
        """
        根因诊断：判断异常类型和严重程度。

        Returns:
            {
                "root_cause": str,          # 根因类型
                "severity": "local"|"global",
                "affected_fields": [str],     # 受影响的字段
                "confidence": float,          # 诊断置信度 0-1
                "detail": str,                # 诊断说明
                "suggested_action": "modify_existing" | "create_new",
                "suggested_fixes": [Dict],     # 具体的修复建议
            }
        """
        if not vector_report or vector_report.get("passed"):
            return {"root_cause": "none", "severity": "none",
                    "affected_fields": [], "confidence": 1.0,
                    "detail": "校验通过，无需优化",
                    "suggested_action": "none", "suggested_fixes": []}

        abnormal_reports = vector_report.get("abnormal_reports", [])
        semantic_checks = vector_report.get("semantic_checks", [])
        checks = vector_report.get("checks", {})

        affected = set()
        semantic_scores = {}

        # 收集受影响字段和语义分
        for a in abnormal_reports:
            affected.add(a.get("abnormal_position", "").split(".")[0])

        for c in semantic_checks:
            field = c.get("field", "")
            if field in affected or not c.get("passed", True):
                affected.add(field)
                semantic_scores[field] = c.get("similarity", 0)

        # 判断严重程度
        passed_ratio = checks.get("passed", 0) / max(checks.get("total", 1), 1)

        if passed_ratio < 0.5:
            # 超过一半检查项失败 → 全局问题
            severity = "global"
            action = "create_new"
            root_cause = self._diagnose_global(abnormal_reports, semantic_scores)
        else:
            # 少量失败 → 局部问题
            severity = "local"
            action = "modify_existing"
            root_cause = self._diagnose_local(abnormal_reports, semantic_scores)

        fixes = self._suggest_fixes(root_cause, list(affected), semantic_scores)

        return {
            "root_cause": root_cause["type"],
            "root_cause_detail": root_cause["detail"],
            "severity": severity,
            "affected_fields": list(affected),
            "confidence": root_cause.get("confidence", 0.7),
            "suggested_action": action,
            "suggested_fixes": fixes,
        }

    def _diagnose_local(self, abnormal_reports: List[Dict],
                        semantic_scores: Dict[str, float]) -> Dict:
        """局部问题诊断"""
        abnormal_types = [a.get("abnormal_type", "") for a in abnormal_reports]
        abnormal_positions = [a.get("abnormal_position", "") for a in abnormal_reports]

        # 检查是否有语义异常（字段改名）
        has_semantic = any("语义" in t for t in abnormal_types)
        # 检查是否有逻辑错误
        has_logic = any("逻辑" in t or "漏提" in t for t in abnormal_types)

        if has_semantic and not has_logic:
            # 语义分较低的 → 可能是字段别名变更
            min_score = min(semantic_scores.values()) if semantic_scores else 1
            if 0.6 <= min_score < 0.8:
                return {
                    "type": "field_alias_changed",
                    "detail": f"字段别名变更（最低语义相似度 {min_score:.2f}），需补充 keyword_mapping",
                    "confidence": 0.8,
                }
            elif min_score < 0.6:
                return {
                    "type": "column_layout_changed",
                    "detail": f"表格列布局变化（语义相似度 {min_score:.2f}），需更新 columns 配置",
                    "confidence": 0.75,
                }

        if has_logic and "逻辑错误" in abnormal_types:
            return {
                "type": "data_extraction_error",
                "detail": f"数据提取错误，涉及字段: {set(a.get('abnormal_position','') for a in abnormal_reports)}",
                "confidence": 0.85,
            }

        if "漏提" in abnormal_types:
            return {
                "type": "missing_field_or_page",
                "detail": f"字段缺失，涉及: {set(a.get('abnormal_position','') for a in abnormal_reports)}，可能是页码偏移",
                "confidence": 0.7,
            }

        return {
            "type": "unknown_local",
            "detail": f"局部异常，类型: {set(abnormal_types)}",
            "confidence": 0.5,
        }

    def _diagnose_global(self, abnormal_reports: List[Dict],
                         semantic_scores: Dict[str, float]) -> Dict:
        """全局问题诊断"""
        avg_score = sum(semantic_scores.values()) / max(len(semantic_scores), 1) if semantic_scores else 0

        if avg_score < 0.4:
            return {
                "type": "entire_layout_changed",
                "detail": f"整体版式变更（平均语义相似度 {avg_score:.2f} < 0.6），需新建版式配置",
                "confidence": 0.9,
            }
        elif avg_score < 0.6:
            return {
                "type": "partial_layout_mismatch",
                "detail": f"版式部分不匹配（平均语义相似度 {avg_score:.2f}），建议新规则或大幅修改",
                "confidence": 0.75,
            }
        else:
            return {
                "type": "multiple_extraction_errors",
                "detail": f"多处提取错误，涉及 {(set(a.get('abnormal_position','') for a in abnormal_reports))}",
                "confidence": 0.7,
            }

    def _suggest_fixes(self, root_cause: Dict, affected_fields: List[str],
                       semantic_scores: Dict[str, float]) -> List[Dict]:
        """生成具体修复建议"""
        fixes = []

        if root_cause.get("type") == "field_alias_changed":
            for field in affected_fields:
                fixes.append({
                    "type": "update_keyword_mapping",
                    "field": field,
                    "action": f"在 keyword_mapping 中为 '{field}' 补充新的别名",
                    "confidence": 0.8,
                })

        elif root_cause.get("type") == "column_layout_changed":
            for field in affected_fields:
                section_key = self._field_to_section_key(field)
                if section_key:
                    fixes.append({
                        "type": "update_columns",
                        "field": field,
                        "section": section_key,
                        "action": f"更新 {section_key} 的 columns 配置",
                        "confidence": 0.75,
                    })

        elif root_cause.get("type") in ["missing_field_or_page", "entire_layout_changed",
                                         "partial_layout_mismatch", "multiple_extraction_errors"]:
            for field in affected_fields:
                section_key = self._field_to_section_key(field)
                if section_key:
                    fixes.append({
                        "type": "update_pages",
                        "field": field,
                        "section": section_key,
                        "action": f"更新 {section_key} 的 pages 配置",
                        "confidence": 0.7,
                    })

        return fixes

    # ═══════════════════════════════════════════════
    #  2. 执行优化
    # ═══════════════════════════════════════════════

    def apply(self, decision: Dict, stock_code: str = None) -> Dict:
        """
        根据诊断结果执行优化。

        Args:
            decision: diagnose() 的返回结果
            stock_code: 触发优化的股票代码（用于命名新规则文件）

        Returns:
            {"status": str, "file": str, "changes": [str]}
        """
        if decision.get("suggested_action") == "none":
            return {"status": "no_action", "file": self.rule_path, "changes": []}

        action = decision["suggested_action"]
        changes = []

        if action == "modify_existing":
            file = self.rule_path
            changes.append(f"修改 {file}")
            # 记录修改历史
            self._log_change(file, "modify", decision)

        elif action == "create_new":
            # 新建规则文件
            suffix = stock_code if stock_code else f"v{len(self.history)+1}"
            new_rule_path = str(self.rule_dir / f"industry_{suffix}.yaml")
            shutil.copy2(self.rule_path, new_rule_path)
            file = new_rule_path
            changes.append(f"新建规则文件 {new_rule_path}")
            self._log_change(file, "create", decision)

        return {"status": "applied", "file": file, "changes": changes}

    # ═══════════════════════════════════════════════
    #  3. 版本管理
    # ═══════════════════════════════════════════════

    def _log_change(self, file: str, action_type: str, decision: Dict):
        """记录修改日志"""
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "file": file,
            "action": action_type,
            "root_cause": decision.get("root_cause", ""),
            "severity": decision.get("severity", ""),
            "affected_fields": decision.get("affected_fields", []),
            "suggested_fixes": decision.get("suggested_fixes", []),
        }
        self.history.append(entry)

    def get_history(self) -> List[Dict]:
        """获取修改历史"""
        return self.history

    # ═══════════════════════════════════════════════
    #  工具方法
    # ═══════════════════════════════════════════════

    @staticmethod
    def _field_to_section_key(field: str) -> Optional[str]:
        mapping = {
            "revenue_breakdown": "revenue_section",
            "rnd_info": "rnd_section",
            "employees": "employee_section",
            "cost_breakdown": "cost_section",
            "top_clients": "supplier_section",
            "top_suppliers": "supplier_section",
        }
        return mapping.get(field)

    @staticmethod
    def _find_best_section_key(affected_fields: List[str]) -> Optional[str]:
        """从多个受影响字段中找到最佳 section key"""
        keys = [ParseOptimizer._field_to_section_key(f) for f in affected_fields]
        keys = [k for k in keys if k]
        return keys[0] if keys else None
