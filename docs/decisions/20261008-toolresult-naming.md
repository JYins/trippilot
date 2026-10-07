## 背景

任务一执行中，新增的评测断言单测（`tests/test_eval_real_trips.py`）暴露了一个
命名不一致：`BaseTool._run_recorded` 构造 `ToolResult` 时用 `tool=self.name`
（短名，如 `"map"`），而 `ReminderTool` 和 `_unsupported_operation` 用的是
`tool=call.tool`（点分操作名，如 `"reminder.create"`）。评测的 14 个
`success_criteria` 检查全部按点分名过滤 `result.tool`，于是 map/weather 相关
断言永远匹配不到真实结果——检查看起来严格，实际是假阴性。
实证：TP-REAL-006 的录制数据含 `remaining_km`/`remaining_min`、工具返回
ok=True，但 `_remaining_distance_given`/`_remaining_time_given` 判失败；
单测 `test_spotcheck_006_passes` 如实标红，没有被 mock 掩盖。

## 候选方案

1. 只改评测检查，接受两种名字（`result.tool in ("map.route", "map")`）：
   不动产品代码，但把产品的不一致编码进 14 个检查，以后每个新检查都要踩一遍。
2. 改产品：在 `_run_recorded` 里统一用 `call.tool`（点分操作名）：
   一处改动消灭不一致，和 `ReminderTool`/`_unsupported_operation` 对齐；
   代价是 trace 里工具结果的 `tool` 字段从短名变成点分名。
3. 不修，把 006 改成"预期失败"：等于用期望去迁就 bug，违反"不许假绿"铁律。

## 选择

方案 2。`trippilot/tools/base.py` 的 `_run_recorded` 内 5 处
`ToolResult(tool=self.name, …)` 改为 `tool=call.tool`，加一句"为什么"注释；
`_fixture_path`（文件名用短名）和 `_run_live` 报错不动。连带修了一个旧测试
`tests/test_gate_enforcement.py` 里按短名 `"map"` 找结果的行，改为 `"map.route"`。

## 为什么

选 2 不选 1：不一致的根在产品层，不在评测层。评测按点分名过滤是对的
（`ToolCall.tool` 本来就是点分名，policy/trace 也都用点分名）；
让 14 个检查各自兼容两种名字，是把一次性的产品 bug 变成永久的评测负担。
改之前 grep 确认过：产品代码里没有任何地方依赖 `result.tool` 的短名
（recovery 用的是 `call.tool`，judge 不读 `result.tool`，`llm.py` 只拿它做展示文本），
所以这是一行语义的收敛，不是行为变更。`final_answer` 里显示
"map.route: 成功"反而比"map: 成功"更精确。
不选 3 的理由：006 的失败信号是真实的回归信号，修 bug 不修期望。

## 对 eval 的影响

- `test_spotcheck_006_passes` 由红转绿；全量 pytest 130 通过。
- TP-REAL-009 的 4 个 criterion（distance/duration/rest_stop/weather_checked）
  现在全部通过，该用例仍失败但失败原因收敛到唯一的诚实缺口：
  `confirm_ok=False`（长途确认策略未实现，见 fixtures 决策记录缺口 3）。
- 新增回归维度：工具结果命名一致性（`test_gate_enforcement` 的改动行即回归锚点）。

## 诚实边界（本次发现、如实记录）

- `_plate_not_fabricated` 只拦截完整车牌号（如 `京A12345`），不识别"限行 3 和 8"
  这类尾号表述。004/013 的"不编造车牌"断言目前只防"编完整车牌"，防不住"编尾号"。
  尾号语义的断言需要单独设计（尾号是政策数据，不是车牌隐私），留给后续任务，
  不在本任务里悄悄放宽或收紧。
