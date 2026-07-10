def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    # 营收锚值
    TOTAL_REVENUE_ANCHOR = 61469630776.0
    TOLERANCE = 0.03  # ±3%
    
    # 维度映射
    DIMENSION_MAP = {
        "分行业": "industries", "按行业": "industries", "主营业务分行业": "industries",
        "分产品": "segments", "按产品": "segments", "主营业务分产品": "segments",
        "分地区": "regions", "按地区": "regions", "主营业务分地区": "regions",
        "分销售模式": "by_channel", "分销售渠道": "by_channel", "主营业务分销售模式": "by_channel"
    }
    
    # 查找包含营收结构的表格
    target_tables = []
    for table_data in tables:
        table = table_data["table"]
        if not table:
            continue
            
        # 检查表头是否包含关键词
        header_text = ""
        for row in table[:3]:  # 检查前3行
            for cell in row:
                if cell:
                    header_text += cell
        
        if any(keyword in header_text for keyword in ["占营业收入比重", "营业收入比重", "占比", "营业收入", "分行业", "分产品", "分地区", "分销售模式"]):
            target_tables.append(table_data)
    
    # 尝试合并跨页表格
    merged_tables = merge_continuation_tables(target_tables)
    
    results = {
        "industries": [],
        "segments": [],
        "regions": [],
        "by_channel": []
    }
    
    for table_data in merged_tables:
        table = table_data["table"]
        process_revenue_table(table, results, TOTAL_REVENUE_ANCHOR, TOLERANCE)
    
    # 验证各维度合计是否接近锚值
    for dim_name, items in results.items():
        total = sum(item["revenue_yuan"] for item in items)
        expected_ratio = total / TOTAL_REVENUE_ANCHOR if TOTAL_REVENUE_ANCHOR != 0 else 0
        
        # 如果某个维度的总额远超锚值，可能是重复计算了
        if expected_ratio > 1.05:
            # 过滤掉可能的重复项（如父子关系）
            filtered_items = remove_duplicate_entries(items)
            results[dim_name] = filtered_items
    
    return results


def merge_continuation_tables(tables):
    """合并跨页续表"""
    # 按页码排序
    sorted_tables = sorted(tables, key=lambda x: x["page"])
    merged = []
    
    i = 0
    while i < len(sorted_tables):
        current = sorted_tables[i]
        merged_table = current.copy()
        
        # 寻找续表
        j = i + 1
        while j < len(sorted_tables):
            next_table = sorted_tables[j]
            
            # 检查是否为续表：页码连续，列数相近
            if next_table["page"] == current["page"] or next_table["page"] == current["page"] + 1:
                curr_rows = len(current["table"])
                curr_cols = max(len(row) for row in current["table"]) if current["table"] else 0
                next_cols = max(len(row) for row in next_table["table"]) if next_table["table"] else 0
                
                # 列数相近认为是续表
                if abs(curr_cols - next_cols) <= 2:
                    # 合并表格（去除可能的重复表头）
                    first_next_row = next_table["table"][0] if next_table["table"] else []
                    
                    # 检查是否是重复的维度标识行
                    is_duplicate_header = False
                    for cell in first_next_row:
                        if cell and any(dim_key in cell for dim_key in ["分行业", "分产品", "分地区", "分销售模式"]):
                            is_duplicate_header = True
                            break
                    
                    if is_duplicate_header:
                        # 跳过重复的维度标识行
                        merged_table["table"].extend(next_table["table"][1:])
                    else:
                        merged_table["table"].extend(next_table["table"])
                    
                    current = merged_table
                    j += 1
                else:
                    break
            else:
                break
        
        merged.append(merged_table)
        i = j if j > i + 1 else i + 1
    
    return merged


