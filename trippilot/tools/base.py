"""工具层：双轨外部服务。

- 自动测试 / 回归默认读取版本化录制响应（recorded），不需要常驻 key。
- 真实接口只由开发者手动触发（live_manual）：临时注入 key，调用后不保存 key，
  两类结果分别标注，不把单次实时成功并入长期稳定性指标。
- CI、每日回归和后台任务禁止依赖临时 key。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..state import ToolCall, ToolResult

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures" / "recorded"


class ToolError(Exception):
    pass


class BaseTool:
    name: str = "base"

    def schema(self) -> dict[str, Any]:
        raise NotImplementedError

    def run(self, call: ToolCall) -> ToolResult:
        if call.source == "live_manual":
            return self._run_live(call)
        return self._run_recorded(call)

    # -- recorded ---------------------------------------------------------
    def _fixture_path(self, call: ToolCall) -> Path:
        key = call.args.get("fixture", "default")
        return FIXTURES_DIR / f"{self.name}_{key}.json"

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        path = self._fixture_path(call)
        if not path.exists():
            return ToolResult(tool=self.name, ok=False, source="recorded",
                              error=f"missing recorded fixture: {path.name}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return ToolResult(tool=self.name, ok=False, source="recorded",
                              error=f"bad fixture json: {e}")
        return ToolResult(tool=self.name, ok=True, data=data, source="recorded")

    # -- live (manual only) ------------------------------------------------
    def _run_live(self, call: ToolCall) -> ToolResult:
        raise ToolError(f"{self.name}: live_manual 未实现或 key 未提供（手动触发专用）")

    @staticmethod
    def _temp_key(env_name: str) -> str:
        """只从本次进程环境读取临时 key；绝不写入文件。"""
        key = os.environ.get(env_name, "")
        if not key:
            raise ToolError(f"live_manual 需要临时环境变量 {env_name}（用后即删，不保存）")
        return key
