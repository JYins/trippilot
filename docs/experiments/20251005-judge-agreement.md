# 实验 A：DeterministicJudge vs 规则 verdict 一致性

日期：2026-10-05 ｜ 运行：`eval/judge_agreement.py` ｜ 原始数据：`data/judge_agreement.json`
live DeepSeek 分数：**pending**（无 `TRIPPILOT_JUDGE_API_KEY`，按铁律不伪造分数）

## 做了什么

1. 对 `fixtures/dataset_v0.jsonl` 全部 12 条用例跑 `run_case(..., judge=DeterministicJudge())`，
   记录每条的规则 pass/fail + `plan_efficiency` / `clarify_quality`。
2. 造了 3 条**合成退化轨迹**（planner 兜圈、调了允许范围外的工具、走了 clarify 却一句追问没说）：
   这些轨迹的规则 verdict 永远 PASS（该走的节点都走齐了），看 judge 能不能指出"走得对但走得蠢"。

## 数字结果

| case_id | 规则 verdict | plan_efficiency | clarify_quality | 备注 |
|---|---|---|---|---|
| TP-ROUTE-001 | PASS | 1.00 | 0.60 | 澄清默认桶 |
| TP-AMBIG-001 | PASS | 1.00 | 1.00 | 歧义已解决 |
| TP-LOWCONF-001 | PASS | 1.00 | 1.00 | 歧义已解决 |
| TP-DRIVE-001 | PASS | 1.00 | 0.60 | 澄清默认桶 |
| TP-MEM-001 | PASS | **0.40** | 0.60 | human_confirm 回绕 3 次 |
| TP-MEM-002 | PASS | 1.00 | 0.60 | 澄清默认桶 |
| TP-MEM-003 | PASS | 1.00 | 0.60 | 澄清默认桶 |
| TP-CLARIFY-002 | PASS | 1.00 | 1.00 | 歧义已解决 |
| TP-DRIVE-002 | PASS | 1.00 | 0.60 | 澄清默认桶 |
| TP-IDEM-001 | PASS | 1.00 | 0.60 | 澄清默认桶 |
| TP-RECOV-001 | PASS | **0.40** | 0.60 | recovery 重试 3 次 |
| TP-RECALL-001 | PASS | 1.00 | 0.60 | 澄清默认桶 |

合成探针（规则全 PASS，judge 打分）：

| 退化轨迹 | plan_efficiency | clarify_quality |
|---|---|---|
| planner 兜圈 3 次 | 0.60 | 1.00 |
| 调了允许范围外的工具 | 0.50 | 1.00 |
| 走了 clarify 但一句追问都没说 | 1.00 | 0.30 |

## 好消息

- judge 在"规则写不出的维度"确实有分辨力：3 条合成烂轨迹 **3/3 被打低分**，而规则 verdict 会全部放行。
  这验证了 judge.py 开头的设计断言——judge 只评 advisory 维度是有信息增量的，不是摆设。
- 0/12 "规则 FAIL 但 judge 高分"（本轮规则全过，本来就没有 FAIL case 可对照）。

## 坏消息（诚实写）

1. **"规则过了但 judge 觉得轨迹烂"有 2 例**：TP-MEM-001（0.40）、TP-RECOV-001（0.40）。
   但细看是**误报**：MEM-001 的重复节点是 `human_confirm` 回绕（敏感记忆确认后重入
   `tool_executor`/`memory_capture`，是设计行为）；RECOV-001 的重复是 `recovery` 节点的工具重试路径。
   DeterministicJudge 把**合法的确认回绕和恢复重试都当成了"绕路"**——它只会数重复，
   不知道重复是为什么。缺一个"合法回绕白名单"。
2. **`clarify_quality = 0.60`（9/12）几乎无信息量**。这是"走了 clarify 但 fixture 没预置答案"时的默认桶。
   可图里 clarify 节点每条轨迹都会过一遍（多数是 no-op），所以这 9 个 0.6 的真实含义是
   "这句本来就不需要澄清"，却被标成"触发了澄清但未确认问法质量"。读报告的人会被误导。
   规则写不出"问得好不好"，这个 0.6 默认桶也不诚实——它是 DeepSeekJudge 该干的活。
3. `plan_efficiency = 1.00`（10/12）：全员满分，零区分度。也说明评测集里**没有真实的规划绕路 case**，
   合成探针的 0.60/0.50 目前只在实验室里存在。

## 结论

DeterministicJudge 能当"烂轨迹烟雾报警器"（合成探针 3/3 命中），但有两个诚实缺陷：
合法回绕会被误判成绕路（2 例假阳性），clarify 0.6 默认桶在误导读报告的人。
改进方向：数重复节点时排除 `human_confirm` / `recovery` 触发的重入；
clarify 没实际提问时记"无需澄清"而不是 0.6。
live 分数等 key 开通后单独报告，不混进这份。

**简历能讲的一句**：12 条评测轨迹规则全过（12/12 PASS）的前提下，
DeterministicJudge 在 3 条合成退化轨迹上 3/3 检出"走得对但走得蠢"的问题，
同时发现 2 例合法回绕被误判为绕路的假阳性并给出修复方向。
