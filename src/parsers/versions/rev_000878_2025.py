def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    # 营业收入锚值
    total_revenue = 179542025926  # 179,542,025,926
    
    # 维度映射
    dimension_mapping = {
        "分行业": "industries", "按行业": "industries",
        "分产品": "segments", "按产品": "segments",
        "分地区": "regions", "按地区": "regions",
        "分销售模式": "by_channel", "分销售渠道": "by_channel",
        "销售模式": "by_channel", "按销售模式": "by_channel",
        "销售渠道": "by_channel", "按销售渠道": "by_channel",
    }
    
    # 初始化结果
    result = {
        "industries": [],
        "segments": [],
        "regions": [],
        "by_channel": []
    }
    
    # 查找包含营收相关关键词的表格
    revenue_tables = []
    for table_info in tables:
        table = table_info["table"]
        text = table_info.get("text", "")
        
        # 检查表头是否包含营收相关关键词
        header_row = table[0] if table else []
        header_text = " ".join([str(cell) for cell in header_row if cell])
        full_text = header_text + " " + text
        
        if any(keyword in full_text for keyword in ["占营业收入比重", "营业收入比重", "占比", "营业收入", "分行业", "分产品", "分地区", "分销售模式"]):
            revenue_tables.append(table_info)
    
    # 遍历所有营收相关表格
    for table_info in revenue_tables:
        table = table_info["table"]
        if not table:
            continue
            
        # 寻找维度标记行
        current_dimension = None
        name_col = None
        amount_col = None
        
        # 找到金额列：通常是数值最大的列
        col_sums = []
        for col_idx in range(max(len(row) for row in table) if table else 0):
            col_sum = 0
            for row in table:
                if col_idx < len(row) and row[col_idx]:
                    parsed = parse_money(str(row[col_idx]))
                    if parsed is not None:
                        col_sum += abs(parsed)
            col_sums.append((col_idx, col_sum))
        
        # 找到最大金额列作为金额列
        if col_sums:
            amount_col = max(col_sums, key=lambda x: x[1])[0]
        
        # 找到名称列：非金额列中包含文本的列
        if amount_col is not None:
            for col_idx in range(max(len(row) for row in table) if table else 0):
                text_count = 0
                for row in table:
                    if col_idx < len(row) and row[col_idx] and isinstance(row[col_idx], str):
                        if any('\u4e00' <= c <= '\u9fff' for c in row[col_idx]):
                            text_count += 1
                if text_count > 0:
                    name_col = col_idx
                    break
        
        if name_col is None:
            for col_idx in range(max(len(row) for row in table) if table else 0):
                text_count = 0
                for row in table:
                    if col_idx < len(row) and row[col_idx] and isinstance(row[col_idx], str):
                        if any('\u4e00' <= c <= '\u9fff' for c in row[col_idx]):
                            text_count += 1
                if text_count > 0:
                    name_col = col_idx
                    break
        
        if amount_col is None:
            for col_idx in range(max(len(row) for row in table) if table else 0):
                num_count = 0
                for row in table:
                    if col_idx < len(row) and row[col_idx]:
                        parsed = parse_money(str(row[col_idx]))
                        if parsed is not None:
                            num_count += 1
                if num_count > 0:
                    amount_col = col_idx
                    break
        
        # 遍历表格行进行解析
        for row_idx, row in enumerate(table):
            # 检查是否为维度切换行
            for cell in row:
                if cell and str(cell).strip() in dimension_mapping:
                    current_dimension = dimension_mapping[str(cell).strip()]
                    break
                # 检查是否以维度词结尾（如"主营业务分行业"）
                elif cell and str(cell).strip():
                    for dim_key, dim_val in dimension_mapping.items():
                        if str(cell).strip().endswith(dim_key) and not is_total_row(str(cell).strip()):
                            current_dimension = dim_val
                            break
                    if current_dimension:
                        break
            
            if current_dimension is None:
                continue
                
            # 获取名称和金额
            name = None
            amount = None
            
            if name_col is not None and name_col < len(row):
                name = row[name_col]
                
            if amount_col is not None and amount_col < len(row):
                amount_str = row[amount_col]
                amount = parse_money(str(amount_str)) if amount_str else None
            
            # 跳过无效行
            if not name or not amount or is_total_row(str(name)):
                continue
                
            # 检查是否为"其中"行，避免重复计算
            if str(name).startswith("其中") or str(name).startswith("其中："):
                continue
                
            # 添加到对应维度
            item = {
                "name": str(name).strip(),
                "revenue_yuan": amount
            }
            
            # 避免重复添加
            existing_names = [item["name"] for item in result[current_dimension]]
            if item["name"] not in existing_names:
                result[current_dimension].append(item)

    # 特别处理候选表2（页220）的数据 - 有色金属冶炼及压延产品分类
    for table_info in tables:
        table = table_info["table"]
        if not table:
            continue
            
        # 检查是否是目标表格（包含产品分类的表格）
        has_product_keywords = False
        for row in table:
            for cell in row:
                if cell and isinstance(cell, str):
                    if "有色金属冶炼及压延" in cell or any(prod in cell for prod in ["电解铜", "贵金属", "硫酸", "其他产品"]):
                        has_product_keywords = True
                        break
            if has_product_keywords:
                break
        
        if has_product_keywords:
            # 根据表格结构，找到产品名称和对应的收入金额
            # 表格结构：第一行为列标题，包括"有色金属冶炼及压延"、"营业收入"、"营业成本"等
            # 实际数据从后续行开始
            for row_idx, row in enumerate(table):
                if len(row) >= 7:  # 确保有足够的列
                    # 检查是否包含产品名称
                    for col_idx, cell in enumerate(row):
                        if cell and isinstance(cell, str):
                            cell_content = cell.strip()
                            if cell_content in ["电解铜", "贵金属", "硫酸", "其他产品"]:
                                # 这个单元格是产品名称，在同一行的第5列（索引4）是营业收入
                                # 根据表格结构，营业收入列在第5列（索引4）和第7列（索引6）都有
                                # 对于产品行，通常在有色金属冶炼及压延列下有对应数值
                                revenue_cell = row[6] if len(row) > 6 else None  # 合计列的营业收入
                                if revenue_cell:
                                    parsed_amount = parse_money(str(revenue_cell))
                                    if parsed_amount is not None:
                                        item = {
                                            "name": cell_content,
                                            "revenue_yuan": parsed_amount
                                        }
                                        
                                        existing_names = [item["name"] for item in result["segments"]]
                                        if item["name"] not in existing_names and "其中" not in item["name"]:
                                            result["segments"].append(item)
                            
                            elif cell_content == "中国大陆":
                                # 查找地区对应的收入
                                revenue_cell = row[6] if len(row) > 6 else None  # 合计列的营业收入
                                if revenue_cell:
                                    parsed_amount = parse_money(str(revenue_cell))
                                    if parsed_amount is not None:
                                        item = {
                                            "name": cell_content,
                                            "revenue_yuan": parsed_amount
                                        }
                                        
                                        existing_names = [item["name"] for item in result["regions"]]
                                        if item["name"] not in existing_names and "其中" not in item["name"]:
                                            result["regions"].append(item)

    # 检查是否有遗漏的行业信息 - 查找分行业表格
    for table_info in tables:
        table = table_info["table"]
        if not table:
            continue
            
        # 检查表头是否包含分行业相关信息
        header_row = table[0] if table else []
        header_text = " ".join([str(cell) for cell in header_row if cell])
        
        if "分行业" in header_text or "行业" in header_text:
            # 寻找维度标记行
            current_dimension = "industries"
            name_col = None
            amount_col = None
            
            # 找到金额列：通常是数值最大的列
            col_sums = []
            for col_idx in range(max(len(row) for row in table) if table else 0):
                col_sum = 0
                for row in table:
                    if col_idx < len(row) and row[col_idx]:
                        parsed = parse_money(str(row[col_idx]))
                        if parsed is not None:
                            col_sum += abs(parsed)
                col_sums.append((col_idx, col_sum))
            
            # 找到最大金额列作为金额列
            if col_sums:
                amount_col = max(col_sums, key=lambda x: x[1])[0]
            
            # 找到名称列：非金额列中包含文本的列
            for col_idx in range(max(len(row) for row in table) if table else 0):
                text_count = 0
                for row in table:
                    if col_idx < len(row) and row[col_idx] and isinstance(row[col_idx], str):
                        if any('\u4e00' <= c <= '\u9fff' for c in row[col_idx]):
                            text_count += 1
                if text_count > 0 and col_idx != amount_col:
                    name_col = col_idx
                    break
            
            # 遍历表格行进行解析
            for row_idx, row in enumerate(table):
                # 获取名称和金额
                name = None
                amount = None
                
                if name_col is not None and name_col < len(row):
                    name = row[name_col]
                    
                if amount_col is not None and amount_col < len(row):
                    amount_str = row[amount_col]
                    amount = parse_money(str(amount_str)) if amount_str else None
                
                # 跳过无效行
                if not name or not amount or is_total_row(str(name)):
                    continue
                    
                # 检查是否为"其中"行，避免重复计算
                if str(name).startswith("其中") or str(name).startswith("其中："):
                    continue
                    
                # 添加到对应维度
                item = {
                    "name": str(name).strip(),
                    "revenue_yuan": amount
                }
                
                # 避免重复添加
                existing_names = [item["name"] for item in result[current_dimension]]
                if item["name"] not in existing_names:
                    result[current_dimension].append(item)

    # 最后清理：移除可能重复添加的项目并处理父子关系
    for dimension in result:
        unique_items = []
        seen_names = set()
        
        for item in result[dimension]:
            name = item["name"]
            # 跳过"其中"开头的项目，避免重复计算
            if name.startswith("其中") or name.startswith("其中："):
                continue
            
            # 检查是否已存在相同名称
            if name not in seen_names:
                seen_names.add(name)
                unique_items.append(item)
        
        result[dimension] = unique_items

    return result