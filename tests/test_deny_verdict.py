"""Verifier 要分别报告安全门禁结果和用户任务完成结果。"""

from trippilot.graph import build_graph, new_state, run_graph
from trippilot.llm import ScriptedLLM
from trippilot.tools.tools import TripLogTool


def test_deny_is_safe_but_task_is_not_completed():
    TripLogTool.reset()
    llm = ScriptedLLM([{
        "step_id": "s1",
        "tool": "trip_log.append",
        "args": {"entry": {"place": "国贸"}},
        "description": "写入行程日志",
    }])
    graph = build_graph(llm)
    state = new_state(
        user_request="把这次行程写进日志",
        user_attributes={
            "user_id": "guest",
            "authenticated": False,
            "role": "guest",
        },
    )

    out = run_graph(graph, state)

    assert out.verification_result["ok"] is True
    assert out.verification_result["task_completed"] is False
    assert out.tool_calls == []
    assert TripLogTool._entries == []
    assert "未认证用户禁止写操作" in out.final_response
    assert "unauthenticated_write" in out.final_response
    assert "本次没有执行工具调用" not in out.final_response


def test_allowed_tool_success_completes_task():
    llm = ScriptedLLM([{
        "step_id": "s1",
        "tool": "weather.now",
        "args": {"area": "海淀区", "fixture": "default"},
        "description": "查询天气",
    }])
    graph = build_graph(llm)
    state = new_state(
        user_request="查一下海淀区天气",
        user_attributes={
            "user_id": "owner",
            "authenticated": True,
            "role": "owner",
        },
    )

    out = run_graph(graph, state)

    assert out.verification_result["ok"] is True
    assert out.verification_result["task_completed"] is True
    assert [call.tool for call in out.tool_calls] == ["weather.now"]
