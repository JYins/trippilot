"""限行查询只读取版本化录制响应，不访问实时服务。"""

from __future__ import annotations

from typing import Any

from ..state import ToolCall, ToolResult
from .base import BaseTool


class RestrictionTool(BaseTool):
    name = "restriction"
    supported_operations = {"restriction.query"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": "restriction.query",
            "description": "查询录制的限行信息",
            "parameters": {"city": "城市", "date": "日期", "fixture": "录制数据名"},
        }

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        # 录制响应只用于可重复评测，不代表实时限行政策。
        return super()._run_recorded(call)


def _unsupported_operation(call: ToolCall) -> ToolResult:
    return ToolResult(tool=call.tool, ok=False, source="recorded",
                      error=f"unsupported operation: {call.tool}")


__all__ = ["RestrictionTool"]
