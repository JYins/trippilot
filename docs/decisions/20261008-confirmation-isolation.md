## 背景

`20261006-eval-assertions.md` 末尾的“已知缺口”记录了一个运行时问题：一次工具确认会把全局 `confirmation_state` 置为 `confirmed`，同一 turn 后续的敏感记忆因此可以直接写入。约束是修复运行时语义，同时不回归旧的单类别流程；`api.py` 的多轮确认、`eval_runner` 的 `confirm_ok` 和现有测试仍然读取 `confirmation_state`，所以保留该字段和原有赋值行为。

## 候选方案

1. 工具和记忆都做全对象级确认，工具按 tool+args 绑定 ID；隔离最细，但 recovery 改 args 重试时会被误判为缺少确认，尤其会破坏 TP-RECOV-002 这类场景。
2. 维持单个 `confirmation_state`；兼容成本最低，但无法修复一次工具确认替敏感记忆放行的问题。
3. 工具保持类别级确认、记忆改为对象级确认；会新增两个状态字段，但能在不改变恢复语义的前提下阻止敏感记忆搭便车。

## 选择

选择方案 3：工具使用类别级 `tool_call_confirmed` 布尔值，记忆使用对象级 `confirmed_memory_ids`，按 `memory_confirm_id` 对账。

## 为什么

工作日志：对照现有图以后，我看到同一 turn 里的工具会先确认和执行，敏感记忆则到 `memory_capture` 才出现；两者共用 `confirmation_state` 正是搭便车的入口。我纠结过是否把工具也一次收紧到对象级，但 recovery 会替换失败调用的参数再重试，对象 ID 随 args 改变会把已经允许的合法恢复判成缺确认。工具因此保留类别级语义。

记忆侧已经有稳定的 `memory_confirm_id`，审计层 `_memory_events_ok` 也按这个 ID 对账。敏感信息写入的代价更高，值得把授权限定到当时挂起的具体候选。代价是状态里要保留已授权 ID，并在 capture 时逐条核对。`tool_call_confirmed` 仍是 turn 内粗粒度：一次工具确认覆盖本 turn 所有工具调用；这与原来一致，没有收紧也没有放松。

## 对 eval 的影响

现有 33 条 eval 的语义不变。新增 `tests/test_confirmation_isolation.py` 三个反例，分别覆盖工具确认不能授权记忆写入、记忆确认不能授权工具调用，以及敏感候选未确认时必须继续 pending。`human_confirm/confirmed` 事件的 `confirmed_ids` 收紧为“本次实际放行的记忆候选 ID”；工具确认时该列表原本就是空列表，因此现有轨迹语义不变。