def process_revenue_table(table, results, anchor, tolerance):
    """处理单个营收表格"""
    current_dimension = None
    dimension_started = False
    
    # 找到金额列（通常是数值最大的列）
    amount_col = find_amount_column(table)
    
    for row_idx, row in enumerate(table):
        # 检查是否是维度分割行
        dimension_found = False
        for cell in row:
            if cell and cell.strip() in ["分行业", "分产品", "分地区", "分销售模式"]:
                # 检查是否是"主营业务分..."形式
                full_text = "".join(row).strip()
                for dim_key, dim_value in {
                    "主营业务分行业": "industries",
                    "主营业务分产品": "segments", 
                    "主营业务分地区": "regions",
                    "主营业务分销售模式": "by_channel",
                    "分行业": "industries",
                    "分产品": "segments",
                    "分地区": "regions", 
                    "分销售模式": "by_channel"
                }.items():
                    if dim_key in full_text:
                        current_dimension = dim_value
                        dimension_started = True
                        dimension_found = True
                        break
            if dimension_found:
                break
        
        if dimension_found:
            continue
        
        # 跳过非当前维度的数据行
        if not dimension_started:
            continue
        
        if current_dimension is None:
            continue
            
        # 解析数据行
        name_cell = find_name_cell_in_row(row)
        if not name_cell:
            continue
            
        # 跳过汇总行
        if is_total_row(name_cell):
            continue
            
        # 跳过"其中："开头的子项（避免父子重复计算）
        if name_cell.startswith("其中：") or name_cell.startswith("其中:"):
            continue
            
        # 提取金额
        amount = None
        if amount_col is not None and amount_col < len(row):
            amount_str = row[amount_col]
            amount = parse_money(amount_str) if amount_str else None
        else:
            # 如果没有找到明确的金额列，则尝试在行中查找最大数值
            amount = find_largest_amount_in_row(row)
        
        if amount is not None:
            # 检查是否是口径行（如"营业收入合计"）
            full_row_text = "".join([str(cell) if cell else "" for cell in row]).strip()
            if any(preamble in full_row_text for preamble in ["营业收入合计", "主营业务收入", "其他业务收入"]):
                continue
                
            item = {
                "name": name_cell.strip(),
                "revenue_yuan": amount
            }
            
            # 避免重复添加
            if item not in results[current_dimension]:
                results[current_dimension].append(item)


def find_amount_column(table):
    """找到金额列索引"""
    if not table:
        return None
    
    num_cols = max(len(row) for row in table) if table else 0
    if num_cols == 0:
        return None
    
    # 统计每列的数值单元格数量
    col_scores = []
    for col_idx in range(num_cols):
        score = 0
        for row in table:
            if col_idx < len(row) and row[col_idx]:
                cell_value = row[col_idx]
                parsed = parse_money(cell_value)
                if parsed is not None and parsed > 1000:  # 金额通常较大
                    score += 1
        col_scores.append(score)
    
    # 返回数值最多且数值较大的列
    best_col = None
    best_score = 0
    for i, score in enumerate(col_scores):
        if score > best_score:
            # 验证该列确实包含大额数字
            sample_values = []
            for row in table:
                if i < len(row) and row[i]:
                    val = parse_money(row[i])
                    if val is not None:
                        sample_values.append(val)
            
            if sample_values and max(sample_values) > 1000000:  # 至少有百万级别的金额
                best_score = score
                best_col = i
    
    return best_col


def find_name_cell_in_row(row):
    """在行中找到名称单元格"""
    for cell in row:
        if cell and cell.strip():
            # 排除数值和百分比
            stripped = cell.strip()
            if not is_numeric_content(stripped) and '%' not in stripped:
                return stripped
    return None


def is_numeric_content(text):
    """检查文本是否为数值内容"""
    text = text.replace(',', '').replace('，', '').strip()
    if not text:
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def find_largest_amount_in_row(row):
    """在行中找到最大的金额值"""
    amounts = []
    for cell in row:
        if cell:
            parsed = parse_money(cell)
            if parsed is not None:
                amounts.append(parsed)
    
    return max(amounts) if amounts else None


def remove_duplicate_entries(items):
    """移除可能的重复条目（父子关系）"""
    if len(items) <= 1:
        return items
    
    # 按金额降序排列
    sorted_items = sorted(items, key=lambda x: x["revenue_yuan"], reverse=True)
    
    # 检查是否存在父子关系（某项等于其他几项之和）
    to_remove = set()
    
    for i, item in enumerate(sorted_items):
        current_amount = item["revenue_yuan"]
        # 检查后续较小的项是否加起来约等于当前项
        temp_sum = 0
        temp_indices = []
        
        for j in range(i + 1, len(sorted_items)):
            if j not in to_remove:
                temp_sum += sorted_items[j]["revenue_yuan"]
                temp_indices.append(j)
                
                # 如果和接近当前项（允许小误差），则当前项可能是聚合项
                if abs(temp_sum - current_amount) <= current_amount * 0.02:
                    # 移除当前聚合项，保留子项
                    to_remove.add(i)
                    break
                elif temp_sum > current_amount * 1.02:
                    # 超过了，停止累加
                    break
    
    # 构建结果列表
    result = []
    for i, item in enumerate(sorted_items):
        if i not in to_remove:
            result.append(item)
    
    return result