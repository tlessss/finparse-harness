# 复核 Agent Prompt Mock（v2 草案）

> **仅设计稿，未接入代码。** 真源对照：`src/prompts/templates/verify.yaml`（v1）、`src/agents/llm_judge.py`（`build_verify_messages`）。  
> 目标：与 `judge_diagnose` 同级 Context Pack；确定性信号由代码注入，LLM 只做对照与定 issue。

---

## 一、与 v1 的差异摘要

| 维度 | v1 | v2 mock |
|------|-----|---------|
| 上下文 | 单表网格 + JSON + 一行锚 | + pick_meta、逐维锚、完整性、续表、top2 候选、FieldSpec |
| 字段 | 文案偏营收 | 按 `FieldSpec.cls`（A/B/C）分块 |
| 跨页 | 启发式「分项和远小于营收」 | 续表原文 + `cross_page_hint` 确定性信号 |
| 选错表 | 只看选中表自证 | top2 候选对照 + 认表 marker |
| issue | 7 种 loosely defined | 与 pipeline / judge 词汇对齐 + 路由提示 |
| 列/期间 | 禁臆测年份 | + 本期列识别规则 |

---

## 二、YAML Mock（拟替换 `verify.yaml`）

```yaml
id: verify
version: v2-mock
role: judge
system: |-
  你是 A 股年报「绿灯复核员」——解析结果已通过跨表锚(过锚)，你的职责是审查**锚证明不了**的盲区：
  表是否选对、维度是否齐全(含跨页续表)、逐项名称/金额/列是否取对。
  铁律：
  - 跨表锚只证明「已被解析出的维度金额合计≈权威锚」，不证明表身份正确、不证明维度齐全。
  - 确定性信号(下方「机器预检」)由系统算好；与之矛盾时以源文表格为准，但须写清 reason。
  - 体检(A/B/C 步)任一不过 → verdict=hold，可跳过逐项；宁可交人工，不放行错数。
  - 只依据源文真实文字；源文未写明的年份/期间绝不编造。
  - 只输出 JSON，不要解释性散文。

user: |-
  # ── 0. 案件元信息 ──
  字段：{{field}}（{{field_label}}，判据类 {{field_cls}}）
  生产选中表：{{pick_meta}}
  源文主依据类型：{{grounding}}
  {{field_spec_note}}

  # ── 1. 机器预检（确定性，请先读再判）──
  【锚】{{anchor_summary}}
  【各维度分项和】{{dims_summary}}
  【缺失维度】{{missing_dims_text}}
  【维度完整性】{{completeness_text}}
  【逐维对锚偏差】{{anchor_diff_text}}
  【跨页可疑】{{cross_page_hint}}

  {{unit_note}}
  {{trust_note}}

  # ── 2. 源文表格 ──
  ## 2a. 当前选中表（主对照）
  {{source}}

  ## 2b. 紧接下一页同主题表（续表/跨页对照，可能为空）
  {{next_table_preview}}

  ## 2c. 其它候选表摘要（认表对照，防选错表）
  {{candidates_brief}}

  # ── 3. 待复核解析结果（JSON）
  {{field_value_json}}

  # ── 4. 复核步骤（严格顺序）──

  ## Step A · 表身份（所有字段）
  对照 2a/2c，判断选中表是否为「{{field_label}}」的**正确目标表**（非销售表/毛利率表/分部汇总/签约额表等）。
  认表 marker 参考：{{table_markers_text}}
  - 若 2c 中明显有更符合 marker 且维度更全的表 → issue=wrong_table（reason 须写候选页码/标题差异）
  - 若通篇无目标表 → issue=wrong_table，reason=无构成表

  ## Step B · 完整性与跨页（按判据类 {{field_cls}}）

  {{#if field_cls == "A"}}
  A 类（营收/成本构成）：
  - 若「维度完整性=不完整」或「跨页可疑=是」：读 2b，看缺失/未过锚的分项是否在续表出现。
    - 续表有、但未拼进 JSON → issue=incomplete_table（等同 cross_page，系统会交人工）
    - 续表也无 → 仍 incomplete_table，reason 写缺哪一维
  - 若各维度过锚但仅「总额」与锚有小口径差(主营业务合计略小于营业收入) → issue=caliber_gap，verdict=pass（附 note，不算数据错）
  {{/if}}

  {{#if field_cls == "B"}}
  B 类（研发明细 / 前五客户供应商）：
  - 核对明细项之和 vs 合计/总占比（机器预检已给 diff，你只做源文对照确认）。
  - 前五客户/供应商：**明细名单缺失但合计占比存在** → 合规情形，verdict=pass（不要因缺名称 hold）。
  - 明细有但合计对不上源文 → issue=amount_error 或 incomplete_table。
  {{/if}}

  {{#if field_cls == "C"}}
  C 类（员工）：
  - 专业构成/教育程度人数之和应≈ total（±2 人容差已在机器层）。
  - 某维度在源文存在但 JSON 缺失 → issue=incomplete_table。
  {{/if}}

  Step A 或 B 不过 → verdict=hold，**不必**进入 Step C。

  ## Step C · 逐项对照（仅 A/B 步都过）
  对 JSON 中**实际存在的**每一项，在 2a（必要时 2b）找同名/同行对照：

  ### 列/期间规则（必守）
  - 表头多列并排时：优先对照**本期/报告期/期末**列；「上期/年初/上年」列不得当作本期值。
  - 未标明年份的两组数：称「左列/右列」或「第一组/第二组」，禁止编造「20XX年」。
  - 单位：解析值已统一为「元」；源文若为万元/千元须心算换算后再比，一致即对，issue 不得标 unit_error。

  ### 名称规则
  - 「其中：XXX」子项不得与顶层分项重复计数 → issue=dup_count
  - 合计行/小计行不得进明细 → issue=extra_row
  - 名称串行/截断/错字 → issue=name_error

  ### 金额规则
  - 取错列(上期/占比列/毛利率列) → issue=amount_error
  - 金额与源文差 > 1% 且非四舍五入 → issue=amount_error

  ### 故意不核
  - **ratio_pct / 占比列**：解析结果可无占比（由金额/锚另算），源文有占比、JSON 无占比 → **不得**因此 hold。

  逐项均对 → verdict=pass。

  # ── 5. issue → 系统路由（写入 suspects 时对齐）──
  | issue | 含义 | 系统后续 |
  | wrong_table | 选错表 | 选表自愈 agent |
  | incomplete_table / cross_page | 跨页未拼接或缺维 | 交人工 |
  | caliber_gap | 口径差，数据可接受 | pass（可备注） |
  | name_error / amount_error / dup_count / extra_row | 逐项错误 | hold → 人工或 L2 |
  | other | 其它 | hold |

  # ── 6. 输出 ──
  只输出 JSON：
  {
    "verdict": "pass|hold|unknown",
    "suspects": [
      {"field": "路径如 segments[2].revenue_yuan 或 table", "issue": "见上表", "reason": "必须引用源文可见依据(页/行/列)"}
    ],
    "summary": "一句话结论",
    "confidence": 0.0~1.0
  }
  unknown 仅当源文完全无法对照（2a/2b/2c 皆空）时使用。

output_schema:
  verdict: pass|hold|unknown
  suspects: '[{"field","issue","reason"}]'
  summary: string
  confidence: 0~1
```

