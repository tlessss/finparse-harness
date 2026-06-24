from src.parsers.infra.table_scanner import parse_money, parse_ratio, is_total_row

def parse(tables, context=None) -> dict:
    result = {"industries": [], "segments": [], "regions": []}
    dimension_map = {"分行业": "industries", "分产品": "segments", "分地区": "regions"}

    # 第一步：筛选候选表（至少有一行包含“分行业/分产品/分地区”）
    candidate_tables = []
    for tbl in tables:
        table = tbl["table"]
        has_dimension = False
        for row in table:
            for cell in row:
                if cell and any(kw in cell for kw in ["分行业", "分产品", "分地区"]):
                    has_dimension = True
                    break
            if has_dimension:
                break
        if not has_dimension:
            continue
        candidate_tables.append(tbl)

    if not candidate_tables:
        return result

    # 第二步：对每个候选表，按维度标记切分数据行，在每个桶内找占比列
    best_table = None
    best_deviation = float('inf')
    best_money_col = None
    best_ratio_col = None

    for tbl in candidate_tables:
        table = tbl["table"]
        # 先找出所有维度标记行和对应的数据行范围
        dimension_ranges = []  # [(dimension_name, start_row, end_row), ...]
        current_dim = None
        current_start = None
        for i, row in enumerate(table[1:], 1):
            first_cell = row[0].strip() if row[0] else ""
            if first_cell in dimension_map:
                if current_dim is not None:
                    dimension_ranges.append((current_dim, current_start, i))
                current_dim = dimension_map[first_cell]
                current_start = i
            elif current_dim is not None and i == len(table) - 1:
                dimension_ranges.append((current_dim, current_start, i + 1))
        if current_dim is not None and (not dimension_ranges or dimension_ranges[-1][1] != current_start):
            dimension_ranges.append((current_dim, current_start, len(table)))

        if not dimension_ranges:
            continue

        # 尝试每个可能的占比列（从第1列开始，跳过第0列名称列）
        for col_idx in range(1, len(table[0])):
            total_deviation = 0
            valid_buckets = 0
            for dim_name, start, end in dimension_ranges:
                bucket_ratios = []
                for r in range(start, end):
                    row = table[r]
                    cell = row[col_idx] if col_idx < len(row) else None
                    if cell is None:
                        continue
                    r_val = parse_ratio(cell)
                    if r_val is not None:
                        bucket_ratios.append(r_val)
                if not bucket_ratios:
                    continue
                bucket_sum = sum(bucket_ratios)
                deviation = abs(bucket_sum - 100)
                total_deviation += deviation
                valid_buckets += 1

            if valid_buckets == 0:
                continue

            avg_deviation = total_deviation / valid_buckets
            if avg_deviation < best_deviation:
                # 检查左侧是否有金额列
                money_col = None
                for left_col in range(col_idx - 1, -1, -1):
                    money_vals = []
                    for row in table[1:]:
                        cell = row[left_col] if left_col < len(row) else None
                        if cell is None:
                            continue
                        m = parse_money(cell)
                        if m is not None:
                            money_vals.append(m)
                    if len(money_vals) >= 3:
                        money_col = left_col
                        break
                if money_col is not None:
                    best_deviation = avg_deviation
                    best_table = tbl
                    best_money_col = money_col
                    best_ratio_col = col_idx

    if best_table is None:
        return result

    # 第三步：解析最佳表
    table = best_table["table"]
    current_dimension = None
    for row in table[1:]:
        first_cell = row[0].strip() if row[0] else ""
        if first_cell in dimension_map:
            current_dimension = dimension_map[first_cell]
            continue
        if current_dimension is None:
            continue
        if not first_cell or is_total_row(first_cell):
            continue

        money_str = row[best_money_col] if best_money_col < len(row) else None
        ratio_str = row[best_ratio_col] if best_ratio_col < len(row) else None
        if money_str is None and ratio_str is None:
            continue

        money = parse_money(money_str) if money_str else None
        ratio = parse_ratio(ratio_str) if ratio_str else None
        if money is None and ratio is None:
            continue

        entry = {"name": first_cell}
        if money is not None:
            entry["revenue_yuan"] = money
        if ratio is not None:
            entry["ratio_pct"] = ratio
        result[current_dimension].append(entry)

    return result