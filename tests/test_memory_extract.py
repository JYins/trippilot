"""记忆抽取：规则式抽取的正例 / 反例 / 敏感分级 / 置信度排序。

评测数据在 fixtures/memory_extract_cases.jsonl：每行是
{text, expect[]}，expect 为空表示不该抽到（反例）。
"""

import json
from pathlib import Path

from trippilot.graph import build_graph, new_state, run_graph
from trippilot.llm import DeterministicStub
from trippilot.memory.extract import extract_candidates
from trippilot.memory.store import PreferenceStore, hash_embedder

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" \
    / "memory_extract_cases.jsonl"

ATTRS = {"user_id": "owner", "authenticated": True, "role": "owner"}


def _cases():
    with open(FIXTURE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_extraction_matches_fixture_expectations():
    """评测集：正例抽到且字段达标，反例一条不抽。"""
    for case in _cases():
        cands = extract_candidates(case["text"])
        assert len(cands) == len(case["expect"]), \
            f"{case['text']!r}: 期望 {len(case['expect'])} 条，抽到 {cands}"
        for cand, exp in zip(cands, case["expect"]):
            assert cand["content"] == exp["content"]
            assert cand["kind"] == exp["kind"]
            assert cand["sensitivity"] == exp["sensitivity"]
            assert cand["confidence"] >= exp["min_confidence"]
            assert cand["source_type"] == "extracted"


def test_confidence_ordering():
    """置信度 sanity：明确的记忆指令 > 称呼 > 持久标记 >
    偏好动词 > 禁止句式（全场最低）。"""
    def confidence_for(text: str) -> float:
        return extract_candidates(text)[0]["confidence"]

    remember = confidence_for("记住我喜欢坐地铁")
    label = confidence_for("叫我 Jeremy")
    persistent = confidence_for("以后都坐地铁")
    like = confidence_for("我喜欢坐地铁")
    forbid = confidence_for("以后别给我放广告")
    assert remember > label > persistent > like > forbid
    assert forbid < 0.7  # 即使有"以后"也分不清真禁忌和带情绪的一次性抱怨，
    # 禁止句式置信度垫底：模糊的让 Gate 问人


def test_bare_negation_suppressed():
    """回归 20251006：裸"别/不要"是一次性指令，不产出候选；
    只有带"以后/再"持久标记的否定才抽（见 fixture 新增用例）。"""
    for text in ["别走这条路", "别急，慢慢来", "不要急", "慢点，别走这条路"]:
        assert extract_candidates(text) == [], text
    # 持久型否定照常抽，置信度仍是全场最低
    kept = extract_candidates("别再给我导航走高速")
    assert len(kept) == 1 and kept[0]["confidence"] == 0.65


def test_output_sorted_by_confidence():
    cands = extract_candidates("以后别放广告，记住我喜欢地铁")
    assert [c["content"] for c in cands] == ["我喜欢地铁", "以后别放广告"]
    confs = [c["confidence"] for c in cands]
    assert confs == sorted(confs, reverse=True)


def test_sensitive_grading_via_content():
    """敏感分级不只看触发词：规则漏标时内容关键词兜底。"""
    cands = extract_candidates("记住我家地址是望京XX小区")
    assert cands and cands[0]["sensitivity"] == "sensitive"
    assert cands[0]["kind"] == "place"  # 内容含"我家"→ place，不是 other


def test_dedup_overlapping_rules():
    """重叠命中只留一条：'记住我家住在望京' 不产出 '望京' 第二条。"""
    cands = extract_candidates("记住我家住在望京XX小区")
    assert len(cands) == 1
    assert cands[0]["content"] == "我家住在望京XX小区"


def test_graph_auto_extract_writes_normal(tmp_path):
    """进图：外部没给候选时自动抽取，normal 候选直接写盘。"""
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    graph = build_graph(DeterministicStub(), memory_store=store)
    try:
        out = run_graph(graph, new_state(user_request="记住我喜欢坐地铁",
                                         user_attributes=ATTRS))
        hits = store.recall("owner", "地铁出行")
    finally:
        store.close()
    assert "memory_capture" in out.visited_nodes
    ev = next(e for e in out.trace if e.node == "memory_capture")
    assert ev.payload["extracted"] == 1
    assert hits and hits[0].content == "我喜欢坐地铁"


def test_graph_extracted_sensitive_still_goes_through_gate(tmp_path):
    """红线：抽到的 sensitive 候选照样挂起走 human_confirm，不绕 Gate。"""
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    graph = build_graph(DeterministicStub(), memory_store=store)
    try:
        out = run_graph(graph, new_state(user_request="记住我家住在望京XX小区",
                                         user_attributes=ATTRS))
        unwritten = store.recall("owner", "望京")
    finally:
        store.close()
    assert "human_confirm" in out.visited_nodes
    assert out.stop_after_confirm is True
    assert len(out.pending_memory_confirms) == 1
    assert "记住这条偏好" in out.final_response
    # 没确认 → 没写盘
    assert unwritten == []


def test_no_extract_when_external_candidates_given(tmp_path):
    """外部给了候选就不自动抽：extracted 计数为 0。"""
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    graph = build_graph(DeterministicStub(), memory_store=store)
    try:
        out = run_graph(graph, new_state(
            user_request="记住我喜欢坐地铁",
            user_attributes=ATTRS,
            memory_candidates=[{"content": "偏好地铁出行", "kind": "other",
                                "sensitivity": "normal"}]))
    finally:
        store.close()
    ev = next(e for e in out.trace if e.node == "memory_capture")
    assert ev.payload["extracted"] == 0
    assert "extracted_from" not in out.trip_context


def test_no_double_extract_on_reentry(tmp_path):
    """human_confirm 回绕进 memory_capture 时不重复抽取。"""
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    graph = build_graph(DeterministicStub(), memory_store=store)
    try:
        out = run_graph(graph, new_state(
            user_request="记住我喜欢坐地铁",
            user_attributes=ATTRS,
            trip_context={"extracted_from": "记住我喜欢坐地铁"}))
    finally:
        store.close()
    ev = next(e for e in out.trace if e.node == "memory_capture")
    assert ev.payload["extracted"] == 0
    assert ev.payload["written"] == []
