## 背景

在线体验需要在同一个 FastAPI 服务里提供聊天页面和轨迹展示，并继续满足无 key、录制数据、纯软件模拟的诚实边界。当前 graph 只在本轮状态里保存 trace，不持久化历史轨迹；`/turn` 已经返回节点、策略和确认状态。

## 候选方案

1. 新开 `/trace/{trace_id}`：接口职责看起来独立，但 graph 不持久化 trace，请求结束后没有可靠数据源。
2. 在 `TurnResponse` 增加可选 `trace`：一次响应拿齐回答和轨迹，代价是 `/turn` 响应体略大。
3. 给未注册能力补前端假数据或临时扩展 stub：演示更热闹，但会伪造执行结果并扩大本次范围。
4. 用 FastAPI `StaticFiles` 托管手写页面：不增加依赖，代价是静态页面和 API 由同一进程提供。

## 选择

选择在 `TurnResponse` 增加可选 `trace: list[dict]`，每轮从 `out.trace` 序列化返回；不新增 trace 查询接口。保持 `DeterministicStub` 和工具注册表不变，media、vehicle、restriction、knowledge 四类工具不在本次实现。用 `StaticFiles(html=True)` 在所有 API 路由之后挂载 `web/` 到 `/`。

## 为什么

trace 目前只属于当轮 graph 状态，新开查询接口会暗示服务能够按 `trace_id` 找回数据，实际上做不到；随 `/turn` 返回最直接，也让旧客户端可以忽略这个可选字段。DeterministicStub 不扩展，是为了不把演示页面需求混进规划器主线；四类未注册工具的按钮仍走真实 `/turn` 链路，plan 为空时页面如实显示“未执行工具调用”，绝不伪造工具结果。静态挂载放在 `/turn` 和 `/health` 之后，利用 FastAPI 路由顺序保证 API 优先，再由 `/` 承接页面和静态资源。

代价是轨迹跟随每次响应重复传输，且未实现跨轮轨迹查询；未支持的场景现在会明确暴露能力边界。后续若主线加入 trace 存储或新工具，应另写决策并补对应评测，不在前端偷偷填结果。

## 对 eval 的影响

新增 demo 回归：`/turn` 必须包含结构化 trace，`/` 必须返回控制台；清空 LLM 环境变量后，DeterministicStub 仍能跑完整链路；现有 confirm 与 nonce 一次性确认流程保持可用。未修改 `eval/` 或任何 fixture，也不改变现有轨迹判分语义。
