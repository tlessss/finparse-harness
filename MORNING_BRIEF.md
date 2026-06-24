# 早安简报 — 夜间自主推进结果（2026-06-23）

## TL;DR
- 把计划里**不需要人工**的代码全部写完并测试通过（19/19）：Phase 0 工具、Phase 1 指纹、Phase 3 条件路由、Phase 4 全量跑批+看板、Phase 2 沙箱框架。
- 修了 2 个真实 bug（营收合计行误计、银行误判）。
- **全量 1249 份跑完，0 报错 0 超时**：硬规则清洁 **44.0%**、净通过 **18.7%**；死信 699 里 **643 是营收漏行**。
- 金标准模板已生成 `goldset/goldset.json`（60 份待标注）。
- 发现 3 个关键问题，决定后续走向（见下）。

## 现在就能跑的命令
```bash
python3 -m scripts.run_status                 # 全量跑批结果 + 死信聚类
python3 -m scripts.run_status --deadletter    # 死信明细
python3 tests/test_hard_rules.py && python3 tests/test_fingerprint.py && python3 tests/test_sandbox.py
```

## 三个关键发现（需要你拍板）
1. **营收漏行是主因**：占比之和 57~87% 的红线，是表格抽取漏掉了产品/地区行，不是合计行。
   合计行修复只把 60 家清洁率 41.7%→43.3%。真正的提升要做 **Phase 1.2 表头驱动列识别 + 多行/跨表合并**，这块建议你我一起按版式 review，不适合我无监督盲改。
2. **解析器不读 YAML（架构级阻塞）**：现有解析器 `parse()` 根本不用 `self.rule`，所以"优化 Agent 改 YAML"对行为零影响——这就是旧迭代闭环空转的根因。
   **Phase 2 的前置任务**：先把解析器改成 rule-driven。我已开了第一个旋钮 `extra_exclude_names` 做样板 + 沙箱验证框架。
3. **银行营收**走错了解析器路径（333% 过度计数），需修银行分支。

## 需要你做的一次性人工事项
- **标注金标准集**（Phase 0.1，唯一绕不开的人工）：
  ```bash
  python3 -m scripts.build_goldset --from run_state/2025/results.jsonl --out goldset/goldset.json
  # 然后编辑 goldset/goldset.json，把每个 correct: null 改成 true/false（建议先标 50~80 份）
  python3 -m scripts.eval_validator --gold goldset/goldset.json --validator hard
  ```
  这一步是为了证明"校验器当裁判"可信（error_recall ≥0.95），是纯自主的地基。

## 建议的下一步优先级
1. 一起做 Phase 1.2（营收漏行）——对净通过率提升最大。
2. Phase 2.0 解析器 rule-driven 化——解锁真正的自主修复。
3. 标注金标准集——锁定可信度。

详细进展见 `docs/全自主解析计划表.md` 的「🌙 夜间自主推进进展」一节。
</content>
