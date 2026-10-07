# 第 2 周 eval report（2026-10-07）

本次运行 `.venv/bin/python eval/eval_runner.py`，确定性规则检查 15/15 PASS。全部地图、天气结果来自版本化 recorded fixture，不代表实时信息；车辆状态为纯软件模拟。DeepSeek judge 走 `deepseek-chat` CLI（advisory：分数只进报告、不影响 pass/fail 判定）。本轮 judge 调用成功，分数如下；CLI 的 `--json` 曾因 DeepSeek JSON-mode 要求请求消息含小写单词 `json` 而 15 次全 400，已修 prompt 措辞（见 decisions/20261007-judge-json-fix.md）。

| case_id | 结果 | plan_eff | clarify_q | 覆盖的分支 |
|---|---|---|---|---|
| TP-ROUTE-001 | PASS | 0.85 | 0.9 | 路线 + 天气 + 提醒确认 |
| TP-AMBIG-001 | PASS | 0.6 | 0.9 | 地点澄清、目的地绑定 |
| TP-LOWCONF-001 | PASS | 0.7 | 0.3 | 低置信度 clarify、deny 副作用 |
| TP-DRIVE-001 | PASS | 0.6 | 0.5 | driving_simulated degrade |
| TP-MEM-001 | PASS | 0.6 | 0.5 | 敏感记忆确认 |
| TP-MEM-002 | PASS | 0.3 | 0.5 | 临时记忆拒绝、稳定偏好写入 |
| TP-MEM-003 | PASS | 0.9 | 1.0 | 敏感记忆未确认不写入 |
| TP-CLARIFY-002 | PASS | 0.7 | 0.4 | 低置信度 clarify 后只读路线 |
| TP-DRIVE-002 | PASS | 0.6 | 0.5 | driving_simulated 降级边界 |
| TP-IDEM-001 | PASS | 0.7 | 0.5 | 重复提醒幂等抑制 |
| TP-RECOV-001 | PASS | 0.5 | 0.5 | 录制 fixture 失败后 recovery |
| TP-RECALL-001 | PASS | 0.8 | 1.0 | 多偏好召回、路线 + 提醒确认 |
| TP-ROUTE-002 | PASS | 0.7 | 0.5 | 望京→国贸 route + 朝阳区 forecast + reminder 长链、确认 |
| TP-AMBIG-002 | PASS | 0.8 | 0.5 | clarify 先于副作用、北京西站绑定 |
| TP-RECOV-002 | PASS | 0.6 | 0.5 | 创建后超时、原幂等键恢复、重复抑制 |

judge 备注（advisory，不进 verdict）：TP-MEM-002 的 plan_efficiency 偏低（0.3），轨迹含冗余执行且澄清未点名歧义选项；TP-LOWCONF-001 的 clarify_quality 偏低（0.3），低置信度场景澄清未点名具体歧义选项。这些是第 3–4 周澄清话术与轨迹简洁度的改进线索，本轮不改 verdict。

## Trace ID

runner 将 15 个实际输出的 `trace_id` 收集后做集合去重，结果为 15/15 唯一。生成逻辑仍是 `tr_` 加 12 位 UUID 十六进制片段；本结论只对应本轮运行，不是对所有未来运行的数学唯一性保证。

## 关键分支覆盖

- clarify：TP-AMBIG-001、TP-LOWCONF-001、TP-CLARIFY-002、TP-AMBIG-002；其中 TP-AMBIG-002 额外程序化验证 clarify 事件早于首次 `reminder.create` 执行事件。
- 幂等：TP-IDEM-001、TP-RECOV-002。
- deny：TP-LOWCONF-001，低 ASR 置信度下副作用未执行。
- degrade：TP-DRIVE-001；TP-DRIVE-002 覆盖无需降级的 2 选项边界。
- recovery：TP-RECOV-001、TP-RECOV-002。

## 昨日 clarify 话术修复

提交 `5016a57` 的确定性澄清模板仍由测试覆盖；本轮 TP-AMBIG-001、TP-CLARIFY-002、TP-AMBIG-002 均经过 clarify 路径并通过轨迹规则。本轮 judge 已真实调用成功（plan_efficiency / clarify_quality 见上表），分数只作 advisory 参考。

## 已知局限

- 计划中的 6 次用户访谈尚未完成，`real_recalled` 是回忆整理的情境，不是本周新访谈证据。
- 真实麦克风、Whisper ASR 和整条真实语音链路尚未接入；ASR 内容与置信度来自 fixture。
- DeepSeek judge 本轮真实调用成功（15/15 有分数），但分数为 advisory 参考，不参与 pass/fail；沙箱受限环境（禁止 CLI 出网）下会回退为 pending，报告如实标注即可。
- 地图与天气仅使用录制响应，不代表 2026-10-07 的实时路况或天气。
- reminder 是进程内原型存储；本轮证明确定性幂等行为，不等同于跨进程持久化保证。
