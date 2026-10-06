## 背景

审查 blocker #12 指出，Policy Gate 拒绝工具后直接进入 verifier。旧实现只检查被拒工具没有执行，于是把这条轨迹标成 `ok=true`，并回复“本次没有执行工具调用”。门禁确实生效了，但用户任务没有完成；把两件事合成一个布尔值会制造假成功，也没有向用户解释拒绝原因。

## 候选方案

1. deny 时把原有 `ok` 改成 `false`：改动最小，但会把“门禁正确拦截”误报成安全校验失败，也会让现有 eval 无法区分违规执行和合规拒绝。
2. 保留 `ok` 表示安全策略是否正确执行，新增 `task_completed` 表示用户任务是否完成：字段多一个，消费方必须同时理解两个状态，但语义不再混淆。
3. 为 verifier 引入枚举状态：表达力更强，但会扩大 API、eval 和测试改动，本次 blocker 不需要这层复杂度。

## 选择

选择方案 2。deny 且工具未执行时记录 `ok=true, task_completed=false`；正常完成记录 `ok=true, task_completed=true`。工具失败会令 `task_completed=false`，恢复路由因此改读任务完成状态。拒绝回复由 verifier 用首条 deny 决策的 `detail` 和 `reason_code` 确定性生成。

## 为什么

工作日志：先沿着 deny 路由确认它不会经过 tool executor，再检查到 recovery 和 eval 都在读取 `ok`。只给结果加字段而不改这两个读点，会导致工具失败不再正确恢复，或 deny 用例仍被普通成功规则误判。最终保留 `ok` 的安全合规语义，避免“安全门禁成功”被写成失败；代价是调用方不能再只看一个布尔值。拒绝话术同时带可读 `detail` 和稳定 `reason_code`，前者给用户解释，后者方便排障；没有把话术交给 LLM，避免同一拒绝产生不稳定表述。

## 对 eval 的影响

eval 现在把预期 deny 定义为：reason code 命中、没有工具执行、`ok=true` 且 `task_completed=false`；普通用例则要求 `ok=true` 且 `task_completed=true`。这样报告能区分“任务真的成功”和“安全策略成功拒绝”。新增回归测试覆盖 deny 的双状态、可读拒绝原因、零工具执行，以及正常成功轨迹的完成状态。
