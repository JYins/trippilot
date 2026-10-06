"""MCP Tool Layer 的四个工具：map / weather / reminder / trip_log。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ..state import ToolCall, ToolResult
from .base import BaseTool, ToolError

# ---------------------------------------------------------------------------
# map.route / map.search
# ---------------------------------------------------------------------------

class MapTool(BaseTool):
    name = "map"
    supported_operations = {"map.route", "map.search"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": "map.route",
            "description": "查询两地间路线（录制响应默认）",
            "parameters": {
                "origin": "起点（区域级描述，不存精确家庭住址）",
                "destination": "终点",
                "departure_time": "出发时间 ISO8601",
                "fixture": "录制 fixture 名，默认 default",
            },
        }

    def _run_live(self, call: ToolCall) -> ToolResult:
        # 手动触发示例：高德 key 仅从环境临时读取（transient use，不写入文件）
        self._temp_key("TRIPPILOT_AMAP_KEY_TEMP")
        raise ToolError("map live_manual：需在手动触发时接入具体路线规划接口（待实现）")

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        return super()._run_recorded(call)


# ---------------------------------------------------------------------------
# weather.now / weather.forecast
# ---------------------------------------------------------------------------

class WeatherTool(BaseTool):
    name = "weather"
    supported_operations = {"weather.now", "weather.forecast"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": "weather.now",
            "description": "查询指定区域当前天气（录制响应默认）",
            "parameters": {"area": "区域，如 海淀区", "fixture": "录制 fixture 名"},
        }

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        return super()._run_recorded(call)


# ---------------------------------------------------------------------------
# reminder.create / update / delete：幂等，禁止重复副作用
# ---------------------------------------------------------------------------

class ReminderTool(BaseTool):
    """提醒工具：内存存储（原型），用幂等键防止重试导致重复创建。

    幂等键 = sha1(content + time + session_id)。同一幂等键重复调用
    返回首次结果，不再写入。Duplicate Side-effect Rate 的第一道防线。
    """

    name = "reminder"
    supported_operations = {"reminder.create", "reminder.delete"}
    _store: dict[str, dict[str, Any]] = {}
    _idempotency: dict[str, str] = {}

    def schema(self) -> dict[str, Any]:
        return {
            "name": "reminder.create",
            "description": "创建提醒（需 Policy Gate 确认后调用）",
            "parameters": {
                "content": "提醒内容",
                "time": "提醒时间 ISO8601",
                "idempotency_key": "幂等键；缺省时由 content+time+session 自动生成",
            },
        }

    @staticmethod
    def idempotency_key(content: str, time: str, session_id: str) -> str:
        raw = f"{content}|{time}|{session_id}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        if call.tool == "reminder.create":
            return self._create(call)
        return self._delete(call)

    def _create(self, call: ToolCall) -> ToolResult:
        args = call.args
        content, time = args.get("content", ""), args.get("time", "")
        if not content or not time:
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error="reminder.create 缺少 content/time")
        key = args.get("idempotency_key") or self.idempotency_key(
            content, time, args.get("session_id", ""))
        if key in self._idempotency:
            rid = self._idempotency[key]
            return ToolResult(tool=call.tool, ok=True, source="recorded",
                              data={"reminder_id": rid, "duplicate_suppressed": True})
        rid = f"rmd_{len(self._store) + 1:04d}"
        self._store[rid] = {"id": rid, "content": content, "time": time,
                            "idempotency_key": key}
        self._idempotency[key] = rid
        return ToolResult(tool=call.tool, ok=True, source="recorded",
                          data={"reminder_id": rid, "duplicate_suppressed": False})

    def _delete(self, call: ToolCall) -> ToolResult:
        rid = call.args.get("reminder_id", "")
        if rid in self._store:
            del self._store[rid]
            return ToolResult(tool=call.tool, ok=True, source="recorded",
                              data={"deleted": rid})
        return ToolResult(tool=call.tool, ok=False, source="recorded",
                          error=f"reminder {rid} 不存在")

    @classmethod
    def reset(cls) -> None:
        cls._store = {}
        cls._idempotency = {}


# ---------------------------------------------------------------------------
# trip_log：行程轨迹记录（只读/追加）
# ---------------------------------------------------------------------------

class TripLogTool(BaseTool):
    name = "trip_log"
    supported_operations = {"trip_log.append"}
    _entries: list[dict[str, Any]] = []

    def schema(self) -> dict[str, Any]:
        return {"name": "trip_log.append", "description": "追加行程记录",
                "parameters": {"entry": "记录内容 dict"}}

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        self._entries.append(dict(call.args.get("entry", {})))
        return ToolResult(tool=call.tool, ok=True, source="recorded",
                          data={"logged": len(self._entries)})

    @classmethod
    def reset(cls) -> None:
        cls._entries = []


def _unsupported_operation(call: ToolCall) -> ToolResult:
    return ToolResult(tool=call.tool, ok=False, source="recorded",
                      error=f"unsupported operation: {call.tool}")


TOOLS: dict[str, BaseTool] = {
    "map": MapTool(),
    "weather": WeatherTool(),
    "reminder": ReminderTool(),
    "trip_log": TripLogTool(),
}


def get_tool(name: str) -> BaseTool:
    base = name.split(".")[0]
    if base not in TOOLS:
        raise ToolError(f"未知工具: {name}")
    return TOOLS[base]


__all__ = ["TOOLS", "get_tool", "ToolError", "BaseTool",
           "MapTool", "WeatherTool", "ReminderTool", "TripLogTool"]