> 注：YAML 内 `{{#if field_cls == "A"}}` 为示意——**接入时**应由 Python 按 `FieldSpec.cls` 渲染对应段落，或拆成 `verify_a.yaml` / `verify_b.yaml` 子模板。

---

## 三、变量契约（接入 `build_verify_messages` 时需注入）

| 变量 | 来源（拟） | 示例 |
|------|------------|------|
| `field` | 入参 | `revenue_breakdown` |
| `field_label` | `FieldSpec.label` | `营收` |
| `field_cls` | `FieldSpec.cls` | `A` |
| `field_spec_note` | `FieldSpec.spec_note` | 准则第二十五条… |
| `table_markers_text` | `FieldSpec.table_markers` join | `占营业收入比重, 营业收入比重, 占比` |
| `pick_meta` | `pick_meta_text(pick)` | `page=24 rows=18 via=anchor …` |
| `grounding` | 现有 | `选中表网格` |
| `source` | `_source_grid` / override | 网格 markdown |
| `next_table_preview` | `next_table_content` | 下一张表 15 行 |
| `candidates_brief` | top2 `candidate_table_lines` 各 10 行 | `#2 p22 …` |
| `anchor_summary` | `anchor_summary_text` | `权威营收 ≈ 12.34 亿元` |
| `dims_summary` | `dims_summary_text` | `segments sum=… industries sum=…` |
| `missing_dims_text` | `missing_dims` | `['by_channel']` 或 `无` |
| `completeness_text` | 同 judge_diagnose | `完整` / `不完整 — 缺失维度:…` |
| `anchor_diff_text` | 逐维 ±% | `segments +0.2%(过锚) \| regions -8.1%(未过锚)` |
| `cross_page_hint` | `cross_page_suspect` | `可疑:…` 或 `未发现跨页信号` |
| `unit_note` | 现有 | 千元→元 |
| `trust_note` | `extra_note`（选表自愈二次复核） | 【选表已确认】… |
| `field_value_json` | 现有 | pretty JSON |

---

## 三-B、完整 Prompt（渲染后 · 最终发给 LLM）

> 下面是没有 `{{占位符}}` 的 **`messages` 数组**，形态与 `chat(messages, role="judge")` / OpenAI API 一致：  
> `[{"role":"system","content":"..."}, {"role":"user","content":"..."}]`  
> Step B 已按 `field_cls` **展开**（A 类见 TC-01/02，B 类见 TC-05）；不再保留 `{{#if}}`。

### 3B.1 TC-01 · 营收 pass（A 类 · 完整 messages）

<details>
<summary><code>messages</code> JSON（点击展开，可复制进调试台）</summary>

```json
[
  {
    "role": "system",
    "content": "你是 A 股年报「绿灯复核员」——解析结果已通过跨表锚(过锚)，你的职责是审查**锚证明不了的**盲区：\n表是否选对、维度是否齐全(含跨页续表)、逐项名称/金额/列是否取对。\n铁律：\n- 跨表锚只证明「已被解析出的维度金额合计≈权威锚」，不证明表身份正确、不证明维度齐全。\n- 确定性信号(下方「机器预检」)由系统算好；与之矛盾时以源文表格为准，但须写清 reason。\n- 体检(A/B/C 步)任一不过 → verdict=hold，可跳过逐项；宁可交人工，不放行错数。\n- 只依据源文真实文字；源文未写明的年份/期间绝不编造。\n- 只输出 JSON，不要解释性散文。"
  },
  {
    "role": "user",
    "content": "# ── 0. 案件元信息 ──\n字段：revenue_breakdown（营收，判据类 A）\n生产选中表：page=24 rows=18 cols=6 via=anchor amount_col=3 anchor_rel=0.012 dim_count=4 caption=营业收入构成(分行业)\n源文主依据类型：选中表网格\n【字段准则】按行业/产品/地区/销售模式披露营业收入构成；目标是占营业收入比重表，不是毛利率表或销售表。\n\n# ── 1. 机器预检（确定性，请先读再判）──\n【锚】权威营业收入 ≈ 1,234,567,890 元\n【各维度分项和】segments=1,234,567,890 | industries=1,234,567,890 | regions=1,234,567,890 | by_channel=1,234,567,890\n【缺失维度】无\n【维度完整性】完整（所有预期维度均过锚）\n【逐维对锚偏差】segments +0.0%(过锚) | industries +0.0%(过锚) | regions +0.0%(过锚) | by_channel +0.0%(过锚)\n【跨页可疑】未发现跨页信号\n\n【单位提示】源文金额单位为「元」；解析结果已为「元」。\n\n# ── 2. 源文表格 ──\n## 2a. 当前选中表（主对照）\n| 项目 | 本期金额 | 本期占比 |\n| 分行业 | | |\n| 集成电路 | 800,000,000 | 64.81% |\n| 其他 | 434,567,890 | 35.19% |\n| 合计 | 1,234,567,890 | 100.00% |\n\n## 2b. 紧接下一页同主题表（续表/跨页对照，可能为空）\n(无 — 下一页 caption=分地区销售情况，非构成续表)\n\n## 2c. 其它候选表摘要（认表对照，防选错表）\n#1 p24 营业收入构成(分行业) rows=18 ✔合计≈锚\n#2 p31 分地区销售情况 rows=12 ✘销售金额口径\n\n# ── 3. 待复核解析结果（JSON）\n{\n  \"segments\": [\n    {\"name\": \"芯片A\", \"revenue_yuan\": 620000000},\n    {\"name\": \"芯片B\", \"revenue_yuan\": 180000000}\n  ],\n  \"industries\": [\n    {\"name\": \"集成电路\", \"revenue_yuan\": 800000000},\n    {\"name\": \"其他\", \"revenue_yuan\": 434567890}\n  ],\n  \"regions\": [\n    {\"name\": \"境内\", \"revenue_yuan\": 1100000000},\n    {\"name\": \"境外\", \"revenue_yuan\": 134567890}\n  ],\n  \"by_channel\": [\n    {\"name\": \"直销\", \"revenue_yuan\": 900000000},\n    {\"name\": \"经销\", \"revenue_yuan\": 334567890}\n  ]\n}\n\n# ── 4. 复核步骤（严格顺序）──\n\n## Step A · 表身份（所有字段）\n对照 2a/2c，判断选中表是否为「营收」的**正确目标表**（非销售表/毛利率表/分部汇总/签约额表等）。\n认表 marker 参考：占营业收入比重, 营业收入比重, 占比\n- 若 2c 中明显有更符合 marker 且维度更全的表 → issue=wrong_table（reason 须写候选页码/标题差异）\n- 若通篇无目标表 → issue=wrong_table，reason=无构成表\n\n## Step B · 完整性与跨页（A 类 · 营收/成本构成）\n- 若「维度完整性=不完整」或「跨页可疑=是」：读 2b，看缺失/未过锚的分项是否在续表出现。\n  - 续表有、但未拼进 JSON → issue=incomplete_table（等同 cross_page，系统会交人工）\n  - 续表也无 → 仍 incomplete_table，reason 写缺哪一维\n- 若各维度过锚但仅「总额」与锚有小口径差(主营业务合计略小于营业收入) → caliber_gap，verdict=pass（附 note，不算数据错）\n\nStep A 或 B 不过 → verdict=hold，**不必**进入 Step C。\n\n## Step C · 逐项对照（仅 A/B 步都过）\n对 JSON 中**实际存在的**每一项，在 2a（必要时 2b）找同名/同行对照：\n\n### 列/期间规则（必守）\n- 表头多列并排时：优先对照**本期/报告期/期末**列；「上期/年初/上年」列不得当作本期值。\n- 未标明年份的两组数：称「左列/右列」或「第一组/第二组」，禁止编造「20XX年」。\n- 单位：解析值已统一为「元」；源文若为万元/千元须心算换算后再比，一致即对，issue 不得标 unit_error。\n\n### 名称规则\n- 「其中：XXX」子项不得与顶层分项重复计数 → issue=dup_count\n- 合计行/小计行不得进明细 → issue=extra_row\n- 名称串行/截断/错字 → issue=name_error\n\n### 金额规则\n- 取错列(上期/占比列/毛利率列) → issue=amount_error\n- 金额与源文差 > 1% 且非四舍五入 → issue=amount_error\n\n### 故意不核\n- **ratio_pct / 占比列**：解析结果可无占比（由金额/锚另算），源文有占比、JSON 无占比 → **不得**因此 hold。\n\n逐项均对 → verdict=pass。\n\n# ── 5. issue → 系统路由 ──\n| issue | 含义 | 系统后续 |\n| wrong_table | 选错表 | 选表自愈 agent |\n| incomplete_table / cross_page | 跨页未拼接或缺维 | 交人工 |\n| caliber_gap | 口径差，数据可接受 | pass（可备注） |\n| name_error / amount_error / dup_count / extra_row | 逐项错误 | hold → 人工或 L2 |\n| other | 其它 | hold |\n\n# ── 6. 输出 ──\n只输出 JSON：\n{\"verdict\":\"pass|hold|unknown\",\"suspects\":[{\"field\":\"...\",\"issue\":\"...\",\"reason\":\"...\"}],\"summary\":\"...\",\"confidence\":0.0~1.0}\nunknown 仅当源文完全无法对照时使用。"
  }
]
```

