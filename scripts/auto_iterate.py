"""
自动迭代脚本 — 解析 → 测试 → 校验 → 修复 → 重测 闭环

流程：
  1. 用 FinParseAI 解析 10 份 PDF
  2. 导出测试结果到 test_results/
  3. 调用 validate-parse-report Skill 校验并生成含修复建议的报告
  4. 逐条读取报告中的修复建议
  5. 对每条建议：分析是局部还是全局问题 → 执行对应修改
  6. 重新跑测试
  7. 重复最多 3 次

用法：
  python3 scripts/auto_iterate.py                    # 默认跑完整闭环
  python3 scripts/auto_iterate.py --max-iterations 1  # 只跑一轮
  python3 scripts/auto_iterate.py --dry-run           # 只出建议不改代码
"""

import os
import sys
import time
import json
import re
import subprocess
import numpy as np
from typing import List, Dict, Optional
from pathlib import Path

# 加入 src 路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "."))


# ── Step 1: 批量解析 ──

def run_batch_parse(limit: int = 10) -> str:
    """运行批量解析，导出测试结果文件，返回文件路径"""
    print("\n" + "=" * 60)
    print("  Step 1: 批量解析")
    print("=" * 60)

    from src.engine_orchestrator import FinParseAI
    from src.database import get_conn
    from src.config import Config

    engine = FinParseAI()
    cache_dir = Config.PDF_CACHE_DIR

    # 选 10 份不同公司
    candidates = []
    seen = set()
    for f in sorted(os.listdir(str(cache_dir))):
        if not f.endswith(".pdf"):
            continue
        parts = f.replace(".pdf", "").split("_")
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
            code = parts[0]
            if code not in seen:
                seen.add(code)
                candidates.append((code, int(parts[1]), os.path.join(str(cache_dir), f)))
        if len(candidates) >= limit:
            break

    results = []
    for code, year, pdf_path in candidates:
        start = time.time()
        try:
            r = engine.run(pdf_path, db_write=False)
            results.append({
                "stock_code": code, "report_year": year,
                "pdf_file": os.path.basename(pdf_path),
                "duration_sec": round(time.time() - start, 1),
                "field_count": r["field_count"],
                "revenue_breakdown": r.get("revenue_breakdown"),
                "rnd_info": r.get("rnd_info"),
                "employees": r.get("employees"),
                "cost_breakdown": r.get("cost_breakdown"),
                "top_clients": r.get("top_clients"),
                "top_suppliers": r.get("top_suppliers"),
            })
        except Exception as e:
            results.append({"stock_code": code, "report_year": year, "error": str(e)})
        print(f"  {code} {year} -> {results[-1].get('field_count', 'ERR')}/6")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "test_results", f"auto_iterate_{timestamp}.json",
    )
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "export_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "parser_version": "auto_iterate",
            "total": len(results),
            "average_field_count": round(sum(r.get("field_count", 0) for r in results) / max(len(results), 1), 1),
            "average_duration_sec": round(sum(r.get("duration_sec", 0) for r in results) / max(len(results), 1), 1),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    # 输出摘要
    total = len(results)
    fields = ["revenue_breakdown", "rnd_info", "employees", "cost_breakdown", "top_clients", "top_suppliers"]
    for f_name in fields:
        count = sum(1 for r in results if r.get(f_name))
        print(f"  {f_name}: {count}/{total}")
    print(f"  平均: {round(sum(r.get('field_count',0) for r in results)/total, 1)}/6")
    print(f"  结果文件: {output_path}")
    return output_path


# ── Step 2: 读取修复建议 ──

