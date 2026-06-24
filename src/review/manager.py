"""
人工复核模块 — PRD §4.6

职责：
  1. 拦截多次迭代失败的任务（iteration 返回 final_status=needs_review）
  2. 提供人工修正数据、标注异常原因、确认优化方案
  3. 人工标注数据自动入库、更新向量库与规则库

状态机：
  pending_review → reviewing → (approved / rejected / manual_fix)
      → 入库归档 / 二次迭代

API 端点将会在 api.py 中注册。
"""

import json
import time
from typing import Dict, List, Optional
from datetime import datetime

from src.config import Config
from src.database import get_conn, update_report_fields


class ReviewManager:
    """人工复核管理器"""

    # 状态常量
    STATUS_PENDING = "pending_review"
    STATUS_REVIEWING = "reviewing"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_FIXED = "manual_fix"

    def __init__(self):
        self._ensure_table()

    def _ensure_table(self):
        """确保 review_tasks 表存在（幂等）"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS review_tasks (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        stock_code VARCHAR(10) NOT NULL,
                        report_year YEAR NOT NULL,
                        status VARCHAR(20) NOT NULL DEFAULT 'pending_review',
                        iteration_count INT DEFAULT 0,
                        abnormal_reports JSON,
                        parse_result JSON,
                        reviewer VARCHAR(100) DEFAULT NULL,
                        review_comment TEXT DEFAULT NULL,
                        manual_fixes JSON DEFAULT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE KEY uk_stock_year (stock_code, report_year)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """)
                conn.commit()
        finally:
            conn.close()

    def submit_for_review(self, stock_code: str, report_year: int,
                          iteration_report: Dict) -> int:
        """
        提交任务到人工复核队列。

        Args:
            stock_code: 股票代码
            report_year: 年份
            iteration_report: IterationEngine.run() 的返回结果

        Returns:
            task_id: 复核任务 ID
        """
        abnormal_reports = iteration_report.get("abnormal_reports", [])
        parse_result = iteration_report.get("parse_result", {})

        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO review_tasks
                       (stock_code, report_year, status, iteration_count,
                        abnormal_reports, parse_result, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                       status=%s, iteration_count=%s,
                       abnormal_reports=%s, parse_result=%s,
                       updated_at=%s""",
                    (
                        stock_code, report_year, self.STATUS_PENDING,
                        iteration_report.get("iterations", 0),
                        json.dumps(abnormal_reports, ensure_ascii=False),
                        json.dumps({
                            "field_count": parse_result.get("field_count") if parse_result else 0,
                            "parsed_fields": parse_result.get("parsed_fields") if parse_result else [],
                        }, ensure_ascii=False),
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                        # ON DUPLICATE 参数
                        self.STATUS_PENDING,
                        iteration_report.get("iterations", 0),
                        json.dumps(abnormal_reports, ensure_ascii=False),
                        json.dumps({
                            "field_count": parse_result.get("field_count") if parse_result else 0,
                            "parsed_fields": parse_result.get("parsed_fields") if parse_result else [],
                        }, ensure_ascii=False),
                        time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                )
                conn.commit()
                return cur.lastrowid or 0
        finally:
            conn.close()

    def list_pending(self, limit: int = 20) -> List[Dict]:
        """列出待审任务"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT * FROM review_tasks
                       WHERE status IN ('pending_review', 'reviewing')
                       ORDER BY created_at DESC LIMIT %s""",
                    (limit,),
                )
                return self._parse_rows(cur.fetchall())
        finally:
            conn.close()

    def list_all(self, limit: int = 50) -> List[Dict]:
        """列出所有复核记录"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM review_tasks ORDER BY created_at DESC LIMIT %s",
                    (limit,),
                )
                return self._parse_rows(cur.fetchall())
        finally:
            conn.close()

    def start_review(self, task_id: int, reviewer: str = "system") -> bool:
        """开始审核（锁定任务）"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE review_tasks SET status=%s, reviewer=%s, updated_at=NOW() WHERE id=%s AND status=%s",
                    (self.STATUS_REVIEWING, reviewer, task_id, self.STATUS_PENDING),
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def approve(self, task_id: int, comment: str = "") -> bool:
        """审核通过 → 标记数据为已确认"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE review_tasks SET status=%s, review_comment=%s, updated_at=NOW() WHERE id=%s",
                    (self.STATUS_APPROVED, comment, task_id),
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def reject(self, task_id: int, comment: str = "") -> bool:
        """驳回：数据质量不可接受，需要重新解析"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE review_tasks SET status=%s, review_comment=%s, updated_at=NOW() WHERE id=%s",
                    (self.STATUS_REJECTED, comment, task_id),
                )
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    def apply_manual_fix(self, task_id: int, fixes: Dict, comment: str = "") -> Dict:
        """
        应用人工修正。

        Args:
            task_id: 复核任务 ID
            fixes: 修正后的数据，格式如 {
                "revenue_breakdown": {...},
                "employees": {...},
                "cost_breakdown": [...],
            }
            comment: 修正说明

        Returns:
            {"status": str, "report_id": Optional[int]}
        """
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                # 获取任务信息
                cur.execute("SELECT * FROM review_tasks WHERE id=%s", (task_id,))
                task = cur.fetchone()
                if not task:
                    return {"status": "task_not_found"}

                stock_code = task["stock_code"]
                report_year = task["report_year"]

                # 查找 financial_reports 记录
                cur.execute(
                    "SELECT id FROM financial_reports WHERE stock_code=%s AND report_year=%s AND report_quarter='annual' LIMIT 1",
                    (stock_code, report_year),
                )
                row = cur.fetchone()
                report_id = row["id"] if row else None

                # 写入修正数据
                if report_id and fixes:
                    # 组装写入字段
                    write_fields = {}
                    for f in ["revenue_breakdown", "rnd_info", "employees",
                              "cost_breakdown", "top_clients", "top_suppliers"]:
                        if f in fixes:
                            write_fields[f] = fixes[f]
                    write_fields["data_source"] = "hybrid"
                    write_fields["pdf_parsed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    update_report_fields(report_id, write_fields)

                # 更新任务状态
                cur.execute(
                    "UPDATE review_tasks SET status=%s, review_comment=%s, manual_fixes=%s, updated_at=NOW() WHERE id=%s",
                    (
                        self.STATUS_FIXED,
                        comment,
                        json.dumps(fixes, ensure_ascii=False),
                        task_id,
                    ),
                )
                conn.commit()

                return {"status": "fixed", "report_id": report_id}
        finally:
            conn.close()

    def get_task(self, task_id: int) -> Optional[Dict]:
        """获取单个任务详情"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM review_tasks WHERE id=%s", (task_id,))
                row = cur.fetchone()
                return self._parse_row(row) if row else None
        finally:
            conn.close()

    def get_task_by_stock(self, stock_code: str, report_year: int) -> Optional[Dict]:
        """按股票代码获取复核任务"""
        conn = get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM review_tasks WHERE stock_code=%s AND report_year=%s ORDER BY created_at DESC LIMIT 1",
                    (stock_code, report_year),
                )
                row = cur.fetchone()
                return self._parse_row(row) if row else None
        finally:
            conn.close()

    # ── 工具 ──

    @staticmethod
    def _parse_rows(rows: List[Dict]) -> List[Dict]:
        return [ReviewManager._parse_row(r) for r in rows]

    @staticmethod
    def _parse_row(row: Dict) -> Dict:
        result = dict(row)
        for field in ["abnormal_reports", "parse_result", "manual_fixes"]:
            val = row.get(field)
            if val and isinstance(val, str):
                try:
                    result[field] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        return result


# ── 快捷函数：提交失败迭代任务到人工复核 ──

def submit_failed_iteration(stock_code: str, report_year: int,
                            iteration_report: Dict) -> Optional[int]:
    """迭代失败后自动提交人工复核"""
    if iteration_report.get("final_status") == "needs_review":
        manager = ReviewManager()
        return manager.submit_for_review(stock_code, report_year, iteration_report)
    return None
