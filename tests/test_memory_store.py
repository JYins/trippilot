"""PreferenceStore 测试：全链路 + fixture 召回@k，全部离线 fake 向量。"""

import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from trippilot.memory.store import (MemoryRejected, PreferenceStore,
                                    _bge_default, hash_embedder)

UID = "u_test"


def test_bge_default_wiring(monkeypatch):
    calls = {}
    fake_module = ModuleType("sentence_transformers")

    class StubSentenceTransformer:
        def __init__(self, model_name):
            calls["model_name"] = model_name

        def encode(self, texts, **kwargs):
            calls["texts"] = texts
            calls["encode_kwargs"] = kwargs
            return [[0.1, 0.2] for _ in texts]

    fake_module.SentenceTransformer = StubSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)

    embed = _bge_default()
    assert embed(["送我去公司"]) == [[0.1, 0.2]]
    assert calls["model_name"] == "BAAI/bge-small-zh-v1.5"
    assert calls["encode_kwargs"]["normalize_embeddings"] is True


@pytest.fixture()
def store(tmp_path):
    s = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    try:
        yield s
    finally:
        s.close()


def test_remember_recall_roundtrip(store):
    status, mid = store.remember(UID, "常去地点：首都机场 T3", kind="place")
    assert status == "written" and mid
    hits = store.recall(UID, "去机场", top_k=3)
    assert len(hits) == 1 and hits[0].id == mid
    assert hits[0].content == "常去地点：首都机场 T3"
    assert hits[0].kind == "place"


def test_recall_isolated_by_user(store):
    store.remember("u1", "常去地点：首都机场 T3", kind="place")
    store.remember("u2", "常去地点：大兴机场", kind="place")
    hits = store.recall("u1", "机场")
    assert hits and {h.user_id for h in hits} == {"u1"}


def test_recall_kind_filter(store):
    store.remember(UID, "常去地点：首都机场 T3", kind="place")
    store.remember(UID, "机场喜欢靠窗座位", kind="other")
    hits = store.recall(UID, "机场", kind="place")
    assert len(hits) == 1 and hits[0].kind == "place"


def test_recall_empty_store(store):
    assert store.recall(UID, "机场") == []
    assert store.get("0" * 32) is None


def test_update_rewrites_vector(store):
    _, mid = store.remember(UID, "常去地点：首都机场 T3", kind="place")
    status, updated = store.update(mid, content="常去地点：大兴机场")
    assert status == "written"
    assert updated is not None
    assert updated.id == mid
    assert updated.content == "常去地点：大兴机场"
    hits = store.recall(UID, "大兴机场")
    assert hits and hits[0].id == mid


def test_update_missing_raises(store):
    with pytest.raises(KeyError):
        store.update("0" * 32, content="x")


def test_update_content_runs_memory_gate(store):
    _, mid = store.remember(UID, "偏好地铁出行", kind="other")
    # 改成 sensitive：不直接写，返回 needs_confirm
    status, updated = store.update(mid, sensitivity="sensitive")
    assert status == "needs_confirm" and updated is None
    assert store.get(mid).sensitivity == "normal"
    # content 变更 + transient：deny，直接抛
    with pytest.raises(MemoryRejected):
        store.update(mid, content="临时去一趟打印店", is_transient=True)
    assert store.get(mid).content == "偏好地铁出行"


def test_update_kind_only_skips_embed(tmp_path):
    calls = []
    base = hash_embedder()

    def counting_embed(texts):
        calls.append(texts)
        return base(texts)

    s = PreferenceStore(tmp_path / "qdrant", embed_fn=counting_embed)
    try:
        _, mid = s.remember(UID, "偏好地铁出行", kind="other")
        embedded = len(calls)
        # 只改 kind：直接写，不重新向量化
        status, updated = s.update(mid, kind="label")
        assert status == "written" and updated is not None
        assert updated.kind == "label"
        assert len(calls) == embedded
        # 改 content：重新向量化一次
        s.update(mid, content="偏好骑车出行")
        assert len(calls) == embedded + 1
    finally:
        s.close()


def test_forget(store):
    _, mid = store.remember(UID, "常去地点：首都机场 T3", kind="place")
    assert store.get(mid) is not None
    store.forget(mid)
    assert store.get(mid) is None


def test_forget_all(store):
    store.remember(UID, "偏好一", kind="other")
    store.remember(UID, "偏好二", kind="other")
    store.remember("u_other", "偏好三", kind="other")
    store.forget_all(UID)
    assert store.recall(UID, "偏好") == []
    assert len(store.recall("u_other", "偏好")) == 1


def _load_cases():
    path = (Path(__file__).resolve().parent.parent
            / "fixtures" / "memory_recall_cases.jsonl")
    seeds, queries = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        (seeds if row["type"] == "seed" else queries).append(row)
    return seeds, queries


def test_recall_at_k_from_fixture(store):
    seeds, queries = _load_cases()
    strict = [q for q in queries if not q.get("semantic_only")]
    assert len(seeds) >= 4 and len(strict) == 3
    # 常去地点 / 称呼 / 授权偏好各至少一条
    assert {"place", "label", "auth"} <= {s["kind"] for s in seeds}
    tag_to_id = {}
    for s in seeds:
        status, mid = store.remember(
            UID, s["content"], kind=s["kind"],
            sensitivity=s.get("sensitivity", "normal"),
            source_type=s.get("source_type", "chat"))
        assert status == "written"
        tag_to_id[s["tag"]] = mid
    for q in strict:
        ids = [h.id for h in store.recall(UID, q["text"], top_k=q["top_k"],
                                          kind=q.get("kind"))]
        assert tag_to_id[q["expect_tag"]] in ids, \
            f"query={q['text']!r} 没召回期望偏好"


def _bigrams(text):
    t = text.lower().strip()
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def test_semantic_only_case_uses_kind_filter(store):
    """纯语义改写 case：query 与期望 seed 零二元字重叠。

    fake 向量（字二元哈希）召回不到这种 case——这是已知局限，
    生产召回以 FastEmbed 为准。此处只锁两条性质：
    ① fixture 里确实零重叠；② kind filter 把 place 干扰项滤掉。
    """
    seeds, queries = _load_cases()
    case = next(q for q in queries if q.get("semantic_only"))
    expected = next(s for s in seeds if s["tag"] == case["expect_tag"])
    assert not (_bigrams(case["text"]) & _bigrams(expected["content"]))
    for s in seeds:
        store.remember(UID, s["content"], kind=s["kind"])
    hits = store.recall(UID, case["text"], top_k=case["top_k"],
                        kind=case["kind"])
    assert hits, "kind filter 下应有候选"
    assert all(h.kind == "other" for h in hits)
    assert not any(h.content == "喜欢安静的咖啡馆办公" for h in hits)
