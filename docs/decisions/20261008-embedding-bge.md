# 默认 embedding 从 FastEmbed 换成 bge-small-zh-v1.5

日期：2026-10-08 ｜ 状态：已决策，回归实测后合入

## 背景

`trippilot/memory/store.py` 的生产默认 embedding 一直是 FastEmbed，
而且是 `TextEmbedding()` 的出厂默认：`BAAI/bge-small-en-v1.5`——一个
**纯英文**小模型（384 维）。它服务的却是中文用户偏好召回：
"送我去公司" → "公司地址：望京 SOHO"，query 和 seed 全是中文口语。
英文模型做中文召回是错配，中文分词和语义都吃亏。

AGENTS.md 第 7 节已经把这事点名记为待整改偏离：
简历白名单里 embedding 一项是"BGE / sentence-transformers
（简历：泰瑞数创 BGE 检索）"，FastEmbed 是简历外技术。
整改方向当时就定了：换成 bge-small-zh-v1.5，中文场景本来就更合适。

约束：CPU 本地可跑、离线可用（车上没网也要能 recall）、延迟别太难看、
维度变化（384→512）要处理好旧库。

## 候选方案

1. **bge-small-zh-v1.5 + sentence-transformers**：BGE 官方 README 的标准
   用法；中文专门优化（词表、训练语料）；正好是简历白名单，面试能讲实现
   细节。代价：多一个 torch 依赖（CPU 轮子约 200MB）、首次运行要从 HF
   下约 96MB 权重、冷启动加载模型要几秒。
2. **bge-small-zh-v1.5 走 fastembed（onnx 版）**：fastembed 原生支持这个
   模型，onnxruntime 已经在环境里，无需 torch，更轻。代价：壳还是简历外
   的 fastembed，整改不彻底——第 7 节点名的是 FastEmbed 本身，换壳不换
   汤等于没改；onnx 量化版和原版输出有细微差异。
3. **bge-base-zh-v1.5**：1 亿参数，中文榜单分数比 small 高一截。代价：
   权重大 4 倍（约 400MB+）、CPU 推理慢 3~4 倍；座舱语音场景对延迟敏感，
   small 的分数已经够偏好召回用，不值。
4. **m3e-base**：中文社区常用，轻量好下。代价：m3e 不在简历上，按第 7
   节规则得先写"简历上的为什么不行"再报批；BGE 在中文检索榜单整体更强，
   且简历里已有 BGE 检索经验可讲，m3e 讲不出故事。

## 选择

方案 1：bge-small-zh-v1.5 + sentence-transformers。

实现要点：保持延迟导入（没装 sentence-transformers 也能 import 模块，
第一次真正向量化时才报错，和原来对 fastembed 的处理一致）；
`normalize_embeddings=True`（BGE 官方用法，配合 qdrant COSINE 距离）；
query/document 不加 instruction 前缀（zh-v1.5 标准用法就是直接 encode，
加前缀是 multilingual/eval 玩法，不引入）。

## 为什么

最直接的理由：现在的默认是英文模型，中文召回先天吃亏。中文偏好文本
（"在家喜欢安静环境工作"这类口语）进英文模型的 tokenizer 会被切得稀碎，
语义相似度基本靠猜。bge-small-zh-v1.5 是中文词表 + 中文语料训的，
"送我去公司"和"公司地址：望京 SOHO"这种字面不重叠、语义相关的 pair，
正是它擅长的。

第二个理由是简历：泰瑞数创实习做的就是 BGE 检索，换成之后面试官问
"你们 embedding 怎么做的"，能讲 normalize、cosine、召回链路、维度选择，
全是真经验。FastEmbed 留着只能讲"我调了个库"，讲不深。

诚实记下代价：

- **依赖变重**：sentence-transformers 会拖进 torch（CPU 约 200MB），
  pyproject 依赖从 qdrant-client 一家变成两家。原型阶段可接受，真上车
  规再考虑 onnx 导出，那是后话。
- **首次下载**：BAAI/bge-small-zh-v1.5 约 96MB，走 HF；没网的机器第一
  次 `remember` 会报错——和原来 fastembed 第一次下载一个性质，行为没变。
- **冷启动**：模型加载进内存要几秒（实测值见下表），之后常驻。recall
  是"intent → memory_recall → clarify"链路上的一环，首 token 延迟里要
  算上这一笔。