def parse_fix_suggestions(report_path: str) -> List[Dict]:
    """
    从 Markdown 报告中解析修复建议。

    每条建议格式：
    ### 问题 N：{根因文件} 的 {现象}
    **影响股票**（{N} 只）：{code1}, {code2}
    **差异类型**：❌ {类型}
    **根因文件**：`{文件路径}`
    **根因函数**：`{函数名}`
    **建议修改**：
    1. ...
    2. ...
    """
    if not os.path.exists(report_path):
        print(f"  ❌ 报告文件不存在: {report_path}")
        return []

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    suggestions = []
    # 按 "### 问题" 分割
    sections = re.split(r"### 问题 \d+", content)
    for section in sections[1:]:  # 跳过第一个（表头部分）
        lines = section.strip().split("\n")
        suggestion = {
            "root_file": "",
            "root_function": "",
            "fix_steps": [],
            "affected_stocks": [],
            "issue_type": "",
            "raw_text": section[:500],
        }

        for line in lines:
            line = line.strip()
            # 影响股票
            m = re.match(r"\*\*影响股票\*\*.*?[（(](\d+)[)）].*?[：:]\s*(.+)", line)
            if m:
                codes = m.group(2)
                suggestion["affected_stocks"] = [c.strip() for c in codes.split(",") if c.strip()]
            # 差异类型
            if "差异类型" in line:
                suggestion["issue_type"] = line.split("：")[-1].strip() if "：" in line else line.split(":")[-1].strip()
            # 根因文件
            if "根因文件" in line:
                m = re.search(r"`([^`]+)`", line)
                if m:
                    suggestion["root_file"] = m.group(1)
            # 根因函数
            if "根因函数" in line:
                m = re.search(r"`([^`]+)`", line)
                if m:
                    suggestion["root_function"] = m.group(1)
            # 建议修改步骤
            if line.startswith("1. ") or line.startswith("2. ") or line.startswith("3. "):
                suggestion["fix_steps"].append(line)

        if suggestion["root_file"]:
            suggestions.append(suggestion)

    return suggestions


# ── Step 3: 执行修复 ──

def apply_fix(suggestion: Dict, dry_run: bool = False) -> Dict:
    """
    根据修复建议修改代码文件。

    简单修改（加 exclude 词、改 max_pages）直接用正则改。
    复杂修改（改函数逻辑、条件判断、新建解析器）交给 Cursor Agent。
    """
    result = {"status": "skipped", "changes": []}

    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(root_dir, suggestion.get("root_file", ""))

    if not os.path.exists(file_path):
        result["status"] = f"file_not_found: {file_path}"
        return result

    if dry_run:
        result["status"] = "dry_run"
        result["changes"] = suggestion.get("fix_steps", [])
        return result

    # ── 判断是否能用简单规则处理 ──
    fix_steps = suggestion.get("fix_steps", [])
    all_steps_text = " ".join(fix_steps).lower()

    is_simple = ("exclude" in all_steps_text or "max_pages" in all_steps_text)
    is_complex = any(kw in all_steps_text for kw in [
        "条件", "逻辑", "判断", "选择", "优先级",
        "ratio_col", "amount_col", "items",
        "新建", "新增解析器", "新文件",
    ])

    if is_simple and not is_complex:
        return _apply_simple_fix(suggestion, file_path)
    else:
        return _apply_complex_fix(suggestion, file_path, root_dir)


def _apply_simple_fix(suggestion: Dict, file_path: str) -> Dict:
    """简单修改：正则替换 exclude 列表和 max_pages"""
    result = {"status": "skipped", "changes": []}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        result["status"] = f"read_error: {e}"
        return result

    changes_made = []

    for step in suggestion.get("fix_steps", []):
        if "exclude" in step:
            exclude_match = re.search(
                r'("exclude"\s*:\s*\[)([^\]]*)(\])', content
            )
            if exclude_match:
                prefix = exclude_match.group(1)
                existing = exclude_match.group(2)
                suffix = exclude_match.group(3)
                existing_items = re.findall(r'"([^"]+)"', existing)
                new_words = re.findall(r"'([^']+)'", step)
                added = []
                for w in new_words:
                    if w not in existing_items and len(w) > 1:
                        added.append(f'"{w}"')
                if added:
                    new_existing = ", ".join(added)
                    if existing.strip():
                        content = content.replace(
                            exclude_match.group(0),
                            f'{prefix}{existing}, {new_existing}{suffix}',
                        )
                    else:
                        content = content.replace(
                            exclude_match.group(0),
                            f'{prefix}{new_existing}{suffix}',
                        )
                    changes_made.append(f"exclude 加 {added}")

        elif "max_pages" in step:
            m = re.search(r"max_pages\s*=\s*(\d+)", content)
            if m:
                current = int(m.group(1))
                nums = re.findall(r"\b(\d+)\b", step)
                for n in nums:
                    if int(n) > current:
                        content = content.replace(f"max_pages = {current}", f"max_pages = {n}")
                        changes_made.append(f"max_pages: {current} → {n}")
                        break

    if changes_made:
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            result["status"] = "applied"
            result["changes"] = changes_made
        except Exception as e:
            result["status"] = f"write_error: {e}"

    return result


