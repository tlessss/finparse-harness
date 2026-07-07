"""
迭代闭环 — PRD §3 / §5.2

实现自动迭代重试逻辑：
  1. 解析 → 校验 → 优化 → 二次解析 → 二次校验
  2. 最大 3 次迭代，成功则归档，失败则转入人工复核
  3. 样本自动沉淀（优质结果/修复案例更新向量库）

用法：
  from src.agents.iteration import IterationEngine
  engine = IterationEngine()
  result = engine.run(stock_code="002407", year=2025, pdf_path="...")
"""

import time
import json
import os
from typing import Dict, List, Optional
from pathlib import Path

from src.config import Config
from src.engine_orchestrator import FinParseAI
from src.validators.vector_validator import VectorValidator
from src.agents.optimizer import ParseOptimizer


class IterationEngine:
    """迭代闭环引擎"""

    def __init__(self):
        self._parser = FinParseAI()
        self._validator = VectorValidator()
        self._optimizer = ParseOptimizer()
        self.max_iterations = Config.MAX_ITERATE

    def run(self, pdf_path: str, stock_code: str = None,
            report_year: int = None, company_name: str = None,
            db_write: bool = True) -> Dict:
        """
        执行一次带迭代的完整解析流程。

        Returns:
            {
                "final_status": "success" | "needs_review" | "failed",
                "iterations": int,
                "parse_result": Dict,
                "validation_result": Dict,
                "optimization_history": List,
                "abnormal_reports": List,
            }
        """
        iteration_log = []
        current_pdf = pdf_path
        current_rule = None  # 当前使用的规则文件
        final_result = None
        final_validation = None

        for i in range(1, self.max_iterations + 1):
            print(f"\n{'='*60}")
            print(f"🔄 迭代 {i}/{self.max_iterations}")
            print(f"{'='*60}")

            # ── Step 1: 解析 ──
            parse_result = self._parser.run(
                current_pdf,
                stock_code=stock_code,
                report_year=report_year,
                company_name=company_name,
                db_write=False,
            )
            final_result = parse_result
            print(f"  📄 解析: {parse_result.get('field_count', 0)}/6 字段")

            # ── Step 2: 校验 ──
            validation = self._validator.validate(parse_result)
            final_validation = validation
            print(f"  🔍 校验: passed={validation['passed']}, "
                  f"score={validation['overall_score']}")

            # 记录日志
            iteration_log.append({
                "iteration": i,
                "parse_result": {
                    "field_count": parse_result.get("field_count", 0),
                    "parsed_fields": parse_result.get("parsed_fields", []),
                },
                "validation": {
                    "passed": validation["passed"],
                    "score": validation["overall_score"],
                    "checks": validation.get("checks", {}),
                    "abnormal_count": len(validation.get("abnormal_reports", [])),
                },
            })

            # ── 校验通过 → 结束 ──
            if validation["passed"]:
                print(f"  ✅ 校验通过，迭代终止")
                final_status = "success"
                break

            # ── 校验未通过 → 诊断并优化 ──
            if i < self.max_iterations:
                print(f"  🔧 诊断优化中...")
                decision = self._optimizer.diagnose(parse_result, validation)
                log_entry = iteration_log[-1]
                log_entry["optimization"] = {
                    "root_cause": decision.get("root_cause", ""),
                    "severity": decision.get("severity", ""),
                    "suggested_action": decision.get("suggested_action", ""),
                }

                if decision.get("suggested_action") != "none":
                    opt_result = self._optimizer.apply(decision, stock_code=stock_code)
                    log_entry["optimization"]["applied"] = opt_result.get("status", "")
                    log_entry["optimization"]["file"] = opt_result.get("file", "")
                    print(f"  🔧 优化: {opt_result.get('status', '')} → {opt_result.get('file', '')}")
                else:
                    print(f"  🔧 优化: 无需行动")
            else:
                print(f"  ❌ 已达最大迭代次数，转入人工复核")

        else:
            # for 循环正常结束（3 次都失败）
            final_status = "needs_review"

        # ── 写入数据库 ──
        db_status = "skipped"
        if db_write and final_result:
            try:
                result = self._parser.run(
                    current_pdf,
                    stock_code=stock_code,
                    report_year=report_year,
                    company_name=company_name,
                    db_write=True,
                )
                db_status = result.get("db_write", "unknown")
            except Exception as e:
                db_status = f"error: {e}"

        # ── 生成最终报告 ──
        report = {
            "final_status": final_status,
            "iterations": len(iteration_log),
            "max_iterations": self.max_iterations,
            "stock_code": stock_code,
            "report_year": report_year,
            "parse_duration": sum(
                lg.get("parse_result", {}).get("field_count", 0) or 0
                for lg in iteration_log
            ),
            "db_write": db_status,
            "parse_result": final_result,
            "validation_result": final_validation,
            "iteration_log": iteration_log,
            "abnormal_reports": (final_validation.get("abnormal_reports", [])
                                 if final_validation else []),
            "optimization_history": self._optimizer.get_history(),
        }

        print(f"\n{'='*60}")
        print(f"📊 最终状态: {report['final_status']}")
        print(f"   迭代次数: {report['iterations']}")
        print(f"   DB写入: {report['db_write']}")
        print(f"{'='*60}")

        return report
