"""字段域外判定 — 不适用解析/不计入失败分母的 case（如金融股营收构成）。"""
# 名称含以下标记 → 营收构成本身不适用（银行/券商等按监管另有披露口径）
_FIN_NAME_MARKERS = ("银行", "证券", "信托", "保险", "期货")


def is_financial_stock(code: str) -> bool:
    """按 stocks 表公司名粗判金融股。"""
    try:
        from src.database import find_stock
        stock = find_stock(code) or {}
    except Exception:
        stock = {}
    name = stock.get("name") or ""
    return any(m in name for m in _FIN_NAME_MARKERS)


def is_revenue_breakdown_out_of_scope(code: str, field: str = "revenue_breakdown") -> bool:
    """营收构成字段对金融股标域外。"""
    return field == "revenue_breakdown" and is_financial_stock(code)


def out_of_scope_reason(code: str, field: str) -> str:
    if is_revenue_breakdown_out_of_scope(code, field):
        try:
            from src.database import find_stock
            name = (find_stock(code) or {}).get("name") or code
        except Exception:
            name = code
        return f"金融股({name})不适用 A 股年报「营业收入构成」口径，标域外"
    return "域外"
