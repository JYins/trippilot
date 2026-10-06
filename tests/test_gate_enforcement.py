"""门禁与工具执行层都必须默认拒绝未知操作。"""

from trippilot.graph import new_state
from trippilot.policy_gate import check_tool_call
from trippilot.state import ToolCall
from trippilot.tools import get_tool


def test_unauthenticated_user_cannot_append_trip_log():
    state = new_state(user_attributes={
        "user_id": "guest",
        "authenticated": False,
        "role": "guest",
    })

    decision = check_tool_call(
        ToolCall(tool="trip_log.append", args={"entry": {"place": "国贸"}}),
        state,
    )

    assert decision.decision == "deny"
    assert decision.reason_code == "unauthenticated_write"


def test_forged_trip_log_operation_is_rejected_by_gate_and_tool():
    call = ToolCall(tool="trip_log.drop_table", args={"entry": {"forged": True}})
    tool = get_tool(call.tool)
    tool.reset()

    decision = check_tool_call(call, new_state())
    result = tool.run(call)
    append_result = tool.run(ToolCall(tool="trip_log.append", args={"entry": {}}))

    assert decision.decision == "deny"
    assert decision.reason_code == "unknown_tool"
    assert not result.ok
    assert result.error == "unsupported operation: trip_log.drop_table"
    assert append_result.data["logged"] == 1


def test_unknown_tool_name_is_denied_by_default():
    decision = check_tool_call(ToolCall(tool="unknown.inspect", args={}),
                               new_state())

    assert decision.decision == "deny"
    assert decision.reason_code == "unknown_tool"
