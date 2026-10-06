## 背景

2026-10-05 的 live DeepSeek judge（实验 B，第二轮）揪出一个真缺陷：
`clarify_node` 把内部 reason code（`place_ambiguity(北京西站,西站地铁站)`、
`asr_low_confidence(0.40)`）直接拼进追问话术
`"想确认一下：…，你指的是哪一个？"`，用户听到的是机器码。
live 在 TP-AMBIG-001、TP-LOWCONF-001、TP-CLARIFY-002 的 clarify 维度给了
0.5/0.3/0.6，而规则 judge 因为这 3 条 context 里预置了 clarify_answer
给了虚高的 1.0——规则根本没走到追问分支。

另外今天 `codex exec` 在本机不可用（`echo ok` 60 秒无响应被 kill，
二进制本身 `codex --version` 秒回，属已知不稳定），这次改动由子 agent
按 AGENTS.md 规范直接实现，Codex 只做主脑的角色这次空缺。

## 候选方案

1. **LLM 生成话术**：把 reason code 喂给真实模型润色（`EnvLLMClient.clarify_question`
   本来就是这么写的）。代价：违反"确定性优先"铁律；话术质量不可回归，
   每次追问多一次模型调用延迟；坏 case 只能靠抽查发现。
2. **graph 里拼人话字符串后仍走 `llm.clarify_question`**：保留接口，
   只改传入的 ambiguity 内容。代价：`ScriptedLLM` 的固定外壳
   `"想确认一下：{ambiguity}，你指的是哪一个？"` 会和人话叠床架屋
   （"想确认一下：你指的是…中的哪一个？，你指的是哪一个？"），接口
   名不副实。
3. **确定性模板渲染，删掉 `llm.clarify_question`**：graph 新增纯函数
   `_render_clarify_question`，按歧义类型套模板并点名选项；reason code
   只进 trace。`LLMClient.clarify_question` 在 4 个实现里删掉——graph
   是唯一调用方，留着就是死接口。

## 选择

方案 3。

## 为什么

- 追问话术是用户每轮都听到的东西，质量必须可回归、可 diff。模板是
  纯函数，pytest 能锁死；LLM 润色再好也锁不住，这是决定性因素。
- 删接口而不是留着：YAGNI。以后真要用 LLM 润色话术，加回来就是
  几行的事；留着没人调的接口，面试官问"这个谁调"答不上来就是 AI 味。
- 模板目前只覆盖两种真实存在的歧义（place_ambiguity、asr_low_confidence，
  都是 clarify_node 里实际产生的），没做"通用 N 种歧义"的过度设计。
  覆盖不到的类型降级到通用话术"没太确定你的意思，能再具体说一下吗？"——
  宁可问得笼统，也绝不把 code 漏给用户。这个降级分支今天走不到
  （need_clarify 为真必有其一），是防御性写法。
- 0.6 的置信度阈值没动：渲染函数里复用同一阈值只是为了选模板，
  触发逻辑还在 clarify_node 里，阈值的来历和调参以后单独记。
- 话术措辞选了最笨的：地点歧义 → "你指的是「北京西站」、「西站地铁站」中
  的哪一个？"；低置信度 → "刚才没太听清，能再说一遍吗？"；叠加时拼一句。
  没加"亲"没加 emoji，车载语音场景 TTS 读出来要顺。
- **第二轮（live 验证后的追改）**：第一版写完后，用去预置变体 + live judge
  验证，发现单地点低置信度（TP-CLARIFY-002 变体）只拿到 clarify 0.5，
  judge  notes 写"未点名'首都机场'这一歧义选项"。这是同一个缺陷的延续——
  有具体选项却不点名。于是加了一个分支：单个地点 + 没听清时按"猜测确认"
  问（"你是说「首都机场」吗？"），措辞上不把猜测当事实。time entity 没点名：
  它从没产生过 reason code，根本没有"选项"可点，judge 在 TP-LOWCONF-001
  变体上想要'明天'也只是 advisory 层面的建议，这次不跟——跟 judge 的每一条
  notes 走会把模板写成 judge 的应声虫，模板只修真缺陷。
- 代价：模板是写死的，以后歧义类型变多要手动加分支；话术风格单一，
  不如 LLM 润色自然。但这是用"可回归"换"灵活性"，现阶段值。

## 对 eval 的影响

- **回归锁在 pytest，不在 dataset fixture**：12 条 dataset 用例里，只有
  3 条 clarify 相关用例预置了 clarify_answer（TP-AMBIG-001、TP-LOWCONF-001、
  TP-CLARIFY-002），其余 9 条根本不触发 clarify——所以 dataset 里没有任何
  一条能走到追问分支（3 条因预置答案跳过追问，9 条不触发）。如果硬加
  TP-CLARIFY-003 进 dataset，它停在 clarify 后 verification ok=False，
  runner 的 verdict 会判 FAIL，得给 runner 开特例才能让数字好看——
  那是为数字服务的机器，不诚实。
  所以回归放在 `tests/test_graph_smoke.py` 三个确定性断言里：
  点名选项、无 reason code 泄露、无置信度数字。模板确定性 → 零 flaky，
  且它是 commit 门禁（eval 分数只是 advisory）。
- `DeterministicJudge` 的 clarify 三档桶（1.0/0.6/0.3）不动：20251005 的
  结论是"问得好不好"是语义判断，规则只做结构信号。"点名了选项"是结构
  检查，已经由 pytest 覆盖，不需要把 judge 也改成查子串——两处查同一
  件事是重复，不是互补。
- 验证计划：全量 pytest 全绿 → 跑 12 条 eval（通过率应保持 12/12）→
  用 live DeepSeek judge 重跑 TP-AMBIG-001、TP-LOWCONF-001、TP-CLARIFY-002，
  确认 clarify 分数回升。若 judge CLI/key 不可用，如实记 pending，不 fake 分数。
- **验证结果（2026-10-06，deepseek-chat via cli，temperature 0.2）**：
  直接重跑 12 条 fixture 看不到修复（分支没走到，trace 与修前完全一致，
  TP-AMBIG-001 clarify 仍是 0.5——这是预料之中的，不是不及格）。
  于是构造了 3 条"去预置 clarify_answer"变体跑真实 graph + live judge：
  TP-AMBIG-001 变体 clarify 1.0（"准确列出两个歧义选项，追问简短口语"）；
  TP-CLARIFY-002 变体第一版 0.5（"未点名'首都机场'"），加猜测确认分支后
  1.0（"直接澄清并点名候选实体"）；TP-LOWCONF-001 变体 0.6，judge 想要点名
  '明天'——time entity 不在点名范围内（见"为什么"），0.6 是 judge 挑剔不是缺陷，
  接受。验证脚本是一次性的（/tmp/clarify_live_check.py，不进仓库），
  长期回归由 pytest 三个断言锁死。