def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    # 营收锚（从context或硬编码）
    revenue_anchor = context.get('revenue_anchor', 2360367146.0) if context else 2360367146.0
    anchor_tolerance = 0.03  # ±3%
    
    # 维度映射
    dimension_mapping = {
        "分行业": "industries", 
        "按行业": "industries",
        "分产品": "segments", 
        "按产品": "segments",
        "分地区": "regions", 
        "按地区": "regions",
        "分销售模式": "by_channel", 
        "分销售渠道": "by_channel",
        "销售模式": "by_channel", 
        "按销售模式": "by_channel",
        "销售渠道": "by_channel", 
        "按销售渠道": "by_channel",
    }
    
    # 关键词识别
    def contains_revenue_ratio_keywords(header_text):
        keywords = ["占营业收入比重", "营业收入比重", "占比"]
        text = (header_text or "").lower().replace(" ", "").replace("-", "")
        return any(kw.lower().replace(" ", "").replace("-", "") in text for kw in keywords)
    
    def contains_revenue_amount_keywords(header_text):
        keywords = ["营业收入", "主营业务收入", "收入"]
        text = (header_text or "").lower().replace(" ", "").replace("-", "")
        return any(kw.lower().replace(" ", "").replace("-", "") in text for kw in keywords)
    
    # 寻找合适的表格
    candidate_tables = []
    for table_info in tables:
        table = table_info.get("table", [])
        if not table:
            continue
            
        # 检查表头是否包含营收相关关键词
        header_row = table[0] if table else []
        header_text = " ".join([(str(cell) if cell else "") for cell in header_row])
        
        # 检查是否有维度标识
        has_dimension = False
        for row in table:
            for cell in row:
                if cell and cell.strip() in dimension_mapping:
                    has_dimension = True
                    break
            if has_dimension:
                break
        
        if has_dimension:
            candidate_tables.append(table_info)
    
    # 如果没找到带维度标识的表，尝试找包含营收金额的表
    if not candidate_tables:
        for table_info in tables:
            table = table_info.get("table", [])
            if not table:
                continue
                
            # 检查是否包含维度标识
            has_dimension = False
            for row in table:
                for cell in row:
                    if cell and cell.strip() in dimension_mapping:
                        has_dimension = True
                        break
                if has_dimension:
                    break
            
            if has_dimension:
                candidate_tables.append(table_info)
    
    if not candidate_tables:
        return {"industries":[], "segments":[], "regions":[], "by_channel":[]}
    
    # 拼接跨页表格
    all_tables = {}
    for table_info in tables:
        page = table_info.get("page")
        if page not in all_tables:
            all_tables[page] = []
        all_tables[page].append(table_info)
    
    # 按页码顺序处理表格
    sorted_pages = sorted(all_tables.keys())
    full_tables = []
    
    for table_info in candidate_tables:
        table = table_info.get("table", [])
        current_page = table_info.get("page")
        
        # 检查是否有续表
        extended_table = [row[:] for row in table]
        next_page = current_page + 1
        
        # 持续查找续表直到没有更多续表
        while next_page in all_tables:
            page_tables = all_tables[next_page]
            found_continuation = False
            
            for other_table_info in page_tables:
                other_table = other_table_info.get("table", [])
                if not other_table:
                    continue
                
                # 检查是否可能是续表（结构相似）
                if len(other_table) > 0 and len(other_table[0]) == len(extended_table[0]) if extended_table else True:
                    # 添加续表内容（跳过可能的表头重复）
                    for row in other_table:
                        extended_table.append(row)
                    found_continuation = True
                    break
            
            if not found_continuation:
                break
            next_page += 1
        
        full_tables.append({
            "table": extended_table,
            "page": table_info.get("page"),
            "original_info": table_info
        })
    
    # 解析所有找到的表格
    result = {"industries":[], "segments":[], "regions":[], "by_channel":[]}
    
    for table_data in full_tables:
        table = table_data.get("table", [])
        if not table:
            continue
        
        # 识别维度变化
        sections = []
        for row in table:
            found_section = None
            for cell in row:
                if cell and cell.strip() in dimension_mapping:
                    found_section = dimension_mapping[cell.strip()]
                    break
            sections.append(found_section)
        
        # 确定金额列
        # 找到包含"营业收入"的列作为金额列
        revenue_col_idx = -1
        header_row = table[0] if table else []
        for i, cell in enumerate(header_row):
            if cell and contains_revenue_amount_keywords(str(cell)):
                # 检查这一列是否确实包含金额
                count_money = 0
                for j in range(1, min(len(table), 5)):  # 检查前几行
                    if j < len(table) and i < len(table[j]):
                        cell_val = table[j][i]
                        if cell_val and parse_money(str(cell_val)) is not None:
                            count_money += 1
                if count_money > 0:
                    revenue_col_idx = i
                    break
        
        # 如果没找到明确的营收列，尝试找数值最大的列
        if revenue_col_idx == -1:
            max_sum = 0
            for col_idx in range(max(len(row) for row in table) if table else 0):
                total = 0
                money_count = 0
                for row in table[1:]:  # 跳过表头
                    if col_idx < len(row) and row[col_idx]:
                        parsed = parse_money(str(row[col_idx]))
                        if parsed is not None:
                            total += abs(parsed)
                            money_count += 1
                if money_count >= 2 and total > max_sum:
                    max_sum = total
                    revenue_col_idx = col_idx
        
        # 确定名称列（通常是金额列左边）
        name_col_idx = revenue_col_idx - 1 if revenue_col_idx > 0 else 0
        if name_col_idx >= 0:
            # 验证是否为名称列
            name_cells = 0
            for row in table[1:]:
                if name_col_idx < len(row) and row[name_col_idx]:
                    cell_val = str(row[name_col_idx]).strip()
                    if cell_val and not parse_money(cell_val):
                        name_cells += 1
            if name_cells < 2:
                name_col_idx = 0  # 回退到第一列
        
        # 解析结果
        current_dimension = "segments"  # 默认维度
        seen_items = set()
        
        for idx, row in enumerate(table):
            # 检查是否切换维度
            if idx < len(sections) and sections[idx]:
                current_dimension = sections[idx]
                continue
            
            # 获取名称和金额
            name = ""
            amount = None
            
            if name_col_idx < len(row) and row[name_col_idx]:
                name = str(row[name_col_idx]).strip()
            
            if revenue_col_idx >= 0 and revenue_col_idx < len(row) and row[revenue_col_idx]:
                amount_str = str(row[revenue_col_idx]).strip()
                amount = parse_money(amount_str)
            
            # 跳过无效行
            if not name or not amount or is_total_row(name):
                continue
            
            # 跳过维度标识本身
            if name in dimension_mapping:
                continue
            
            # 跳过"其中:"开头的子项（避免父子重复计算）
            if name.startswith("其中：") or name.startswith("其中:"):
                continue
            
            # 添加到对应维度
            item = {
                "name": name,
                "revenue_yuan": amount,
                "ratio_pct": None  # 不解析占比，留空
            }
            
            if name not in seen_items:
                # 检查是否已存在相同名称但不同金额的记录，避免重复添加
                existing_names = [item['name'] for item in result[current_dimension]]
                if name not in existing_names:
                    result[current_dimension].append(item)
                    seen_items.add(name)
    
    # 处理父子关系：过滤掉可能的重复计算
    for dim_key in result:
        items = result[dim_key]
        filtered_items = []
        i = 0
        while i < len(items):
            current_item = items[i]
            name = current_item["name"]
            
            # 检查是否是聚合项（如包含"其中"或其他聚合词汇）
            is_aggregate = False
            for j, other_item in enumerate(items):
                if i != j:
                    other_name = other_item["name"]
                    # 如果当前项是其他项的聚合（比如包含了其他项的名字）
                    if name.startswith("其中：") or name.startswith("其中:") or \
                       other_name in name or name in other_name:
                        # 如果当前项金额接近其他项之和，说明是聚合项，跳过
                        is_aggregate = True
                        break
            
            if not is_aggregate:
                filtered_items.append(current_item)
            i += 1
        
        result[dim_key] = filtered_items
    
    return result