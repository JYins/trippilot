# 决策：demo 免费部署改走 HF Gradio

日期：2026-10-08

## 背景

用户明确 HF Docker Space 要收费、不付；HF 免费只剩 Static 和 Gradio。
demo 必须在线可体验（简历/面试外发），部署方案必须重定。
约束：① 简历白名单（AGENTS.md 第 7 节），Gradio 是白名单外依赖，需决策记录+用户批准——
用户原话"hf只有static和gradio免费"即点名 Gradio 为候选，视为批准立项；
② 诚实边界不变；③ 主线三件套（trippilot/、eval/、fixtures/）已冻结，不许动。

## 候选方案

1. **HF Gradio Space（免费）**：Python in-process 直接跑 agent（DeterministicStub，
   零 key、零外部调用），Gradio Blocks 重写 demo 壳：聊天 + HTML trajectory 时间线 +
   15 场景按钮 + 诚实徽章。单 Space 搞定一切，无 CORS、无冷后端。
   代价：① Gradio 白名单外（本决策即立项批准）；② demo UI 重写一份（web/ 手写版保留本地，
   Gradio 版为部署专用）；③ Gradio 审美上限不如手写版，但"能点开用"优先。
2. **HF Static + 免费 Python 后端**：Render 免费 tier（休眠唤醒慢）、Railway（免费额度小）。
   代价：两处部署、CORS 配置、冷启动体验差；省不下钱还多一堆故障点。
3. **不部署**：代价：直接违背用户要求，出局。

## 选择

方案 1。

## 为什么

- 免费是硬约束，Gradio 是 HF 免费档里唯一能跑 Python agent 的。
- agent 核心（LangGraph 编排、policy_gate、工具、trajectory 结构）**一行不动**，
  Gradio 只是把 `web/` 那套展示层换成 Blocks——简历故事依然是
  "LangGraph+FastAPI+Qdrant"，Gradio 只出现在"demo 部署"一句带过，不进简历技能表。
- DeterministicStub + recorded fixtures 意味着 Space 里无 secret；常规演示场景无外部调用、
  无模型加载，免费档睡觉唤醒也快。例外：BGE 只在首次触发偏好记忆时懒加载，
  届时可能需要联网下载一次模型。

放弃的东西：手写版控制台的精致度；Docker 一体部署的"前后端同构"干净感。
以后真要花钱，再迁回 Docker 不迟——agent 接口不变，迁就是换壳。

## 对 eval 的影响

- Gradio 壳不进 pytest 主链路（加一个 import 级冒烟测试即可），不进 eval；
- Gradio 层只调已有 graph/API 接口，不新增 agent 行为、不新增工具；
- 若 Gradio 展示需要 trace 新字段，回主线排期，不许在 demo 壳里 fork 逻辑。
