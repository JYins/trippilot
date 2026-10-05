# TripPilot｜途行智驾 · v0.1 脚手架

基于北京真实出行任务的语音优先座舱 Agent 原型。
完整产品计划见 `~/workspace/goals/goal/files/车企Agent项目计划.md`。

> **诚实边界**：车况（驻车/行驶）全部为**纯软件模拟**，不宣称来自真实车辆；
> 地图/天气默认走**录制响应**（`fixtures/recorded/`），不代表实时数据；
> 无 key 时 LLM 为**确定性 stub**，只保证主循环跑通，不充当模型能力。

## 目录

```
trippilot/            # 主包
  state.py            # LangGraph 显式 Pydantic 状态（append-only 轨迹）
  policy_gate.py      # 确定性 ABAC Policy Gate + Memory Gate + 注入检查
  graph.py            # intent→clarify→planner→policy_gate→confirm→tools→verifier→recovery
  llm.py              # LLM 接口：DeterministicStub / ScriptedLLM / EnvLLMClient（手动 key）
  api.py              # FastAPI 会话 API（trace_id、脱敏日志）
  voice.py            # ASR/TTS 接口（stub；FunASR/Whisper 实测接入点已留 TODO）
  tools/              # map / weather / reminder（幂等）/ trip_log；recorded/live_manual 双轨
fixtures/
  policy_matrix.yaml  # ABAC 策略表（对照 policy_gate.py 评审用）
  dataset_v0.jsonl    # 种子评测集（4 条：多工具/歧义/低置信拦截/行驶降级）
  recorded/           # 版本化录制响应
eval/eval_runner.py   # 轨迹级回归：必经节点/禁止动作/确认合规/deny-degrade 断言
tests/                # 16 个单元+冒烟测试
web/index.html        # 最小控制台：文本输入、确认卡片、模拟车况切换
```

## 快速开始

```bash
cd ~/workspace/your_files/trippilot
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 1. 单元测试（16 个）
.venv/bin/python -m pytest tests/ -q

# 2. 轨迹级回归（4 条种子用例）
.venv/bin/python eval/eval_runner.py

# 3. 启动 API + 控制台
.venv/bin/uvicorn trippilot.api:app --port 8130 &
# 浏览器打开 web/index.html（或反代 /turn 到 8130）
```

## 关键设计（对照计划 §③）

- **Policy Gate 不经过模型**：`deny` 直接短路到 verifier，`confirm` 走人工确认；
  地点歧义在 clarify 已解决后不再重复拦截。
- **低 ASR 置信度禁止副作用**：阈值 0.6（`policy_gate.LOW_CONFIDENCE_THRESHOLD`）。
- **提醒幂等**：`reminder.create` 用 `sha1(content|time|session)` 幂等键，
  重试返回首次结果，不重复写入（Duplicate Side-effect Rate 第一道防线）。
- **工具返回先过注入检查**：命中模式 → 该次结果记失败并拦截，不进入系统指令。
- **双轨地图**：自动回归只读 recorded；真实接口需手动设
  `TRIPPILOT_AMAP_KEY_TEMP`（transient use，用后即删，不写入文件）后触发
  `live_manual`。
- **真实模型**：手动设置 `TRIPPILOT_LLM_BASE_URL/_API_KEY/_MODEL` 后
  `make_llm()` 自动切换；key 只存进程环境。

## 下一步（按计划第 1 周）

1. 回溯你最近 10 次真实出行 → 扩充 `dataset_v0.jsonl` 到 25–30 条；
2. 录制 5–8 个地图/天气 fixture（脱敏后的真实任务参数）；
3. 接入 FunASR/Whisper 实测 WER（`trippilot/voice.py` TODO 处）；
4. 把策略表拿给朋友做一次红队 review（越权/prompt 注入变体）。
