"""工具确认和敏感记忆确认不能互相搭便车。"""

from trippilot.graph import build_graph, new_state, run_graph
from trippilot.graph_nodes import (human_confirm_node, memory_capture_node,
                                   memory_confirm_id)
from trippilot.llm import DeterministicStub
from trippilot.memory.store import PreferenceStore, hash_embedder
from trippilot.tools.tools import ReminderTool, TripLogTool

ATTRS = {"user_id": "owner", "authenticated": True, "role": "owner"}


def setup_function():
    ReminderTool.reset()
    TripLogTool.reset()


def test_tool_confirm_does_not_authorize_memory_write(tmp_path):
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    candidate = {
        "content": "家庭住址：望京XX小区",
        "kind": "place",
        "sensitivity": "sensitive",
    }
    try:
        graph = build_graph(DeterministicStub(), memory_store=store)
        state = new_state(
            user_request="明天下午两点去中关村面试，帮我查路线并提前一小时提醒我",
            trip_context={
                "confirm": True,
                "destination": "中关村",
                "reminder_content": "去中关村面试",
                "reminder_time": "2026-10-06T13:00:00+08:00",
                "session_id": "smoke",
            },
            user_attributes=ATTRS,
            memory_candidates=[candidate],
        )

        out = run_graph(graph, state)

        confirmed_events = [
            event for event in out.trace
            if event.node == "human_confirm" and event.event == "confirmed"
        ]
        assert len(confirmed_events) == 2
        confirm_id = memory_confirm_id(candidate)
        assert confirm_id not in confirmed_events[0].payload["confirmed_ids"]

        capture_events = [event for event in out.trace
                          if event.node == "memory_capture"]
        assert capture_events[0].payload["pending"] == 1
        assert capture_events[0].payload["written"] == []
        assert confirm_id in capture_events[1].payload["written_confirm_ids"]
        assert store.recall("owner", "家庭住址")
    finally:
        store.close()


def test_memory_confirm_does_not_authorize_tools():
    candidate = {
        "content": "家庭住址：望京XX小区",
        "kind": "place",
        "sensitivity": "sensitive",
    }
    state = new_state(
        trip_context={"confirm": True},
        pending_memory_confirms=[candidate],
        pending_tool_calls=[],
    )

    update = human_confirm_node(state)

    assert memory_confirm_id(candidate) in update["confirmed_memory_ids"]
    assert update.get("tool_call_confirmed", False) is False


def test_unconfirmed_memory_candidate_stays_pending(tmp_path):
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    candidate = {
        "content": "家庭住址：望京XX小区",
        "kind": "place",
        "sensitivity": "sensitive",
    }
    state = new_state(
        user_attributes=ATTRS,
        pending_memory_confirms=[candidate],
        confirmed_memory_ids=[],
    )
    try:
        update = memory_capture_node(state, memory_store=store)

        assert store.recall("owner", "家庭住址") == []
        assert update["pending_memory_confirms"] == [candidate]
        event = update["trace"][-1]
        assert event.node == "memory_capture"
        assert event.payload["pending"] == 1
        assert event.payload["written"] == []
    finally:
        store.close()
