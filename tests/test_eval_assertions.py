import pytest

from eval.eval_runner import _memory_events_ok, run_case
from trippilot.graph import memory_confirm_id
from trippilot.memory.store import PreferenceStore, hash_embedder
from trippilot.state import TraceEvent


@pytest.fixture()
def store(tmp_path):
    memory_store = PreferenceStore(tmp_path / "qdrant",
                                   embed_fn=hash_embedder())
    try:
        yield memory_store
    finally:
        memory_store.close()


def test_unknown_success_criterion_fails_case():
    case = {
        "case_id": "unknown-criterion",
        "user_request": "你好",
        "context": {"vehicle_state": "parked_simulated"},
        "expected": {
            "allowed_tools": [],
            "required_nodes": [],
            "success_criteria": {"not_registered": True},
        },
    }

    result = run_case(case)

    assert result["passed"] is False
    assert result["criterion_failures"] == [
        "unknown success criterion: not_registered"
    ]


def test_memory_events_reject_mismatched_confirm_id(store):
    mid = store._insert("owner", "家庭住址：望京", "place", "sensitive", "chat")
    trace = [
        TraceEvent(node="memory_recall", event="recalled"),
        TraceEvent(node="human_confirm", event="confirmed",
                   payload={"confirmed_ids": ["wrong"]}),
        TraceEvent(node="memory_capture", event="memories_written",
                   payload={"written": [mid],
                            "written_confirm_ids": ["different"]}),
    ]

    assert _memory_events_ok(trace, store) is False


def test_memory_events_accept_matching_confirm_id(store):
    candidate = {"kind": "place", "content": "家庭住址：望京"}
    confirm_id = memory_confirm_id(candidate)
    mid = store._insert("owner", candidate["content"], candidate["kind"],
                        "sensitive", "chat")
    trace = [
        TraceEvent(node="memory_recall", event="recalled"),
        TraceEvent(node="memory_capture", event="confirm_needed",
                   payload={"pending_confirm_ids": [confirm_id]}),
        TraceEvent(node="human_confirm", event="confirmed",
                   payload={"confirmed_ids": [confirm_id]}),
        TraceEvent(node="memory_capture", event="memories_written",
                   payload={"written": [mid],
                            "written_confirm_ids": [confirm_id]}),
    ]

    assert _memory_events_ok(trace, store) is True
