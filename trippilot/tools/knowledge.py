"""小知识库只回答录制且已核实的条目。"""

from __future__ import annotations

import json
from typing import Any

from ..state import ToolCall, ToolResult
from .base import BaseTool


class KnowledgeTool(BaseTool):
    name = "knowledge"
    supported_operations = {"knowledge.qa"}

    def schema(self) -> dict[str, Any]:
        return {
            "name": "knowledge.qa",
            "description": "从录制小知识库回答问题",
            "parameters": {"question": "问题", "fixture": "录制数据名"},
        }

    def _run_recorded(self, call: ToolCall) -> ToolResult:
        if call.tool not in self.supported_operations:
            return _unsupported_operation(call)
        question = call.args.get("question")
        if not isinstance(question, str) or not question:
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error="knowledge.qa 缺少 question")

        path = self._fixture_path(call)
        if not path.exists():
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error=f"missing recorded fixture: {path.name}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error=f"bad fixture json: {error}")

        best_entry = None
        best_keywords: list[str] = []
        for entry in data.get("entries", []):
            if not entry.get("answer"):
                continue  # 条目缺答案视为无效，不参与匹配
            matched = [keyword for keyword in entry.get("keywords", [])
                       if keyword in question]
            if len(matched) > len(best_keywords):
                best_entry = entry
                best_keywords = matched

        # 不调用外部 API，也不现编答案；录制库答不上来就诚实返回不知道。
        if best_entry is None:
            return ToolResult(tool=call.tool, ok=False, source="recorded",
                              error="知识库里没有这条，不知道（不编答案）")
        return ToolResult(
            tool=call.tool,
            ok=True,
            source="recorded",
            data={"answer": best_entry["answer"], "question": question,
                  "matched_keywords": best_keywords, "source": "recorded"},
        )


def _unsupported_operation(call: ToolCall) -> ToolResult:
    return ToolResult(tool=call.tool, ok=False, source="recorded",
                      error=f"unsupported operation: {call.tool}")


__all__ = ["KnowledgeTool"]
