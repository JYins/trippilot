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
        expected = data.pop("expect", None)
        if expected is not None and not isinstance(expected, dict):
            return ToolResult(tool=self.name, ok=False, source="recorded",
                              error=f"bad fixture expect: {path.name}")
        for field, wanted in (expected or {}).items():
            actual = call.args.get(field)
            # 只校验调用方实际给出的字段：没传（缺失/空）的不判，
            # 传了但和录制数据对不上的才算 mismatch（如"去国贸"拿到"中关村"路线）
            if actual is None or actual == "":
                continue
            if actual != wanted:
                return ToolResult(
                    tool=self.name,
                    ok=False,
                    source="recorded",
                    error=(f"recorded fixture parameter mismatch: {field} "
                           f"expected {wanted!r}, got {actual!r}"),
                )
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


def fixture_key_for(tool_name: str, args: dict[str, Any]) -> str:
    """按调用参数找 expect 最匹配的录制 fixture key，找不到回 default。

    给 recovery 的 fixture 回退用：原 fixture 缺失/损坏时，
    按目的地等关键字段选一个对得上的，而不是硬回 default
    （default 的 expect 只覆盖中关村，回给国贸的调用照样 mismatch）。
    """
    best, best_score = "default", -1
    prefix = f"{tool_name}_"
    try:
        paths = sorted(FIXTURES_DIR.glob(f"{prefix}*.json"))
    except OSError:
        return best
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        expect = data.get("expect") or {}
        if not expect:
            continue
        score = 0
        ok = True
        for field, wanted in expect.items():
            actual = args.get(field)
            if actual is None or actual == "":
                continue
            if actual != wanted:
                ok = False
                break
            score += 1
        if ok and score > best_score and score > 0:
            best, best_score = path.stem[len(prefix):], score
    return best