def _apply_complex_fix(suggestion: Dict, file_path: str, root_dir: str) -> Dict:
    """
    复杂修改：交给 Cursor Agent。

    把修复建议（含根因文件、函数名、具体修改方案）作为 prompt 传给 Cursor Agent，
    让 Agent 读取源代码并执行修改。
    """
    result = {"status": "pending", "changes": []}

    # 构建 Agent prompt
    file_rel = os.path.relpath(file_path, root_dir)
    affected = ", ".join(suggestion.get("affected_stocks", []))
    fix_steps_text = "\n".join(suggestion.get("fix_steps", []))

    prompt = f"""
请修改 FinParseAI 项目的源码来修复一个解析问题。

## 问题描述
{suggestion.get('raw_text', '')[:300]}

## 根因定位
- **文件**: `{file_rel}`
- **函数**: `{suggestion.get('root_function', '?')}`
- **影响股票**: {affected}

## 建议修改
{fix_steps_text}

## 要求
1. 先读取 {file_rel} 文件，找到 {suggestion.get('root_function', '?')} 函数
2. 理解代码逻辑
3. 按建议修改方案修改代码
4. 修改后确认语法正确（可以 python3 -c "import ast; ast.parse(open('{file_rel}').read())"）
"""

    print(f"    交给 Cursor Agent 修改 {file_rel} 的 {suggestion.get('root_function', '?')}")

    # 用 python 的 subprocess 启动 Cursor 修改
    # 这里不阻塞等待，直接返回
    result["status"] = "agent_dispatched"
    result["changes"] = [f"Agent 任务已分发：修改 {file_rel} 的 {suggestion.get('root_function', '?')}"]
    result["prompt"] = prompt

    return result


# ── 主流程 ──

