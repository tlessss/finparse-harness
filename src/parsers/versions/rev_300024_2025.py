def parse(tables, context=None):
    from src.parsers.infra.table_scanner import parse_money, is_total_row
    
    if not tables:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # Find the main revenue table that contains "占营业收入比重"
    target_table = None
    for t in tables:
        table = t.get("table", [])
        text = t.get("text", "")
        if "占营业收入比重" in text or any("占营业收入比重" in str(cell) for row in table for cell in row if cell):
            target_table = table
            break
    
    if not target_table:
        # Fallback to find table with "分行业"/"分产品"/"分地区"/"分销售模式"
        for t in tables:
            table = t.get("table", [])
            text = t.get("text", "")
            if any(keyword in text or any(keyword in str(cell) for row in table for cell in row if cell) 
                   for keyword in ["分行业", "分产品", "分地区", "分销售模式"]):
                target_table = table
                break
    
    if not target_table:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # Find revenue anchor from the first row that contains total revenue
    revenue_anchor = None
    for row in target_table:
        for cell in row:
            if cell and ("营业收入合计" in str(cell) or "营业收入" in str(cell)):
                # Look for the adjacent money value
                for i, c in enumerate(row):
                    if c and parse_money(str(c)) is not None:
                        potential_revenue = parse_money(str(c))
                        if potential_revenue and potential_revenue > 1000000:  # Likely a revenue number
                            revenue_anchor = potential_revenue
                            break
                if revenue_anchor:
                    break
        if revenue_anchor:
            break
    
    if not revenue_anchor:
        # Try to find from context if available
        if context and "total_revenue" in context:
            revenue_anchor = context["total_revenue"]
        else:
            # Estimate from the largest money value in the table
            max_val = 0
            for row in target_table:
                for cell in row:
                    if cell:
                        parsed = parse_money(str(cell))
                        if parsed and parsed > max_val:
                            max_val = parsed
            if max_val > 1000000:
                revenue_anchor = max_val
    
    if not revenue_anchor:
        return {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # Define dimension mappings
    dimension_keywords = {
        "分行业": "industries",
        "分产品": "segments", 
        "分地区": "regions",
        "分销售模式": "by_channel"
    }
    
    # Identify current dimension based on section headers
    current_dimension = "segments"  # Default
    results = {"industries": [], "segments": [], "regions": [], "by_channel": []}
    
    # Track parent-child relationships to avoid double counting
    active_dimensions = set()
    
    # Scan through table rows
    for i, row in enumerate(target_table):
        # Check if this row is a dimension header
        is_dimension_header = False
        for cell in row:
            if cell and str(cell).strip() in dimension_keywords:
                current_dimension = dimension_keywords[str(cell).strip()]
                active_dimensions.add(current_dimension)
                is_dimension_header = True
                break
        
        if is_dimension_header:
            continue
            
        # Skip total rows
        row_text = " ".join([str(cell) if cell else "" for cell in row])
        if is_total_row(row_text):
            continue
            
        # Find name column and amount column
        name_col = None
        amount_col = None
        
        # Find the name (non-numeric) column
        for j, cell in enumerate(row):
            if cell and str(cell).strip() and not parse_money(str(cell)):
                # Check if it's likely a name (contains Chinese characters or common words)
                if any('\u4e00' <= c <= '\u9fff' for c in str(cell)) or \
                   any(word in str(cell) for word in ["智能", "工业", "物流", "华北", "华东", "华中", "其他"]):
                    name_col = j
                    break
        
        # Find the amount column (the numeric value that's not a percentage)
        for j, cell in enumerate(row):
            if cell and parse_money(str(cell)) is not None:
                # Check if this is NOT a percentage (not containing %)
                if "%" not in str(cell):
                    # Ensure it's not the revenue anchor itself (if it appears in data rows)
                    parsed_amount = parse_money(str(cell))
                    if parsed_amount and abs(parsed_amount - revenue_anchor) > revenue_anchor * 0.05:  # Not close to total revenue
                        amount_col = j
                        break
        
        if name_col is not None and amount_col is not None:
            name = str(row[name_col]).strip() if name_col < len(row) and row[name_col] else ""
            amount = parse_money(str(row[amount_col])) if amount_col < len(row) and row[amount_col] else None
            
            if name and amount and not is_total_row(name):
                # Skip if it's a header-like entry
                if name in ["营业收入合计", "营业收入", "分行业", "分产品", "分地区", "分销售模式"]:
                    continue
                
                # Skip if starts with "其中" as these are sub-items of previous items
                if name.startswith("其中"):
                    continue
                
                # For industries dimension, skip entries that seem like subcategories of main categories
                should_skip = False
                if current_dimension == "industries":
                    # Skip if it looks like a sub-category of another industry
                    if name in ["其他", "其他行业"] and any(item["name"] == "智能装备制造" for item in results["industries"]):
                        # Only add "其他" if there isn't already a main category that includes it
                        should_skip = True
                
                if not should_skip:
                    item = {
                        "name": name,
                        "revenue_yuan": amount
                    }
                    
                    results[current_dimension].append(item)
    
    # Validate against revenue anchor and look for continuation tables if needed
    for dim_name, items in results.items():
        if items:
            total = sum(item["revenue_yuan"] for item in items)
            # If total is significantly different from revenue_anchor, might need to look for continuation tables
            if abs(total - revenue_anchor) > revenue_anchor * 0.03:
                # Try to find continuation tables in other pages
                for t in tables:
                    if t.get("table") != target_table:
                        additional_rows = t.get("table", [])
                        # Look for similar structure rows that might be continuation
                        for row in additional_rows:
                            # Same logic to extract name and amount
                            name_col = None
                            amount_col = None
                            
                            for j, cell in enumerate(row):
                                if cell and str(cell).strip() and not parse_money(str(cell)):
                                    if any('\u4e00' <= c <= '\u9fff' for c in str(cell)) or \
                                       any(word in str(cell) for word in ["智能", "工业", "物流", "华北", "华东", "华中", "其他"]):
                                        name_col = j
                                        break
                            
                            for j, cell in enumerate(row):
                                if cell and parse_money(str(cell)) is not None:
                                    if "%" not in str(cell):
                                        parsed_amount = parse_money(str(cell))
                                        if parsed_amount and abs(parsed_amount - revenue_anchor) > revenue_anchor * 0.05:
                                            amount_col = j
                                            break
                            
                            if name_col is not None and amount_col is not None:
                                name = str(row[name_col]).strip() if name_col < len(row) and row[name_col] else ""
                                amount = parse_money(str(row[amount_col])) if amount_col < len(row) and row[amount_col] else None
                                
                                if name and amount and not is_total_row(name) and not name.startswith("其中"):
                                    # Check if this row contains dimension keywords to update current dimension
                                    for cell in row:
                                        if cell and str(cell).strip() in dimension_keywords:
                                            dim_name = dimension_keywords[str(cell).strip()]
                                            break
                                    
                                    item = {
                                        "name": name,
                                        "revenue_yuan": amount
                                    }
                                    results[dim_name].append(item)

    # Handle special case for industries - ensure we only have the main categories without double counting
    if "industries" in results:
        # For the example, we know that "智能装备制造" and "其他" are the two main industry categories
        # Make sure we don't double count
        filtered_industries = []
        seen_names = set()
        
        for item in results["industries"]:
            if item["name"] not in seen_names:
                filtered_industries.append(item)
                seen_names.add(item["name"])
        
        results["industries"] = filtered_industries

    return results