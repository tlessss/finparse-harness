from src.parsers.infra.table_scanner import parse_money, parse_ratio, is_total_row

def parse(tables, context=None) -> dict:
    result = {"industries": [], "segments": [], "regions": []}
    dimension_map = {"分行业": "industries", "分产品": "segments", "分地区": "regions"}
    
    # 第一步：筛选候选表
    candidates = []
    for t in tables:
        table = t["table"]
        if not table or len(table) < 2:
            continue
        num_cols = max(len(row) for row in table)
        if num_cols < 3:
            continue
        has_dim = False
        for row in table:
            for cell in row:
                if cell and any(kw in cell for kw in ["分行业", "分产品", "分地区"]):
                    has_dim = True
                    break
            if has_dim:
                break
        if not has_dim:
            continue
        candidates.append(t)
    
    if not candidates:
        return result
    
    # 第二步：识别占比构成表
    best_table = None
    best_score = -1
    for t in candidates:
        table = t["table"]
        # 找所有维度标记行
        dim_rows = []
        for i, row in enumerate(table):
            for cell in row:
                if cell and any(kw in cell for kw in ["分行业", "分产品", "分地区"]):
                    dim_rows.append(i)
                    break
        
        if not dim_rows:
            continue
        
        # 对每个维度桶，检查占比列
        for dim_start in dim_rows:
            dim_end = len(table)
            for dr in dim_rows:
                if dr > dim_start:
                    dim_end = dr
                    break
            bucket_rows = []
            for i in range(dim_start + 1, dim_end):
                row = table[i]
                if not row or not any(cell and cell.strip() for cell in row):
                    continue
                first_cell = row[0].strip() if row[0] else ""
                if is_total_row(first_cell) or not first_cell:
                    continue
                bucket_rows.append(i)
            if len(bucket_rows) < 2:
                continue
            
            for col_idx in range(1, len(table[0])):
                ratios = []
                for r in bucket_rows:
                    cell = table[r][col_idx] if col_idx < len(table[r]) else None
                    if cell:
                        val = parse_ratio(cell)
                        if val is not None:
                            ratios.append(val)
                if len(ratios) >= 2:
                    total = sum(ratios)
                    if 95 <= total <= 105:
                        money_col = col_idx - 1
                        if money_col >= 1:
                            money_vals = []
                            for r in bucket_rows:
                                cell = table[r][money_col] if money_col < len(table[r]) else None
                                if cell:
                                    val = parse_money(cell)
                                    if val is not None:
                                        money_vals.append(val)
                            if len(money_vals) >= 2:
                                score = len(bucket_rows) + (100 - abs(total - 100)) / 10
                                if score > best_score:
                                    best_score = score
                                    best_table = {
                                        "table": table,
                                        "dim_rows": dim_rows,
                                        "money_col": money_col,
                                        "ratio_col": col_idx
                                    }
    
    if best_table is None:
        return result
    
    table = best_table["table"]
    money_col = best_table["money_col"]
    ratio_col = best_table["ratio_col"]
    dim_rows = best_table["dim_rows"]
    
    # 第三步：提取数据
    # 处理第一个维度标记之前的行（归为industries）
    first_dim_row = dim_rows[0] if dim_rows else len(table)
    for i in range(1, first_dim_row):
        row = table[i]
        if not row or not any(cell and cell.strip() for cell in row):
            continue
        first_cell = row[0].strip() if row[0] else ""
        if is_total_row(first_cell) or not first_cell:
            continue
        money_str = row[money_col] if money_col < len(row) else None
        revenue = parse_money(money_str) if money_str else None
        if revenue is None:
            continue
        ratio_str = row[ratio_col] if ratio_col < len(row) else None
        ratio = parse_ratio(ratio_str) if ratio_str else None
        if ratio is None:
            continue
        item = {"name": first_cell, "revenue_yuan": revenue, "ratio_pct": ratio}
        result["industries"].append(item)
    
    # 处理每个维度桶
    for idx, dim_row in enumerate(dim_rows):
        # 确定维度类型
        dim_cell = table[dim_row][0].strip() if table[dim_row] and table[dim_row][0] else ""
        dim_key = None
        for kw, key in dimension_map.items():
            if kw in dim_cell:
                dim_key = key
                break
        if dim_key is None:
            continue
        
        # 确定桶结束行
        if idx + 1 < len(dim_rows):
            end_row = dim_rows[idx + 1]
        else:
            end_row = len(table)
        
        # 提取桶内数据行
        for i in range(dim_row + 1, end_row):
            row = table[i]
            if not row or not any(cell and cell.strip() for cell in row):
                continue
            first_cell = row[0].strip() if row[0] else ""
            if is_total_row(first_cell) or not first_cell:
                continue
            money_str = row[money_col] if money_col < len(row) else None
            revenue = parse_money(money_str) if money_str else None
            if revenue is None:
                continue
            ratio_str = row[ratio_col] if ratio_col < len(row) else None
            ratio = parse_ratio(ratio_str) if ratio_str else None
            if ratio is None:
                continue
            item = {"name": first_cell, "revenue_yuan": revenue, "ratio_pct": ratio}
            result[dim_key].append(item)
    
    return result