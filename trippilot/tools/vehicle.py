"""纯软件模拟座舱状态，不读取车辆数据，也不控制真车。"""

from __future__ import annotations

from typing import Any

from ..state import ToolCall, ToolResult
from .base import BaseTool


class VehicleTool(BaseTool):
    name = "vehicle"
    supported_operations = {"vehicle.climate", "vehicle.sunroof"}
    _temp_c = 24
    _fan = "auto"
    _sunroof = "closed"

    def schema(self) -> dict[str, Any]:
        return {
            "name": "vehicle.climate",
            "description": "调整纯软件模拟的空调或天窗状态",
            "parameters": {"action": "increase_ac/decrease_ac/open/close"},
        }

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        if call.tool == "vehicle.climate":
            return self._climate(call)
        return self._set_sunroof(call)

    def _climate(self, call: ToolCall) -> ToolResult:
        action = call.args.get("action")
        cls = type(self)
        if action == "increase_ac":
            cls._temp_c = max(16, cls._temp_c - 1)
            cls._fan = "high"
        elif action == "decrease_ac":
            cls._temp_c = min(30, cls._temp_c + 1)
            cls._fan = "low"
        else:
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error=f"vehicle.climate 不支持 action: {action}")
        return ToolResult(
            tool=call.tool,
            ok=True,
            source="recorded",
            data={"climate": {"temp_c": cls._temp_c, "fan": cls._fan},
                  "simulated": True},
        )

    def _set_sunroof(self, call: ToolCall) -> ToolResult:
        action = call.args.get("action")
        if action not in ("open", "close"):
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error=f"vehicle.sunroof 不支持 action: {action}")
        cls = type(self)
        cls._sunroof = "open" if action == "open" else "closed"
        return ToolResult(tool=call.tool, ok=True, source="recorded",
                          data={"sunroof": cls._sunroof, "simulated": True})

    @classmethod
    def reset(cls) -> None:
        cls._temp_c = 24
        cls._fan = "auto"
        cls._sunroof = "closed"


def _unsupported_operation(call: ToolCall) -> ToolResult:
    return ToolResult(tool=call.tool, ok=False, source="recorded",
                      error=f"unsupported operation: {call.tool}")


__all__ = ["VehicleTool"]
