"""偏好召回进入规划上下文，澄清答案必须绑定原地点选项。"""

from typing import Any

from trippilot.graph import build_graph, new_state, run_graph
from trippilot.llm import DeterministicStub
from trippilot.memory.store import PreferenceStore, hash_embedder
from trippilot.state import ASRResult

ATTRS = {"user_id": "owner", "authenticated": True, "role": "owner"}
PLACES = [{"name": "北京西站"}, {"name": "北京南站"}]


class RecordingLLM(DeterministicStub):
    def __init__(self) -> None:
        self.plan_context: dict[str, Any] | None = None

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        self.plan_context = context
        return super().plan(intent=intent, context=context,
                            available_tools=available_tools)


def _route_state(answer: str):
    return new_state(
        user_request="导航去西站",
        asr_result=ASRResult(text="导航去西站", confidence=0.9,
                             place_entities=PLACES),
        trip_context={"clarify_answer": answer, "destination": "错误地点"},
        user_attributes=ATTRS,
    )


def test_recalled_preferences_are_visible_to_planner(tmp_path):
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    store.remember("owner", "公司地址：望京 SOHO", kind="place")
    llm = RecordingLLM()

    try:
        run_graph(build_graph(llm, memory_store=store), new_state(
            user_request="送我去公司", user_attributes=ATTRS))
    finally:
        store.close()

    assert llm.plan_context is not None
    assert llm.plan_context["preferences"] == [
        {"kind": "place", "content": "公司地址：望京 SOHO"}
    ]


def test_valid_clarify_answer_updates_destination_and_continues():
    out = run_graph(build_graph(DeterministicStub()), _route_state("西站"))

    assert out.stop_after_clarify is False
    assert out.trip_context["clarify_resolved"] == "北京西站"
    assert out.trip_context["destination"] == "北京西站"
    route_call = next(call for call in out.tool_calls
                      if call.tool == "map.route")
    assert route_call.args["destination"] == "北京西站"


def test_unrelated_clarify_answer_keeps_waiting_without_route_call():
    out = run_graph(build_graph(DeterministicStub()), _route_state("随便吧"))

    assert out.stop_after_clarify is True
    assert out.confirmation_state == "pending"
    assert "北京西站" in out.final_response
    assert "北京南站" in out.final_response
    assert not any(call.tool == "map.route" for call in out.tool_calls)
