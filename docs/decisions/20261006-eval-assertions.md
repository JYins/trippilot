## 背景

审查 blocker #13、#14 指出两处会制造假 PASS 的缺口：dataset 声明了 `success_criteria`，runner 却没有执行；敏感记忆审计只看写入前是否出现过任意 `human_confirm`，工具确认也能搭便车。约束是所有判断必须确定性、未知规则必须显式失败，并且不为审计增加新的 state 字段。

## 候选方案

1. 继续扩充 `run_case` 内的条件分支：改动集中，但规则很快会变成长函数，也容易漏掉未知 key。
2. 用小函数表逐项解释 criterion，并用候选内容生成稳定确认 ID：多了少量函数，但每条规则可单测，未知 key 也能统一 fail loud。
3. 给 pending state 增加随机 nonce：隔离性更强，但会改变状态协议，也不利于 fixture 的确定性回归。

## 选择

选择方案 2。确认 ID 固定为 `sha1(kind + content)` 的前 16 位；它只进入 trace 的 `pending_confirm_ids`、`confirmed_ids` 和 `written_confirm_ids`，不增加 state 字段。runner 用函数表执行当前 dataset 的全部 criterion，并把失败原因写入单条用例结果。

## 为什么

工作日志：我先对照了 recovery 轨迹，发现恢复逻辑已经按目的地把国贸映射到 `guomao`，所以旧名 `recovered_with_default_fixture` 描述的是已经不存在的实现细节；继续断言 default 会把正确恢复判错。改名为 `recovered_with_matching_fixture` 后，断言关注 fixture 确实发生替换且重试调用使用了替换后的值。

确认审计不能使用记忆库 ID，因为它要到写入之后才产生；也不采用随机值，因为离线回归需要稳定复现。内容散列的代价是相同 kind/content 会得到相同 ID，但这正符合“同一个候选”的审计粒度，也避免改动 `TripPilotState`。criterion 函数表会增加一处登记成本，换来的是未知规则立即失败，不再静默扩大评测盲区。

## 对 eval 的影响

`success_criteria` 现在进入 pass/fail：创建提醒、瞬时内容拒绝、普通偏好写入、敏感候选挂起、幂等抑制、失败恢复、最终验证和精确写入条数均有确定性断言。未知 key 产生 `unknown success criterion` 失败。敏感记忆只有在相同 confirm ID 更早出现在已确认事件中，且写入事件声明同一 ID 时，`memory_events_ok` 才能通过；工具确认不再能冒充记忆确认。
