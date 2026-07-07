def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    def find_continuation_tables(base_table_info, all_tables):
        """查找续表并拼接"""
        base_page = base_table_info["page"]
        base_bbox = base_table_info.get("cell_bbox")
        
        if not base_bbox:
            return base_table_info["table"]
            
        # 获取基础表最后一行的位置信息
        last_row_bbox = base_bbox[-1] if base_bbox else None
        if not last_row_bbox:
            return base_table_info["table"]
            
        # 寻找同页或下一页的续表
        continuation_tables = []
        start_collecting = False
        
        for t_idx, table_info in enumerate(all_tables):
            if table_info == base_table_info:
                start_collecting = True
                continue
                
            if not start_collecting:
                continue
                
            # 检查是否可能是续表
            tbl = table_info["table"]
            if not tbl:
                continue
                
            # 检查表头是否相似（续表通常有相同的列结构）
            if len(tbl) > 0:
                # 检查是否有类似的表头或维度标记
                first_row = tbl[0] if tbl else []
                has_dimension_marker = any(
                    "分行业" in str(cell) or "分产品" in str(cell) or 
                    "分地区" in str(cell) or "分销售模式" in str(cell)
                    for cell in first_row if cell
                )
                
                if has_dimension_marker:
                    # 如果找到新的维度开始，则停止收集续表
                    break
                    
                # 检查列数是否相近
                base_ncol = max(len(row) for row in base_table_info["table"]) if base_table_info["table"] else 0
                tbl_ncol = max(len(row) for row in tbl) if tbl else 0
                if abs(base_ncol - tbl_ncol) <= 2:  # 列数相近
                    continuation_tables.append(tbl)
                    
        # 拼接所有续表（跳过第一行，因为可能是重复的表头）
        full_table = [row[:] for row in base_table_info["table"]]
        for cont_tbl in continuation_tables:
            for row_idx, row in enumerate(cont_tbl):
                if row_idx == 0:
                    # 检查是否是重复的表头行，如果是则跳过
                    is_header_like = any(
                        "营业收入" in str(cell) or "营业成本" in str(cell) or 
                        "毛利率" in str(cell) for cell in row if cell
                    )
                    if is_header_like:
                        continue
                full_table.append(row)
                
        return full_table

    def get_section_type(row):
        """识别行是否为维度切桶标记"""
        for cell in row:
            if cell:
                cell_str = str(cell).strip().replace(" ", "")
                if ("分行业" in cell_str or "按行业" in cell_str):
                    return "industries"
                elif ("分产品" in cell_str or "按产品" in cell_str):
                    return "segments"
                elif ("分地区" in cell_str or "按地区" in cell_str):
                    return "regions"
                elif ("分销售模式" in cell_str or "分销售渠道" in cell_str or 
                      "销售模式" in cell_str or "销售渠道" in cell_str):
                    return "by_channel"
        return None

    def is_revenue_related_header(row):
        """检查是否为营收相关的表头"""
        text = " ".join(str(cell) for cell in row if cell)
        return ("营业收入" in text or "营收" in text or "收入" in text) and (
            "占比" in text or "比重" in text or "占营业收入比重" in text or "营业收入比重" in text
        )

    def find_amount_column(header_row, table):
        """找到金额列的索引"""
        # 查找包含"营业收入"或类似关键词的列
        revenue_keywords = ["营业收入", "收入", "营收"]
        
        # 先尝试找到明确的营收列
        for col_idx in range(len(header_row) if header_row else 0):
            cell = header_row[col_idx] if col_idx < len(header_row) else ""
            if cell and any(keyword in str(cell) for keyword in revenue_keywords):
                # 检查这一列是否主要是数字
                numeric_count = 0
                for row in table[1:]:  # 跳过表头
                    if col_idx < len(row) and row[col_idx]:
                        parsed = parse_money(str(row[col_idx]))
                        if parsed is not None:
                            numeric_count += 1
                if numeric_count >= 2:  # 至少2个数字
                    return col_idx
        
        # 如果没找到明确的营收列，找最大的数字列
        max_numeric_count = 0
        best_col = -1
        for col_idx in range(max(len(row) for row in table) if table else 0):
            numeric_count = 0
            total_count = 0
            for row in table[1:]:  # 跳过表头
                if col_idx < len(row) and row[col_idx]:
                    total_count += 1
                    parsed = parse_money(str(row[col_idx]))
                    if parsed is not None:
                        numeric_count += 1
            
            if numeric_count >= 2 and numeric_count > max_numeric_count:
                # 检查平均数值大小，选择金额较大的列
                avg_value = 0
                value_count = 0
                for row in table[1:]:
                    if col_idx < len(row) and row[col_idx]:
                        parsed = parse_money(str(row[col_idx]))
                        if parsed is not None:
                            avg_value += parsed
                            value_count += 1
                if value_count > 0:
                    avg_value /= value_count
                    if avg_value > 1000:  # 金额应该比较大
                        max_numeric_count = numeric_count
                        best_col = col_idx
        
        return best_col

    def find_name_column(table, amount_col):
        """找到名称列的索引"""
        if not table:
            return 0
            
        # 找到第一个主要包含文本的列（排除金额列）
        for col_idx in range(max(len(row) for row in table) if table else 0):
            if col_idx == amount_col:
                continue
                
            text_count = 0
            for row in table[1:]:  # 跳过表头
                if col_idx < len(row) and row[col_idx]:
                    cell = str(row[col_idx]).strip()
                    if cell and not parse_money(cell) and any('\u4e00' <= c <= '\u9fff' for c in cell):
                        text_count += 1
            
            if text_count >= 2:  # 至少2个文本单元格
                return col_idx
        
        return 0  # 默认返回第0列

    # 遍历所有表格寻找营收构成表
    for table_info in tables:
        table = find_continuation_tables(table_info, tables)
        if not table or len(table) < 2:
            continue
            
        # 检查表头是否包含营收相关信息
        header_row = table[0] if table else []
        
        # 改进：检查表头是否包含营收相关关键词，不一定非要包含"占比"
        has_revenue_header = any("营业收入" in str(cell) or "收入" in str(cell) or "营收" in str(cell) for cell in header_row if cell)
        if not has_revenue_header:
            continue
            
        # 找到金额列和名称列
        amount_col = find_amount_column(header_row, table)
        if amount_col == -1:
            continue
            
        name_col = find_name_column(table, amount_col)
        
        # 解析表格内容
        result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
        current_section = "segments"  # 默认桶
        
        # 添加用于处理父子关系的变量
        hierarchy_stack = []
        
        for row_idx, row in enumerate(table):
            # 检查是否为维度切桶标记
            section_type = get_section_type(row)
            if section_type:
                current_section = section_type
                hierarchy_stack = []  # 重置层级栈
                continue
                
            # 跳过汇总行
            row_text = " ".join(str(cell) for cell in row if cell)
            if is_total_row(row_text):
                continue
                
            # 提取金额
            amount_cell = row[amount_col] if amount_col < len(row) else None
            if amount_cell:
                amount = parse_money(str(amount_cell))
                if amount is not None:
                    name_cell = row[name_col] if name_col < len(row) else None
                    if name_cell:
                        name = str(name_cell).strip()
                        
                        # 检查是否为"其中"类的子项
                        if name.startswith("其中：") or name.startswith("其中"):
                            # 将此行作为子项，添加到层级栈中
                            hierarchy_stack.append((current_section, name, amount))
                            continue
                        
                        # 检查是否是维度标记行（虽然没被get_section_type识别，但可能包含维度信息）
                        if any(dim_keyword in name for dim_keyword in 
                               ["分行业", "分产品", "分地区", "分销售模式", "按行业", "按产品", "按地区", "按销售模式"]):
                            continue
                                
                        # 检查是否是口径前导行（如营业收入合计、主营业务收入等）
                        if any(keyword in name for keyword in ["营业收入合计", "主营业务收入", "其他业务收入"]):
                            continue
                            
                        # 过滤掉非业务分类的行
                        if name and not any(keyword in name for keyword in ["项目", "合计", "总计", "小计"]):
                            item = {
                                "name": name,
                                "revenue_yuan": float(amount)
                            }
                            
                            # 检查是否需要忽略此行（如果当前层级栈中有父项且当前项属于子项范围）
                            should_skip = False
                            for _, stack_name, _ in hierarchy_stack:
                                if name != stack_name and (stack_name in name or name in stack_name):
                                    # 如果当前名称与栈中的某个名称有关联，可能是一个聚合项，跳过
                                    should_skip = True
                                    break
                            
                            if not should_skip:
                                result[current_section].append(item)
    
    # 过滤掉明显不是业务分类的数据（如生产量、销售量等）
    for dimension in result:
        filtered_items = []
        for item in result[dimension]:
            name = item["name"]
            # 排除一些非营收分类的描述
            if not any(exclude_kw in name for exclude_kw in 
                      ["量", "生产", "销售", "库存", "产能", "产量", "销量"]):
                if len([c for c in name if '\u4e00' <= c <= '\u9fff']) > 1:  # 至少包含几个中文字符
                    filtered_items.append(item)
        result[dimension] = filtered_items
    
    # 重新遍历所有表格，确保没有遗漏
    all_results = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    for table_info in tables:
        table = find_continuation_tables(table_info, tables)
        if not table or len(table) < 2:
            continue
            
        # 检查表头是否包含营收相关信息
        header_row = table[0] if table else []
        
        # 改进：检查表头是否包含营收相关关键词，不一定非要包含"占比"
        has_revenue_header = any("营业收入" in str(cell) or "收入" in str(cell) or "营收" in str(cell) for cell in header_row if cell)
        if not has_revenue_header:
            continue
            
        # 找到金额列和名称列
        amount_col = find_amount_column(header_row, table)
        if amount_col == -1:
            continue
            
        name_col = find_name_column(table, amount_col)
        
        # 解析表格内容
        current_section = None  # 重置当前section
        
        for row_idx, row in enumerate(table):
            # 检查是否为维度切桶标记
            section_type = get_section_type(row)
            if section_type:
                current_section = section_type
                continue
                
            # 跳过汇总行
            row_text = " ".join(str(cell) for cell in row if cell)
            if is_total_row(row_text):
                continue
                
            # 提取金额
            amount_cell = row[amount_col] if amount_col < len(row) else None
            if amount_cell:
                amount = parse_money(str(amount_cell))
                if amount is not None and current_section:
                    name_cell = row[name_col] if name_col < len(row) else None
                    if name_cell:
                        name = str(name_cell).strip()
                        
                        # 检查是否是维度标记行
                        if any(dim_keyword in name for dim_keyword in 
                               ["分行业", "分产品", "分地区", "分销售模式", "按行业", "按产品", "按地区", "按销售模式"]):
                            continue
                                
                        # 检查是否是口径前导行（如营业收入合计、主营业务收入等）
                        if any(keyword in name for keyword in ["营业收入合计", "主营业务收入", "其他业务收入"]):
                            continue
                            
                        # 过滤掉非业务分类的行
                        if name and not any(keyword in name for keyword in ["项目", "合计", "总计", "小计"]):
                            # 检查名称是否包含中文字符，避免误提取
                            if any('\u4e00' <= c <= '\u9fff' for c in name):
                                item = {
                                    "name": name,
                                    "revenue_yuan": float(amount)
                                }
                                
                                # 检查是否已存在相同项目，避免重复
                                existing_names = [item['name'] for item in all_results[current_section]]
                                if name not in existing_names:
                                    all_results[current_section].append(item)

    return all_results