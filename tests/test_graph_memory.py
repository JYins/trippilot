"""memory_recall / memory_capture 进图：走现有 human_confirm，不另起确认机制。"""

import pytest

from trippilot.graph import build_graph, new_state, run_graph
from trippilot.llm import DeterministicStub
from trippilot.memory.store import PreferenceStore, hash_embedder

ATTRS = {"user_id": "owner", "authenticated": True, "role": "owner"}


@pytest.fixture()
def graph_and_store(tmp_path):
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    try:
        yield build_graph(DeterministicStub(), memory_store=store), store
    finally:
        store.close()


def test_recall_stores_preferences_in_state(graph_and_store):
    graph, store = graph_and_store
    store.remember("owner", "公司地址：望京 SOHO", kind="place")
    out = run_graph(graph, new_state(user_request="送我去公司",
                                     user_attributes=ATTRS))
    assert "memory_recall" in out.visited_nodes
    assert any(e.node == "memory_recall" for e in out.trace)
    assert any("望京 SOHO" in p["content"] for p in out.preferences)


def test_recall_without_store_is_noop():
    graph = build_graph(DeterministicStub())  # 不传 store
    out = run_graph(graph, new_state(user_request="送我去公司",
                                     user_attributes=ATTRS))
    assert "memory_recall" in out.visited_nodes
    assert out.preferences == []


def test_sensitive_memory_goes_through_human_confirm(graph_and_store):
    graph, store = graph_and_store
    state = new_state(
        user_request="记住我家地址",
        trip_context={"confirm": True},  # 非交互：视为用户已确认
        user_attributes=ATTRS,
        memory_candidates=[
            {"content": "偏好地铁出行", "kind": "other",
             "sensitivity": "normal"},
            {"content": "家庭住址：望京XX小区", "kind": "place",
             "sensitivity": "sensitive"},
            {"content": "临时去一趟打印店", "kind": "other",
             "is_transient": True},
        ],
    )
    out = run_graph(graph, state)
    assert "memory_capture" in out.visited_nodes
    assert "human_confirm" in out.visited_nodes
    # normal 直接写了，transient 被拒没写
    assert store.recall("owner", "地铁出行")
    assert store.recall("owner", "打印店") == []
    # sensitive：human_confirm 事件先于敏感偏好的实际写入（eval 回归要求）
    hc_idx = next(i for i, e in enumerate(out.trace)
                  if e.node == "human_confirm")
    sensitive_id = store.recall("owner", "家庭住址")[0].id
    write_idx = next(i for i, e in enumerate(out.trace)
                     if e.node == "memory_capture"
                     and sensitive_id in e.payload.get("written", []))
    assert hc_idx < write_idx
    assert out.pending_memory_confirms == []


def test_sensitive_memory_waits_for_user(graph_and_store):
    graph, store = graph_and_store
    state = new_state(
        user_request="记住我家地址",
        user_attributes=ATTRS,
        memory_candidates=[
            {"content": "家庭住址：望京XX小区", "kind": "place",
             "sensitivity": "sensitive"},
        ],
    )
    out = run_graph(graph, state)
    # 走现有 human_confirm：pending + 话术里带确认内容，不另起机制
    assert out.stop_after_confirm is True
    assert out.confirmation_state == "pending"
    assert len(out.pending_memory_confirms) == 1
    assert "记住这条偏好" in out.final_response
    assert "家庭住址" in out.final_response
    # 没确认 → 没写盘
    assert store.recall("owner", "家庭住址") == []
