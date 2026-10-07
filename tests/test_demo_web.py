import pytest
from fastapi.testclient import TestClient

from trippilot import api
from trippilot.graph import build_graph
from trippilot.llm import DeterministicStub, make_llm
from trippilot.memory.store import PreferenceStore, hash_embedder


@pytest.fixture()
def client(monkeypatch):
    graph = build_graph(DeterministicStub())
    monkeypatch.setattr(api, "_graph", graph)
    monkeypatch.setattr(api, "_memory_store", None)
    api._pending_memory_confirms.clear()
    with TestClient(api.app) as test_client:
        yield test_client
    api._pending_memory_confirms.clear()


def test_turn_response_contains_trace(client):
    response = client.post("/turn", json={"text": "帮我查路线"})

    assert response.status_code == 200
    trace = response.json()["trace"]
    assert trace
    assert {"node", "event", "payload"} <= trace[0].keys()


def test_root_serves_demo_page(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "TripPilot" in response.text
    assert "座舱状态·模拟" in response.text


def test_full_flow_uses_stub_without_llm_environment(client, monkeypatch):
    for name in (
        "TRIPPILOT_LLM_BASE_URL",
        "TRIPPILOT_LLM_API_KEY",
        "TRIPPILOT_LLM_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert isinstance(make_llm(), DeterministicStub)
    response = client.post("/turn", json={"text": "帮我查路线"})

    assert response.status_code == 200
    body = response.json()
    assert body["final_response"]
    assert "verifier" in body["visited_nodes"]


def test_confirm_flow_keeps_working(tmp_path, monkeypatch):
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    graph = build_graph(DeterministicStub(), memory_store=store)
    monkeypatch.setattr(api, "_graph", graph)
    api._pending_memory_confirms.clear()
    try:
        pending = api.turn(api.TurnRequest(
            text="记住我家住在望京XX小区"
        )).model_dump()
        item = pending["pending_memory_confirms"][0]
        response = api.turn(api.TurnRequest(
            session_id=pending["session_id"],
            text="确认",
            confirm=True,
            confirm_nonce=item["nonce"],
        ))
    finally:
        api._pending_memory_confirms.clear()
        store.close()

    assert response.confirmation_state == "confirmed"
