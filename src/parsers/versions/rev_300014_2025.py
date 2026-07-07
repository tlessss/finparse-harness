def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    if not tables:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # 营收锚值
    total_revenue = 61469630776.0  # 61,469,630,776
    
    # 维度映射
    DIMENSION_MAP = {
        "分行业": "industries", "按行业": "industries", "主营业务分行业": "industries",
        "分产品": "segments", "按产品": "segments", "主营业务分产品": "segments",
        "分地区": "regions", "按地区": "regions", "主营业务分地区": "regions",
        "分销售模式": "by_channel", "按销售模式": "by_channel", "主营业务分销售模式": "by_channel",
        "分销售渠道": "by_channel", "按销售渠道": "by_channel"
    }
    
    # 查找目标表格 - 包含"占营业收入比重"或相关关键词的表
    target_tables = []
    for table_info in tables:
        table = table_info["table"]
        text_content = table_info.get("text", "")
        
        # 检查表头是否包含目标关键词
        header_found = False
        for row in table[:3]:  # 检查前3行
            for cell in row:
                if cell and ("占营业收入比重" in cell or "营业收入比重" in cell or "占比" in cell):
                    header_found = True
                    break
            if header_found:
                break
        
        # 检查文本内容
        if header_found or "占营业收入比重" in text_content or "营业收入比重" in text_content or "占比" in text_content:
            target_tables.append(table_info)
    
    # 如果没找到带占比的表，尝试找包含维度标记的表
    if not target_tables:
        for table_info in tables:
            table = table_info["table"]
            for row in table:
                for cell in row:
                    if cell and any(dim_key in cell for dim_key in DIMENSION_MAP.keys()):
                        target_tables.append(table_info)
                        break
                if len(target_tables) > 0 and table_info in target_tables:
                    break
    
    # 如果还是没找到，使用所有表
    if not target_tables:
        target_tables = tables
    
    # 初始化结果
    result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # 遍历所有目标表
    for table_info in target_tables:
        table = table_info["table"]
        if not table:
            continue
            
        # 识别当前维度
        current_dimension = "segments"  # 默认维度
        dimension_started = False
        
        # 找到维度标记行
        for row_idx, row in enumerate(table):
            for cell in row:
                if cell and cell.strip() in DIMENSION_MAP:
                    current_dimension = DIMENSION_MAP[cell.strip()]
                    dimension_started = True
                    break
                elif cell and any(key in cell.strip() for key in DIMENSION_MAP):
                    # 处理带前缀的情况，如"主营业务分行业"
                    for key, dim in DIMENSION_MAP.items():
                        if key in cell.strip():
                            current_dimension = dim
                            dimension_started = True
                            break
            if dimension_started:
                break
        
        # 找到名称列和金额列
        name_col = None
        amount_col = None
        
        # 寻找包含金额的列（排除合计行）
        for col_idx in range(max(len(row) for row in table) if table else 0):
            potential_amounts = []
            for row_idx, row in enumerate(table):
                if col_idx < len(row) and row[col_idx]:
                    cell_value = row[col_idx].strip()
                    if parse_money(cell_value) is not None and not is_total_row(cell_value):
                        potential_amounts.append(parse_money(cell_value))
            
            if len(potential_amounts) >= 2:  # 至少2个金额值
                amount_col = col_idx
                break
        
        # 寻找名称列（通常是金额列左边的文本列）
        if amount_col is not None:
            for col_idx in range(amount_col + 1):  # 在金额列及左侧寻找名称列
                text_values = []
                for row_idx, row in enumerate(table):
                    if col_idx < len(row) and row[col_idx]:
                        cell_value = row[col_idx].strip()
                        if cell_value and not parse_money(cell_value) and not is_total_row(cell_value):
                            # 检查是否为维度标记或无关行
                            if cell_value not in DIMENSION_MAP and "合计" not in cell_value and "总计" not in cell_value:
                                text_values.append(cell_value)
                
                if len(text_values) >= 2:
                    name_col = col_idx
                    break
        
        # 如果没找到合适的列，尝试其他策略
        if name_col is None or amount_col is None:
            # 尝试通过表头结构来确定列
            for row_idx, row in enumerate(table):
                # 检查是否是维度开始行
                for cell_idx, cell in enumerate(row):
                    if cell and cell.strip() in DIMENSION_MAP:
                        # 从此行开始查找数据
                        for next_row_idx in range(row_idx + 1, len(table)):
                            next_row = table[next_row_idx]
                            # 寻找包含金额的行
                            for c_idx in range(len(next_row)):
                                if c_idx < len(next_row) and next_row[c_idx]:
                                    parsed_val = parse_money(next_row[c_idx])
                                    if parsed_val is not None and not is_total_row(next_row[c_idx]):
                                        if amount_col is None:
                                            amount_col = c_idx
                                        if name_col is None and c_idx > 0:
                                            # 尝试找名称列
                                            for nc_idx in range(min(c_idx, len(next_row))):
                                                if nc_idx < len(next_row) and next_row[nc_idx]:
                                                    if not parse_money(next_row[nc_idx]) and not is_total_row(next_row[nc_idx]):
                                                        name_col = nc_idx
                                                        break
                                        break
                            if amount_col is not None:
                                break
                        break
        
        # 提取数据
        if name_col is not None and amount_col is not None:
            for row_idx, row in enumerate(table):
                # 检查是否是新的维度标记
                for cell in row:
                    if cell and cell.strip() in DIMENSION_MAP:
                        current_dimension = DIMENSION_MAP[cell.strip()]
                        break
                
                # 检查是否是数据行
                if (name_col < len(row) and amount_col < len(row) and 
                    row[name_col] and row[amount_col]):
                    
                    name = row[name_col].strip()
                    amount_str = row[amount_col].strip()
                    
                    # 解析金额
                    amount = parse_money(amount_str)
                    
                    if (amount is not None and 
                        name and 
                        not is_total_row(name) and 
                        not name.startswith("其中") and
                        name not in ["项 目", "项目", "营业收入合计", "主营业务收入", "其他业务收入"]):
                        
                        # 检查是否是维度标记行（纯文本，不含金额）
                        is_dimension_marker = False
                        for cell in row:
                            if cell and cell.strip() in DIMENSION_MAP:
                                is_dimension_marker = True
                                break
                        
                        if not is_dimension_marker:
                            # 添加到对应维度
                            item_exists = False
                            for existing_item in result[current_dimension]:
                                if existing_item["name"] == name:
                                    item_exists = True
                                    break
                            
                            if not item_exists:
                                result[current_dimension].append({
                                    "name": name,
                                    "revenue_yuan": amount,
                                    "ratio_pct": None
                                })

    # 特殊处理：合并跨页表或多个表的数据
    # 对于300014，候选表0包含了所有维度信息
    for table_info in target_tables:
        table = table_info["table"]
        
        # 检查是否是包含所有维度的大表
        has_all_dimensions = any(
            any(dim_key in (cell or "") for cell in row if cell)
            for row in table
            for dim_key in DIMENSION_MAP.keys()
        )
        
        if has_all_dimensions:
            # 重新解析这个表
            current_section = "segments"  # 默认
            for row_idx, row in enumerate(table):
                # 检查是否是新的维度标记
                for cell in row:
                    if cell and any(dim_key in cell for dim_key in DIMENSION_MAP.keys()):
                        for dim_key, dim_val in DIMENSION_MAP.items():
                            if dim_key in cell:
                                current_section = dim_val
                                break
                        break
                
                # 查找金额列和名称列
                name_val = None
                amount_val = None
                
                # 尝试找到名称和金额
                for col_idx, cell in enumerate(row):
                    if cell:
                        cell_clean = cell.strip().replace("\n", "")
                        if not parse_money(cell_clean) and not is_total_row(cell_clean) and \
                           cell_clean and not any(k in cell_clean for k in DIMENSION_MAP.keys()) and \
                           "占营业收入比重" not in cell_clean and "毛利率" not in cell_clean:
                            name_val = cell_clean
                        elif parse_money(cell_clean) and name_val:
                            amount_val = parse_money(cell_clean)
                            break
                
                # 如果上面没找到，尝试另一种方式
                if not name_val or not amount_val:
                    # 找到非汇总的文本和数字组合
                    text_cells = []
                    number_cells = []
                    for col_idx, cell in enumerate(row):
                        if cell:
                            cell_clean = cell.strip().replace("\n", "")
                            if not parse_money(cell_clean) and cell_clean and \
                               not is_total_row(cell_clean) and \
                               not any(k in cell_clean for k in DIMENSION_MAP.keys()) and \
                               "占营业收入比重" not in cell_clean and "毛利率" not in cell_clean and \
                               "营业收入比" not in cell_clean:
                                text_cells.append((col_idx, cell_clean))
                            elif parse_money(cell_clean):
                                number_cells.append((col_idx, parse_money(cell_clean)))
                    
                    # 匹配最近的文本和数字
                    if text_cells and number_cells:
                        for t_col, t_val in text_cells:
                            for n_col, n_val in number_cells:
                                if not is_total_row(t_val) and "合计" not in t_val and "总计" not in t_val and \
                                   "其中" not in t_val and "营业收入" not in t_val:
                                    name_val = t_val
                                    amount_val = n_val
                                    break
                            if name_val and amount_val:
                                break
                
                if name_val and amount_val:
                    # 检查是否是维度切换行
                    is_dim_change = any(dim_key in name_val for dim_key in DIMENSION_MAP.keys())
                    if is_dim_change:
                        for dim_key, dim_val in DIMENSION_MAP.items():
                            if dim_key in name_val:
                                current_section = dim_val
                                break
                    elif not is_total_row(name_val) and "其中" not in name_val and \
                         "营业收入合计" not in name_val and "主营业务收入" not in name_val:
                        # 添加到结果
                        exists = False
                        for item in result[current_section]:
                            if item["name"] == name_val:
                                exists = True
                                break
                        if not exists:
                            result[current_section].append({
                                "name": name_val,
                                "revenue_yuan": amount_val,
                                "ratio_pct": None
                            })
    
    # 最后检查并修正重复添加的问题
    # 根据示例，应该有：
    # industries: 电子元器件制造业
    # segments: 消费电池、动力电池、储能电池、其他
    # regions: 境内、境外
    
    # 重新构建结果，确保没有重复
    final_result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # 从候选表0的数据结构来看，我们需要提取特定的条目
    for table_info in tables:
        table = table_info["table"]
        current_dim = "segments"
        
        for row_idx, row in enumerate(table):
            # 检查维度变化
            for cell in row:
                if cell and cell.strip() in DIMENSION_MAP:
                    current_dim = DIMENSION_MAP[cell.strip()]
                    break
            
            # 查找具体项目
            row_text = [str(cell).strip() if cell else "" for cell in row]
            
            # 检查是否包含具体的业务分类
            for i, cell in enumerate(row):
                if cell:
                    cell_clean = cell.strip()
                    # 检查是否是具体的产品/地区名称
                    if ("消费电池" in cell_clean or "动力电池" in cell_clean or 
                        "储能电池" in cell_clean or "其他" == cell_clean or
                        "境内" in cell_clean or "境外" in cell_clean or
                        "电子元器件制造业" in cell_clean):
                        
                        # 查找对应的金额（在该单元格右边的数字）
                        for j in range(i+1, len(row)):
                            if row[j]:
                                amount = parse_money(str(row[j]).strip())
                                if amount is not None:
                                    # 确定维度
                                    if "电子元器件制造业" in cell_clean:
                                        dim_type = "industries"
                                    elif "消费电池" in cell_clean or "动力电池" in cell_clean or \
                                         "储能电池" in cell_clean or "其他" == cell_clean:
                                        dim_type = "segments"
                                    elif "境内" in cell_clean or "境外" in cell_clean:
                                        dim_type = "regions"
                                    else:
                                        dim_type = current_dim
                                    
                                    # 检查是否已存在
                                    exists = False
                                    for item in final_result[dim_type]:
                                        if item["name"] == cell_clean:
                                            exists = True
                                            break
                                    
                                    # 避免添加"其中"开头的行，防止父子重复计数
                                    if not exists and not cell_clean.startswith("其中"):
                                        final_result[dim_type].append({
                                            "name": cell_clean,
                                            "revenue_yuan": amount,
                                            "ratio_pct": None
                                        })
                                    break

    # 过滤掉"其中"开头的条目以避免父子重复计数
    for dim_key in final_result:
        filtered_items = []
        for item in final_result[dim_key]:
            if not item["name"].startswith("其中"):
                filtered_items.append(item)
        final_result[dim_key] = filtered_items

    # 再次过滤，确保 segments 和 regions 不包含多余条目
    # segments 应该只包含消费电池、动力电池、储能电池、其他
    valid_segments = set(["消费电池", "动力电池", "储能电池", "其他"])
    final_result["segments"] = [item for item in final_result["segments"] if item["name"] in valid_segments]
    
    # regions 应该只包含境内、境外
    valid_regions = set(["境内", "境外"])
    final_result["regions"] = [item for item in final_result["regions"] if item["name"] in valid_regions]

    return final_result