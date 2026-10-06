# TripPilot 开发规范（AGENTS.md）

> 给所有写代码的人（Codex、子 agent、未来的我）看的。
> 最高原则：**代码要像人写的**。面试官会打开仓库看。

## 1. 目录约定

```
trippilot/            # 主包， import trippilot.xxx
  state.py            # LangGraph 状态（Pydantic），只加字段不改语义
  graph.py            # 编排：节点函数 + 路由，业务逻辑不要堆在这里
  policy_gate.py      # 确定性安全判定：ABAC / Memory Gate / 注入检查，纯函数
  memory/             # 用户偏好记忆（qdrant 本地文件模式）
  tools/              # MCP 风格工具：map / weather / reminder / trip_log
  llm.py              # LLM 接口：DeterministicStub（默认）/ EnvLLMClient
  api.py              # FastAPI 会话接口
  voice.py            # ASR/TTS 接口（stub，实测留 TODO）
eval/                 # 轨迹评测 runner，只读 fixtures + 跑断言
fixtures/             # 评测数据：dataset_*.jsonl、policy_matrix.yaml、recorded/
tests/                # pytest，每个模块一个 test_*.py
docs/decisions/       # 决策记录（见第 4 节）
web/                  # 最小控制台，不搞前端工程
```

## 2. 代码风格：像人写的

- **目标读者是一个认真的初级工程师。** 清楚、老实、不炫技；他能一眼看懂每段在干嘛，
  不需要猜。宁可多写两行直白代码，也不秀一行 clever 的。
- **注释只写"为什么"，不写"是什么"。** `x += 1  # 计数加一` 这种直接删。
  能写的例子：`# 行驶中禁副作用：交规要求，见 decisions/20251005-*.md`。
- **拒绝过度抽象。** 不许出现 manager/handler/util 套娃；三层以内能写完就别建新类。
  拿不准时选更笨的写法。
- **命名说人话。** `check_tool_call` 好，`validate_action_parameters_compliance` 坏。
- **docstring 只写非显而易见的**：参数的特殊约定、副作用、失败时返回什么。
  纯包装函数不写 docstring。
- **函数保持短。** 一个函数只做一件事；超过 40 行先想想是不是该拆。
- **错误处理诚实。** 失败就抛/返回失败状态，不许静默吞掉；verifier 必须明确标记失败。
- **确定性优先。** 安全判定、评测断言全部是纯程序，不许调 LLM 做判断。

## 3. 测试规范

- 每个新模块配 `tests/test_<模块>.py`，fixture 数据放 `fixtures/`。
- 网络、模型、麦克风全部用 stub/fake：测试里不许下载模型、不许调外部 API。
- 安全相关必须有反例测试：deny 的要测"确实被拦下"，confirm 的要测"没确认不执行"。
- 跑全量：`.venv/bin/pytest -q`，提交前必须全绿。

## 4. 决策记录（铁律）

**任何技术取舍，没有决策记录就不许合入。**

路径：`docs/decisions/YYYYMMDD-主题.md`，格式固定五段：

```
## 背景        —— 在解决什么问题，约束是什么
## 候选方案    —— 至少 2 个，每个一句话 + 代价
## 选择        —— 选了哪个
## 为什么      —— tradeoff，诚实写下选它的代价和放弃的东西
## 对 eval 的影响 —— 新增/变化了什么评测维度、指标、回归项
```

**记的密度：不只记大取舍，所有改动和选择都要留下痕迹。** 阈值为什么是这个数、
默认值为什么这样定、试过但放弃的路、当时是什么证据让决定倾斜的——都要写。
面试官会问"为什么是 0.8 不是 0.7"，答不上来就是 AI 味。写得像工程师的工作日志：
当时看到了什么、纠结了什么、最后怎么拍的板。改动小文档可以短，但不能没有。

## 5. Commit 规范

- 一个 commit 只做一件事；先有代码/文档，再 commit。
- message 用中文，动词开头，一句话说清改了什么：
  `加用户偏好记忆模块（qdrant 本地模式）` 好，`update` 坏。
- 每个 commit 前跑全量测试。

## 6. 诚实边界（写死，不许突破）

- 车况（驻车/行驶）全部是**纯软件模拟**，README 和代码注释里不许暗示来自真车。
- 地图/天气默认走 `fixtures/recorded/` 录制响应，不代表实时数据。
- 无 key 时 LLM 是确定性 stub；简历和面试话术里的数字只能来自真实评测报告。

## 7. 技术选型约束（简历对齐）

**只用简历上的技术。** 面试官会按简历问，仓库里出现简历外的技术就是给自己挖坑。
简历原文：`~/workspace/user/resume_2027_cn.tex`，"专业技能"节是唯一白名单来源。

白名单映射（2026-10-06 按简历整理）：
- Agent 编排：LangGraph ｜ 后端：FastAPI ｜ 向量库：Qdrant（偏好记忆）、FAISS（RAG 备选）
- Embedding：BGE / sentence-transformers（简历：泰瑞数创 BGE 检索）
- 本地模型：llama.cpp、Transformers、Qwen2.5 ｜ ASR：Whisper（Transformers）
- 评测/工程：pytest、GitHub Actions ｜ 数据库：PostgreSQL/SQLite

规则：
- 白名单外的依赖不许直接引入。先写决策记录说清"简历上的为什么不行"，用户批准后才能用。
- 已存在的偏离记为待整改项，排期换成白名单方案，不许"能用就行"。
  当前已知偏离：`trippilot/memory/store.py` 默认 embedding 用 FastEmbed（简历外），
  待换成 BGE（bge-small-zh-v1.5，中文场景本来就更合适）。
