"""数据库连接层 — 复用 caibaoxia 库"""

import json
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
            cur.execute("SELECT * FROM financial_reports WHERE id = %s", (report_id,))
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
            parts = ["SELECT fr.* FROM financial_reports fr WHERE 1=1"]
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
                f"UPDATE financial_reports SET {field} = %s, updated_at = NOW() WHERE id = %s",
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
                f"UPDATE financial_reports SET {', '.join(sets)}, updated_at = NOW() WHERE id = %s",
                vals,
            )
            conn.commit()
    finally:
        conn.close()