</details>

**期望 LLM 回复（TC-01 金标准）：**

```json
{
  "verdict": "pass",
  "suspects": [],
  "summary": "p24 为占营业收入比重的分行业构成表，四维度完整；逐项本期金额与源文一致。",
  "confidence": 0.91
}
```

---

### 3B.2 TC-02 · wrong_table（A 类 · 完整 messages）

<details>
<summary><code>messages</code> JSON（点击展开）</summary>

```json
[
  {
    "role": "system",
    "content": "你是 A 股年报「绿灯复核员」——解析结果已通过跨表锚(过锚)，你的职责是审查**锚证明不了的**盲区：\n表是否选对、维度是否齐全(含跨页续表)、逐项名称/金额/列是否取对。\n铁律：\n- 跨表锚只证明「已被解析出的维度金额合计≈权威锚」，不证明表身份正确、不证明维度齐全。\n- 确定性信号(下方「机器预检」)由系统算好；与之矛盾时以源文表格为准，但须写清 reason。\n- 体检(A/B/C 步)任一不过 → verdict=hold，可跳过逐项；宁可交人工，不放行错数。\n- 只依据源文真实文字；源文未写明的年份/期间绝不编造。\n- 只输出 JSON，不要解释性散文。"
  },
  {
    "role": "user",
    "content": "# ── 0. 案件元信息 ──\n字段：revenue_breakdown（营收，判据类 A）\n生产选中表：page=31 rows=12 cols=5 via=keyword amount_col=2 anchor_rel=0.31 dim_count=1 caption=分地区销售情况\n源文主依据类型：选中表网格\n【字段准则】目标是占营业收入比重表，不是分区域销售情况/签约额表。\n\n# ── 1. 机器预检（确定性，请先读再判）──\n【锚】权威营业收入 ≈ 980,000,000 元\n【各维度分项和】regions=980,000,000\n【缺失维度】['segments', 'industries', 'by_channel']\n【维度完整性】不完整 — 缺失维度: ['segments', 'industries', 'by_channel']\n【逐维对锚偏差】regions +0.0%(过锚)\n【跨页可疑】未发现跨页信号\n\n# ── 2. 源文表格 ──\n## 2a. 当前选中表（主对照）\n| 地区 | 销售金额(本期) | 销售金额(上期) |\n| 华东 | 520,000,000 | 480,000,000 |\n| 华南 | 460,000,000 | 420,000,000 |\n| 合计 | 980,000,000 | 900,000,000 |\n\n## 2b. 紧接下一页同主题表（续表/跨页对照，可能为空）\n(无)\n\n## 2c. 其它候选表摘要（认表对照，防选错表）\n#1 p31 分地区销售情况 rows=12 ✘销售金额\n#2 p22 占营业收入比重(分产品) rows=20 ✔合计≈锚 marker=占营业收入比重\n\n# ── 3. 待复核解析结果（JSON）\n{\n  \"regions\": [\n    {\"name\": \"华东\", \"revenue_yuan\": 520000000},\n    {\"name\": \"华南\", \"revenue_yuan\": 460000000}\n  ]\n}\n\n# ── 4. 复核步骤（严格顺序）──\n\n## Step A · 表身份（所有字段）\n对照 2a/2c，判断选中表是否为「营收」的**正确目标表**（非销售表/毛利率表/分部汇总/签约额表等）。\n认表 marker 参考：占营业收入比重, 营业收入比重, 占比\n- 若 2c 中明显有更符合 marker 且维度更全的表 → issue=wrong_table（reason 须写候选页码/标题差异）\n- 若通篇无目标表 → issue=wrong_table，reason=无构成表\n\n## Step B · 完整性与跨页（A 类 · 营收/成本构成）\n- 若「维度完整性=不完整」或「跨页可疑=是」：读 2b，看缺失/未过锚的分项是否在续表出现。\n  - 续表有、但未拼进 JSON → issue=incomplete_table（等同 cross_page，系统会交人工）\n  - 续表也无 → 仍 incomplete_table，reason 写缺哪一维\n- 若各维度过锚但仅「总额」与锚有小口径差(主营业务合计略小于营业收入) → issue=caliber_gap，verdict=pass（附 note，不算数据错）\n\nStep A 或 B 不过 → verdict=hold，**不必**进入 Step C。\n\n## Step C · 逐项对照（仅 A/B 步都过）\n对 JSON 中**实际存在的**每一项，在 2a（必要时 2b）找同名/同行对照：\n\n### 列/期间规则（必守）\n- 表头多列并排时：优先对照**本期/报告期/期末**列；「上期/年初/上年」列不得当作本期值。\n- 未标明年份的两组数：称「左列/右列」或「第一组/第二组」，禁止编造「20XX年」。\n- 单位：解析值已统一为「元」；源文若为万元/千元须心算换算后再比，一致即对，issue 不得标 unit_error。\n\n### 名称规则\n- 「其中：XXX」子项不得与顶层分项重复计数 → issue=dup_count\n- 合计行/小计行不得进明细 → issue=extra_row\n- 名称串行/截断/错字 → issue=name_error\n\n### 金额规则\n- 取错列(上期/占比列/毛利率列) → issue=amount_error\n- 金额与源文差 > 1% 且非四舍五入 → issue=amount_error\n\n### 故意不核\n- **ratio_pct / 占比列**：解析结果可无占比（由金额/锚另算），源文有占比、JSON 无占比 → **不得**因此 hold。\n\n逐项均对 → verdict=pass。\n\n# ── 5. issue → 系统路由 ──\n| issue | 含义 | 系统后续 |\n| wrong_table | 选错表 | 选表自愈 agent |\n| incomplete_table / cross_page | 跨页未拼接或缺维 | 交人工 |\n| caliber_gap | 口径差，数据可接受 | pass（可备注） |\n| name_error / amount_error / dup_count / extra_row | 逐项错误 | hold → 人工或 L2 |\n| other | 其它 | hold |\n\n# ── 6. 输出 ──\n只输出 JSON：\n{\"verdict\":\"pass|hold|unknown\",\"suspects\":[{\"field\":\"...\",\"issue\":\"...\",\"reason\":\"...\"}],\"summary\":\"...\",\"confidence\":0.0~1.0}\nunknown 仅当源文完全无法对照时使用。"
  }
]
```

