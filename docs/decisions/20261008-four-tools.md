## 背景

dataset_v0 的 7 条真实回放用例（TP-REAL-004/010/011/012/013/015c/015d）依赖
media / vehicle / restriction / knowledge 四个命名空间的操作名，
但仓库里没有对应工具实现，policy_gate 以 unknown_tool 全部拒绝，
7 条用例在基线 eval 里全部 FAIL。这是产品能力的缺口，不是评测写错了。

约束：
- AGENTS.md 第 6 节：车况纯软件模拟，代码和注释不许暗示来自真车；
- AGENTS.md 第 7 节：只用简历白名单技术，不引入新依赖；
- 全部操作走 TOOL_POLICIES 显式注册 + policy_gate，未知操作默认 deny；
- 知识问答不许调外部 API、不许现编答案。

## 候选方案

1. 四个工具全部接真实外部能力（真车接口、实时限行 API、在线知识库）：
   数据更新、演示更像产品，但测试依赖网络和硬件，违背"不控制真车"的
   诚实边界，也超出当前原型阶段。
2. 进程内模拟状态 + 版本化录制 fixture：结果确定、离线可重复、零新依赖；
   代价是进程重启状态清零，限行/知识数据不是实时的，
   且模拟状态不能冒充真实设备。

## 选择

方案 2。
- media / vehicle：类级进程内状态（和 ReminderTool / TripLogTool 同一手法），
  每次返回都带 "simulated": True；
- restriction：沿用 BaseTool 的录制通道，复用已有的
  restriction_beijing_20261007.json；
- knowledge：新建 knowledge_default.json 小知识库，关键词命中才答，
  命中不上直接 ok=False（不编答案）；
- policy_gate 新增规则 8：media/vehicle 的 update 操作，已认证车主直接 allow
  （reason_code=simulated_cabin_owner_allow），未认证/低 ASR/乘客仍走更早的
  拒绝或默认确认通道；
- DeterministicStub 按关键词补 6 条规划规则（测试脚手架，不是产品能力）。

数值怎么定的：
- 音量 0–10、步进 2、默认 5：和常见车机音量档位对齐，步进 2 让上下界
  用几次调用就能走完，测试里钳制行为容易观察；
- 空调默认 24°C/auto、天窗默认 closed：中性初始状态，不冒充任何真实读数；
- increase_ac 语义定为"制冷开大"（温度 -1、风量 high），因为中文口语里
  "空调开大点"几乎总是指更冷/风更大，反向理解会让 TP-REAL-012 的动作
  和用户意图拧巴。

## 为什么

放弃方案 1 的真实感，换来三样东西：离线可重复的评测、
不越诚实边界、零新依赖（简历白名单干净）。
代价如实记下：media/vehicle 的状态是进程级的，多进程/重启就丢；
restriction 数据是 2026-10-07 录制的国庆假期版本，不代表实时政策，
fixture 的 note 段和工具注释都写明了这点；
knowledge 目前只有 1 条核实过的条目（798），覆盖面几乎为零——
这是故意选的：用"不知道"换"不编造"，覆盖面以后靠加核实条目补，
不靠模型现场发挥。

policy 上 media.next/media.volume/vehicle.* 标为 update 是诚实的
（它们都改变状态），但默认写确认规则会让"换首歌"这种无风险操作
每次都要用户确认，体验上说不过去。例外只开给"纯模拟+已认证车主"，
注释里写死了"未来接入真车必须改为 confirm"，防止这条例外被沿用。

_unsupported_operation 在四个新模块里各写了一份，没有抽成公共函数：
三行小函数，复制比跨模块引用私有函数更笨、更直白，符合"拿不准选更笨的"。

## 对 eval 的影响

- TP-REAL-004/010/011/012/013/015c/015d：7 条由 FAIL 转 PASS，
  全量 eval 从 21/33 升到 28/33。
- 仍红的 5 条（001/002/008/014 clarify 触发行为、009 长途确认策略）
  是别的产品缺口，stash 对照确认改动前就红，本任务未动它们。
- tests/test_eval_real_trips.py 的 test_spotcheck_010_honestly_fails
  已翻转为 test_spotcheck_010_passes：工具实现后"诚实失败"的前提不存在了，
  留着旧断言会跟任务目标打架。
- 新增 4 个 tests/test_*_tool.py：正常路径 + 反例（未知操作被 deny、
  工具层返回 unsupported、音量钳制、知识库未命中诚实失败）。
- 回归项：新规则只影响 media/vehicle 资源；全量 pytest 144 通过，
  原有 policy_matrix 用例无变化。
