## 背景

TripPilot 主线任务六：扩展 `DeterministicStub`，让 15 条真实行程用例
（TP-REAL-001~014，及 015 拆出的 015a~015d 共 18 条）的核心句式在无 key 下
真实走通工具链。

实证现状（改前跑 eval 确认）：33 条用例里，旧 15 条中 10 条走真实 stub、
6 条（TP-LOWCONF-001、TP-IDEM-001、TP-RECOV-001、TP-ROUTE-002、
TP-AMBIG-002、TP-RECOV-002）用 `scripted_plan`；18 条真实用例里 17 条用
`scripted_plan`，只有 TP-REAL-014（"去那个河边"）走真实 stub。
任务一选 scripted_plan 是因为当时 stub 只认"路线/导航/怎么去/几点出发/
天气/提醒"几个词，改 stub 等于改产品行为、超出任务一范围——现在任务三的
media/vehicle/restriction/knowledge 四类工具已实现，主线要求把意图理解这
一环也打通。

约束：规则必须是通用模式（如"地点+路线动词"），不许写只认测试题原句的
if；每个规则配反例测试（"798为什么叫798"不许触发导航、"今天限号吗"不许
编造车牌）；红就是红，不许为绿而绿；不动 web/、Dockerfile、README、deploy。

## 候选方案

1. 18 条真实用例继续用 scripted_plan：零风险，但 15 条真实口语的意图理解
   永远不被测，stub 还是只能认几个词——任务目标直接落空。
2. 扩展 stub 的通用意图规则 + 删掉 17 条真实用例的 scripted_plan：
   stub 是无 key 默认 LLM（`make_llm` 回退），规则即脚手架层面的产品行为，
   改动可见、可回归；代价是关键词规则有天花板，个别老用例行为变化要修回归。
3. 接小模型做意图分类：无 key 约束下不可用（要 key 或本地权重），且失去
   确定性，超出 stub"测试脚手架"的职责。

## 选择

选择方案 2。planner_node 给 `llm.plan` 的 context 补 `place_names`
（从 asr place_entities 提取）和 `vehicle_state`，available_tools 补上
`weather.forecast`；`DeterministicStub.plan()` 重写为一组小纯函数谓词；
删掉 17 条真实用例的 `scripted_plan`（旧 6 条不动）；
新增 `tests/test_llm.py`（9 个测试，规则覆盖 + 反例，全部用改写句式、
不用原题，证通用性）。

## 为什么

按当时纠结的顺序写，每条规则的设计依据：

**主要意图互斥（知识问句/天气问句优先，直接返回）。**
反例驱动："798为什么叫798"里"798"是话题不是目的地，"为什么"问的是来历，
没有导航动词——解释性疑问（为什么/是什么/介绍一下/讲讲/怎么来的/
什么意思）命中时只走 knowledge.qa。"一会儿去潮白河，天气怎么样"里
"去潮白河"只是背景，真正的疑问是"要不要去"的决策信息——天气类词+
天气疑问词（怎么样/如何/好不好/会不会）同时出现时只走天气工具，
不强制导航。导航是副作用类动作，意图不明时保守不触发，这是安全取向，
不是为某条用例写的。

**导航双模式。** 显式模式（导航/路线/怎么去/怎么走/几点出发/几点到/
几点能到/还有多远/还有多久）本身足以表达导航；祈使模式要求句中有"去"
**且**有可落地的目的地。可落地只认三处：context["destination"]、
context["clarify_resolved"]、planner 注入的 place_names——stub 绝不从原文
正则提取地名。反例"去那个河边"：三处全空，不生成路线（014 的
no_wild_guess 就靠这条）。place_names 来自 asr 已识别实体：真实规划
prompt 本来就会带实体，stub 用它不是作弊，是复用产品自己的实体管线。

**destination 解析顺序：context > place_names[0] > clarify_resolved
（仅无实体时）> ""。** place_names 优先于 clarify_resolved 是有意保守：
001 的 clarify_resolved="南门"是局部澄清，脱离"798"没有独立导航意义，
这是 clarify 绑定缺口，stub 不替产品脑补；014 无实体时 clarify_resolved=
"潮白河"是用户亲口确认的完整答案，采用它是通用行为（planner 本来就该用
澄清答案），不是瞎猜。014 因此真实触发了带正确目的地的路线，
no_wild_guess 照过，剩下的 destination_updated 红精确指向
clarify_node 不写 destination 的产品缺口。