</details>

**期望 LLM 回复（TC-02）：**

```json
{
  "verdict": "hold",
  "suspects": [
    {
      "field": "table",
      "issue": "wrong_table",
      "reason": "2a 为分地区销售情况(销售金额口径)，非占营业收入比重构成表；2c #2 p22 含 marker「占营业收入比重」且合计≈锚，应为目标表"
    }
  ],
  "summary": "选错表：当前为销售情况表，应使用 p22 营业收入构成表。",
  "confidence": 0.93
}
```

---

### 3B.3 TC-05 · top_clients pass（B 类 · Step B 不同）

system 同上。user 中 **Step B 替换为 B 类段落**（其余 Step A/C 相同）：

```
## Step B · 完整性与勾稽（B 类 · 研发明细 / 前五客户供应商）
- 核对明细项之和 vs 合计/总占比（机器预检已给 diff，你只做源文对照确认）。
- 前五客户/供应商：**明细名单缺失但合计占比存在** → 合规情形，verdict=pass（不要因缺名称 hold）。
- 明细有但合计对不上源文 → issue=amount_error 或 incomplete_table。
```

**TC-05 完整 user 前半（0~3 节 + B 类 Step B 之前部分）：**

```
# ── 0. 案件元信息 ──
字段：top_clients（前五大客户，判据类 B）
生产选中表：page=18 rows=6 cols=4 via=keyword caption=主要客户
源文主依据类型：选中表网格
【字段准则】前5名客户销售额占比强制；明细名单鼓励非强制，可无明细。

# ── 1. 机器预检 ──
【锚】（本字段无 revenue 锚，B 类看合计占比）
【各维度分项和】total_ratio_pct=45.2
【缺失维度】无
【维度完整性】完整（合计占比存在；明细缺失属合规）
【逐维对锚偏差】无锚字段
【跨页可疑】未发现跨页信号

# ── 2. 源文表格 ──
## 2a. 当前选中表
| 项目 | 金额 | 占年度销售总额比例 |
| 前五名客户合计 | — | 45.20% |

## 2b. (无)
## 2c. #1 p18 主要客户 rows=6

# ── 3. 待复核解析结果（JSON）
{
  "total_ratio_pct": 45.2,
  "top_clients": []
}

# ── 4. 复核步骤 ──
## Step A · 表身份
认表 marker：前五大客户, 前5名客户, 主要客户
（…）

## Step B · （B 类段落见上）

## Step C · 逐项对照
（B 类仅核对 total_ratio_pct 与源文 45.20% 一致即可；无明细不要求逐项）

# ── 6. 输出 ──
只输出 JSON：...
```

---

### 3B.4 与线上 v1 对比（同一案件 · 仅 user 差异）

**当前生产 `verify.yaml` v1** 对 TC-01 只会发（约 1.5k 字符，无预检/续表/候选）：

```
年报源文（选中表网格，权威）：
| 项目 | 本期金额 | 本期占比 |
| 分行业 | | |
| 集成电路 | 800,000,000 | 64.81% |
...

【锚参考】权威营业收入≈1,234,567,890 元。跨表锚只证明...

待复核的解析结果（字段 revenue_breakdown）：
{ ... JSON ... }

# 第一步·体检
A. 表选对没：...
B. 全不全 / 跨页：...

# 第二步·逐项对照
...
只输出 JSON：{...}
```

| 对比项 | v1 生产 | v2 mock（3B.1） |
|--------|---------|------------------|
| system | 2 步复核员 | 绿灯复核员 + 5 条铁律 |
| 机器预检 | ❌ | ✅ 6 行确定性信号 |
| 2b 续表 | ❌ | ✅ |
| 2c 候选 | ❌ | ✅ |
| FieldSpec | ❌ | ✅ |
| Step 按 cls 分 | ❌ | ✅ |
| 典型 user 长度 | ~1.5k 字符 | ~4.5k 字符 |

