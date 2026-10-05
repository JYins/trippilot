# 决策：用户偏好记忆用 qdrant 本地文件模式

日期：2026-10-05

## 背景

TripPilot 要记住用户偏好（常去地点、称呼、授权偏好），并在对话中召回使用。
约束：① 用户亲口指定用 qdrant（复用他已有技术）；② 原型阶段零运维，
clone 下来就能跑；③ 召回要处理语义相似（"常去机场"≈"浦东机场"），
关键词匹配不够；④ 敏感偏好（家庭住址）写入前必须过 Memory Gate 确认。

## 候选方案

1. **qdrant-client 本地文件模式**（`QdrantClient(path=...)`，无 server）。
   代价：多进程并发写有文件锁限制；embedding 模型首次下载慢。
2. **PostgreSQL + jsonb/pgvector**。
   代价：要装库、建表、管连接；原型阶段为 4 条偏好起一个数据库是杀鸡用牛刀；
   用户 Coval 项目里 qdrant 才是长期记忆线，pg 会让"同一套记忆 infra"的
   简历叙事断掉。
3. **纯内存 dict + JSON 文件持久化**。
   代价：零依赖最简单，但召回只能关键词/遍历，语义偏好召回质量差；
   数据量稍大就得自己写索引，迟早重写。

## 选择

方案 1：qdrant-client 本地文件模式。

## 为什么

- **复用用户已有技术**：用户 Coval CRM/Health 的长期记忆线就是 qdrant，
  面试可以讲"偏好记忆和项目记忆同一套 infra"，这是简历加分项。
  这是用户亲口指定的，首先尊重。
- **零运维**：本地文件模式无 server、无 docker、无端口，
  符合"纯软件原型、clone 即跑"的定位；server 模式留作部署阶段再说。
- **向量召回语义偏好**：偏好类文本短、口语化，"送我去公司"要能召回
  "常去地点：望京 SOHO"。向量 top-k 比关键词稳，这是方案 3 做不到的。
- 诚实代价：① embedding 用 FastEmbed 轻量模型，中文质量不如大模型 API，
  原型可接受，`embed_fn` 做成可插拔，留 EnvLLMClient 扩展点；
  FastEmbed 默认模型是 BAAI/bge-small-en-v1.5，首次加载要下载约 100MB，
  无网络环境生产召回会直接失败（离线/测试走 hash_embedder fake 向量，
  只是占位，不代表召回质量）；
  ② 单进程假设写进 README，多进程是已知限制；
  ③ 测试用确定性 fake embedder，不下载模型，保证 CI 离线可跑。

## 对 eval 的影响

- 新增评测维度：**偏好召回准确率**（recall@k）。
  fixtures 加 `memory_recall_cases.jsonl`：query → 期望命中的偏好 id。
- Memory Gate 行为进回归：transient 拒绝、sensitive 走 confirm、
  confirm 前不写入——各一条反例测试。
- eval_runner 加断言：含记忆的轨迹必须出现 `memory_recall` 事件，
  且敏感偏好写入前必须有 `human_confirm` 事件。
- 当前没有节点自动往 `memory_candidates` 写数据：记忆捕获靠外部（调用方）
  填充，从对话自动抽取候选是下一步。
