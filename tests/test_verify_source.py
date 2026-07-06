"""verify/judge 源文表应与解析溯源对齐，不能盲重跑 select_table。"""

from src.prompts.context.table import pick_table_from_provenance, grid_text_from_pick


def _mk_table(page, grid, cell_bbox=None, caption=""):
    return {"page": page, "table": grid, "cell_bbox": cell_bbox or [], "caption": caption}


def test_pick_table_from_provenance_prefers_majority_table():
    """000785 类：毛利率表与占比表同名行，但独有行应把票投给占比表。"""
    gross = _mk_table(10, [["分行业"], ["租赁及加盟管理业务", "100"]], caption="收入成本毛利率")
    ratio = _mk_table(11, [
        ["分行业"], ["租赁及加盟管理业务", "4434320121", "39.79%"],
        ["装修服务", "190871564", "1.71%"],
        ["分地区"], ["东北地区", "653726855", "5.87%"],
    ], caption="占营业收入比重")
    tables = [gross, ratio]
    prov = {
        "industries[0].name": {"page": 11, "bbox": [10, 20, 30, 40]},
        "industries[1].name": {"page": 11, "bbox": [10, 50, 30, 70]},
        "regions[0].name": {"page": 11, "bbox": [10, 80, 30, 100]},
        "regions[0].revenue_yuan": {"page": 11, "bbox": [40, 80, 80, 100]},
    }
    pick = pick_table_from_provenance(prov, tables)
    assert pick is not None
    assert pick["page"] == 11
    assert "占营业收入" in (pick.get("caption") or "")
    text = grid_text_from_pick(pick)
    assert "装修服务" in text
    assert "东北地区" in text
    assert "毛利率" not in text


def test_pick_table_none_when_no_prov():
    assert pick_table_from_provenance({}, [_mk_table(1, [["a"]])]) is None