---

### 3B.5 接入后如何导出「真实完整 prompt」

```python
# 将来接 v2 后，VerifyTest / 调试台可这样 dump：
from src.agents.llm_judge import build_verify_messages
messages, grounding = build_verify_messages(
    "revenue_breakdown", "MOCK001", 2025, field_value, sig=sig, debug=False)
import json
print(json.dumps(messages, ensure_ascii=False, indent=2))
```

文档内 **3B.1** 的 JSON 即为 `build_verify_messages(TC-01 variables)` 的**预期渲染结果**。

---

## 四、填充实例（简略版 · 完整版见 **三-B**）

> 本节仅保留摘要；**最终发给 LLM 的全文**见 **§三-B.1（TC-01 messages JSON）** 与 **§三-B.2（TC-02 user 全文）**。

---

### 4.1 System（固定）

```
你是 A 股年报「绿灯复核员」——解析结果已通过跨表锚(过锚)，你的职责是审查锚证明不了的盲区：
表是否选对、维度是否齐全(含跨页续表)、逐项名称/金额/列是否取对。
……（同 YAML system 全文）
```

### 4.2 User（实例）

```markdown
# ── 0. 案件元信息 ──
字段：revenue_breakdown（营收，判据类 A）
生产选中表：page=24 rows=18 cols=6 via=anchor amount_col=3 anchor_rel=0.012 dim_count=4 caption=营业收入构成(分行业)
源文主依据类型：选中表网格
【字段准则】准则第二十五条：按行业/产品/地区/销售模式披露营业收入构成。目标是占营业收入比重表(金额+占比)，不是收入/成本/毛利率表。

# ── 1. 机器预检（确定性，请先读再判）──
【锚】权威营业收入 ≈ 1,234,567,890 元
【各维度分项和】segments=1.23e9 | industries=1.23e9 | regions=1.18e9 | by_channel=1.23e9
【缺失维度】无
【维度完整性】完整（所有预期维度均过锚）
【逐维对锚偏差】segments +0.1%(过锚) | industries +0.1%(过锚) | regions -4.2%(过锚) | by_channel +0.0%(过锚)
【跨页可疑】未发现跨页信号

【单位提示】源文金额单位为「元」；解析结果已为「元」。

# ── 2. 源文表格 ──
## 2a. 当前选中表（主对照）
| 项目 | 本期金额 | 本期占比 | 上期金额 | 上期占比 |
| 分行业 | | | | |
| 集成电路 | 800,000,000 | 64.81% | … | … |
| 其他 | 434,567,890 | 35.19% | … | … |
| 合计 | 1,234,567,890 | 100.00% | … | … |

## 2b. 紧接下一页同主题表（续表/跨页对照，可能为空）
(无 — 下一页为「分地区销售情况」，非营业收入构成续表)

## 2c. 其它候选表摘要（认表对照，防选错表）
#1 p24 营业收入构成(分行业) rows=18 ✔列合计≈锚
#2 p31 分地区销售情况 rows=12 （销售金额，非营业收入构成）

# ── 3. 待复核解析结果（JSON）
{
  "segments": [],
  "industries": [
    {"name": "集成电路", "revenue_yuan": 800000000},
    {"name": "其他", "revenue_yuan": 434567890}
  ],
  "regions": [...],
  "by_channel": [...]
}

# ── 4. 复核步骤 ──
（同 YAML Step A/B/C 全文）

# ── 6. 输出 ──
只输出 JSON：...
```

### 4.3 期望输出（该实例）

```json
{
  "verdict": "pass",
  "suspects": [],
  "summary": "表身份为分行业营业收入构成，四维度完整且逐项与源文本期金额列一致；regions 略低于锚属口径差已在预检标注，不构成 hold。",
  "confidence": 0.88
}
```

---

## 五、反例 mock（应 hold）

### 5.1 wrong_table

- 2a 是「分地区**销售**情况」；2c #1 才是「占**营业收入**比重」  
- **期望**：`verdict=hold`, `issue=wrong_table`, reason 引用 2c 页码差异

### 5.2 incomplete_table / cross_page

- `completeness_text=不完整 — 缺失维度: ['segments']`  
- 2b 续表出现「分产品」且含 segments 行，但 JSON 无 segments  
- **期望**：`issue=incomplete_table`, `verdict=hold`

### 5.3 amount_error（取上期列）

- JSON `revenue_yuan` = 源文「上期金额」列的值，本期列不同  
- **期望**：`issue=amount_error`, field 指向具体项

### 5.4 应 pass（D 类合规）

- 字段 `top_clients`，JSON 仅有 `total_ratio_pct: 45.2`，无明细名单  
- 源文也只有合计占比行  
- **期望**：`verdict=pass`（不因缺明细 hold）

---

## 九、测试 Mock 数据集

> 供 `tests/test_prompts_verify.py` / VerifyTest 手工回放。每条含 **`variables`**（渲染前占位符字典）与 **`expected`**（金标准裁决）。  
> 可复制整段 JSON 到 `goldset/verify_prompt_fixtures.json`（接入代码时）。

### 9.1 用例索引

| case_id | 字段 | 场景 | 期望 verdict | 期望 issue | pipeline 后续 |
|---------|------|------|--------------|------------|---------------|
| TC-01 | revenue_breakdown | A 类四维完整、表对、逐项对 | pass | — | commit |
| TC-02 | revenue_breakdown | 选中表=销售情况表 | hold | wrong_table | heal_select |
| TC-03 | revenue_breakdown | segments 在 p25 续表未拼接 | hold | incomplete_table | human |
| TC-04 | revenue_breakdown | 金额取成上期列 | hold | amount_error | human |
| TC-05 | top_clients | 仅合计占比、无明细（合规） | pass | — | commit |
| TC-06 | rnd_info | 明细之和与合计差 >1% | hold | amount_error | human |
| TC-07 | employees | 教育程度维度缺失 | hold | incomplete_table | human |
| TC-08 | revenue_breakdown | 选表自愈二次复核 + trust_note | pass | — | commit |

---

### 9.2 TC-01 · pass（营收完整）

