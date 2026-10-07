## 背景

15 次 DeepSeek judge 调用全部返回 400。`deepseek-chat --json` 会发送 `response_format: json_object`，DeepSeek API 要求请求消息包含小写单词 `json`；原 system prompt 只有大写 `JSON`。

## 候选方案

1. 在 system prompt 中加入小写 `json`；仓库侧改动最小，评分语义不变。
2. 修改共享 CLI，不再发送 `response_format`；会影响其他使用者，并失去结构化输出约束。
3. 放弃 live judge；不再报错，但会丢失 advisory 语义评分。

## 选择

选择修改 system prompt，写成“只输出 json 对象（JSON）”。

## 为什么

问题由仓库 prompt 与 API 的格式约束组合触发，在 prompt 中满足约束是最小且局部的修复。CLI 属于共享 skill，不应为单个仓库改动；评分维度、分值范围和输出字段均保持不变。

## 对 eval 的影响

DeepSeek judge 的 advisory 分数恢复可用，仍只写入报告，不进入确定性 pass/fail 门。
