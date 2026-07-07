from src.parsers.infra.table_scanner import parse_money, is_total_row

def parse(tables, context=None):
    if not tables:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # 寻找包含营收分解信息的表格
    target_tables = []
    for t in tables:
        table = t.get("table", [])
        if not table:
            continue
            
        # 检查表头是否包含关键标识
        first_rows = table[:3]  # 检查前几行
        text_content = ""
        for row in first_rows:
            for cell in row:
                if cell:
                    text_content += cell
        
        # 如果包含营收分解相关关键词，则加入候选
        if any(keyword in text_content for keyword in ["占营业收入比重", "营业收入比重", "占比", "分行业", "分产品", "分地区", "分销售模式"]):
            target_tables.append(t)
    
    # 合并跨页表格
    merged_tables = merge_continued_tables(target_tables)
    
    # 解析各个维度的数据
    result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    for table_data in merged_tables:
        table = table_data.get("table", [])
        if not table:
            continue
            
        # 识别维度标记行
        dimension_sections = identify_dimension_sections(table)
        
        # 解析各维度数据
        for dim_type, start_row, end_row in dimension_sections:
            if end_row > start_row:
                rows = table[start_row+1:end_row]
                items = extract_items_from_rows(rows, table)
                
                # 过滤掉合计行和无效行
                valid_items = []
                for name, amount in items:
                    if amount is not None and not is_total_row(name) and not is_preamble_row(name):
                        valid_items.append({
                            "name": name.strip(),
                            "revenue_yuan": amount,
                            "ratio_pct": None
                        })
                
                # 添加到对应维度
                if dim_type == "industries":
                    result["industries"].extend(valid_items)
                elif dim_type == "segments":
                    result["segments"].extend(valid_items)
                elif dim_type == "regions":
                    result["regions"].extend(valid_items)
                elif dim_type == "by_channel":
                    result["by_channel"].extend(valid_items)
    
    # 去除父子重复项
    result = remove_parent_child_duplicates(result)
    
    return result

def merge_continued_tables(tables):
    """合并跨页续表"""
    # 按页码排序
    sorted_tables = sorted(tables, key=lambda x: x.get("page", 0))
    
    # 检查是否需要合并（相同结构的相邻表）
    merged = []
    i = 0
    while i < len(sorted_tables):
        current = sorted_tables[i]
        current_table = current.get("table", [])
        
        # 查找可能的续表
        j = i + 1
        while j < len(sorted_tables):
            next_table_data = sorted_tables[j]
            next_table = next_table_data.get("table", [])
            
            # 检查列数是否匹配（允许轻微差异，比如序号列）
            if (current_table and next_table and 
                abs(len(current_table[0]) if current_table else 0 - len(next_table[0]) if next_table else 0) <= 2):
                
                # 检查表头相似性以确认是续表
                if is_continuation_table(current_table, next_table):
                    current_table.extend(next_table[1:])  # 跳过标题行
                    current["table"] = current_table
                    j += 1
                else:
                    break
            else:
                break
        
        merged.append(current)
        i = j
    
    return merged

def is_continuation_table(prev_table, curr_table):
    """判断当前表是否为前一个表的续表"""
    if not prev_table or not curr_table:
        return False
    
    # 检查是否有共同的列头特征
    prev_first_row = prev_table[-1] if prev_table else []
    curr_first_row = curr_table[0] if curr_table else []
    
    # 检查两表是否有相同的维度标记（如分行业、分产品等）
    prev_text = "".join([(cell or "").strip() for cell in prev_first_row])
    curr_text = "".join([(cell or "").strip() for cell in curr_first_row])
    
    # 如果当前表第一行包含维度标记，说明不是续表
    if any(keyword in curr_text for keyword in ["分行业", "分产品", "分地区", "分销售模式"]):
        return False
    
    # 检查列数和结构相似性
    if len(prev_table) > 0 and len(curr_table) > 0:
        prev_last_row = prev_table[-1]
        curr_first_row = curr_table[0] if len(curr_table) > 1 else curr_table[0]
        
        # 检查是否存在金额数据，如果是续表，应该延续数据行而非标题行
        has_prev_amount = any(parse_money(cell) for cell in prev_last_row if cell)
        has_curr_amount = any(parse_money(cell) for cell in curr_first_row if cell)
        
        # 如果前表最后一行和当前表第一行都包含金额数据，可能是续表
        if has_prev_amount and has_curr_amount:
            return True
    
    return True

def identify_dimension_sections(table):
    """识别表格中的维度划分"""
    sections = []
    current_dim = None
    start_row = -1
    
    for idx, row in enumerate(table):
        row_text = "".join([(cell or "").strip() for cell in row])
        
        # 检查是否是维度标记行
        if "分行业" in row_text:
            if current_dim and start_row != -1:
                sections.append((current_dim, start_row, idx))
            current_dim = "industries"
            start_row = idx
        elif "分产品" in row_text:
            if current_dim and start_row != -1:
                sections.append((current_dim, start_row, idx))
            current_dim = "segments"
            start_row = idx
        elif "分地区" in row_text:
            if current_dim and start_row != -1:
                sections.append((current_dim, start_row, idx))
            current_dim = "regions"
            start_row = idx
        elif "分销售模式" in row_text or "销售模式" in row_text:
            if current_dim and start_row != -1:
                sections.append((current_dim, start_row, idx))
            current_dim = "by_channel"
            start_row = idx
    
    # 添加最后一个段
    if current_dim and start_row != -1:
        sections.append((current_dim, start_row, len(table)))
    
    return sections

