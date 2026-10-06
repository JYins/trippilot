## 背景

2026-10-06 审查指出两处会让评测报告失真。节点检查只把
`required_nodes` 当集合看：节点存在就算覆盖，乱序、缺少用例真正关心的关键步骤，
以及同一节点反复绕行都可能漏过。另一方面，图里的 `clarify` 是必经检查节点，
但 `DeterministicJudge` 把“访问过 clarify”当成“发生了澄清”，导致
`clarify_checked.need_clarify=False` 的普通请求也被扣分。

约束是保持已有 12 条 fixture 的集合检查语义；新检查必须由用例显式声明，不把全部
历史轨迹一次性改成严格序列。judge 仍是本地确定性 advisory 分数，不修改
DeepSeek judge，也不让分数参与 pass/fail。

## 候选方案

1. 把所有 `required_nodes` 直接改成完整序列并要求逐项相邻：规则最严，但会破坏现有
   集合语义，正常插入审计或记忆节点也会让用例无意义地失败。
2. 保留集合覆盖，另加可选子序列与默认重复上限：能分别表达“来过”“先后关系”与
   “异常绕行”，代价是报告多三个结果字段，用例作者要按需要声明顺序和豁免。
3. 仅按轨迹长度限制绕行：实现最短，但不同合法路径长度差别很大，不能指出哪个节点
   在循环。
4. judge 继续根据节点和对话长度猜测澄清：兼容旧输入，但把控制流经过和用户真的需要
   澄清混为一谈。
5. judge 优先读取 `clarify_checked.need_clarify`，只有值为 `True` 时才看答案和追问：
   语义与图一致；旧的手写 trace 没有事件时需要保留兼容分支。

## 选择

节点断言选择方案 2，拆成三段：`required_order` 沿用 `required_nodes`（并兼容
`expected_nodes`）和 `forbidden_nodes` 的集合语义；`sequence_check` 对可选
`node_sequence` 做子序列匹配，允许中间插入其他节点；`no_loop` 默认不允许任何节点
访问超过 2 次，`allow_revisit` 中的节点不受该上限约束。fixture 字段放在 `expected`
中，runner 同时兼容顶层声明，避免已有外部用例因字段位置不同而失效。

judge 选择方案 5。runner 从 append-only trace 提取最后一个 `clarify_checked` payload
交给本地 judge。`need_clarify=False` 直接给澄清质量 1.0；只有
`need_clarify=True` 才根据已解决、已追问或无有效追问分档。事件缺失时走旧输入兼容
分支。DeepSeek judge 的 prompt、调用和评分完全不动。

## 为什么

工作日志：先定位审查描述里的“节点检查”，实际代码在 `eval/eval_runner.py`，而
`harness_audit.py` 检查的是模型可见输入和工具注册表；因此把新 verdict 接到 runner，
否则 12/12 报告不会受到约束。第一轮测试先用四条人工轨迹固定失败面：乱序、缺节点、
第三次访问和 recovery 豁免。

子序列而非连续序列是有意选择。编排以后可能在 `planner` 与 `tool_executor` 之间增加
审计节点，这不改变“先澄清、再规划、最后执行”的业务事实，不应迫使 fixture 跟着改。
默认上限取 2 次，是为了允许一次正常重试，同时第三次才明确视为绕行；这不是统计调参，
而是当前图只有“首次执行 + 一次 recovery”的重试预算。某些恢复流程确实需要再次访问
节点，所以豁免按节点显式列出，不提高全局上限。代价是被豁免节点可能无限重访；这项
声明必须只给已知恢复路径，TP-RECOV-001 因此只豁免 `tool_executor`。

`required_order` 的名字保留三段式报告约定，但它本身不检查顺序：集合语义不能暗中改变，
真正的先后约束只在 `sequence_check`。这点写明是为了避免后续维护者看到名字后误以为
旧 fixture 已经覆盖时序。

judge 侧采用事件语义，因为一次 `clarify` 节点访问只说明程序做了检查，不说明用户被
追问。`need_clarify=False` 满分不是奖励对话短，而是确认该轨迹无需澄清；
`need_clarify=True` 时仍保留 1.0/0.6/0.3 三档，没有在这次改动里假装规则能评价自然
语言质量。notes 明写事件值和所走分支，方便看到分数时追溯原因。兼容分支会继续接受
缺事件的旧手写 trace，代价是那些输入仍使用较弱启发式；真实 runner 轨迹已经始终携带
事件，不受这个盲区影响。

## 对 eval 的影响

新增 `tests/test_harness_sequence.py`，分别锁定乱序、缺节点和第三次访问失败，以及
`allow_revisit` 通过；新增 `tests/test_judge_clarify.py`，用真实图结构会经过 clarify
的普通轨迹形状断言 `need_clarify=False` 得 1.0，且 notes 明示“无需澄清”分支。

`dataset_v0.jsonl` 只给 TP-AMBIG-001、TP-CLARIFY-002 增加
`clarify → planner → tool_executor` 示例子序列，没有把 12 条全部机械补齐；
TP-RECOV-001 显式允许 `tool_executor` 重访。runner 报告新增
`required_order`、`sequence_check`、`no_loop` 和具体超限节点，三项都进入硬 verdict；
旧用例不声明新字段时，空子序列自动通过，且节点不超过两次就保持原结果。
