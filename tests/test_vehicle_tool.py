from trippilot.graph import new_state
from trippilot.policy_gate import check_tool_call
from trippilot.state import ToolCall
from trippilot.tools.vehicle import VehicleTool


def setup_function():
    VehicleTool.reset()


def test_climate_change_is_explicitly_simulated():
    result = VehicleTool().run(ToolCall(
        tool="vehicle.climate", args={"action": "increase_ac"}))

    assert result.ok is True
    assert result.data["climate"] == {"temp_c": 23, "fan": "high"}
    assert result.data["simulated"] is True


def test_sunroof_open_and_close_are_simulated():
    tool = VehicleTool()

    opened = tool.run(ToolCall(tool="vehicle.sunroof", args={"action": "open"}))
    closed = tool.run(ToolCall(tool="vehicle.sunroof", args={"action": "close"}))

    assert opened.data == {"sunroof": "open", "simulated": True}
    assert closed.data == {"sunroof": "closed", "simulated": True}


def test_unknown_vehicle_operation_is_denied_and_not_run():
    call = ToolCall(tool="vehicle.door", args={})

    decision = check_tool_call(call, new_state())
    result = VehicleTool().run(call)

    assert decision.decision == "deny"
    assert decision.reason_code == "unknown_tool"
    assert result.ok is False

