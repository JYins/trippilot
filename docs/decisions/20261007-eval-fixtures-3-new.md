## 背景

第 2 周要把轨迹评测从 12 条扩到 15 条，补齐长链出行、地点歧义后创建提醒、以及“服务端已创建但客户端超时”的恢复路径。地图和天气仍只读 `fixtures/recorded/`，车况仍是 `parked_simulated`；这些结果不代表实时路况、天气或真实车辆信号。

## 候选方案

1. 三条都只写 `scripted_plan` 和节点覆盖断言；改动最少，但“经过 clarify”不等于“副作用发生前已澄清”，也证明不了最终地点绑定。
2. 保留节点子序列，再增加确定性的成功标准检查；能检查事件先后和最终状态，代价是 runner 多两个小检查函数。
3. 为超时另造一个生产工具模式；复用方便，但会把评测故障注入带进生产路径。
4. 在 eval 内构建同一张图并注入工具包装器；不改生产工具，代价是 eval 需要显式维护图边，图结构变化时要同步。

## 选择

选择方案 2 和 4。`TP-ROUTE-002` 用脚本固定调用 `map.route`、`weather.forecast`、`reminder.create`，避免默认 stub 把 forecast 改成 now；路线使用 `guomao`，天气使用现有 `default` 录制响应。`TP-AMBIG-002` 同时用 `node_sequence` 检查 `clarify → planner → human_confirm → tool_executor`，用 `clarify_before_side_effect` 比较 trace 中 `clarify_checked` 与首次含 `reminder.create` 结果的 `tool_executor` 事件索引，并用 `destination_bound` 检查最终 `trip_context.destination == 北京西站`。`TP-RECOV-002` 的 eval 包装器第一次先真实调用内存 ReminderTool，再返回 `timeout after create`；恢复节点使用原参数和原幂等键重试，第二次必须返回 `duplicate_suppressed=true`。

## 为什么

只看节点集合会漏掉顺序错误，只看最终提醒又会漏掉地点没有绑定的问题，所以歧义用例必须同时检查顺序和状态。没有用 monkeypatch：包装器只由 case 的 `tool_fault` 配置创建，并通过 eval 自己的工具查找函数传入执行节点，生产工具注册表不变。放弃给生产 ReminderTool 增加“超时模式”，因为那会让测试开关污染产品语义。代价是 eval 图与生产图有少量重复；目前边数有限、差异清楚，后续若图频繁变化，应再提取只读的图装配入口并另写决策记录。

## 对 eval 的影响

数据集变为 15 条。新增长链三工具与确认覆盖、澄清先于副作用和目的地绑定、超时后原幂等键恢复三类回归。`tool_fault` 目前只接受 `reminder.create` 且要求 `run_tool_first=true`，不支持任意故障，避免配置看似通用但实际语义不明确。

## 补记（2026-10-07 工程自查）

最初为了只在 eval 注入故障工具，选择在 runner 复制整张生产图；当时边数少，认为同步成本可控。自查发现两份 `memory_capture` 路由已经出现细微分叉：生产代码会把空的 `verification_result` 归一成字典，eval 代码直接调用 `.get`。这说明复制图会让生产图演进后 eval 静默跑偏，因此改为给生产 `build_graph` 增加默认值为 `get_tool` 的 `tool_lookup` 参数，eval 只注入 `_eval_tool_lookup(case)`。生产调用没有传新参数，执行节点拿到的仍是同一个 `get_tool`；现有全量测试与 15 条回归共同作为生产默认行为零变化的证据。

`TP-ROUTE-002` 的目的地国贸位于朝阳区，沿用海淀区天气会让“结合天气”的用例语义自相矛盾。新增 `weather_chaoyang.json` 并让用例查询朝阳区，而不是继续复用海淀区录制文件。新文件的数值只是从已有版本化样本复制，用于稳定测试，不宣称是朝阳区真实天气；文件继续明确标注“不代表实时天气”。

## 补记（20261007 评审打回）：TP-AMBIG-002 context 去掉 destination 种子

reviewer A 实证：fixture context 自带 `"destination": "北京西站"`，而 `_run_case_with_store` 把 context 原样灌进初始 `trip_context`，于是 `_destination_bound` 断言的 `out.trip_context.get("destination")` 从第一行就是期望值——删掉 clarify 的绑定逻辑也照样通过，是假绿。已从 context 删掉 destination 种子（保留 clarify_answer），删后 15/15 仍通过；此时 `destination_bound` 才真正探测 clarify 绑定而不是 fixture 种子。教训：成功标准检查要读"行为产生"的状态，不能读 fixture 自己播下的种子——和 10-06 全库审查点名的"读 success_criteria 但不断言"同类。
