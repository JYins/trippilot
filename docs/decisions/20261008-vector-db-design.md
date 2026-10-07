# 向量库 Collection 设计：512 维、HNSW 参数、payload 索引与量化取舍

日期：2026-10-08 ｜ 状态：已决策，代码已落地（trippilot/memory/store.py）

## 背景

任务四把默认 embedding 换成 bge-small-zh-v1.5（512 维），旧 384 维
collection 已不兼容（见 20261008-embedding-bge.md，recall@3 从 0.857
升到 1.000 的实测）。但 store.py 建 collection 只传了 size + distance，
HNSW 参数全是"继承来的默认值"、payload 索引只有一个 user_id：
参数说不清为什么，384 维老库混用时报的是 qdrant 内部错而不是人话。

约束：单用户本地原型（qdrant 本地文件模式，无 server），偏好记忆
几百到几千条；面试官会问"m 为什么是 16 不是 32"。

## 候选方案

1. **显式设定 HNSW 参数（m=16、ef_construct=100，其余默认）+ 补全
   payload 索引 + 512/384 维度校验与重建入口**。代价：多几十行配置
   代码和 4 个新测试。
2. **维持全默认**（现状）。代价：参数说不清为什么；老库混用时报错
   信息不可读；created_at 范围查询（过期删除）无索引走全扫。
3. **按"未来百万级"提前调优**（m=32、ef_construct=200、scalar
   quantization 开启）。代价：为不存在的规模付内存和复杂度，
   违背 AGENTS.md"不许过度设计"。

## 选择

方案 1。落点：EXPECTED_DIM = 512；VectorParams(size=512,
distance=COSINE, hnsw_config=HnswConfigDiff(m=16, ef_construct=100))；
payload 索引：user_id / kind / sensitivity（KEYWORD）+
created_at（DATETIME）；_ready() 里做维度校验，mismatch 抛人话
ValueError；加 rebuild_collection() 手动重建入口。

## 为什么

**distance 选 COSINE 不选 dot**：BGE 用 normalize_embeddings=True
编码，向量单位化后 cosine 和 dot 数学等价；选 COSINE 是为鲁棒——
将来换 embedder 若向量非严格单位化，cosine 仍只度量夹角，
dot 会把模长混进相似度。这是 20261008-embedding-bge.md 已定的搭配，
延续。

**m=16 不选 32**：qdrant 的 full_scan_threshold 默认 10000，
1 万条以下走精确全扫描——确定性、最高召回，HNSW 图根本不起作用。
当前规模几百到几千条，m 这个参数今天就是 no-op；即使将来超 1 万条，
m=32 的召回增益也只在百万级向量 + 严苛 recall@k 时才拉开，
而本仓库 probe 的 recall@5 已经是 1.000。32 的代价是约 2 倍的图边内存
（每条边约 8 字节，m=16 时每向量约 256 字节图内存），换不到可测提升。
16 是 qdrant 默认值，久经考验。

**ef_construct=100 不调高**：默认值；写入是一次一条的聊天写入，
构建期索引质量差异在这个量级不可测；调高只会拖慢单次写入延迟。

**为什么显式写出而不是用默认**：<1 万条时这些参数是 no-op，但超 1 万条
HNSW 生效的那天，参数应该是 deliberate 选择而不是"当年没填"。
面试官问起来有答案。

**payload 索引取舍**：
- user_id（KEYWORD）：每次 recall 都按它过滤，不建就是全扫。
- kind（KEYWORD）：recall 支持 kind 过滤；"按类别查偏好"是自然的产品查询。
- sensitivity（KEYWORD）：审计/管理查询"列出所有敏感记忆"，基数小但
  过滤语义明确。
- created_at（DATETIME）：过期删除 forget_expired 的范围查询靠它，
  这是建它的直接理由（闭环见 20261008-memory-expiry.md）。
- source_type 不建：几乎不被过滤。content 不建全文索引：召回走向量，
  hybrid lexical search 是另一个决策，不为"可能有用"预建。
  索引有写放大和存储成本，只给"会被过滤的字段"建。

**quantization 现阶段不做**：512 维 × 4 字节 = 2KB/条，5000 条约 10MB，
本地文件模式走 mmap，RAM 不是约束。scalar quantization（int8）能省
约 4 倍内存，但在小库上精确扫描已是 1.000 的 recall@5，量化只会引入
可测的质量损失，省不下有意义的内存——付代价零收益。重审触发线：
向量数 >10 万，或车规给出明确 RAM 预算。

**维度校验 + rebuild_collection**：384→512 换模型时老库混用是真实踩坑点。
单用户原型不做自动迁移（migration 代码比问题本身重，不值——
embedding-bge 记录已定），但报错必须是人话：报出 collection 名、
现有维度、期望维度、处理办法（调 rebuild_collection() 或删库重建）。
rebuild 只给开发/换模型时手动调用。

**诚实记下放弃的东西**：为未来规模提前调优的"安全感"；承认当前
HNSW 参数在 <1 万条时就是 no-op——诚实比"看起来专业"重要。
也承认 payload 索引在本地文件模式下按 qdrant 的 warning 是"no effect"
（filter 照常工作），建它们是为将来切 server 模式不断链，
这个 warning 是预期内的，已在代码里抑制并注释。

## 对 eval 的影响

- eval 33 条用 hash_embedder（64 维）+ 临时目录建库，不受 512 维设计影响；
  回归要求：33 条零变化（28/33 基线，5 红是 clarify/长途确认产品缺口，
  与本任务无关，改动前后对照）。
- 新增测试即回归项：HNSW 参数断言（m/ef_construct）、payload 索引断言、
  维度 mismatch 人话报错、rebuild_collection 可用。以后换 embedding
  或调参，必须同步更新这组断言。
- memory_recall_cases.jsonl 的 probe（21 条）仍是 embedding 侧的护栏，
  与本记录的 collection 参数正交。