```json
{
  "case_id": "TC-01",
  "code": "MOCK001",
  "year": 2025,
  "field": "revenue_breakdown",
  "variables": {
    "field": "revenue_breakdown",
    "field_label": "营收",
    "field_cls": "A",
    "field_spec_note": "【字段准则】按行业/产品/地区/销售模式披露营业收入构成；目标是占营业收入比重表，不是毛利率表或销售表。",
    "table_markers_text": "占营业收入比重, 营业收入比重, 占比",
    "pick_meta": "page=24 rows=18 cols=6 via=anchor amount_col=3 anchor_rel=0.012 dim_count=4 caption=营业收入构成(分行业)",
    "grounding": "选中表网格",
    "anchor_summary": "权威营业收入 ≈ 1,234,567,890 元",
    "dims_summary": "segments=1,234,567,890 | industries=1,234,567,890 | regions=1,234,567,890 | by_channel=1,234,567,890",
    "missing_dims_text": "无",
    "completeness_text": "完整（所有预期维度均过锚）",
    "anchor_diff_text": "segments +0.0%(过锚) | industries +0.0%(过锚) | regions +0.0%(过锚) | by_channel +0.0%(过锚)",
    "cross_page_hint": "未发现跨页信号",
    "unit_note": "【单位提示】源文金额单位为「元」；解析结果已为「元」。",
    "trust_note": "",
    "source": "| 项目 | 本期金额 | 本期占比 |\n| 分行业 | | |\n| 集成电路 | 800,000,000 | 64.81% |\n| 其他 | 434,567,890 | 35.19% |\n| 合计 | 1,234,567,890 | 100.00% |",
    "next_table_preview": "(无 — 下一页 caption=分地区销售情况，非构成续表)",
    "candidates_brief": "#1 p24 营业收入构成(分行业) rows=18 ✔合计≈锚\n#2 p31 分地区销售情况 rows=12 ✘销售金额口径",
    "field_value_json": "{\n  \"segments\": [{\"name\": \"芯片A\", \"revenue_yuan\": 620000000}, {\"name\": \"芯片B\", \"revenue_yuan\": 180000000}],\n  \"industries\": [{\"name\": \"集成电路\", \"revenue_yuan\": 800000000}, {\"name\": \"其他\", \"revenue_yuan\": 434567890}],\n  \"regions\": [{\"name\": \"境内\", \"revenue_yuan\": 1100000000}, {\"name\": \"境外\", \"revenue_yuan\": 134567890}],\n  \"by_channel\": [{\"name\": \"直销\", \"revenue_yuan\": 900000000}, {\"name\": \"经销\", \"revenue_yuan\": 334567890}]\n}"
  },
  "expected": {
    "verdict": "pass",
    "suspects": [],
    "summary_contains": ["构成", "一致"],
    "confidence_min": 0.7,
    "pipeline_outcome": "committed"
  }
}
```

---

### 9.3 TC-02 · wrong_table（选错销售表）

```json
{
  "case_id": "TC-02",
  "code": "MOCK002",
  "year": 2025,
  "field": "revenue_breakdown",
  "variables": {
    "field": "revenue_breakdown",
    "field_label": "营收",
    "field_cls": "A",
    "field_spec_note": "【字段准则】目标是占营业收入比重表，不是分区域销售情况/签约额表。",
    "table_markers_text": "占营业收入比重, 营业收入比重, 占比",
    "pick_meta": "page=31 rows=12 cols=5 via=keyword amount_col=2 anchor_rel=0.31 dim_count=1 caption=分地区销售情况",
    "grounding": "选中表网格",
    "anchor_summary": "权威营业收入 ≈ 980,000,000 元",
    "dims_summary": "regions=980,000,000",
    "missing_dims_text": "['segments', 'industries', 'by_channel']",
    "completeness_text": "不完整 — 缺失维度: ['segments', 'industries', 'by_channel']",
    "anchor_diff_text": "regions +0.0%(过锚)",
    "cross_page_hint": "未发现跨页信号",
    "unit_note": "",
    "trust_note": "",
    "source": "| 地区 | 销售金额(本期) | 销售金额(上期) |\n| 华东 | 520,000,000 | 480,000,000 |\n| 华南 | 460,000,000 | 420,000,000 |\n| 合计 | 980,000,000 | 900,000,000 |",
    "next_table_preview": "(无)",
    "candidates_brief": "#1 p31 分地区销售情况 rows=12 ✘销售金额\n#2 p22 占营业收入比重(分产品) rows=20 ✔合计≈锚 marker=占营业收入比重",
    "field_value_json": "{\n  \"regions\": [{\"name\": \"华东\", \"revenue_yuan\": 520000000}, {\"name\": \"华南\", \"revenue_yuan\": 460000000}]\n}"
  },
  "expected": {
    "verdict": "hold",
    "suspects": [{"issue": "wrong_table", "field": "table"}],
    "summary_contains": ["销售", "p22", "占营业收入"],
    "pipeline_outcome": "heal_select"
  }
}
```

---

### 9.4 TC-03 · incomplete_table（跨页续表未拼）

```json
{
  "case_id": "TC-03",
  "code": "MOCK003",
  "year": 2025,
  "field": "revenue_breakdown",
  "variables": {
    "field": "revenue_breakdown",
    "field_label": "营收",
    "field_cls": "A",
    "field_spec_note": "【字段准则】营业收入构成表，需含分产品等维度。",
    "table_markers_text": "占营业收入比重, 占比",
    "pick_meta": "page=24 rows=14 cols=6 via=anchor caption=营业收入构成(分行业) table_bbox_near_page_bottom=true",
    "grounding": "选中表网格",
    "anchor_summary": "权威营业收入 ≈ 500,000,000 元",
    "dims_summary": "industries=500,000,000 | regions=498,000,000",
    "missing_dims_text": "['segments']",
    "completeness_text": "不完整 — 缺失维度: ['segments']",
    "anchor_diff_text": "industries +0.0%(过锚) | regions -0.4%(过锚)",
    "cross_page_hint": "可疑:选中页靠近页底，且 p25 有同主题表，可能是跨页续表未拼接",
    "unit_note": "",
    "trust_note": "",
    "source": "| 项目 | 本期金额 | 本期占比 |\n| 分行业 | | |\n| 制造业 | 300,000,000 | 60% |\n| 服务业 | 200,000,000 | 40% |\n| 合计 | 500,000,000 | 100% |",
    "next_table_preview": "| 项目 | 本期金额 | 本期占比 |\n| 分产品 | | |\n| 产品甲 | 180,000,000 | 36% |\n| 产品乙 | 120,000,000 | 24% |\n| 产品丙 | 200,000,000 | 40% |\n| 小计 | 500,000,000 | 100% |",
    "candidates_brief": "#1 p24 营业收入构成(分行业) rows=14\n#2 p25 营业收入构成(分产品) rows=10 ✔续表嫌疑",
    "field_value_json": "{\n  \"industries\": [{\"name\": \"制造业\", \"revenue_yuan\": 300000000}, {\"name\": \"服务业\", \"revenue_yuan\": 200000000}],\n  \"regions\": [{\"name\": \"境内\", \"revenue_yuan\": 498000000}]\n}"
  },
  "expected": {
    "verdict": "hold",
    "suspects": [{"issue": "incomplete_table"}],
    "summary_contains": ["segments", "p25", "续表"],
    "pipeline_outcome": "verify_hold"
  }
}
```

