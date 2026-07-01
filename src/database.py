"""数据库连接层 — 复用 caibaoxia 库"""

import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor

from src.config import Config


# ── 连接工具 ──────────────────────────────────────


def _parse_dsn() -> dict:
    """从 DATABASE_URL 解析连接参数"""
    raw = Config.DATABASE_URL
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    user_pass, rest = raw.split("@", 1)
    user, password = user_pass.split(":", 1)
    if ":" in rest:
        host_port, db = rest.split("/", 1)
        host, port = host_port.split(":", 1)
        port = int(port)
    else:
        host = rest.split("/", 1)[0]
        port = 3306
        db = rest.split("/", 1)[1] if "/" in rest else ""
    return {"host": host, "port": port, "user": user, "password": password, "database": db}


def get_conn():
    cfg = _parse_dsn()
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=DictCursor,
    )


# ── 目标表（生产 / 测试镜像）──────────────────────
# 所有对报告表的读写都过 reports_table()，由 REPORTS_TABLE 开关决定打到生产还是测试镜像表。
# 表名只允许字母/数字/下划线（防注入：它被直接拼进 SQL，不能走参数占位）。
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")


def reports_table() -> str:
    t = Config.REPORTS_TABLE or "financial_reports"
    if not _TABLE_RE.match(t):
        raise ValueError(f"非法表名 REPORTS_TABLE={t!r}（只允许字母/数字/下划线）")
    return t


def ensure_test_table(source: str = "financial_reports", copy_data: bool = True) -> dict:
    """建镜像测试表（CREATE TABLE ... LIKE 完全同构）+ 可选把生产数据整表复制过来（含 id，
    这样按 id 的 SELECT/UPDATE 与生产完全一致）。幂等：表已存在不重建；有数据不重复灌。
    安全闸：REPORTS_TABLE 必须已切到非生产表，且 != source，否则拒绝（绝不动生产表）。"""
    target = reports_table()
    if not _TABLE_RE.match(source):
        raise ValueError(f"非法 source 表名 {source!r}")
    if target == source:
        raise RuntimeError(f"REPORTS_TABLE 仍指向生产表 {source}；请先设 REPORTS_TABLE=financial_reports_test")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE TABLE IF NOT EXISTS `{target}` LIKE `{source}`")
            cur.execute(f"SELECT COUNT(*) AS n FROM `{target}`")
            existing = cur.fetchone()["n"]
            copied = 0
            if copy_data and existing == 0:
                cur.execute(f"INSERT INTO `{target}` SELECT * FROM `{source}`")
                copied = cur.rowcount
            conn.commit()
        return {"target": target, "source": source, "existing_rows": existing, "copied_rows": copied}
    finally:
        conn.close()


def json_serialize(obj):
    if isinstance(obj, (Decimal,)):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


# ── financial_reports 查询 ────────────────────────


def find_stock(code: str) -> Optional[dict]:
    """按股票代码查询 stocks 表"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, code, name FROM stocks WHERE code = %s", (code,))
            return cur.fetchone()
    finally:
        conn.close()


def get_report(report_id: int) -> Optional[dict]:
    """按 ID 查询 financial_reports 的完整记录"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{reports_table()}` WHERE id = %s", (report_id,))
            return cur.fetchone()
    finally:
        conn.close()


def list_reports(
    stock_code: str = None,
    year: int = None,
    data_source: str = None,
    limit: int = 100,
) -> list[dict]:
    """多条件查询 financial_reports"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            parts = [f"SELECT fr.* FROM `{reports_table()}` fr WHERE 1=1"]
            params = []
            if stock_code:
                parts.append("AND fr.stock_code = %s")
                params.append(stock_code)
            if year:
                parts.append("AND fr.report_year = %s")
                params.append(year)
            if data_source:
                parts.append("AND fr.data_source = %s")
                params.append(data_source)
            parts.append("ORDER BY fr.report_year DESC, fr.stock_code LIMIT %s")
            params.append(limit)
            cur.execute(" ".join(parts), params)
            return cur.fetchall()
    finally:
        conn.close()


def update_report_field(report_id: int, field: str, value):
    """更新 financial_reports 某个字段（支持 JSON 自动序列化）"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False, default=json_serialize)
            cur.execute(
                f"UPDATE `{reports_table()}` SET {field} = %s, updated_at = NOW() WHERE id = %s",
                (value, report_id),
            )
            conn.commit()
    finally:
        conn.close()


def update_report_fields(report_id: int, fields: dict):
    """批量更新 financial_reports 的多个字段"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            sets = []
            vals = []
            for field, value in fields.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, default=json_serialize)
                sets.append(f"{field} = %s")
                vals.append(value)
            vals.append(report_id)
            cur.execute(
                f"UPDATE `{reports_table()}` SET {', '.join(sets)}, updated_at = NOW() WHERE id = %s",
                vals,
            )
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    # 建测试镜像表：REPORTS_TABLE=financial_reports_test python3 -m src.database
    print(ensure_test_table())
