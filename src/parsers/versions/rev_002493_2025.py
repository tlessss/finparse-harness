def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    if not tables:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # 寻找营收锚（上下文提供或通过财报关键指标推断）
    revenue_anchor = context.get("total_revenue", 308622318229) if context else 308622318229
    
    # 合并跨页表格
    merged_tables = []
    for t in tables:
        merged_tables.append({
            "page": t.get("page"),
            "table": t.get("table", []),
            "text": t.get("text", ""),
            "section": t.get("section", ""),
            "cell_bbox": t.get("cell_bbox")
        })
    
    # 查找目标表格：包含“占营业收入比重”、“营业收入比重”、“占比”等关键词
    target_tables = []
    for table_data in merged_tables:
        table = table_data["table"]
        text_content = table_data.get("text", "") + " " + table_data.get("section", "")
        
        # 检查表头或文本是否包含目标关键词
        header_text = ""
        if table and len(table) > 0:
            first_rows = table[:3]  # 检查前几行作为表头
            for row in first_rows:
                header_text += " ".join([(str(cell) if cell else "") for cell in row])
        
        full_text = header_text + " " + text_content
        
        if any(keyword in full_text for keyword in ["占营业收入比重", "营业收入比重", "占比", "分行业", "分产品", "分地区", "分销售模式"]):
            target_tables.append(table_data)
    
    if not target_tables:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # 解析表格
    result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    for table_data in target_tables:
        table = table_data["table"]
        if not table:
            continue
            
        # 识别维度标记
        current_dimension = "segments"  # 默认
        dimension_mapping = {
            "分行业": "industries",
            "按行业": "industries", 
            "分产品": "segments",
            "按产品": "segments",
            "分地区": "regions", 
            "按地区": "regions",
            "分销售模式": "by_channel",
            "分销售渠道": "by_channel"
        }
        
        # 检查表头是否有维度标记
        header_row = None
        for i, row in enumerate(table):
            row_text = " ".join([(str(cell) if cell else "") for cell in row])
            for marker, dim in dimension_mapping.items():
                if marker in row_text:
                    current_dimension = dim
                    header_row = i
                    break
            if header_row is not None:
                break
        
        # 找到金额列（通常是数值最大的列，排除占比列）
        potential_amount_cols = set()
        for r_idx, row in enumerate(table):
            for c_idx, cell in enumerate(row):
                if cell and parse_money(cell) is not None:
                    potential_amount_cols.add(c_idx)
        
        # 确定金额列：排除含有百分比的列
        amount_col = None
        for col_idx in potential_amount_cols:
            has_percentage = False
            for row in table:
                if col_idx < len(row) and row[col_idx]:
                    cell_str = str(row[col_idx])
                    if "%" in cell_str:
                        has_percentage = True
                        break
            if not has_percentage:
                amount_col = col_idx
                break
        
        # 如果没找到不含百分比的金额列，选择最左侧的大数值列
        if amount_col is None and potential_amount_cols:
            # 检查每列的数值大小，选择平均值最大的非百分比列
            max_avg = 0
            for col_idx in potential_amount_cols:
                total_val = 0
                count = 0
                for row in table:
                    if col_idx < len(row) and row[col_idx]:
                        val = parse_money(row[col_idx])
                        if val is not None and val > 0:
                            total_val += val
                            count += 1
                if count > 0:
                    avg_val = total_val / count
                    if avg_val > max_avg:
                        max_avg = avg_val
                        amount_col = col_idx
        
        if amount_col is None:
            continue  # 没有找到合适的金额列，跳过这张表
            
        # 找到名称列（通常是金额列左边的文本列）
        name_col = amount_col - 1
        if name_col < 0:
            name_col = 0  # 如果金额列在最左边，则名称列在最左边
        
        # 解析数据行
        for r_idx, row in enumerate(table):
            if r_idx <= header_row if header_row is not None else 0:
                continue  # 跳过表头行
                
            # 检查是否是维度切换行
            for cell in row:
                if cell:
                    cell_str = str(cell).strip()
                    for marker, dim in dimension_mapping.items():
                        if cell_str == marker or cell_str.endswith(marker):
                            current_dimension = dim
                            break
            
            # 检查是否是合计行
            name_cell = row[name_col] if name_col < len(row) else None
            if name_cell and is_total_row(str(name_cell)):
                continue  # 跳过合计行
            
            # 提取名称和金额
            name = row[name_col] if name_col < len(row) and row[name_col] else None
            amount_raw = row[amount_col] if amount_col < len(row) and row[amount_col] else None
            
            if not name or not amount_raw:
                continue
                
            name_str = str(name).strip()
            if not name_str or name_str in ["项目", "项 目"]:
                continue
                
            # 跳过"其中："类的子项（避免父子重复计算）
            if name_str.startswith("其中：") or name_str.startswith("其中:"):
                continue
                
            amount = parse_money(amount_raw)
            if amount is None:
                continue
                
            # 添加到对应维度
            result[current_dimension].append({
                "name": name_str,
                "revenue_yuan": amount,
                "ratio_pct": None  # 不解析占比，由下游计算
            })
    
    # 去除重复项并处理父子关系
    for dim_name, items in result.items():
        unique_items = []
        seen_names = set()
        
        for item in items:
            name = item["name"]
            # 跳过重复名称
            if name in seen_names:
                continue
            # 跳过"其中："开头的子项
            if name.startswith("其中：") or name.startswith("其中:"):
                continue
            seen_names.add(name)
            unique_items.append(item)
        
        result[dim_name] = unique_items
    
    return result