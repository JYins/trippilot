## 背景

TripPilot 主线任务一：把用户口述的 15 条真实行程（TP-REAL-001~015，2026-10-07
访谈替代方案，设计稿见 `docs/eval/real-trips-15-cases.md`）逐条转成
`fixtures/dataset_v0.jsonl`，评测集从 15 条扩到 30 条，`source_type=real_recalled`。
约束：地图/天气只走 `fixtures/recorded/` 录制响应（只录制、不调真实接口）；
车况纯软件模拟；限号用例不许编造用户车牌；`success_criteria` 逐项真断言
（延续 20261006-eval-assertions 的"逐项断言、不许假绿"铁律）；
media/vehicle/restriction/knowledge 四类工具尚未实现（任务三），依赖它们的用例
若跑不通，诚实报告是哪几条、卡在什么上，不许为过评测放水。

## 候选方案

1. 15 条原样追加（含 `follow_ups` 多轮结构），缺的断言先空着：改动最小，
   但 runner 根本不支持多轮（`run_case` 是单轮直跑，没有 `follow_ups` 相关代码），
   多轮语义会被静默丢掉；空断言直接违反"逐项真断言"铁律。
2. 15 条逐条转 fixture + 补全 recorded fixtures + 26 个新 criterion 逐项实现
   + TP-REAL-015 降级拆单轮：工作量大，但每条 `success_criteria` 都有真断言，
   跑不通的用例失败原因精确可归因到运行时缺口或工具缺失。
3. 等任务三工具实现后再转：延期，违反主线排期（用户 10-07 明确"先推完主线"）。

## 选择

选择方案 2。实际产出 18 条新 case（001~014 共 14 条 + 015 拆成 4 条单轮），
dataset 15→33 条（不是 30，原因见"为什么"第一条）。

## 为什么

工作日志，按当时纠结的顺序写：

**条数是 33 不是 30。** 设计稿的"15→30"假设 TP-REAL-015 保持 1 条；
但实证 `eval_runner.py` 里没有任何 `follow_ups` 处理——`run_case` 调一次
`run_graph` 就结束。按设计稿实现备注 #3 的预批路径降级拆成 4 条单轮
（015a 去亦庄 / 015b 还有多远 / 015c 换首歌 / 015d 空调小一点），
14+4=18 条新 case，15+18=33。没有为凑 30 而合并或删除用例；
条数是诚实拆分的结果，不是凑数。

**destination 种子全部拔掉（001/002/008/014）。** 20261007-eval-fixtures-3-new
的补记教训：context 自带 `destination` 会让 `destination_updated` 类断言
读到 fixture 自己播下的种子——删掉 clarify 绑定逻辑也照样通过，是假绿。
拔掉后这些断言才真正探测运行时行为。代价是 001/002/008/014 的
`destination_updated` 诚实失败（见缺口 1），认了。

**014 的 asr confidence 从 0.62 改为 0.55。** 设计稿写的是"低置信度"场景，
但运行时阈值 `LOW_CONFIDENCE_THRESHOLD=0.6`，0.62 在阈值之上，clarify 根本
不会触发。这是设计稿的笔误（写的时候没查阈值），改为 0.55 是忠实表达
"低置信度"场景的最小修正，不是为过评测调参——0.55 和 0.62 在"是否触发澄清"
这个二值语义上是同一侧，改的是笔误不是标准。

**scripted_plan 而不是扩展 DeterministicStub。** stub 只认
"路线/导航/怎么去/几点出发/天气/提醒"关键词，15 条真实口语
（"带我去798""换首歌""还有多远"）大部分落空。候选 A 是给 stub 加关键词——
但 stub 是无 key 时的产品默认 LLM（`make_llm` 回退），改它等于改产品行为，
超出任务一范围；候选 B 是沿用仓库已有惯例
（TP-LOWCONF-001、TP-ROUTE-002 已在用）给用例配 `scripted_plan`。选 B。
代价写清楚：这些用例测的是"规划器给出正确计划后"的执行/策略/验证链路，
不测 stub 的意图理解——而 stub 的意图理解本来就不是产品能力
（`llm.py` 注释原话："这是测试脚手架，不是产品能力"）。

**004/013 的 scripted_plan 同时包含 restriction.query 和 map.route。**
policy_gate 对未知工具判 `unknown_tool` deny，会短路整轮（两个工具都不执行）。
这是今天的真实行为——如实录下来，失败归因精确到
"restriction 工具未实现 → unknown_tool → 整轮 deny"。
如果只写 map.route，会掩盖"用户同时问了限号"的真实场景，
把一次"整轮失败"粉饰成"部分成功"。