---

### 9.5 TC-04 · amount_error（取错列）

```json
{
  "case_id": "TC-04",
  "code": "MOCK004",
  "year": 2025,
  "field": "revenue_breakdown",
  "variables": {
    "field": "revenue_breakdown",
    "field_label": "营收",
    "field_cls": "A",
    "field_spec_note": "【字段准则】占营业收入比重表。",
    "table_markers_text": "占营业收入比重, 占比",
    "pick_meta": "page=22 rows=16 cols=6 via=anchor caption=营业收入构成(分产品)",
    "grounding": "选中表网格",
    "anchor_summary": "权威营业收入 ≈ 200,000,000 元",
    "dims_summary": "segments=200,000,000",
    "missing_dims_text": "['industries', 'regions', 'by_channel']",
    "completeness_text": "完整（所有预期维度均过锚）",
    "anchor_diff_text": "segments +0.0%(过锚)",
    "cross_page_hint": "未发现跨页信号",
    "unit_note": "",
    "trust_note": "",
    "source": "| 产品 | 本期金额 | 上期金额 |\n| 产品X | 120,000,000 | 100,000,000 |\n| 产品Y | 80,000,000 | 70,000,000 |\n| 合计 | 200,000,000 | 170,000,000 |",
    "next_table_preview": "(无)",
    "candidates_brief": "#1 p22 占营业收入比重(分产品) rows=16 ✔",
    "field_value_json": "{\n  \"segments\": [\n    {\"name\": \"产品X\", \"revenue_yuan\": 100000000},\n    {\"name\": \"产品Y\", \"revenue_yuan\": 70000000}\n  ]\n}"
  },
  "expected": {
    "verdict": "hold",
    "suspects": [
      {"issue": "amount_error", "field": "segments[0].revenue_yuan"},
      {"issue": "amount_error", "field": "segments[1].revenue_yuan"}
    ],
    "summary_contains": ["上期", "本期"],
    "pipeline_outcome": "verify_hold"
  }
}
```

---

### 9.6 TC-05 · pass（前五客户无明细）

```json
{
  "case_id": "TC-05",
  "code": "MOCK005",
  "year": 2025,
  "field": "top_clients",
  "variables": {
    "field": "top_clients",
    "field_label": "前五大客户",
    "field_cls": "B",
    "field_spec_note": "【字段准则】前5名客户销售额占比强制；明细名单鼓励非强制，可无明细。",
    "table_markers_text": "前五大客户, 前5名客户, 主要客户",
    "pick_meta": "page=18 rows=6 cols=4 via=keyword caption=主要客户",
    "grounding": "选中表网格",
    "anchor_summary": "（本字段无 revenue 锚，B 类看合计占比）",
    "dims_summary": "total_ratio_pct=45.2",
    "missing_dims_text": "无",
    "completeness_text": "完整（合计占比存在；明细缺失属合规）",
    "anchor_diff_text": "无锚字段",
    "cross_page_hint": "未发现跨页信号",
    "unit_note": "",
    "trust_note": "",
    "source": "| 项目 | 金额 | 占年度销售总额比例 |\n| 前五名客户合计 | — | 45.20% |",
    "next_table_preview": "(无)",
    "candidates_brief": "#1 p18 主要客户 rows=6",
    "field_value_json": "{\n  \"total_ratio_pct\": 45.2,\n  \"top_clients\": []\n}"
  },
  "expected": {
    "verdict": "pass",
    "suspects": [],
    "summary_contains": ["合规", "合计"],
    "pipeline_outcome": "committed"
  }
}
```

---

### 9.7 TC-06 · hold（研发费用明细和不等于合计）

```json
{
  "case_id": "TC-06",
  "code": "MOCK006",
  "year": 2025,
  "field": "rnd_info",
  "variables": {
    "field": "rnd_info",
    "field_label": "研发",
    "field_cls": "B",
    "field_spec_note": "【字段准则】研发费用明细之和应≈合计 total_this。",
    "table_markers_text": "研发费用, 研发投入",
    "pick_meta": "page=42 rows=10 cols=5 via=anchor caption=研发费用",
    "grounding": "选中表网格",
    "anchor_summary": "权威研发费用 ≈ 88,000,000 元",
    "dims_summary": "明细和=82,000,000 total_this=88,000,000 diff=6.82%",
    "missing_dims_text": "无",
    "completeness_text": "完整",
    "anchor_diff_text": "合计 +0.0%(过锚) | 明细和未闭合 diff>1%",
    "cross_page_hint": "未发现跨页信号",
    "unit_note": "【单位提示】源文「万元」，解析值已换算为「元」。",
    "trust_note": "",
    "source": "| 项目 | 本期金额(万元) |\n| 职工薪酬 | 4,000 |\n| 直接投入 | 3,000 |\n| 折旧摊销 | 1,200 |\n| 合计 | 8,800 |",
    "next_table_preview": "(无)",
    "candidates_brief": "#1 p42 研发费用 rows=10",
    "field_value_json": "{\n  \"rnd_detail\": [\n    {\"name\": \"职工薪酬\", \"amount_this\": 40000000},\n    {\"name\": \"直接投入\", \"amount_this\": 30000000},\n    {\"name\": \"折旧摊销\", \"amount_this\": 12000000}\n  ],\n  \"total_this\": 88000000\n}"
  },
  "expected": {
    "verdict": "hold",
    "suspects": [{"issue": "amount_error", "field": "total_this"}],
    "summary_contains": ["明细", "合计", "82000000"],
    "pipeline_outcome": "verify_hold"
  }
}
```

---

### 9.8 TC-07 · hold（员工教育程度缺失）

