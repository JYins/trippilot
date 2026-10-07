"""媒体控制只维护进程内演示状态，不接触真实播放器。"""

from __future__ import annotations

from typing import Any

from ..state import ToolCall, ToolResult
from .base import BaseTool


class MediaTool(BaseTool):
    name = "media"
    supported_operations = {"media.next", "media.volume"}
    _playlist = [
        {"title": "清晨公路", "artist": "演示乐队甲"},
        {"title": "城市微风", "artist": "演示歌手乙"},
        {"title": "远方灯火", "artist": "演示组合丙"},
        {"title": "晚霞归途", "artist": "演示音乐人丁"},
    ]
    _track_index = -1
    _volume = 5

    def schema(self) -> dict[str, Any]:
        return {
            "name": "media.next",
            "description": "切换演示播放列表或调整模拟音量",
            "parameters": {"action": "increase/decrease/set", "level": "0-10"},
        }

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        if call.tool == "media.next":
            return self._next(call)
        return self._set_volume(call)

    def _next(self, call: ToolCall) -> ToolResult:
        cls = type(self)
        cls._track_index = (cls._track_index + 1) % len(cls._playlist)
        track = dict(cls._playlist[cls._track_index])
        return ToolResult(
            tool=call.tool,
            ok=True,
            source="recorded",
            data={
                "track": track,
                "now_playing": f"{track['title']} - {track['artist']}",
                "simulated": True,
            },
        )

    def _set_volume(self, call: ToolCall) -> ToolResult:
        action = call.args.get("action")
        cls = type(self)
        previous = cls._volume
        if action == "increase":
            wanted = previous + 2
        elif action == "decrease":
            wanted = previous - 2
        elif action == "set":
            level = call.args.get("level")
            if not isinstance(level, int) or isinstance(level, bool):
                return ToolResult(tool=call.tool, ok=False, source="recorded",
                                  error="media.volume set 需要整数 level")
            wanted = level
        else:
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error=f"media.volume 不支持 action: {action}")

        cls._volume = max(0, min(10, wanted))
        return ToolResult(tool=call.tool, ok=True, source="recorded",
                          data={"volume": cls._volume, "previous": previous,
                                "simulated": True})

    @classmethod
    def reset(cls) -> None:
        cls._track_index = -1
        cls._volume = 5


def _unsupported_operation(call: ToolCall) -> ToolResult:
    return ToolResult(tool=call.tool, ok=False, source="recorded",
                      error=f"unsupported operation: {call.tool}")


__all__ = ["MediaTool"]
