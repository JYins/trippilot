from copy import deepcopy

import pytest

import trippilot.graph as graph_module
from trippilot.graph import build_graph, new_state, run_graph
from trippilot.llm import ScriptedLLM
from trippilot.state import ToolCall, ToolResult
from trippilot.tools.tools import MapTool, ReminderTool, WeatherTool


def setup_function():
    ReminderTool.reset()


def _create_reminder(tool: ReminderTool, content: str, time: str) -> ToolResult:
    return tool.run(ToolCall(
        tool="reminder.create",
        args={"content": content, "time": time, "session_id": "test"},
    ))


def test_reminder_ids_are_not_reused_and_deleted_content_can_be_recreated():
    tool = ReminderTool()
    first = _create_reminder(tool, "第一条", "2026-10-07T09:00:00+08:00")
    second = _create_reminder(tool, "第二条", "2026-10-07T10:00:00+08:00")

    deleted = tool.run(ToolCall(
        tool="reminder.delete",
        args={"reminder_id": first.data["reminder_id"]},
    ))
    third = _create_reminder(tool, "第三条", "2026-10-07T11:00:00+08:00")
    replayed = _create_reminder(tool, "第一条", "2026-10-07T09:00:00+08:00")

    assert deleted.ok is True
    assert second.data["reminder_id"] == "rmd_0002"
    assert third.data["reminder_id"] == "rmd_0003"
    assert replayed.data == {
        "reminder_id": "rmd_0004",
        "duplicate_suppressed": False,
    }
    assert set(ReminderTool._store) == {"rmd_0002", "rmd_0003", "rmd_0004"}


def test_recovery_retries_reminder_with_all_original_args(monkeypatch):
    reminder = ReminderTool()
    seen_calls: list[ToolCall] = []

    class TimeoutAfterCreate:
        def run(self, call: ToolCall) -> ToolResult:
            seen_calls.append(deepcopy(call))
            result = reminder.run(call)
            if len(seen_calls) == 1:
                return ToolResult(tool=call.tool, ok=False,
                                  error="timeout after create")
            return result

    flaky_tool = TimeoutAfterCreate()
    monkeypatch.setattr(graph_module, "get_tool", lambda _: flaky_tool)
    original_args = {
        "content": "带伞",
        "time": "2026-10-07T08:00:00+08:00",
        "session_id": "recovery-test",
        "idempotency_key": "fixed-key",
    }
    llm = ScriptedLLM([{
        "step_id": "s1",
        "tool": "reminder.create",
        "args": original_args,
        "description": "创建提醒",
    }])
    state = new_state(
        session_id="recovery-test",
        user_request="提醒我带伞",
        trip_context={"confirm": True},
        user_attributes={"user_id": "owner", "authenticated": True,
                         "role": "owner"},
    )

    out = run_graph(build_graph(llm), state)

    assert [call.args for call in seen_calls] == [original_args, original_args]
    assert len(ReminderTool._store) == 1
    assert out.tool_results[-1].data["duplicate_suppressed"] is True
    recovery = next(event for event in out.trace
                    if event.node == "recovery")
    assert recovery.payload["changes"] == []


def test_recovery_records_only_the_fixture_fallback():
    llm = ScriptedLLM([{
        "step_id": "s1",
        "tool": "map.route",
        "args": {"origin": "望京", "destination": "中关村",
                 "fixture": "missing", "option_count": 2},
        "description": "查询路线",
    }])
    state = new_state(
        user_request="导航去中关村",
        trip_context={},
        user_attributes={"user_id": "owner", "authenticated": True,
                         "role": "owner"},
    )

    out = run_graph(build_graph(llm), state)

    assert out.tool_calls[-1].args == {
        "origin": "望京",
        "destination": "中关村",
        "fixture": "default",
        "option_count": 2,
    }
    recovery = next(event for event in out.trace
                    if event.node == "recovery")
    assert recovery.payload["changes"] == [{
        "tool": "map.route",
        "parameter": "fixture",
        "from": "missing",
        "to": "default",
    }]


@pytest.mark.parametrize(
    ("tool", "call", "field", "expected", "actual"),
    [
        (MapTool(), ToolCall(tool="map.route", args={
            "origin": "国贸", "destination": "中关村",
            "fixture": "default",
        }), "origin", "望京", "国贸"),
        (MapTool(), ToolCall(tool="map.route", args={
            "origin": "望京", "destination": "国贸",
            "fixture": "default",
        }), "destination", "中关村", "国贸"),
        (WeatherTool(), ToolCall(tool="weather.now", args={
            "area": "朝阳区", "fixture": "default",
        }), "area", "海淀区", "朝阳区"),
    ],
)
def test_recorded_fixtures_reject_mismatched_parameters(
        tool, call, field, expected, actual):
    result = tool.run(call)

    assert result.ok is False
    assert field in result.error
    assert repr(expected) in result.error
    assert repr(actual) in result.error
