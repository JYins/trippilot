import hashlib
import logging
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trippilot import api
from trippilot.state import PolicyDecision


client = TestClient(api.app)


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_asr_confidence_accepts_boundaries(confidence):
    request = api.TurnRequest(asr_confidence=confidence)

    assert request.asr_confidence == confidence


def test_asr_confidence_out_of_range_returns_422():
    response = client.post("/turn", json={"asr_confidence": 1.5})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "asr_confidence"]


def test_invalid_vehicle_state_returns_422():
    response = client.post("/turn", json={"vehicle_state": "flying"})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "vehicle_state"]


def test_logs_hide_request_text_and_policy_detail(caplog, monkeypatch):
    text = "联系我13800138000，生日是1990年1月2日"
    asr_text = "地址是北京市朝阳区望京小区"
    decision = PolicyDecision(
        decision="deny",
        reason_code="private_detail",
        detail="手机号13800138000，生日为1990-1-2，住址是望京花园小区",
    )
    output = SimpleNamespace(
        session_id="session-1",
        trace_id="trace-1",
        final_response="未执行",
        confirmation_state="not_required",
        stop_after_clarify=False,
        visited_nodes=["policy_gate"],
        policy_decisions=[decision],
        verification_result={"ok": False},
        pending_memory_confirms=[],
    )
    monkeypatch.setattr(api, "get_graph", lambda: object())
    monkeypatch.setattr(api, "run_graph", lambda graph, state: output)

    with caplog.at_level(logging.INFO, logger="trippilot.api"):
        api.turn(api.TurnRequest(text=text, asr_text=asr_text))

    logs = caplog.text
    text_sha1 = hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
    asr_sha1 = hashlib.sha1(asr_text.encode("utf-8")).hexdigest()[:12]
    assert f"'length': {len(text)}, 'sha1': '{text_sha1}'" in logs
    assert f"'length': {len(asr_text)}, 'sha1': '{asr_sha1}'" in logs
    assert text not in logs
    assert asr_text not in logs
    assert "13800138000" not in logs
    assert "1990-1-2" not in logs
    assert "望京花园小区" not in logs
    assert "***phone***" in logs
    assert "***birthday***" in logs
    assert "***address***" in logs


def test_health_declares_single_user_mode_without_authentication():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["mode"] == "single_user_local"
    assert response.json()["authentication"] == "none"
    assert api.LOCAL_USER_ATTRIBUTES == {
        "user_id": "local",
        "authenticated": False,
        "role": "local",
    }