```json
{
  "case_id": "TC-07",
  "code": "MOCK007",
  "year": 2025,
  "field": "employees",
  "variables": {
    "field": "employees",
    "field_label": "员工",
    "field_cls": "C",
    "field_spec_note": "【字段准则】专业构成与教育程度人数之和应≈ total。",
    "table_markers_text": "专业构成, 教育程度, 在职员工",
    "pick_meta": "page=55 rows=12 cols=4 via=keyword caption=员工情况",
    "grounding": "选中表网格",
    "anchor_summary": "（C 类无 DB 营收锚）",
    "dims_summary": "composition_sum=868 total=868 | education_sum=缺失",
    "missing_dims_text": "['education']",
    "completeness_text": "不完整 — 缺失维度: ['education']",
    "anchor_diff_text": "composition 过锚 | education 未解析",
    "cross_page_hint": "未发现跨页信号",
    "unit_note": "",
    "trust_note": "",
    "source": "| 类别 | 人数 |\n| 专业构成 | |\n| 生产人员 | 500 |\n| 技术人员 | 200 |\n| 销售人员 | 100 |\n| 合计 | 868 |\n| 教育程度 | |\n| 本科及以上 | 320 |\n| 大专 | 280 |\n| 合计 | 868 |",
    "next_table_preview": "(无)",
    "candidates_brief": "#1 p55 员工情况 rows=12",
    "field_value_json": "{\n  \"total\": 868,\n  \"composition\": [\n    {\"type\": \"生产人员\", \"count\": 500},\n    {\"type\": \"技术人员\", \"count\": 200},\n    {\"type\": \"销售人员\", \"count\": 100}\n  ],\n  \"education\": []\n}"
  },
  "expected": {
    "verdict": "hold",
    "suspects": [{"issue": "incomplete_table", "field": "education"}],
    "summary_contains": ["教育程度", "源文存在"],
    "pipeline_outcome": "verify_hold"
  }
}
```

---

### 9.9 TC-08 · pass（选表自愈二次复核 + trust_note）

```json
{
  "case_id": "TC-08",
  "code": "MOCK008",
  "year": 2025,
  "field": "revenue_breakdown",
  "variables": {
    "field": "revenue_breakdown",
    "field_label": "营收",
    "field_cls": "A",
    "field_spec_note": "【字段准则】营业收入构成表。",
    "table_markers_text": "占营业收入比重, 占比",
    "pick_meta": "page=22 rows=20 cols=6 via=llm_select caption=营业收入构成(分产品) heal_select=true",
    "grounding": "选表自愈表",
    "anchor_summary": "权威营业收入 ≈ 1,100,000,000 元",
    "dims_summary": "segments=1,100,000,000",
    "missing_dims_text": "['industries', 'regions', 'by_channel']",
    "completeness_text": "完整（所有预期维度均过锚）",
    "anchor_diff_text": "segments +0.0%(过锚)",
    "cross_page_hint": "未发现跨页信号",
    "unit_note": "",
    "trust_note": "\n【选表已确认】上面这张表是选表 agent 从全表里认定的**正确营业收入构成表**。因此**不要再判 wrong_table,也不要因'合计略小于营业收入'就判不完整**——你只需逐项核对数据，逐项对得上就 pass。\n",
    "source": "| 产品 | 本期金额 | 本期占比 |\n| 产品A | 660,000,000 | 60% |\n| 产品B | 440,000,000 | 40% |\n| 合计 | 1,100,000,000 | 100% |",
    "next_table_preview": "(无)",
    "candidates_brief": "(二次复核 — 不再比对候选，以 2a 为准)",
    "field_value_json": "{\n  \"segments\": [\n    {\"name\": \"产品A\", \"revenue_yuan\": 660000000},\n    {\"name\": \"产品B\", \"revenue_yuan\": 440000000}\n  ]\n}"
  },
  "expected": {
    "verdict": "pass",
    "suspects": [],
    "must_not_contain_issue": ["wrong_table", "cross_page"],
    "summary_contains": ["逐项", "一致"],
    "pipeline_outcome": "committed"
  }
}
```

---

### 9.10 汇总文件（`goldset/verify_prompt_fixtures.json`）

接入测试时可将下列数组写入仓库（**当前仅文档内 mock，文件未创建**）：

```json
{
  "schema_version": "verify_prompt_fixtures_v1",
  "prompt_template": "verify",
  "prompt_version": "v2-mock",
  "cases": [
    "TC-01", "TC-02", "TC-03", "TC-04",
    "TC-05", "TC-06", "TC-07", "TC-08"
  ],
  "note": "完整 case 定义见 docs/mock-verify-prompt-v2.md 第九节；pytest 可 parametrize case_id 读入 variables + expected"
}
```

### 9.11 pytest 用法 sketch（未实现）

```python
# tests/test_prompts_verify.py （将来）
# @pytest.mark.parametrize("case", load_fixtures("goldset/verify_prompt_fixtures.json"))
# def test_verify_prompt_golden(case):
#     messages = render_verify_v2(case["variables"])
#     out = call_llm_or_mock(case.get("llm_mock_response"))
#     assert out["verdict"] == case["expected"]["verdict"]
#     for s in case["expected"].get("suspects", []):
#         assert any(x["issue"] == s["issue"] for x in out["suspects"])
```

### 9.12 人工回放清单

在 VerifyTest 控制台或 curl 调试时，按 case_id 粘贴 **`variables.field_value_json`** 与 **`variables.source`**，核对 LLM 是否给出 **expected.verdict**：

1. TC-01 → 必须 pass  
2. TC-02 → 必须 wrong_table（触发选表自愈）  
3. TC-03 → 必须 incomplete_table（不判 wrong_table）  
4. TC-04 → 必须 amount_error 且 reason 含「上期」  
5. TC-05 → 必须 pass（勿因缺 top_clients 明细 hold）  
6. TC-06 → 必须 hold（明细和 8200万 ≠ 合计 8800万）  
7. TC-07 → 必须 hold（education 源文有、JSON 空）  
8. TC-08 → 必须 pass 且 suspects 无 wrong_table  

---

## 六、Token 预算（mock 估算）

| 块 | 字符量级 |
|----|----------|
| 机器预检 | ~800 |
| 2a 主表网格 | ~2k–4k（35 行内） |
| 2b 续表 | ~0–1.5k |
| 2c top2 候选 | ~1k |
| JSON | ~1k–3k |
| 规则正文 | ~2.5k |
| **合计 user** | **约 8k–12k 字符（~4k–6k token）** |

与 Agent 深度化 Phase 2 预算（judge 8k–15k）对齐；超长时：2c 只保留 top1、2a 截 35 行。

---

## 七、接入清单（将来改代码时用）

- [ ] `src/prompts/context/verify.py` — `gather_verify_context(code, year, field, value, sig, …)`
- [ ] `build_verify_messages` 调用 context，不再只传 `anchor_note`
- [ ] `verify.yaml` 替换为 v2（或 cls 分模板）
- [ ] `tests/test_prompts_verify.py` — **第九节 TC-01~08** 金标准
- [ ] VerifyTest 控制台展示新增 context 块

---

## 八、与 judge_diagnose 的分工（避免重复）

| 场景 | agent |
|------|--------|
| 未过锚 / 冷启动失败 | judge_diagnose |
| **已过锚绿灯**，入库前最后一关 | **verify（本 mock）** |
| 选表自愈后二次复核 | verify + `trust_note` |

verify **不输出** `next_action` / `fix_json`；只输出 `verdict` + `suspects`。
