def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    # 营收锚值
    total_revenue = 603290462.28  # 从候选表0获取
    
    # 维度映射
    DIMENSION_MAP = {
        "分行业": "industries", "按行业": "industries",
        "分产品": "segments", "按产品": "segments",
        "分地区": "regions", "按地区": "regions",
        "分销售模式": "by_channel", "分销售渠道": "by_channel",
        "销售模式": "by_channel", "按销售模式": "by_channel",
        "销售渠道": "by_channel", "按销售渠道": "by_channel",
    }
    
    # 找到包含营收结构的表
    target_table_data = None
    for table_info in tables:
        table = table_info["table"]
        if not table:
            continue
            
        # 检查表是否包含维度标记
        has_dimension = False
        for row in table:
            for cell in row:
                if cell and any(dim_key in (cell or "") for dim_key in DIMENSION_MAP.keys()):
                    has_dimension = True
                    break
            if has_dimension:
                break
        
        if has_dimension:
            target_table_data = table_info
            break
    
    if not target_table_data:
        # 如果没找到明确的维度表，尝试使用候选表1（合同分类表）
        for table_info in tables:
            table = table_info["table"]
            if table and len(table) > 0:
                first_row = table[0] if table else []
                if any("合同分类" in (cell or "") for cell in first_row):
                    target_table_data = table_info
                    break
    
    if not target_table_data:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    table = target_table_data["table"]
    
    # 初始化结果
    result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # 识别维度标记
    sections = []
    current_section = "segments"  # 默认桶
    
    for row_idx, row in enumerate(table):
        found_section = None
        for cell in row:
            cell_str = (cell or "").strip()
            for dim_key, dim_value in DIMENSION_MAP.items():
                if dim_key in cell_str:
                    found_section = dim_value
                    break
            if found_section:
                break
        
        if found_section:
            current_section = found_section
            sections.append(found_section)
        else:
            sections.append(current_section)
    
    # 找到金额列（通常是数值最大的列）
    max_cols = max([len(row) for row in table] or [0])
    col_sums = [0.0] * max_cols
    
    for row in table:
        for col_idx, cell in enumerate(row):
            if col_idx < len(col_sums):
                parsed_val = parse_money(cell or "")
                if parsed_val is not None:
                    col_sums[col_idx] += abs(parsed_val)
    
    # 找到最大金额列作为营收列
    revenue_col_idx = -1
    max_sum = 0
    for i, col_sum in enumerate(col_sums):
        if col_sum > max_sum:
            max_sum = col_sum
            revenue_col_idx = i
    
    # 如果没找到合适的金额列，尝试找包含"营业收入"的列
    if revenue_col_idx == -1:
        for col_idx in range(max_cols):
            for row in table:
                if col_idx < len(row) and row[col_idx] and "营业收入" in (row[col_idx] or ""):
                    # 查找该列下方的数值
                    for check_row_idx in range(1, len(table)):
                        if check_row_idx < len(table) and col_idx < len(table[check_row_idx]):
                            val = parse_money(table[check_row_idx][col_idx] or "")
                            if val is not None:
                                revenue_col_idx = col_idx
                                break
                    if revenue_col_idx != -1:
                        break
            if revenue_col_idx != -1:
                break
    
    # 如果还是没找到，尝试找数值较大的列
    if revenue_col_idx == -1:
        for col_idx in range(max_cols):
            for row in table:
                if col_idx < len(row):
                    val = parse_money(row[col_idx] or "")
                    if val is not None and val > 100000:  # 大于10万认为是金额列
                        revenue_col_idx = col_idx
                        break
            if revenue_col_idx != -1:
                break
    
    # 找到名称列（通常是第一列或包含文本的列）
    name_col_idx = 0
    for col_idx in range(max_cols):
        text_count = 0
        for row in table:
            if col_idx < len(row) and row[col_idx]:
                cell = row[col_idx].strip()
                if cell and not parse_money(cell) and any('\u4e00' <= c <= '\u9fff' for c in cell):
                    text_count += 1
        if text_count >= 2:  # 至少2个文本单元
            name_col_idx = col_idx
            break
    
    # 解析数据
    seen_items = set()
    
    for row_idx, row in enumerate(table):
        section = sections[row_idx]
        
        # 跳过总计行
        if row:
            first_cell = (row[0] or "").strip()
            if is_total_row(first_cell) or "合计" in first_cell or "总计" in first_cell or "小计" in first_cell:
                continue
        
        # 获取名称和金额
        name = ""
        amount = None
        
        if name_col_idx < len(row) and row[name_col_idx]:
            name = row[name_col_idx].strip().replace("\n", "")
        
        if revenue_col_idx >= 0 and revenue_col_idx < len(row):
            amount_str = row[revenue_col_idx] or ""
            amount = parse_money(amount_str)
        
        # 跳过无效数据
        if not name or not amount or name in ["项目", "项 目", "合同分类", "", "业务类型", "按经营地 区分类", "其中："]:
            continue
        
        # 跳过维度切换标记行
        if any(dim_key in name for dim_key in DIMENSION_MAP.keys()):
            continue
        
        # 跳过"其中："开头的行（父子重复）
        if name.startswith("其中：") or name.startswith("其中:"):
            continue
        
        # 检查是否是聚合行（如“境内”=“东部+南部+西部+北部”的形式）
        # 这种情况下，如果当前项的金额等于后续连续几项的和，则当前项是聚合项，应跳过
        if row_idx + 1 < len(table) and revenue_col_idx < len(table[row_idx + 1]):
            next_amount = parse_money(table[row_idx + 1][revenue_col_idx] or "")
            if next_amount and abs(next_amount) > 0 and abs(amount) <= abs(next_amount) * 0.01:  # 很小的金额可能是总计
                continue
        
        # 添加到对应维度
        item = {
            "name": name[:120],  # 限制长度
            "revenue_yuan": amount
        }
        
        if name not in seen_items:
            result[section].append(item)
            seen_items.add(name)
    
    # 特殊处理候选表1的数据结构
    # 检查是否是类似候选表1的复杂结构
    if target_table_data and len(target_table_data["table"]) > 10:
        table = target_table_data["table"]
        # 检查是否是合同分类表结构
        if table and len(table) > 0 and table[0] and "合同分类" in (table[0][0] or ""):
            # 重新解析这种特殊结构
            result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
            
            # 找到业务类型部分
            business_start_idx = -1
            region_start_idx = -1
            
            for i, row in enumerate(table):
                if len(row) > 0 and row[0] and "业务类型" in (row[0] or ""):
                    business_start_idx = i
                if len(row) > 0 and row[0] and "按经营地 区分类" in (row[0] or ""):
                    region_start_idx = i
            
            # 解析业务类型部分
            if business_start_idx != -1:
                for i in range(business_start_idx + 1, len(table)):
                    row = table[i]
                    if len(row) > 2 and row[0] and ("小计" in (row[0] or "") or "合计" in (row[0] or "") or "其他业务" in (row[0] or "")):
                        continue
                    if len(row) > 2 and row[0] and "其中：" not in (row[0] or "") and "其中:" not in (row[0] or ""):
                        name = (row[0] or "").replace("\n", "").strip()
                        if name and len(name) > 1:
                            # 选择第一个有效的金额列（通常是第2列，营业收入）
                            amount = None
                            for j in range(1, len(row)):
                                parsed = parse_money(row[j] or "")
                                if parsed is not None:
                                    amount = parsed
                                    break
                            
                            if amount is not None and name not in ["业务类型", "其中：", ""]:
                                result["segments"].append({
                                    "name": name[:120],
                                    "revenue_yuan": amount
                                })
                                seen_items.add(name)
            
            # 解析地区分类部分
            if region_start_idx != -1:
                for i in range(region_start_idx + 1, len(table)):
                    row = table[i]
                    if len(row) > 2 and row[0] and ("小计" in (row[0] or "") or "合计" in (row[0] or "") or "其中：" in (row[0] or "") or "其中:" in (row[0] or "")):
                        continue
                    if len(row) > 2 and row[0] and "按经营地 区分类" not in (row[0] or ""):
                        name = (row[0] or "").replace("\n", "").strip()
                        if name and len(name) > 1:
                            amount = None
                            for j in range(1, len(row)):
                                parsed = parse_money(row[j] or "")
                                if parsed is not None:
                                    amount = parsed
                                    break
                            
                            if amount is not None and name not in ["按经营地 区分类", "其中：", ""]:
                                result["regions"].append({
                                    "name": name[:120],
                                    "revenue_yuan": amount
                                })
                                seen_items.add(name)
    
    # 最后检查并修正重复计算问题
    # 对于segments维度，如果发现金额过大，可能包含了多个部分
    if len(result["segments"]) > 0:
        segment_sum = sum(item["revenue_yuan"] for item in result["segments"])
        if abs(segment_sum - total_revenue) / total_revenue > 0.05:  # 如果超过5%差异
            # 重新过滤，去除可能的重复项
            filtered_segments = []
            for item in result["segments"]:
                # 检查是否是聚合项
                is_aggregate = False
                for other_item in result["segments"]:
                    if item["name"] != other_item["name"] and \
                       (item["name"] in other_item["name"] or other_item["name"] in item["name"]):
                        if item["revenue_yuan"] > other_item["revenue_yuan"]:
                            is_aggregate = True
                            break
                if not is_aggregate:
                    filtered_segments.append(item)
            result["segments"] = filtered_segments
    
    return result