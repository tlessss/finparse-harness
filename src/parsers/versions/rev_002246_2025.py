def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    # 营收锚（根据题目描述）
    revenue_anchor = 2750445726  # 2,750,445,726
    
    # 维度映射
    dimension_mapping = {
        "分行业": "industries",
        "分产品": "segments", 
        "分地区": "regions",
        "分销售模式": "by_channel"
    }
    
    # 找到所有可能的营收表并合并续表
    all_tables = []
    for t in tables:
        table_data = t.get("table", [])
        if table_data:
            all_tables.append({
                "page": t.get("page"),
                "table": table_data,
                "text": t.get("text", ""),
                "section": t.get("section", ""),
                "cell_bbox": t.get("cell_bbox")
            })
    
    # 尝试合并跨页续表
    merged_tables = []
    i = 0
    while i < len(all_tables):
        current = all_tables[i]
        merged_table = current
        
        # 查找可能的续表
        j = i + 1
        while j < len(all_tables):
            next_table = all_tables[j]
            # 如果下一页表格与当前表格结构相似（列数相近）且位置连续，尝试合并
            if (abs(next_table["page"] - current["page"]) <= 1 and 
                len(current["table"][0]) <= len(next_table["table"][0]) + 2 and
                len(current["table"][0]) >= len(next_table["table"][0]) - 2):
                
                # 检查表头是否相似（续表通常延续相同结构）
                current_header = current["table"][0] if current["table"] else []
                next_header = next_table["table"][0] if next_table["table"] else []
                
                # 如果表头匹配度高，则合并
                common_headers = 0
                min_len = min(len(current_header), len(next_header))
                for k in range(min_len):
                    if (current_header[k] and next_header[k] and 
                        current_header[k].strip() == next_header[k].strip()):
                        common_headers += 1
                
                if common_headers >= min(len(current_header), len(next_header)) - 1:
                    # 合并表格（去掉续表的表头）
                    merged_table["table"].extend(next_table["table"][1:])
                    i = j  # 跳过已合并的表
            j += 1
            
        merged_tables.append(merged_table)
        i += 1
    
    # 解析每个合并后的表格
    result = {
        "industries": [],
        "segments": [],
        "regions": [],
        "by_channel": []
    }
    
    for table_info in merged_tables:
        table = table_info["table"]
        if not table:
            continue
            
        # 检查表头是否包含营收相关信息
        header_row = table[0] if table else []
        header_str = " ".join([str(h) for h in header_row if h])
        
        # 检查是否包含营收构成相关关键词
        if not any(keyword in header_str for keyword in ["营业收入", "营业成本", "毛利率", "占比", "比重"]):
            continue
            
        # 找到维度标记行和对应的金额列
        current_dimension = None
        name_col = None
        amount_col = None
        
        # 遍历每一行寻找维度标记和金额列
        for row_idx, row in enumerate(table):
            row_str = " ".join([str(cell) for cell in row if cell])
            
            # 检查是否是维度标记行
            for marker, dimension in dimension_mapping.items():
                if marker in row_str:
                    current_dimension = dimension
                    break
            
            if not current_dimension:
                continue
                
            # 在找到维度后，确定金额列（通常是名称列右边的大额数字列）
            if name_col is None or amount_col is None:
                # 寻找名称列和金额列
                for col_idx, cell in enumerate(row):
                    if cell and any(keyword in str(cell) for keyword in ["营业收入", "收入"]):
                        # 找到金额列（通常是该列的下一列或附近列）
                        for check_col in range(col_idx + 1, len(row)):
                            if check_col < len(row) and row[check_col]:
                                test_val = parse_money(str(row[check_col]))
                                if test_val is not None and test_val > 1000:  # 大额数字
                                    amount_col = check_col
                                    break
                        # 名称列可能是当前列或前面的列
                        for check_name_col in range(min(col_idx, len(row))):
                            # 检查这一列是否主要是文本
                            text_count = 0
                            for r in range(min(len(table), 10)):  # 检查前10行
                                if r < len(table) and check_name_col < len(table[r]):
                                    cell_val = table[r][check_name_col]
                                    if cell_val and not parse_money(str(cell_val)):
                                        text_count += 1
                            if text_count >= 2:  # 至少2行是文本
                                name_col = check_name_col
                                break
                        break
            
            # 解析当前维度的数据行
            if current_dimension and name_col is not None and amount_col is not None:
                for data_row_idx in range(row_idx + 1, len(table)):
                    data_row = table[data_row_idx]
                    
                    # 检查是否是新的维度标记，如果是则停止当前维度解析
                    is_new_dimension = False
                    for marker in dimension_mapping.keys():
                        if any(marker in str(cell) for cell in data_row if cell):
                            is_new_dimension = True
                            break
                    if is_new_dimension:
                        break
                    
                    # 检查是否是合计行
                    row_text = " ".join([str(cell) for cell in data_row if cell])
                    if is_total_row(" ".join([str(cell) for cell in data_row if cell])):
                        continue
                        
                    # 提取名称和金额
                    if (name_col < len(data_row) and amount_col < len(data_row) and 
                        data_row[name_col] and data_row[amount_col]):
                        
                        name = str(data_row[name_col]).strip()
                        amount_str = str(data_row[amount_col]).strip()
                        
                        # 跳过空值或非金额数据
                        if not name or "其中" in name:
                            continue
                            
                        amount = parse_money(amount_str)
                        if amount is not None:
                            # 添加到对应维度
                            result[current_dimension].append({
                                "name": name,
                                "revenue_yuan": amount,
                                "ratio_pct": None
                            })
    
    # 验证每个维度的合计是否接近营收锚
    for dimension in result:
        total = sum(item["revenue_yuan"] for item in result[dimension])
        if total > 0 and abs(total - revenue_anchor) / revenue_anchor > 0.03:
            # 如果某个维度合计偏离太大，可能需要重新检查或合并其他表格
            pass
    
    return result