## 背景

eval 的 DeepSeekJudge 一直是"有后端无 live 分数"状态：之前无 key 只能标
pending，用户 2026-10-05 开通了 DeepSeek API（走 deepseek skill 的
authd 连接器，`custom.deepseek`）。现在要把 live 分数真正跑出来，
但 key 的用法必须一次定对——评测代码进 GitHub 公开仓库，任何把明文
key 写进配置/环境/日志的做法都是简历事故。

## 候选方案

1. **key 写进 `.env` / 配置文件，httpx 直调**：最省事，但 key 落盘，
   误 commit 一次就泄露，且每个跑评测的人都要自己管一份 key。
2. **TRIPPILOT_JUDGE_API_KEY 环境变量，httpx 直调**：不落盘，但 key
   要进进程环境，报错堆栈、子进程 env dump 都可能把它带出来；而且
   用户已经在 skill 里连过一次，再让他手动 export 是重复劳动。
3. **优先走 skill CLI 子进程**（`deepseek-chat --json`，prompt 经 stdin），
   CLI 不可用才回退环境变量：进程里永远见不到明文 key，认证走 authd
   surrogate；代价是多一次子进程开销，且 CLI 挂了要有降级路径。

## 选择

方案 3。`DeepSeekJudge._chat` 先调 CLI（`--system` 传 system prompt，
stdin 传 user payload，`--json` 要 JSON 输出）；CLI 非零退出/超时/
不存在时，有 `TRIPPILOT_JUDGE_API_KEY` 就降级 httpx 直调，都没有就
如实抛错（eval_runner 捕获后记 `judge.error`，不炸整轮评测，更不
fake 分数）。构造时按"CLI 可执行 → env 有 key → 抛错"三档决定，
`via` 字段写进 eval 报告头和实验 B 数据行，诚实标注分数来源。

## 为什么

- 安全是决定性因素：key 不可读 > 一切便利。skill 的 surrogate 机制
  是平台侧已经做对的部分，评测代码只管"调子进程、读 stdout"，
  不碰认证，攻击面最小。
- 降级链是诚实工程：CLI 是用户机器上的快乐路径；换台没装 skill 的
  机器（比如面试官本地跑），env key 照样能出分数；都没就 pending，
  幻觉分数比没分数更伤评测可信度——这条铁律不变。
- 代价：每次 judge 多一次进程 fork（12 条用例多花约 10 秒，可接受）；
  CLI 非零退出/超时/探测后被删都一视同仁走降级（没按 exit code 细分，
  行为够用就不加分支）；CLI 退出码 0 但吐出垃圾 JSON 时不降级，
  直接记 judge.error——如实但不优雅，已知。

## 对 eval 的影响

- eval 报告行新增 `via cli|env`，12 条用例全部 `via cli` 跑出 live 分，
  通过率 12/12（judge 分数只进报告、不进 verdict 门，约束不变）。
- 新增实验 B（`eval/judge_agreement.py --live`）：DeterministicJudge vs
  live DeepSeekJudge 在 12 条真实用例上的对比，原始数据落盘到
  `docs/experiments/data/judge_live_vs_det.json`。

### 实验 B 结果（2026-10-05，deepseek-chat，temperature 0.2，第二轮 live 跑）

| case | det plan | live plan | det clar | live clar |
|---|---|---|---|---|
| TP-ROUTE-001 | 1.00 | 0.90 | 0.60 | 1.00 |
| TP-AMBIG-001 | 1.00 | 0.70 | 1.00 | 0.50 |
| TP-LOWCONF-001 | 1.00 | 0.60 | 1.00 | 0.30 |
| TP-DRIVE-001 | 1.00 | 0.70 | 0.60 | 0.50 |
| TP-MEM-001 | 0.40 | 0.60 | 0.60 | 0.50 |
| TP-MEM-002 | 1.00 | 0.40 | 0.60 | 0.50 |
| TP-MEM-003 | 1.00 | 0.90 | 0.60 | 1.00 |
| TP-CLARIFY-002 | 1.00 | 0.90 | 1.00 | 0.60 |
| TP-DRIVE-002 | 1.00 | 0.60 | 0.60 | 0.50 |
| TP-IDEM-001 | 1.00 | 0.90 | 0.60 | 0.70 |
| TP-RECOV-001 | 0.40 | 0.50 | 0.60 | 0.30 |
| TP-RECALL-001 | 1.00 | 0.90 | 0.60 | 1.00 |

平均绝对差：plan 0.233，clarify 0.300——两边不是互相印证，而是有
信息增量，留着都有价值。

**什么时候规则够用**：结构信号（节点兜圈、工具越界、澄清节点空转）
det 全抓住了，零成本、零幻觉、可复现。合成退化探针（实验 A）里
3 条"规则 verdict 永远 PASS 的烂轨迹" det 全部打低分——这种地方
上 LLM judge 是浪费钱。

**什么时候需要 LLM judge**：澄清"问得好不好"是纯语义判断，规则
只能给 1.0/0.6/0.3 三档粗桶。实验 B 里 live 更严的 3 条
（TP-AMBIG-001、TP-LOWCONF-001、TP-CLARIFY-002）全是澄清语义问题：
det 因为"context 里预置了答案"给了 clarify 1.0，live 读了
final_response 发现追问根本没点名歧义选项（"北京西站还是西站地铁站"）。
反过来 det 的 0.6 默认桶也有误伤（TP-ROUTE-001、TP-MEM-003、
TP-IDEM-001、TP-RECALL-001），live 读了实际话术给回了高分。两者互补，
不是替代。

还有一类"混合分歧"两边桶都装不下：TP-RECOV-001 上 det 在 plan 更严
（0.4 vs 0.5）、live 在 clarify 更严（0.6 vs 0.3）——它恰恰是第 4 个
支持"澄清语义需要 LLM"的数据点（live 指出"导航去国贸意图明确却先
澄清，追问还没点名歧义选项"）。分类桶是看故事的脚手架，不是故事本身。

**已知局限**：没做 live judge 的重复稳定性专项实验，但两次 live 跑
本身已经暴露了 run-to-run 方差：plan 平均绝对差 0.258→0.233，
clarify 0.250→0.300，`det_stricter` 从 3 条变 4 条（TP-IDEM-001
clarify 0.5→0.7 跨过阈值）。temperature 0.2 只是压方差不是消灭方差；
advisory 维度本来就不进门，漂一点可以接受，真要上生产门得先做
稳定性实验（同一轨迹 N 次打分，看分布）。