- **维度 384→512，旧库不兼容**：本地文件模式单用户原型，直接删库重建
  即可，不做自动迁移（migration 代码比问题本身还重，不值）。这一点写进
  了 `_bge_default` 上面的注释，免得后人踩坑。
- **放弃的东西**：fastembed 的 onnx 轻量推理、m3e 的更小体积、bge-base
  的更高分数上限。small 的中文检索分数对"几十条偏好里召回 top5"这个量
  级绰绰有余——偏好库不是百万文档，不需要 base。

### 回归实测（2026-10-08，本机实测）

probe 集：从现有 memory 测试/fixtures 里取，共 21 条中文 query
（fixtures/memory_recall_cases.jsonl 4 条 + tests/test_memory_store.py
6 条 + tests/test_graph_memory.py 3 条 + 11 条 seed 自召回），seed 库
14 条偏好。FastEmbed 侧用原来的默认模型 bge-small-en-v1.5（onnx），
BGE 侧用 BAAI/bge-small-zh-v1.5（sentence-transformers）。

| 指标 | FastEmbed（bge-small-en-v1.5） | BGE（bge-small-zh-v1.5） |
|---|---|---|
| recall@3 | 0.857（18/21） | **1.000**（21/21） |
| recall@5 | 0.905（19/21） | **1.000**（21/21） |
| 平均 rank | 1.37 | **1.05** |
| top5 未召回 | "去机场"、"机场"（期望"常去地点：首都机场 T3"） | 无 |
| 单条 embed（热） | 0.0136s | 0.0175s |
| 批量 embed（21 条） | 0.1792s | 0.1735s |
| 21 条 query 端到端 recall | 0.2119s | 0.4848s |
| 冷启动（模型加载） | 未测 | 24.1s（torch/transformers import 为主） |
| 首条编码 | 未测 | 0.029s |
| 输出维度 | 384 | 512 |

怎么读这张表：

- **召回是决定性差距**。21 条 probe 里 FastEmbed 在 top5 丢了 2 条，
  丢的恰恰是 fixture 自己的语义改写 case："去机场"/"机场" 期望召回
  "常去地点：首都机场 T3"——英文模型连仓库自带的 fixture 期望都满足不了，
  这就是"英文模型做中文召回"错配的实证。BGE 21 条全中，平均 rank 1.05。
- **延迟基本打平，单条略慢**。热状态下单条 embed 13.6ms vs 17.5ms，
  批量 21 条两者都在 0.18s 上下——onnx 和 torch 在这个量级没拉开差距。
- **端到端 21 条 BGE 反而慢一倍**（0.21s vs 0.48s）：原因是 probe 里每条
  query 都是单独 encode 一次，BGE 每次单文本走 torch 前向的固定开销比
  onnx 大。生产 recall 也是一次一 query（memory_recall 节点），单条
  17.5ms + qdrant 检索完全可接受；真要批量场景再考虑攒批，不为此加复杂度。
- **冷启动 24s 是本次最大的代价**，几乎全是 torch + transformers 的 import
  和权重加载。好在是延迟加载——不用记忆功能就不付这笔钱；用了的话，
  第一次 remember/recall 会卡一下。原型可接受，上车规前再做 onnx 导出。
- 维度 384→512 已验证：旧 collection 必须删库重建（见 store.py 注释）。

## 对 eval 的影响

- 33 条 eval（fixtures/dataset_v0.jsonl）走的是 `hash_embedder` 确定性
  向量（eval/eval_runner.py 写死），不受这次更换影响；回归要求是 33 条
  全绿、零变化——跑一遍确认即可。
- 新增回归维度：上面的 21 条 probe 对比就是 memory recall 的回归基线，
  以后换 embedding 都要跑这一套，recall@3/5 不许低于本次 BGE 的值。
- planner context 那条线（10-06 修的"偏好真正进 planner context"）：
  memory_recall 节点只消费 `PreferenceStore.recall` 的返回，不感知 embedder
  是谁，所以 33 条 eval 里 memory 相关的 case 不需要改期望；但为了保险，
  合入前把 33 条全跑一遍，memory_recall 进 planner 的 case 重点看。
- 测试规范（AGENTS.md 第 3 节）：pytest 里继续用 `hash_embedder`，
  不下载模型、不调外部 API——这次没动测试的 embedder，不存在违规。
