# 20251005: Jev 判定模型调研——暂不接入

## 背景

用户提到"JEV 这种快速判断类的模型"。调研确认：Jev 是 TypeSafe AI 2026-09
中旬发布的商业"System One 判定模型"。它不是自回归 LLM，不生成文本；
输入是结构化 state + 类型化问题（Noul 是非判断 / Score 有序评分 /
Choice 选项选择），一次前向直接输出带概率的类型化答案。

它的定位是 deterministic code eval 和 LLM-as-a-judge 之间的第三选项：
快（70–500ms）、便宜（$0.042/M 输入 token，输出免费）、方差小。
LangChain 的 Daniel Shea / Sean Roche 在 5 条固定 agent 轨迹 × 100 次重复
上测得它与人工标注 100% 一致、方差比 GPT/Claude 低两个数量级——但那是
单一小实验，不能外推。

## 候选方案

1. 接入 Jev API 做 trajectory judge（替代或前置 DeepSeek judge）。
2. 用开源复刻 Laya（ModernBERT encoder，Apache-2.0）本地跑。
3. 维持现状：DeepSeek 可插拔 LLM judge（advisory）+ deterministic 硬断言。

## 选择

方案 3：暂不接入 Jev。Jev 作为未来"快筛层"保留在插件路线图里，
不欠技术债（JudgeBackend 接口已抽象好，加一个实现类即可）。

## 为什么

1. **维度不匹配。** 我们 judge 的是开放维度（轨迹合理性、澄清质量）。
   第三方横测（aimlapi，HelpSteer2 答案质量）：Jev 与人类评分相关性
   0.394 垫底（Gemini/Claude 0.48–0.49）；nuanced decision table 上
   Jev 80.6% vs LLM judge 98.2%。Mastra 官方指引也写明：需要多步推理
   或书面解释的判断，LLM judge 更合适。Jev 强的是窄的是非判断，
   不是我们的开放维度。
2. **中文 + 长轨迹装不下。** Jev 中文小样本（40 条工单）显示含糊和
   边界输入问题明显；开源 Laya 上下文只有 512–1024 token，装不下完整
   agent 轨迹（多轮对话 + 工具日志）。
3. **概率不能直接当门槛。** Jev 自报 80–95% 把握的样本实际正确率只有
   64%，还有 6.6% 的样本把正确标签打成零概率；必须按任务重做 Platt
   校准。我们当前 12 条轨迹的体量，还没到需要它省成本的规模——它的
   成本优势在每天上万条轨迹时才改变运营方式。
4. **叙事对齐。** 国产低成本 DeepSeek judge 更对齐"中国车企"故事线；
   Jev 是美国商业 API，国内可达性未验证。

## 对 eval 的影响

- eval 维持 deterministic verdict + LLM advisory judge 双层结构，不变。
- 若未来 eval 规模上千、且维度收敛为封闭 rubric（如"是否违规 /
  是否拿到确认"的是非判断），接入 Jev 做第一层快筛：高置信自动过，
  低置信回落 LLM judge，走已有 JudgeBackend 插件口。
- 这次"调研后决定不用"本身记入简历故事：不是没听过新东西，
  是拿证据排除了不合适的。