**009 的 requires_confirmation=true 保留，不删。** 设计稿明确要求长途出发前
用户确认；运行时 policy_gate 对 map.route/weather.forecast 只有 read-allow，
没有长途确认规则 → `confirm_ok` 诚实失败。这是一个真实的产品缺口
（安全相关：350km 长途规划无确认），删期望来凑绿是放水，不干。

**verdict/eta/announcement 类断言取数据级语义。** stub 的 final_response
是模板句（"已执行：map.route: 成功"），念不出真正的出行结论/ETA/逐项播报。
候选：(a) 直接断言 final_response 文本 → 全挂，但挂的是脚手架不是产品，
信号是噪音；(b) 不断言 → 违反铁律；(c) 断言"结论所需的数据已取回且本轮
走完 verifier"（verdict/advice/rest_stops/duration_min 字段存在 +
task_completed），并在函数 docstring 里写明边界。选 (c)：
断言是真的（数据缺了就挂，不是 `return True`），边界是诚实的
（口语化结论待真实 LLM 实测，judge 只做 advisory 评分，不进 pass/fail）。

**restriction fixture 录的是 2026-10-07 真实状态。** 查了北京日报：
2026-09-28 起轮换（周一至周五限行尾号分别为 5和0、1和6、2和7、3和8、4和9），
10-01~10-07 国庆假期机动车尾号不限行。所以
`restriction_beijing_20261007.json` 里 `restricted_tails=[]`、`holiday=true`——
今天问"今天限号吗"，诚实答案就是"不限行（国庆假期）"。
key 用日期（`beijing_20261007`），fixture 是快照，不代表实时政策；
哪天轮换变了，录新的日期 key，不改这个文件。

## 对 eval 的影响

- `dataset_v0.jsonl` 15→33 条；新增 26 个 criterion key，每个都有确定性断言
  函数（见 `eval_runner.py` 的 CRITERION_CHECKS）；未知 key 仍 fail loud。
- 新增回归维度：多入口/分区/航站楼澄清、天气决定出行（不强制导航）、
  限行查询、避拥堵路线偏好、在途距离/ETA 查询、长途规划、模糊指代抗幻觉、
  多轮目的地保持（降级为单轮）。
- 预期结果：33 条中 21 通过、12 失败（旧 15 条必须保持全绿，
  本次只新增断言函数，未改动已有逻辑）。12 条失败精确归因见"已知缺口"，
  每一条的失败原因都是运行时真实行为，不是断言 bug。
- `destination_stable_across_turns` 在单轮降级下的语义是
  "本轮未丢失/篡改会话目的地"，不是真正的跨轮保持（见诚实边界）。

## 诚实边界（写死）

- recorded fixtures 是版本化测试样本：路线距离/时长、天气数值、限行快照
  都不代表实时数据，每个文件都有 note 声明。
- 车况 `parked_simulated`/`driving_simulated` 是纯软件模拟（AGENTS.md §6），
  README 和注释里不许暗示来自真车。
- 口语化结论/ETA 播报/逐项确认类断言测的是"数据就位"，不是"助手真的说出来了"——
  stub 下后者不可测，不虚构；各断言函数的 docstring 里都写了这条边界。
- 015 降级为单轮后，跨轮目的地保持没有被真正测到；多轮 runner 是明确的
  待办，不是"以后再说"的含糊。

## 已知缺口（留给后续任务，评测已如实标红）

1. clarify_node 不支持多入口/分区/航站楼点名澄清：只认低置信度
   （<0.6）和多地点实体歧义。001/002/008 的期望行为（"南门还是北门？"
   "T2/T3/大兴？"）今天走不通 → 3 条在 clarify_asked 上诚实失败。
2. clarify_node 不把单实体低置信度澄清的答案绑定到 destination：
   只写 `clarify_resolved`，planner 拿不到。014 在 destination_updated
   上诚实失败（clarify_asked 和 no_wild_guess 通过：确实追问了，也确实没瞎猜）。
3. 长途行程无确认策略：policy_gate 对 map.route 只有 read-allow，
   350km 的 009 在 requires_confirmation 上诚实失败。修要加策略规则，
   不是任务三的工具范畴，单独立项。
4. restriction/media/vehicle/knowledge 四类工具未实现（任务三）：
   004/010/011/012/013/015c/015d 共 7 条，调用被 policy 判 unknown_tool
   后整轮 deny，诚实失败。recorded 数据（13 个文件）已备好，工具实现后
   这些用例应直接转绿——这是留给任务三的验收标准。
5. eval_runner 不支持 `follow_ups` 多轮：015 降级为单轮是本次的决定；
   真正的跨轮评测需要多轮 runner（同会话状态沿轮传递），待立项。
