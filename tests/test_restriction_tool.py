from trippilot.graph import new_state
from trippilot.policy_gate import check_tool_call
from trippilot.state import ToolCall
from trippilot.tools.restriction import RestrictionTool


def test_recorded_restriction_contains_date_and_restricted_tails():
    result = RestrictionTool().run(ToolCall(
        tool="restriction.query",
        args={"city": "北京", "fixture": "beijing_20261007"},
    ))

    assert result.ok is True
    assert result.data["date"] == "2026-10-07"
    assert "restricted_tails" in result.data


def test_unknown_restriction_operation_is_denied_and_not_run():
    call = ToolCall(tool="restriction.delete", args={})

    decision = check_tool_call(call, new_state())
    result = RestrictionTool().run(call)

    assert decision.decision == "deny"
    assert decision.reason_code == "unknown_tool"
    assert result.ok is False

