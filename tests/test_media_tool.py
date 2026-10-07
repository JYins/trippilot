from trippilot.graph import new_state
from trippilot.policy_gate import check_tool_call
from trippilot.state import ToolCall
from trippilot.tools.media import MediaTool


def setup_function():
    MediaTool.reset()


def test_next_track_cycles_through_simulated_playlist():
    tool = MediaTool()

    results = [tool.run(ToolCall(tool="media.next")) for _ in range(5)]

    assert all(result.ok for result in results)
    assert results[0].data["track"] == results[4].data["track"]
    assert results[0].data["simulated"] is True
    assert " - " in results[0].data["now_playing"]


def test_volume_is_clamped_to_supported_range():
    tool = MediaTool()

    too_high = tool.run(ToolCall(
        tool="media.volume", args={"action": "set", "level": 99}))
    lowered = [tool.run(ToolCall(
        tool="media.volume", args={"action": "decrease"})) for _ in range(6)]

    assert too_high.data == {"volume": 10, "previous": 5, "simulated": True}
    assert lowered[-1].data["volume"] == 0


def test_unknown_media_operation_is_denied_and_not_run():
    call = ToolCall(tool="media.play", args={})

    decision = check_tool_call(call, new_state())
    result = MediaTool().run(call)

    assert decision.decision == "deny"
    assert decision.reason_code == "unknown_tool"
    assert result.ok is False

