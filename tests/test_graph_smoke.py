"""主循环冒烟测试：无 key 也能跑通 intent→…→verifier 全链路。"""

from trippilot.graph import build_graph, new_state, run_graph
from trippilot.llm import DeterministicStub
from trippilot.tools.tools import ReminderTool, TripLogTool


def setup_function():
    ReminderTool.reset()
    TripLogTool.reset()


def test_full_loop_with_confirmation():
    graph = build_graph(DeterministicStub())
    state = new_state(
        user_request="明天下午两点去中关村面试，帮我查路线并提前一小时提醒我",
        trip_context={"confirm": True, "destination": "中关村",
                      "reminder_content": "去中关村面试",
                      "reminder_time": "2026-10-06T13:00:00+08:00",
                      "session_id": "smoke"},
        user_attributes={"user_id": "owner", "authenticated": True,
                         "role": "owner"})
    out = run_graph(graph, state)
    assert out.verification_result.get("ok") is True
    assert out.confirmation_state == "confirmed"
    assert "policy_gate" in out.visited_nodes
    assert "human_confirm" in out.visited_nodes
    tools = {c.tool for c in out.tool_calls}
    assert {"map.route", "reminder.create"} <= tools
    # 轨迹 append-only：事件数 >= 节点数
    assert len(out.trace) >= len(out.visited_nodes)


def test_clarify_stops_without_answer():
    from trippilot.state import ASRResult
    graph = build_graph(DeterministicStub())
    state = new_state(
        user_request="导航去西站",
        asr_result=ASRResult(text="导航去西站", confidence=0.9,
                             place_entities=[{"name": "北京西站"},
                                             {"name": "西站地铁站"}]),
        trip_context={},
        user_attributes={"user_id": "owner", "authenticated": True,
                         "role": "owner"})
    out = run_graph(graph, state)
    # 地点歧义且无澄清答案 → 停在 clarify，等用户输入，不猜测执行
    assert out.stop_after_clarify is True
    assert out.confirmation_state == "pending"
    assert out.tool_calls == []