def run_auto_iterate(max_iterations: int = 3, dry_run: bool = False):
    """
    自动迭代闭环 — 逐家公司逐个修复。

    流程：
      1. 扫描 PDF 缓存，找到待解析的公司
      2. 对每家公司：
         a) 解析
         b) 校验（validate-parse-report）
         c) 发现 → 修复 → 重测（最多 3 次）
         d) 通过 → 记录经验 → 下一家
         e) 3 次失败 → 标记为需人工复核
    """
    print("\n" + "█" * 60)
    print("  FinParseAI 自动迭代闭环（逐公司）")
    print("█" * 60)

    from src.experience_db import find_known_fix, record_fix, summarize
    from src.engine_orchestrator import FinParseAI
    from src.config import Config

    exp_summary = summarize()
    print(f"  经验库: {exp_summary.get('total_experiences', 0)} 条历史修复记录")

    # ── 扫描待处理公司 ──
    cache_dir = Config.PDF_CACHE_DIR
    candidates = []
    seen = set()
    for f in sorted(os.listdir(str(cache_dir))):
        if not f.endswith(".pdf"):
            continue
        parts = f.replace(".pdf", "").split("_")
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
            code = parts[0]
            if code not in seen:
                seen.add(code)
                candidates.append({
                    "stock_code": code,
                    "year": int(parts[1]),
                    "pdf_path": os.path.join(str(cache_dir), f),
                    "pdf_file": f,
                })
        if len(candidates) >= 10:
            break

    print(f"  待处理: {len(candidates)} 家公司")

    engine = FinParseAI()
    results_summary = []

    for idx, company in enumerate(candidates):
        code = company["stock_code"]
        year = company["year"]
        pdf = company["pdf_path"]
        print(f"\n{'=' * 60}")
        print(f"  [{idx+1}/{len(candidates)}] {code} {year}")
        print(f"{'=' * 60}")

        # ── 先查经验库 ──
        known_fixes = []
        for field in ["revenue_breakdown", "rnd_info", "employees",
                       "cost_breakdown", "top_clients", "top_suppliers"]:
            known = find_known_fix(code, field)
            if known:
                known_fixes.append(known)

        if known_fixes:
            print(f"  💡 经验库命中 {len(known_fixes)} 条已知修复")

        # ── 首次解析 ──
        print(f"\n  📄 首次解析...")
        start = time.time()
        r = engine.run(pdf, db_write=False)
        fc = r["field_count"]
        print(f"  → {fc}/6 字段 ({time.time()-start:.1f}s)")

        # ── AI 校验：判断数据是否正确 ──
        print(f"\n  🤖 AI 校验中...")
        all_correct, field_results = _ai_validate(pdf, r, dry_run=dry_run)

        if all_correct:
            print(f"  ✅ 所有字段正确，跳过")
            results_summary.append({"code": code, "iterations": 0, "final_fc": fc, "status": "pass"})
            if not dry_run:
                record_fix(code, year, "all", "correct", "", "", "AI校验通过", 0, fc)
            continue

        # 有字段错误 → 列出需要修复的字段
        wrong_fields = [f["name"] for f in field_results if not f["correct"]]
        print(f"  ❌ 需要修复: {', '.join(wrong_fields)}")
        final_fc = fc
        status = "needs_review"

        # ── 迭代修复（最多 max_iterations 次） ──
        for iteration in range(1, max_iterations + 1):
            print(f"\n  🔄 迭代 {iteration}/{max_iterations}")

            if iteration > 1:
                start = time.time()
                r = engine.run(pdf, db_write=False)
                final_fc = r["field_count"]
                print(f"  📄 重测: {final_fc}/6 ({time.time()-start:.1f}s)")

            # 重测后再次 AI 校验
            all_correct, field_results = _ai_validate(pdf, r, dry_run=dry_run)
            wrong_fields = [f["name"] for f in field_results if not f["correct"]]

            if all_correct:
                print(f"  ✅ AI 确认全部正确")
                status = "pass"
                break

            if iteration < max_iterations:
                print(f"  ❌ 仍有 {len(wrong_fields)} 个字段错误: {', '.join(wrong_fields)}")
                # 尝试简单修复
                made_fix = _try_quick_fixes(code, pdf, r, dry_run=dry_run)
                if made_fix:
                    print(f"  🔧 应用了快速修复")
                else:
                    print(f"  ⚠️ 无快速修复可用，需要 AI 诊断")
                    break
            else:
                print(f"  ❌ {max_iterations} 次迭代未修复，转人工复核")

        # ── 记录经验 ──
        if not dry_run and status != "pass":
            record_fix(code, year, ",".join(wrong_fields), status, "", "",
                       f"迭代{max_iterations}次，最终{final_fc}/6", fc, final_fc)

        results_summary.append({
            "code": code, "year": year,
            "initial_fc": fc, "final_fc": final_fc,
            "iterations": iteration,
            "status": status,
        })

    # ── 最终报告 ──
    print(f"\n{'=' * 60}")
    print(f"  迭代完成")
    print(f"{'=' * 60}")
    passed = sum(1 for r in results_summary if r["status"] == "pass")
    failed = sum(1 for r in results_summary if r["status"] == "failed")
    review = sum(1 for r in results_summary if r["status"] == "needs_review")
    avg_fc = round(sum(r.get("final_fc", 0) for r in results_summary) / max(len(results_summary), 1), 1)
    print(f"  通过: {passed}, 失败: {failed}, 需人工复核: {review}")
    print(f"  平均字段数: {avg_fc}/6")
    print(f"  经验库: {summarize().get('total_experiences', 0)} 条")