def extract_items_from_rows(rows, full_table):
    """从行中提取名称和金额对"""
    if not rows:
        return []
    
    # 确定金额列位置
    amount_col = find_amount_column(rows, full_table)
    if amount_col is None:
        return []
    
    # 确定名称列位置
    name_col = find_name_column(rows)
    if name_col is None:
        return []
    
    items = []
    for row in rows:
        if len(row) > name_col and len(row) > amount_col:
            name = row[name_col] or ""
            amount_str = row[amount_col] or ""
            
            # 解析金额
            amount = parse_money(amount_str)
            if amount is not None:
                items.append((name.strip(), amount))
    
    return items

def find_amount_column(rows, full_table):
    """找到金额列的索引"""
    if not rows:
        return None
    
    # 检查每一列是否可能是金额列
    max_cols = max([len(row) for row in rows] if rows else [0])
    
    # 首先检查表头是否包含"金额"或"营业收入"等关键词
    header_row = full_table[0] if full_table else []
    for col_idx in range(len(header_row)):
        if header_row[col_idx] and any(kw in header_row[col_idx] for kw in ["金额", "营业收入", "营业总收入"]):
            # 检查下一列是否是数值
            if col_idx + 1 < len(header_row) and "%" in (header_row[col_idx + 1] or ""):
                # 如果下一列是占比列，那么当前列是金额列
                return col_idx
            elif col_idx < len(header_row) and "%" not in (header_row[col_idx] or ""):
                # 当前列不是占比列，检查是否包含金额数据
                count_amounts = 0
                for row in rows:
                    if col_idx < len(row) and row[col_idx]:
                        if parse_money(row[col_idx]) is not None:
                            count_amounts += 1
                if count_amounts >= max(2, len(rows) // 2):
                    return col_idx
    
    # 检查每一列是否可能是金额列
    for col_idx in range(max_cols):
        potential_amount_count = 0
        total_numeric_count = 0
        
        for row in rows:
            if col_idx < len(row) and row[col_idx]:
                cell_value = row[col_idx].strip()
                # 检查是否是金额格式（跳过百分比）
                if parse_money(cell_value) is not None and "%" not in cell_value:
                    potential_amount_count += 1
                elif parse_money(cell_value) is not None:
                    total_numeric_count += 1
        
        # 如果大部分行在该列都有金额值，则认为是金额列
        if potential_amount_count >= max(2, len(rows) // 2):
            return col_idx
    
    # 如果没找到，尝试在整个表中查找
    max_full_cols = max([len(row) for row in full_table] if full_table else [0])
    for col_idx in range(max_full_cols):
        potential_amount_count = 0
        for row in full_table:
            if col_idx < len(row) and row[col_idx]:
                cell_value = row[col_idx].strip()
                if parse_money(cell_value) is not None and "%" not in cell_value and "万元" not in cell_value:
                    potential_amount_count += 1
        
        if potential_amount_count >= max(3, len(full_table) // 3):
            return col_idx
    
    return None

def find_name_column(rows):
    """找到名称列的索引"""
    if not rows:
        return None
    
    max_cols = max([len(row) for row in rows] if rows else [0])
    
    for col_idx in range(max_cols):
        potential_text_count = 0
        for row in rows:
            if col_idx < len(row) and row[col_idx]:
                cell_value = row[col_idx].strip()
                # 检查是否包含中文字符（通常是名称）
                if any('\u4e00' <= char <= '\u9fff' for char in cell_value) and not any(c.isdigit() for c in cell_value.replace(',', '').replace('.', '').replace('-', '')) and '%' not in cell_value:
                    potential_text_count += 1
        
        if potential_text_count >= max(2, len(rows) // 2):
            return col_idx
    
    # 默认返回第一列
    return 0

def is_preamble_row(name):
    """检查是否是口径前导行"""
    preamble_keywords = ["营业收入合计", "主营业务收入", "其他业务收入", "营业收入", "合计", "总计", "小计"]
    return any(keyword in name for keyword in preamble_keywords)

def remove_parent_child_duplicates(result):
    """去除父子重复项"""
    for dim_key in result:
        items = result[dim_key]
        filtered_items = []
        
        i = 0
        while i < len(items):
            current_item = items[i]
            current_name = current_item["name"]
            current_amount = current_item["revenue_yuan"]
            
            # 检查是否是父项（包含"其中"或类似标记）
            is_parent = any(keyword in current_name for keyword in ["其中", "其中：", "其中:", "："])
            
            if is_parent:
                # 跳过父项，继续下一个
                i += 1
                continue
            else:
                # 检查当前项是否是某个父项的子项
                is_subitem = False
                for j in range(min(i, len(items))):
                    parent_item = items[j]
                    parent_name = parent_item["name"]
                    
                    # 如果前面的项包含"其中"且当前项名称是其一部分或相关
                    if any(keyword in parent_name for keyword in ["其中", "其中：", "其中:"]) or \
                       (parent_name.endswith("业务") and current_name in parent_name) or \
                       (current_name in parent_name and current_amount <= parent_item["revenue_yuan"] * 1.05):
                        is_subitem = True
                        break
                
                if not is_subitem:
                    filtered_items.append(current_item)
                
                i += 1
        
        result[dim_key] = filtered_items
    
    return result