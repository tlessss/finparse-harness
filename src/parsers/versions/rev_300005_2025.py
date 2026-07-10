from src.parsers.infra.table_scanner import parse_money, is_total_row

def parse(tables, context=None):
    # 寻找成本构成相关的表格
    target_tables = []
    for t in tables:
        text = " ".join(c for row in t["table"] for c in row if c)
        if any(keyword in text for keyword in ["占营业成本比重", "营业成本构成", "成本构成"]):
            target_tables.append(t)
    
    if not target_tables:
        # 如果没找到明确的成本构成表，尝试从所有表格中寻找
        target_tables = tables
    
    # 寻找营业成本总额作为锚点
    total_cost = None
    for t in tables:
        for row in t["table"]:
            row_text = " ".join(c for c in row if c)
            if "营业成本" in row_text and any(kw in row_text for kw in ["合计", "总计", "小计"]):
                # 找到金额列
                for cell in row:
                    parsed = parse_money(cell)
                    if parsed is not None:
                        total_cost = parsed
                        break
            if total_cost is not None:
                break
        if total_cost is not None:
            break
    
    # 如果没找到营业成本总额，尝试从利润表结构中找
    if total_cost is None:
        for t in tables:
            for row in t["table"]:
                if not row:
                    continue
                first_cell = (row[0] if row[0] else "").strip()
                if "主营业务" in first_cell or "其他业务" in first_cell:
                    # 查找成本列（通常是倒数第二列）
                    for i in range(len(row)-1, -1, -1):
                        cell = row[i]
                        if cell and "成本" in cell:
                            # 找到同行的金额
                            for j in range(len(row)):
                                if j != i:
                                    parsed = parse_money(row[j])
                                    if parsed is not None:
                                        total_cost = parsed
                                        break
                            if total_cost is not None:
                                break
                if total_cost is not None:
                    break
            if total_cost is not None:
                break
    
    # 如果还是没找到，则使用上下文中的信息
    if total_cost is None and context:
        total_cost = context.get('operating_cost')
    
    results = []
    
    for table_data in target_tables:
        table = table_data["table"]
        
        # 检查表头是否包含成本构成相关信息
        header_row_idx = None
        for i, row in enumerate(table):
            row_text = " ".join(c for c in row if c)
            if any(keyword in row_text for keyword in ["占营业成本比重", "营业成本构成", "成本构成", "成本项目"]):
                header_row_idx = i
                break
        
        if header_row_idx is None:
            continue
            
        # 确定列索引
        header_row = table[header_row_idx]
        name_col_idx = -1
        amount_col_idx = -1
        
        # 寻找名称列和金额列
        for i, cell in enumerate(header_row):
            if cell and any(keyword in cell for keyword in ["项目", "成本构成", "构成项目", "类别"]):
                name_col_idx = i
            elif cell and any(keyword in cell for keyword in ["金额", "成本"]):
                # 找到金额列，选择第一个数值列
                for j in range(i, min(len(header_row), i+3)):
                    if j >= len(table[header_row_idx]) or not table[header_row_idx][j]:
                        continue
                    if any(kw in table[header_row_idx][j] for kw in ["金额", "成本"]) and \
                       not any(kw in table[header_row_idx][j] for kw in ["同比", "增减"]):
                        amount_col_idx = j
                        break
                if amount_col_idx != -1:
                    break
        
        # 如果没找到合适的列索引，尝试默认方式
        if name_col_idx == -1 or amount_col_idx == -1:
            # 默认第一列为名称，找数值最大的列为金额
            for i in range(len(table[header_row_idx])):
                if i < len(table[0]) and table[header_row_idx][i] and \
                   any(kw in table[header_row_idx][i] for kw in ["金额", "成本"]):
                    amount_col_idx = i
                    break
            # 找到后一列或前一列为名称
            if amount_col_idx != -1:
                if amount_col_idx > 0:
                    name_col_idx = amount_col_idx - 1
                else:
                    name_col_idx = amount_col_idx + 1
        
        # 遍历数据行
        for i in range(header_row_idx + 1, len(table)):
            row = table[i]
            if not row or is_total_row(row):
                continue
                
            if len(row) <= max(name_col_idx, amount_col_idx):
                continue
                
            name_cell = row[name_col_idx] if name_col_idx < len(row) else None
            amount_cell = row[amount_col_idx] if amount_col_idx < len(row) else None
            
            if not name_cell or not amount_cell:
                continue
                
            # 解析金额
            amount = parse_money(amount_cell)
            if amount is None:
                continue
                
            # 过滤掉合计、小计等汇总行
            name_clean = name_cell.strip()
            if any(keyword in name_clean.lower() for keyword in ["合计", "总计", "小计", "其中"]):
                continue
                
            # 检查是否为父子关系（如"其中：XX"）
            if "其中：" in name_clean or "其中:" in name_clean:
                continue
                
            results.append({
                "name": name_clean,
                "amount_yuan": amount,
                "ratio_pct": None
            })
    
    # 如果结果为空，尝试从其他表格中获取
    if not results:
        # 检查是否有主营业务收入和成本的表格
        for t in tables:
            table = t["table"]
            for row in table:
                if not row:
                    continue
                first_cell = (row[0] if row[0] else "").strip()
                
                if any(kw in first_cell for kw in ["主营业务", "其他业务"]):
                    # 查找成本列
                    for i, cell in enumerate(row):
                        if cell and "成本" in cell:
                            # 尝试解析该行的金额
                            for j in range(len(row)):
                                if j != i:
                                    parsed = parse_money(row[j])
                                    if parsed is not None:
                                        # 获取项目名称
                                        name = first_cell
                                        results.append({
                                            "name": name,
                                            "amount_yuan": parsed,
                                            "ratio_pct": None
                                        })
                                        break
    
    return results