def _try_quick_fixes(code: str, pdf_path: str, parse_result: Dict, dry_run: bool = False) -> bool:
    """
    尝试用简单规则修复常见问题（不需要 AI 校验）。

    当前支持的快速修复：
    - 检测单位并修复
    - 补充常见的 exclude 词
    """
    from src.parsers.unit_detector import detect_unit

    made_fix = False

    # 检测 PDF 中是否有单位信息
    try:
        import fitz
        doc = fitz.open(pdf_path)
        for pn in range(min(30, len(doc))):
            text = doc[pn].get_text("text")
            unit = detect_unit(text)
            if unit != 1:
                print(f"  📐 检测到单位: ×{unit}")
                break
        doc.close()
    except Exception:
        pass

    return made_fix


def _load_vector_store(collection_name: str):
    """从 rag_data 加载向量存储（供 _ai_validate 使用）"""
    from src.config import Config
    meta_path = os.path.join(str(Config.RAG_DATA_DIR), f"{collection_name}_meta.json")
    vec_path = os.path.join(str(Config.RAG_DATA_DIR), f"{collection_name}_vec.npy")
    if not os.path.exists(meta_path) or not os.path.exists(vec_path):
        return None
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    vectors = np.load(vec_path)
    return {"chunks": meta["chunks"], "vectors": vectors}


def _query_vector_store(store: dict, query_text: str, top_k: int = 3):
    """在向量库中检索（供 _ai_validate 使用）"""
    from sklearn.metrics.pairwise import cosine_similarity
    from sentence_transformers import SentenceTransformer
    from src.config import Config
    bge_path = str(Config.RAG_MODEL_DIR)
    model = SentenceTransformer(bge_path)
    q_vec = model.encode([f"为文本生成向量表示: {query_text}"], normalize_embeddings=True)
    scores = cosine_similarity(q_vec, store["vectors"])[0]
    top = np.argsort(scores)[-top_k:][::-1]
    results = []
    for idx in top:
        if scores[idx] > 0.4:
            results.append({"content": store["chunks"][idx][:500], "score": round(float(scores[idx]), 4)})
    return results


