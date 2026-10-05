# 决策：评测集 dataset_v0 扩到 12 条（含放弃对抗载荷 fixture）

日期：2026-10-05

## 背景

eval 数据集 v0 从 5 条扩到 12 条，覆盖 v0.1 已合入的能力：记忆 Gate（敏感/瞬态/多偏好）、
澄清质量、行驶降级边界、幂等去重、恢复路径。其中 TP-INJ-001 最初设计为对抗用例：
scripted_plan 里塞一个"被污染"的录制 fixture，验证 tool_executor 层的注入拦截。
问题是——为了测拦截，仓库里得常驻一份恶意指令载荷，这本身踩了安全红线。

## 候选方案

- A. 保留 TP-INJ-001：端到端对抗样本最完整，但仓库常驻恶意载荷文本，且对抗载荷被
  `scan_tool_text` 拦截后走的其实是同一条恢复路径，端到端增量信息有限。
- B. 删掉 TP-INJ-001，换成 TP-RECOV-001（恢复用例）：用无害方式制造工具失败
  （`fixture: "no_such_fixture"` → 录制文件缺失 → 工具报错），照样覆盖 verifier 标失败
  → recovery 重试 → 默认 fixture 成功的整条恢复链路。
- C. 两边都不要，注入拦截只靠单元测试，eval 不再覆盖失败/恢复路径。

## 选择

B。顺带确认 C 的前提已经成立：`tests/test_policy_gate.py:89-92` 已有 `scan_tool_text`
的 2 正例 + 1 反例测试，污染拦截在单元测试层有回归，不需要 eval 再背一份。

## 为什么

- **安全红线优先**：评测仓库不放恶意载荷，是原则问题，不是信息量问题。想测拦截，
  单元测试的断言更直接（输入→拦截判定），不需要把载荷走一遍全图。
- **恢复链路是独立能力，值得单独一条用例**：失败→重试是生产里真实会发生的路径
  （录制缺失、工具超时），TP-RECOV-001 用无害手段触发它，trace 里
  `tool_failed → retry_once → 成功` 全留痕，符合 harness-event-sourcing 的设计。
- **代价（诚实记下）**：失去"真实恶意输入进图"的端到端样本。如果将来有人改了
  tool_executor 的拦截调用位置，单元测试能拦住判定逻辑，但拦不住"调用位置被挪走"
  这类集成回归——这是已知盲区，接受。

## 对 eval 的影响

- 新增评测维度：**失败恢复**。TP-RECOV-001 的 `required_nodes` 含 `recovery`，
  `success_criteria` 要求 tool_failed 后恢复、默认 fixture 重试成功、终态
  verification ok。eval_runner 现有断言（节点覆盖 + verification ok）天然覆盖，无需改 runner。
- 7 条新用例维度：记忆自动抽取（TP-MEM-002，瞬态拒绝/稳定偏好写入）、敏感记忆挂起
  （TP-MEM-003，未确认不写）、澄清质量（TP-CLARIFY-002）、行驶降级边界
  （TP-DRIVE-002）、幂等去重（TP-IDEM-001）、恢复路径（TP-RECOV-001）、
  记忆多偏好召回写入（TP-RECALL-001）。
- source_type 分布：derived_variant 8 / synthetic 3 / real_recalled 1。
  合成类合计 3/12，满足 ≤3/12 上限；真实召回 1 条打底。
  （注：TP-LOWCONF-001 的原注入对抗用例已按本决策替换为 TP-RECOV-001，
  因此不再保留 synthetic_attack 标注，避免把"低置信拦截"误称"攻击样本"。）
- 污染拦截回归项保留在单元测试层（test_policy_gate.py::test_injection_scan），
  eval 不再重复覆盖注入维度。
