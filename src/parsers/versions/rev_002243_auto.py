from src.parsers.infra.table_scanner import parse_money, is_total_row

def parse(tables, context=None):
    # 寻找包含营收分解信息的表格
    target_tables = []
    for t in tables:
        table = t.get("table", [])
        if not table:
            continue
        
        # 检查表头是否包含相关关键词
        first_rows = table[:3]  # 检查前几行
        text_content = ""
        for row in first_rows:
            for cell in row:
                if cell:
                    text_content += cell
                    
        if any(keyword in text_content for keyword in ["分行业", "分产品", "分地区", "分销售模式", "营业收入构成", "占营业收入比重"]):
            target_tables.append(t)
    
    # 如果没有找到目标表格，尝试更宽松的匹配
    if not target_tables:
        for t in tables:
            table = t.get("table", [])
            if not table:
                continue
            full_text = ""
            for row in table:
                for cell in row:
                    if cell:
                        full_text += cell
            if any(keyword in full_text for keyword in ["营业收入", "主营业务"]):
                target_tables.append(t)
    
    # 解析每个候选表格
    result = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    for table_data in target_tables:
        table = table_data.get("table", [])
        if not table:
            continue
            
        # 查找维度标记并解析
        current_dimension = None
        section_labels = {
            "分行业": "industries",
            "分产品": "segments", 
            "分地区": "regions",
            "分销售模式": "by_channel",
            "主营业务分行业": "industries",
            "主营业务分产品": "segments",
            "主营业务分地区": "regions", 
            "主营业务分销售模式": "by_channel"
        }
        
        # 先扫描整个表格找出所有维度标记的位置
        sections = []
        for row_idx, row in enumerate(table):
            found_section = None
            for cell in row:
                if cell and any(label in cell for label in section_labels.keys()):
                    for label, dim in section_labels.items():
                        if label in cell:
                            found_section = dim
                            break
            sections.append(found_section)
        
        # 确定名称列和金额列
        name_col = None
        amount_col = None
        
        # 找到第一个包含中文文本的列作为名称列
        for col_idx in range(max(len(row) for row in table) if table else 0):
            text_count = 0
            for row in table:
                if col_idx < len(row) and row[col_idx]:
                    cell = row[col_idx]
                    if any('\u4e00' <= c <= '\u9fff' for c in cell):
                        text_count += 1
            if text_count >= 2:  # 至少2个中文文本单元
                name_col = col_idx
                break
                
        # 找到第一个包含大额数字的列作为金额列
        for col_idx in range(max(len(row) for row in table) if table else 0):
            if col_idx == name_col:
                continue
            money_count = 0
            for row in table:
                if col_idx < len(row) and row[col_idx]:
                    cell = row[col_idx]
                    parsed = parse_money(cell)
                    if parsed is not None and parsed > 1000:  # 大额数字
                        money_count += 1
            if money_count >= 2:  # 至少2个金额单元
                amount_col = col_idx
                break
        
        # 如果还是没找到合适的列，尝试其他策略
        if name_col is None:
            name_col = 0
        if amount_col is None:
            # 尝试找包含"营业收入"的列附近的大额数字列
            for row_idx, row in enumerate(table):
                for col_idx, cell in enumerate(row):
                    if cell and "营业收入" in cell:
                        # 在该列右侧寻找金额列
                        for right_col in range(col_idx + 1, len(row)):
                            parsed = parse_money(row[right_col])
                            if parsed is not None and parsed > 1000:
                                amount_col = right_col
                                break
                        if amount_col is not None:
                            break
                if amount_col is not None:
                    break
        
        # 开始解析数据
        current_dim = "segments"  # 默认维度
        for row_idx, row in enumerate(table):
            # 检查是否是新的维度标记
            section_mark = sections[row_idx]
            if section_mark:
                current_dim = section_mark
                continue
                
            # 跳过汇总行
            if row and len(row) > 0:
                first_cell = row[0] if row[0] else ""
                if is_total_row(first_cell):
                    continue
                    
            # 提取名称和金额
            name = ""
            amount = None
            
            if name_col is not None and name_col < len(row) and row[name_col]:
                name = row[name_col].strip().replace("\n", "")
                
            if amount_col is not None and amount_col < len(row) and row[amount_col]:
                amount = parse_money(row[amount_col])
            
            # 跳过无效行
            if not name or not amount or amount <= 0:
                continue
                
            # 跳过一些前导行（如"营业收入合计"等）
            if any(preamble in name for preamble in ["营业收入合计", "主营业务收入", "营业收入", "合计", "小计", "总计"]):
                continue
                
            # 跳过"其中："类的子项，避免重复计算
            if name.startswith("其中") or name.startswith("其中："):
                continue
                
            # 添加到对应维度
            item = {
                "name": name,
                "revenue_yuan": amount,
                "ratio_pct": None  # 不解析占比，由下游计算
            }
            
            if current_dim in result:
                # 检查是否已经存在相同名称的项，避免重复添加
                exists = False
                for existing_item in result[current_dim]:
                    if existing_item["name"] == name:
                        exists = True
                        break
                if not exists:
                    result[current_dim].append(item)
    
    # 最后检查是否有跨页续表的情况，合并相关信息
    # 这里简化处理，直接返回当前解析结果
    
    return result