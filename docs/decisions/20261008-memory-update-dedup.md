# 偏好记忆更新去重：同一事实新值覆盖旧值

日期：2026-10-08 ｜ 状态：已决策，代码已落地（store.remember + graph_nodes._capture_candidate）

## 背景

update 链路有个缺口：remember() 只管插入。用户先说"公司地址：望京 SOHO"，
半年后说"公司搬到国贸了"，抽取会写第二条，旧的"望京 SOHO"继续参与召回——
同一事实两个值，planner 可能拿到过期偏好。按 id 的 update() 早就有了，
但"用户复述同一事实"走不到它，因为调用方手里没有旧 id。

约束：确定性优先，不许 LLM 裁判"是不是同一事实"；阈值要能讲清为什么是
这个数；敏感写入的人工确认铁律不许动。

## 候选方案

1. **写入时近似去重**：Gate allow 之后，同 kind 下 recall top3，
   最高分 ≥0.92 视为同一事实 → 复用 update() 更新旧值，
   remember 返回新状态 "updated"。代价：每次写入多一次向量召回
   （BGE 热状态约 17ms）；阈值是经验值。
2. **显式版本状态机**（status=active/superseded + superseded_by 链）：
   代价：多字段多状态，查询要过滤 status；原型阶段用不上链式追溯，
   是过度设计。
3. **不做**（现状，两条并存靠排序）：代价：过期偏好污染 planner context，
   且召回排序救不了"两个都相关但值冲突"的情况。

## 选择

方案 1。落点：DEDUP_THRESHOLD = 0.92（模块常量）；
remember() 在 Gate allow 后做同 kind recall，命中则调 self.update()
完成更新并返回 ("updated", 旧 id)；graph 的 _capture_candidate 把
"updated" 按 "written" 处理。

## 为什么

**0.92 为什么是这个数**：BGE 向量归一化后走余弦相似度；同一事实的
复述/改写（"公司地址望京 SOHO"→"公司搬到国贸了"不算，这是值变了但
事实主体同——靠 kind=place + 高相似主体命中）相似度通常 >0.95，
明确不同的事实通常 <0.85，0.92 在中间留 margin。
诚实：这个数是推理定的起点，不是网格搜索调出来的；护栏是
eval/embed_recall_probe.py 的 21 条 probe——以后重调阈值必须跑 probe，
recall@3 不许掉。测试里用 hash_embedder pin 行为：
完全相同内容余弦=1.0→合并；不同内容低分→两条。

**复用 update() 而不是另写更新路径**："改 content 必过 Memory Gate"
的不变量不能破——update() 内部对 content 变更会重跑 Gate、重算向量
（语义变了旧向量失效）。remember 里已经 allow 过一次，再跑一次是
纯函数重复计算，可接受。id 不变：外部引用（audit 记录、trace 里
written 列表）不断。

**返回新状态 "updated" 而不是复用 "written"**：调用方需要区分
"新建"和"覆盖"来记审计（audit 记 updated 事件）；graph 层按 written
处理，保证 trace 和 eval 断言零变化。docstring 写清三种返回。

**needs_confirm 透传**：dedup 命中后调 update()，若 Gate 对新内容判
confirm，("needs_confirm", None) 原样返回给调用方走人工确认——
敏感内容的去重更新不绕过铁律（有反例测试）。

**诚实记下放弃的东西**：显式版本链的追溯能力；承认近似去重对
"语义相近但确实是两条偏好"有误并风险（如"喜欢安静"vs"喜欢热闹"），
0.92 的高门槛就是控制误并率的手段——宁可漏并（多一条）不可误并
（丢一条用户没说要删的偏好）。

## 对 eval 的影响

- 33 条回归：同一 case 内重复写相同内容的场景现在走 updated 而不是
  duplicate insert；trace 断言只认 id 可解析（store.get），零变化。
  基线 28/33（5 红是 clarify/长途确认产品缺口）改动前后对照。
- 新增测试：相同内容写两次→("updated", 同一 id) 且库里只有一条；
  不同内容→两个 id；敏感内容 dedup 仍走 needs_confirm。
