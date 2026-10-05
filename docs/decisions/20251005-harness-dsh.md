# 决策：DeepSeek Harness 思想怎么进 TripPilot

日期：2026-10-05

## 背景

DeepSeek 开源了 dsh（deepseek-ai/deepseek-harness）：一个"一切皆插件"的
agent harness。研读了它的 README 和几份第三方架构梳理（acryl 的
DEEPSEEK-HARNESS-ARCHITECTURE、s2p2 的 dsh-baseline、clos01 的
deepseek-harness-study），提炼出三个核心设计思想：

1. **一切皆插件**：没有特权内核。模型适配器、工具注册表、会话日志，
   甚至 agent loop 本身都是插件，可以从配置替换。
2. **事件溯源**：append-only 的会话事件日志是唯一真相源，运行时强制
   不变式"model-visible ⟺ logged"（模型看到的 ⟺ 日志里有的）；
   模型消息只是日志的投影。由此得到 resume / fork / replay /
   trajectory / audit 全套能力。
3. **Capability seam（能力边界）**：每个能力都是三段式——
   Service Definition（接口）/ Provider（实现）/ Consumer（面向模型的工具）；
   面向模型的工具永远不知道具体执行 provider 是谁，换一个 provider，
   整个工具家族跟着走，不用 fork consumer。

问题：这些思想怎么用？约束：① 用户明确"不单独开 DeepSeek harness 小项目，
把思想吸收进 TripPilot"；② TripPilot 的卖点是车载座舱 Agent +
trajectory eval，任何投入都要直接服务这个叙事；③ 原型阶段人力有限，
不能开第二条产品线。

## 候选方案

1. **单独开一个 harness 小项目**（mini-dsh，照着 dsh 抄一个通用 harness）。
   代价：多一条产品线的维护负担；通用 harness 离车载场景远，
   trajectory eval 受益间接；简历故事分散成"两个半成品"。
2. **在 TripPilot 内吸收**：把 dsh 的两个可操作思想——事件溯源不变式、
   capability seam——做成 eval/harness_audit.py，常驻 trajectory eval 回归。
   代价：放弃独立的 harness 通用性（别人拿不走复用）；插件化只能做到
   轻量级（注册表 + 不许直调的门禁），做不到 dsh 那种运行时热插拔。

## 选择

方案 2：在 TripPilot 内吸收，不单独开项目。

## 为什么

放弃的东西：一个"通用 harness"的独立可复用性，以及 dsh 式
"换 agent loop 只需改配置"的彻底插件化。TripPilot 的 seam
停在"注册表 + 不许直调的门禁"这一层，承认这是阉割版——
dsh 用 258 个包和 Cordis 组合框架解决的问题，我们用一个 dict
和一次 AST 扫描守住底线。

得到的东西：① trajectory eval 直接受益——"轨迹可审计"从口号
变成每次回归都跑的断言；② 简历故事更聚焦："车载座舱 Agent +
自研 trajectory eval"是完整叙事，"还顺手写了个通用 harness"
反而稀释重点；③ 零新增运维面，不增加第二条产品线的维护成本。

## 对 eval 的影响

- 新增 `eval/harness_audit.py`：① 事件溯源不变式检查——跑一条轨迹，
  断言 planner/LLM 看到的输入（intent、preferences、tool 参数与结果摘要）
  全部出现在 append-only trace 里，即"模型可见 ⟺ 日志可审计"；
  ② 插件式工具注册检查——tools/ 下的工具必须经 get_tool(TOOLS)
  注册表获取，业务代码直调具体工具类会被 AST 门禁揪出。
- 输出审计报告（通过 / 缺失项列表），exit code 0/1，门禁性质，
  不计入百分制 eval 报告。
- 回归项：trace 丢事件、新增工具绕过注册表，都会 loud fail。
- 有意的审计盲区（诚实记录）：session_id 等挥发性标识不进比对；
  planner context 里的环境配置（timezone/vehicle_state/user_role）目前
  也不进 trace，只审计语义性输入（intent、工具参数、工具结果摘要）；
  preference 内容目前不进模型上下文（planner context 不含 prefs），
  只审计 recall 事件存在且 count 对得上——如果以后 planner 把偏好
  注入 prompt，必须同时把投影写进 trace，否则审计会 fail。

## 简历口径

借鉴 DeepSeek Harness 设计思想自研 trajectory eval。

## 已知局限（2026-10-05 双审）

- `harness_audit.present()` 目前是全 blob 子串匹配：若某个值恰好出现在
  错误事件的叶子里也会通过，断言强度偏弱；改进方向：绑定到具体事件节点
  再断言。当前 12 条轨迹审计通过的结论不受影响。
