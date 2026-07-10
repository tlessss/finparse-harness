def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    # 营收锚（根据题目描述）
    TOTAL_REVENUE_ANCHOR = 654514782.0
    
    # 维度映射
    DIMENSION_MAP = {
        "分行业": "industries", "按行业": "industries",
        "分产品": "segments", "按产品": "segments", 
        "分地区": "regions", "按地区": "regions",
        "分销售模式": "by_channel", "分销售渠道": "by_channel",
        "销售模式": "by_channel", "按销售模式": "by_channel",
        "销售渠道": "by_channel", "按销售渠道": "by_channel",
    }
    
    # 查找目标表
    target_table_data = None
    for table_info in tables:
        table = table_info["table"]
        text = table_info.get("text", "")
        
        # 检查表头是否包含营收相关关键词
        first_rows = table[:3] if table else []
        flat_headers = []
        for row in first_rows:
            for cell in row:
                if cell:
                    flat_headers.append(str(cell))
        
        header_text = " ".join(flat_headers)
        
        # 寻找包含"营业收入"、"占比"等关键词的表
        if ("营业收入" in header_text or "营业总收入" in header_text or "收入" in header_text) and \
           ("占比" in header_text or "比重" in header_text or "比例" in header_text):
            target_table_data = table_info
            break
    
    # 如果没找到带关键词的表，尝试寻找数值最大的表（可能是候选0那种纯数值表）
    if not target_table_data:
        for table_info in tables:
            table = table_info["table"]
            # 检查是否有大量数值的表
            numeric_count = 0
            total_cells = 0
            for row in table:
                for cell in row:
                    if cell and parse_money(str(cell)) is not None:
                        numeric_count += 1
                    total_cells += 1
            
            if total_cells > 0 and numeric_count / total_cells > 0.5:  # 超过一半是数字
                # 检查数值是否接近营收锚
                amounts = []
                for row in table:
                    for cell in row:
                        if cell:
                            parsed = parse_money(str(cell))
                            if parsed is not None and parsed > 1000:  # 排除小数值
                                amounts.append(parsed)
                
                if amounts:
                    total = sum(amounts)
                    if abs(total - TOTAL_REVENUE_ANCHOR) / TOTAL_REVENUE_ANCHOR < 0.1:  # 在合理范围内
                        target_table_data = table_info
                        break
    
    # 如果还是没找到，尝试候选1（看起来像分渠道的表）
    if not target_table_data:
        for table_info in tables:
            table = table_info["table"]
            # 检查是否有"按销售渠道分"这样的标识
            for row in table:
                for cell in row:
                    if cell and "销售渠道" in str(cell):
                        target_table_data = table_info
                        break
                if target_table_data:
                    break
            if target_table_data:
                break
    
    if not target_table_data:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    table = target_table_data["table"]
    
    # 寻找维度标记和对应的金额列
    sections = []
    for row in table:
        found_section = None
        for cell in row:
            if cell:
                cell_str = str(cell).strip()
                for dim_key, dim_value in DIMENSION_MAP.items():
                    if dim_key in cell_str:
                        found_section = dim_value
                        break
            if found_section:
                break
        sections.append(found_section)
    
    # 确定金额列：寻找数值较大的列作为金额列
    col_sums = []
    for col_idx in range(max(len(row) for row in table) if table else 0):
        col_sum = 0
        for row in table:
            if col_idx < len(row) and row[col_idx]:
                parsed = parse_money(str(row[col_idx]))
                if parsed is not None:
                    col_sum += abs(parsed)
        col_sums.append((col_idx, col_sum))
    
    # 按列总和排序，选择数值最大的列作为金额列（排除可能是名称列的）
    col_sums.sort(key=lambda x: x[1], reverse=True)
    
    # 尝试找到合适的金额列
    amount_col = None
    for col_idx, _ in col_sums:
        # 检查这一列是否主要是数值
        numeric_count = 0
        total_count = 0
        for row_idx, row in enumerate(table):
            if col_idx < len(row) and row[col_idx]:
                if parse_money(str(row[col_idx])) is not None:
                    numeric_count += 1
                total_count += 1
        
        if total_count > 0 and numeric_count / total_count > 0.5:
            amount_col = col_idx
            break
    
    if amount_col is None:
        # 如果没找到明显的金额列，使用最大数值列
        if col_sums:
            amount_col = col_sums[0][0]
    
    # 确定名称列：通常是金额列左边的列（如果是布局规整的话）
    name_col = None
    if amount_col is not None:
        # 寻找左侧最有可能是名称的列
        for col_idx in range(amount_col):
            text_count = 0
            total_count = 0
            for row in table:
                if col_idx < len(row) and row[col_idx]:
                    cell_str = str(row[col_idx]).strip()
                    if cell_str and any('\u4e00' <= c <= '\u9fff' for c in cell_str):
                        text_count += 1
                    total_count += 1
            
            if total_count > 0 and text_count / total_count > 0.5:
                name_col = col_idx
                break
        
        # 如果没找到，尝试右侧
        if name_col is None:
            for col_idx in range(min(len(table[0]) if table else 0, amount_col + 1), -1, -1):
                if col_idx != amount_col:
                    text_count = 0
                    total_count = 0
                    for row in table:
                        if col_idx < len(row) and row[col_idx]:
                            cell_str = str(row[col_idx]).strip()
                            if cell_str and any('\u4e00' <= c <= '\u9fff' for c in cell_str):
                                text_count += 1
                            total_count += 1
                    
                    if total_count > 0 and text_count / total_count > 0.3:
                        name_col = col_idx
                        break
    
    # 解析数据
    result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    current_dimension = "segments"  # 默认维度
    
    for row_idx, row in enumerate(table):
        # 检查是否是维度切换行
        section_type = sections[row_idx] if row_idx < len(sections) else None
        if section_type:
            current_dimension = section_type
            continue
        
        # 获取名称和金额
        name = ""
        amount = None
        
        if name_col is not None and name_col < len(row) and row[name_col]:
            name = str(row[name_col]).strip()
        
        if amount_col is not None and amount_col < len(row) and row[amount_col]:
            amount = parse_money(str(row[amount_col]))
        
        # 跳过无效行
        if not name or not amount:
            continue
        
        # 跳过合计行
        if is_total_row(name):
            continue
        
        # 跳过"其中:"开头的子项（避免父子重复计数）
        if name.startswith("其中") or name.startswith("其中："):
            continue
        
        # 添加到对应维度
        item = {
            "name": name[:100],  # 限制长度
            "revenue_yuan": amount,
            "ratio_pct": None  # 不解析占比，留空
        }
        
        # 检查是否已经存在相同名称的项（避免重复）
        exists = False
        for existing_item in result[current_dimension]:
            if existing_item["name"] == item["name"]:
                exists = True
                break
        
        if not exists:
            result[current_dimension].append(item)
    
    # 特殊处理候选1的结构（看起来是分部信息）
    if not any(result.values()) or sum(len(v) for v in result.values()) == 0:
        # 重新分析表格，特别是候选1那种复杂结构
        for table_info in tables:
            table = table_info["table"]
            
            # 检查是否有"按销售渠道分"的标记
            for row_idx, row in enumerate(table):
                for cell in row:
                    if cell and "销售渠道" in str(cell):
                        # 从这个标记开始解析
                        start_row = row_idx + 1
                        for sub_row_idx in range(start_row, len(table)):
                            sub_row = table[sub_row_idx]
                            
                            # 查找包含金额的行
                            amounts_in_row = []
                            names_in_row = []
                            
                            for col_idx, cell in enumerate(sub_row):
                                if cell:
                                    parsed_amount = parse_money(str(cell))
                                    if parsed_amount is not None and parsed_amount > 1000:
                                        amounts_in_row.append((col_idx, parsed_amount))
                                    elif any('\u4e00' <= c <= '\u9fff' for c in str(cell)) and len(str(cell).strip()) > 1:
                                        names_in_row.append((col_idx, str(cell).strip()))
                            
                            # 如果同时有名称和金额，则添加
                            if names_in_row and amounts_in_row:
                                # 选择最可能的名称和金额
                                name = names_in_row[0][1] if names_in_row else ""
                                amount = amounts_in_row[0][1] if amounts_in_row else None
                                
                                if name and amount and not is_total_row(name) and not name.startswith("其中"):
                                    item = {
                                        "name": name[:100],
                                        "revenue_yuan": amount,
                                        "ratio_pct": None
                                    }
                                    
                                    # 检查是否已存在
                                    exists = False
                                    for existing_item in result["by_channel"]:
                                        if existing_item["name"] == item["name"]:
                                            exists = True
                                            break
                                    
                                    if not exists:
                                        result["by_channel"].append(item)
                        
                        break
    
    return result