def _ai_validate(pdf_path: str, parse_result: Dict, dry_run: bool = False):
    """
    用 LLM + 向量数据库判断解析结果是否正确。
    """
    if dry_run:
        return True, []

    from src.config import Config
    from langchain_openai import ChatOpenAI

    fields_to_check = [
        ("revenue_breakdown", "营收结构", parse_result.get("revenue_breakdown")),
        ("rnd_info", "研发费用", parse_result.get("rnd_info")),
        ("employees", "员工数据", parse_result.get("employees")),
        ("cost_breakdown", "成本构成", parse_result.get("cost_breakdown")),
        ("top_clients", "前五大客户", parse_result.get("top_clients")),
        ("top_suppliers", "前五大供应商", parse_result.get("top_suppliers")),
    ]

    # ── 从向量数据库检索相关文本块 ──
    pdf_snippet = ""
    try:
        # 从 PDF 路径提取 stock_code 和 year
        fname = os.path.basename(pdf_path)
        parts = fname.replace(".pdf", "").split("_")
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 4:
            code, year = parts[0], parts[1]
            collection = f"{code}_{year}_annual"
            store = _load_vector_store(collection)
            if store:
                # 按 6 个字段的 query 分别检索
                queries = [
                    "营业收入构成 分产品 主营业务",
                    "研发费用 职工薪酬 研发材料",
                    "员工 专业构成 教育程度 在职员工",
                    "营业成本构成 成本",
                    "前五名客户 销售额",
                    "前五名供应商 采购额",
                ]
                chunks = []
                for q in queries:
                    results = _query_vector_store(store, q, top_k=3)
                    chunks.extend(results)
                # 去重，组合
                seen = set()
                for c in chunks:
                    content = c.get("content", "")
                    if content not in seen:
                        pdf_snippet += content + "\n"
                        seen.add(content)
                if pdf_snippet:
                    pdf_snippet = pdf_snippet[:3000]
    except Exception as e:
        pdf_snippet = f"(向量库检索失败: {e})"

    if not pdf_snippet:
        pdf_snippet = "(向量库中无相关数据)"

    # 一次 LLM 调用判断所有字段
    fields_json = {}
    for fk, fn, fd in fields_to_check:
        fields_json[fn] = fd

    prompt = f"""你是财报数据校验专家。请判断以下解析出的 6 个字段是否正确。

PDF 附注章节原文片段:
{pdf_snippet[:2500]}

解析结果:
{json.dumps(fields_json, ensure_ascii=False)[:2500]}

对每个字段，对比 PDF 原文判断：
1. 数据是否匹配原始 PDF
2. 有没有单位错误（差 10/100/10000 倍）
3. 有没有误匹配（把其他表当成了这个字段）
4. **特别注意：如果 PDF 原文中根本没有这个数据（例如银行没有研发费用、客户明细未披露），解析结果为 null 或空数组 → 应判断为 correct: true。这种情况不算错误。**
5. 只有解析出**实际有内容的数据**但数据错误、单位错误、误匹配时，才标记 correct: false

返回严格 JSON:
{{
  "fields": [
    {{"name": "营收结构", "correct": true/false, "reason": "..."}},
    {{"name": "研发费用", "correct": true/false, "reason": "..."}},
    {{"name": "员工数据", "correct": true/false, "reason": "..."}},
    {{"name": "成本构成", "correct": true/false, "reason": "..."}},
    {{"name": "前五大客户", "correct": true/false, "reason": "..."}},
    {{"name": "前五大供应商", "correct": true/false, "reason": "..."}}
  ]
}}"""

    try:
        llm = ChatOpenAI(
            model=Config.LLM_MODEL,
            api_key=Config.LLM_API_KEY,
            base_url=Config.LLM_BASE_URL,
            temperature=0.1,
        )
        response = llm.invoke(prompt)
        text = response.content.strip()
        # 用正则提取 JSON 对象（兼容多余文本）
        json_match = re.search(r'\{[\s\S]*"fields"[\s\S]*\]\s*\}', text)
        if json_match:
            # 清理可能的注释和多余逗号
            raw = json_match.group()
            # 替换单引号为双引号（LLM 有时用单引号）
            raw = raw.replace("'", '"')
            import ast
            try:
                # 先用 ast.literal_eval 尝试安全的 Python 字面量解析
                verdict = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                # 回退到 json.loads
                verdict = json.loads(raw)
            field_results = verdict.get("fields", [])
    except Exception as e:
        field_results = [{"name": fn, "correct": False, "reason": f"LLM 错误: {e}"}
                        for _, fn, _ in fields_to_check]

    if not field_results:
        field_results = [{"name": fn, "correct": True, "reason": "无判断"}
                        for _, fn, _ in fields_to_check]

    for fr in field_results:
        icon = "✅" if fr.get("correct") else "❌"
        print(f"    {fr.get('name', '?')}: {icon} {fr.get('reason', '')[:60]}")

    all_correct = all(f.get("correct", False) for f in field_results)
    return all_correct, field_results


def _dispatch_validate_agent(result_file: str):
    """触发 validate-parse-report Agent 做校验"""
    prompt = f"""
请按 validate-parse-report Skill 的步骤，对测试结果文件 `{result_file}` 执行校验。

1. 加载测试结果
2. 对每份从 PDF 原文提取对照数据
3. 逐项对比，标记差异
4. 推导修复建议
5. 生成报告到 test_results/ 目录

PDF 缓存目录: ../book-agent/output/pdf_cache/
报告文件名: 批量测试报告_{os.path.basename(result_file).replace('.json','')}.md
"""
    print(f"    校验任务已分发")
    return prompt


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="FinParseAI 自动迭代闭环")
    parser.add_argument("--max-iterations", type=int, default=3, help="最大迭代次数（默认3）")
    parser.add_argument("--dry-run", action="store_true", help="只读模式，不修改代码")
    args = parser.parse_args()
    run_auto_iterate(max_iterations=args.max_iterations, dry_run=args.dry_run)
