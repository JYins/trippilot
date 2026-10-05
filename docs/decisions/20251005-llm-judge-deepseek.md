# 决策：LLM-as-judge 后端选型（DeepSeek）

日期：2026-10-05

## 背景

eval 需要"规则写不出的维度"：轨迹合理性（plan 步骤是否冗余/绕路）、
澄清质量（clarify 问得是否到位）。硬规则（deny/degrade/confirm/required
节点覆盖）已有程序断言，不能交给 LLM（AGENTS.md 铁律）。所以要一个
pluggable 的 judge 后端：默认纯规则（离线），live 评测时可换成 LLM。
约束：① 便宜——judge 是每轮 eval 每条用例都要跑的成本项；
② 中文口语质量好——评的是中文车载澄清话术；
③ OpenAI-compatible API——接入成本最低；④ 国内可用、合规省心。

## 候选方案

| 维度 | DeepSeek（deepseek-chat） | GPT-4o-mini | Qwen（通义，qwen-plus 级） | GLM（智谱，glm-4-flash/air 级） |
|---|---|---|---|---|
| 价格 | 最低档（输入约 ¥1/百万 tokens 量级） | 高出一个数量级量级，美元结算 | 低，人民币结算 | 有免费档/极低价 |
| 速度 | 中等，judge 是离线批处理不敏感 | 快 | 快 | 快 |
| 中文质量 | 强，中文推理/口语细节好 | 好，但中文口语细节略逊 | 强 | 中上 |
| 国产/合规 | 国产，国内直接可用 | 跨境，结算与合规顾虑 | 国产，阿里云生态 | 国产 |
| API 兼容性 | OpenAI-compatible，`response_format: json_object` 可用 | 原生 | 兼容但细节参数有差异 | 兼容但细节参数有差异 |

（价格为 2026-10-05 记忆中的量级对比，实际以各官网实时价为准；
judge 成本量级小，价格不是唯一决定项。）

1. **DeepSeek deepseek-chat**。代价：judge 与被测模型可能同源
   （未来被测若也用 DeepSeek），存在 self-preference 偏差风险。
2. **GPT-4o-mini**。代价：价格贵一个量级、美元结算、跨境合规顾虑；
   中文车载口语评测上没有可感知的质量优势。
3. **Qwen**。代价：API 兼容层的细节差异要单独适配测试；
   综合性价比略输 DeepSeek。
4. **GLM**。代价：低价档模型在"按固定 rubric 稳定输出 JSON"
   的遵循能力上弱于 deepseek-chat，judge 最怕的就是格式漂移。

## 选择

DeepSeek（`deepseek-chat`，`https://api.deepseek.com`），
`eval/judge.py` 的 `DeepSeekJudge`。

## 为什么

- **价格最低档**：judge 是每轮全量用例都要调用的成本项，
  deepseek-chat 的输入价格是候选里最低的，长期跑 eval 不心疼。
- **中文质量够用且强**：评的是中文澄清话术和轨迹合理性，
  DeepSeek 的中文细节不输 GPT-4o-mini，没有为"洋品牌"付溢价的理由。
- **国产 + OpenAI 兼容**：国内直接可用、无跨境顾虑，
  且 `response_format: json_object` 让 JSON 解析稳定，接入零适配。
- 诚实代价：① **同源偏好偏差**——如果以后被测模型也用 DeepSeek，
  judge 可能系统性偏爱"自己人"的表达风格。缓解办法是架构级的：
  judge 只评 advisory 维度、分数永不进入 pass/fail 门（见下节），
  且 prompt 用固定 rubric 锁死评分标准；真到选型对比模型时再引入
  第二家 judge 做交叉。② **外部依赖**：live judge 依赖网络和 key，
  不可用时报告必须标 pending、绝不 fake 分数（已在构造器里写死）。

## 对 eval 的影响

- 新增两个 **advisory 维度**：`plan_efficiency`（轨迹合理性）、
  `clarify_quality`（澄清质量）。只进报告，不进 verdict。
- `eval/judge.py`：`JudgeBackend` 协议 + `DeterministicJudge`
  （纯规则，离线可用）+ `DeepSeekJudge`（live）。
- `eval_runner.py`：每条用例跑完后调 judge，报告行追加
  `judge_plan_eff` / `clarify_q` 和 `judge_notes`；无
  `TRIPPILOT_JUDGE_API_KEY` 时报告头标注
  `judge: pending(需 TRIPPILOT_JUDGE_API_KEY)`。
- 回归项：① `DeepSeekJudge` 无 key 构造必抛错（测试覆盖，
  测试里不许调真实 API）；② fake backend 打 0 分也不改变
  verdict（judge 不进门的回归测试）。

## 已知局限（2026-10-05 双审）

1. `DeterministicJudge.plan_efficiency` 把 human_confirm 回绕、recovery 重试
   误判为绕路（2 例假阳性，见 docs/experiments/20251005-judge-agreement.md）；
   改进方向：数重复节点时给合法回绕加白名单。语义级判断等 DeepSeek judge
   开 key 后替代。
2. `clarify_quality=0.60` 默认桶（9/12）真实含义是"本来就不需要澄清"，
   不是"问法质量待定"，读报告时注意不要误读为低分。
