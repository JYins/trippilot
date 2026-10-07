"""embedding 回归探针：FastEmbed（旧默认）vs BGE（新默认）的召回质量与延迟对比。

不是 pytest——本脚本会下载模型（FastEmbed onnx、BGE 权重），只做一次
回归实测用。pytest 里 embedding 永远用 fake（见 tests/test_memory_store.py）。

probe 来源（全部取自现有测试/fixtures，不自编）：
- seeds s1..s7 / queries q1..q4：fixtures/memory_recall_cases.jsonl
- seeds s8..s11：tests/test_memory_store.py（remember 文本）
- seed s12 / queries q8..q10：tests/test_graph_memory.py（memory_candidates / recall 文本）
- queries q5/q11：tests/test_memory_store.py（"机场"召回、kind 过滤）
- self-recall：每条 seed 用自身文本做 query（测试里 roundtrip 的写法），期望 rank 1

seed 写入走 _insert 直接插盘，绕过 Memory Gate——测的是 embedding 召回，
不是门禁行为（门禁有自己的测试）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from trippilot.memory.store import PreferenceStore  # noqa: E402

# (tag, content, kind) —— content 逐字取自 fixture / 测试源码
SEEDS = [
    ("home_airport", "常去地点：首都机场 T3", "place"),      # fixture
    ("office", "公司地址：望京 SOHO", "place"),              # fixture
    ("name", "称呼偏好：叫我 Jeremy", "label"),              # fixture
    ("auth_reminder", "允许自动创建提醒事项", "auth"),       # fixture
    ("park", "常去地点：朝阳公园", "place"),                 # fixture
    ("quiet_home", "在家喜欢安静环境工作", "other"),         # fixture
    ("quiet_cafe", "喜欢安静的咖啡馆办公", "place"),         # fixture
    ("subway", "偏好地铁出行", "other"),                     # test_memory_store
    ("printshop", "临时去一趟打印店", "other"),              # test_memory_store
    ("window_seat", "机场喜欢靠窗座位", "other"),            # test_memory_store
    ("bike", "偏好骑车出行", "other"),                       # test_memory_store
    ("home_addr", "家庭住址：望京XX小区", "place"),          # test_graph_memory
]

# (query, expect_tag, kind_filter) —— query 逐字取自 fixture / 测试源码
QUERIES = [
    ("送我去公司", "office", None),                          # fixture
    ("去机场", "home_airport", None),                        # fixture
    ("叫我什么", "name", None),                              # fixture
    ("闹市区太吵，想找个清静地儿写代码", "quiet_home", "other"),  # fixture(semantic_only)
    ("机场", "home_airport", None),                          # test_memory_store
    ("机场", "home_airport", "place"),                       # test_memory_store(kind过滤)
    ("地铁出行", "subway", None),                            # test_graph_memory
    ("打印店", "printshop", None),                           # test_graph_memory
    ("家庭住址", "home_addr", None),                         # test_graph_memory
]
# self-recall：seed 自身文本做 query，期望 rank 1
QUERIES += [(content, tag, None) for tag, content, _ in SEEDS]

UID = "u_probe"


def fastembed_fn():
    from fastembed import TextEmbedding

    model = TextEmbedding("BAAI/bge-small-en-v1.5")  # 旧生产默认

    def embed(texts):
        return [list(v) for v in model.embed(texts)]

    return embed, "FastEmbed/bge-small-en-v1.5"


def bge_fn():
    from trippilot.memory.store import _bge_default

    return _bge_default(), "BGE/bge-small-zh-v1.5"


def build_store(embed_fn, tmpdir):
    store = PreferenceStore(Path(tmpdir) / "qdrant", embed_fn=embed_fn)
    ids = {}
    for tag, content, kind in SEEDS:
        # _insert 直写，绕过 Memory Gate（门禁行为另有测试覆盖）
        ids[tag] = store._insert(UID, content, kind, "normal", "chat")
    return store, ids


def timed(fn, *args, repeat=5):
    fn(*args)  # warmup
    t0 = time.perf_counter()
    for _ in range(repeat):
        fn(*args)
    return (time.perf_counter() - t0) / repeat


def main():
    results = {}
    for make_fn in (fastembed_fn, bge_fn):
        embed_fn, label = make_fn()
        with tempfile.TemporaryDirectory() as tmpdir:
            store, ids = build_store(embed_fn, tmpdir)

            # --- 召回质量 ---
            ranks = []
            for query, expect_tag, kind in QUERIES:
                hits = store.recall(UID, query, top_k=5, kind=kind)
                hit_ids = [h.id for h in hits]
                try:
                    ranks.append(hit_ids.index(ids[expect_tag]) + 1)
                except ValueError:
                    ranks.append(None)
            r3 = sum(1 for r in ranks if r is not None and r <= 3) / len(ranks)
            r5 = sum(1 for r in ranks if r is not None and r <= 5) / len(ranks)
            ranked = [r for r in ranks if r is not None]
            mean_rank = sum(ranked) / len(ranked) if ranked else float("nan")

            # --- 延迟 ---
            single = timed(embed_fn, ["送我去公司"])
            batch_texts = [q for q, _, _ in QUERIES]
            batch = timed(embed_fn, batch_texts, repeat=3)
            e2e = timed(lambda: [store.recall(UID, q, top_k=5) for q in
                                 [qq for qq, _, _ in QUERIES]], repeat=3)
            store.close()

        results[label] = {
            "n_probes": len(QUERIES),
            "recall@3": round(r3, 3),
            "recall@5": round(r5, 3),
            "mean_rank": round(mean_rank, 2),
            "missed": [QUERIES[i][0] for i, r in enumerate(ranks) if r is None],
            "single_embed_s": round(single, 4),
            "batch_embed_21q_s": round(batch, 4),
            "e2e_21q_recall_s": round(e2e, 4),
        }
        print(f"== {label} ==")
        print(json.dumps(results[label], ensure_ascii=False, indent=2))

    out = REPO / "eval" / "embed_recall_probe_result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n结果已写入 {out}")


if __name__ == "__main__":
    main()