**origin：在途问句（还有多远/还有多久）或 vehicle_state 以 driving 开头
时用"当前位置"。** 设计依据：在途问的是"当前位置到目的地"的剩余距离，
起点不可能是家里。vehicle_state 之前只在 trip_context 里（eval 有，
api 真实链路不一定有），planner_node 现在从 state 显式注入——小的产品
行为补齐。

**天气 area 顺序：context["area"] > context["destination"] >
place_names[0] > "海淀区"。** area 优先是因为录制 fixture 按 area 校验
（weather_default 的 expect 是 area=海淀区），TP-ROUTE-001 这类老用例
context 自带 area=海淀区，顺序反了就回归。forecast 只给两种强信号：
天气问句里的未来词（一会儿/明天/下周…，如 003）、长途规划（规划/自驾，
如 009，设计依据：长途出行天气是决策输入，和 003"天气决定出行"同类）。
辅助性提及（"结合天气"）保持 weather.now——第一版曾让未来词一律触发
forecast，导致 TP-ROUTE-001 回归（它的 allowed_tools 没有
weather.forecast），修回来了：预报语义不替用户脑补，老用例契约不动。

**限行只传 city + fixture，不传 plate/date。** 车牌未知是已知条件，
反例"今天限号吗"只能查城市规则；录制工具不消费 date，不传。
final_answer 保持模板句（只报成功/失败）是第二道防线。

**诚实代价（认了的红）。** 001/002/008 的原始地名（798/三里屯/机场）
对不上录制 fixture 的标准名（798艺术区南门/三里屯北区/首都机场T3），
工具诚实返回 fixture mismatch——这是 clarify 多入口/分区/航站楼点名
澄清缺口的下游症状。不许在 stub 里写"798→798艺术区南门"映射表来"修"它，
那就是作弊匹配。013 同理：三里屯→三里屯北区是 clarify 分区缺口；
附带暴露一个既有行为——recovery_node 会清空上一轮 tool_results，
013 第一轮成功的 restriction.query 结果被连带清空，所以
restriction_answered 也红。这是既有语义，本次不动，如实记录。

## 对 eval 的影响

- 17 条真实用例去 scripted_plan，全部走 DeterministicStub 真实规划；
  旧 6 条 scripted_plan 不动。
- 新增回归维度（tests/test_llm.py）：知识/天气问句不强制导航、无实体不猜
  目的地、在途起点、预报条件、路线偏好透传、限行不编车牌、提醒/媒体/车控
  组合、planner 上下文注入。
- 结果：27/33（基线 28/33）。12 条真实用例（003/004/005/006/007/010/011/
  012/015a/015b/015c/015d）现在经真实 stub 规划走通工具链；旧 15 条保持全绿。
- 6 条红精确归因：001/002/008=clarify 多入口/分区/航站楼点名澄清缺口
  （已知缺口1）；009=长途确认策略缺口（4 个 criterion 全过，仅 confirm_ok
  挂）；013=clarify 分区缺口 + recovery 清空 tool_results 的既有语义连带；
  014=clarify 不绑定 destination 缺口（clarify_asked、no_wild_guess 均过）。
  没有为绿而绿：013 比基线多红一条，原因是诚实 stub 拿不到"北区"信息，
  如实认了。

## 诚实边界（写死）

- stub 是测试脚手架，不是产品能力；eval 里 LLM 相关能力以真实模型实测为准。
- 关键词规则有天花板：如"几度"会同时命中天气和空调温度句式（"空调调到几度"），
  当前无 fixture 覆盖，暂记为局限，不为它加特例。
- "去"在闲聊中可能误触（如"我明天去北京出差，帮我记住"且有地点实体时），
  stub 局限，真实 LLM 应做消歧。
- 口语化结论/ETA 播报类断言仍只断言"数据就位"（延续
  20261008-real-trips-fixtures.md 的边界），不虚构 stub 说了什么。
