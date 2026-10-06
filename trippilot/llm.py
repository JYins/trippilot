"""LLM 客户端接口：可注入 stub（测试/无 key 运行），真实调用只走手动配置。

- 默认无 key 时使用 DeterministicStub：按规则生成 plan，不调用任何外部模型。
  保证「语音交互主循环先跑通」不依赖任何 key。
- 真实模型只在 TRIPPILOT_LLM_* 环境变量手动提供时启用；
  key 只存在于本次进程环境，不写入文件、不进日志。
"""

from __future__ import annotations

import json
import os
from typing import Any


class LLMClient:
    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        """返回 plan steps：[{step_id, tool, args, description}]。"""
        raise NotImplementedError

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        raise NotImplementedError


class ScriptedLLM(LLMClient):
    """按用例脚本返回固定 plan：eval 专用，构造特定测试场景。"""

    def __init__(self, plan: list[dict[str, Any]]) -> None:
        self._plan = plan

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        return self._plan

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        return "脚本执行完成。"


class DeterministicStub(LLMClient):
    """确定性 stub：按关键词规则生成 plan，用于无 key 跑通主循环与回归。

    注意：这是测试脚手架，不是产品能力；eval 报告中 LLM 相关能力
    以真实模型实测为准，不拿 stub 结果充数。
    """

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        steps: list[dict[str, Any]] = []
        text = intent
        if any(k in text for k in ("路线", "导航", "怎么去", "几点出发")):
            steps.append({"step_id": "s1", "tool": "map.route",
                          "args": {"origin": context.get("origin", ""),
                                   "destination": context.get("destination", ""),
                                   "fixture": "default",
                                   "option_count": context.get("option_count", 1)},
                          "description": "查询路线"})
        if any(k in text for k in ("天气",)):
            steps.append({"step_id": "s2", "tool": "weather.now",
                          "args": {"area": context.get("area", "海淀区"),
                                   "fixture": "default"},
                          "description": "查询天气"})
        if any(k in text for k in ("提醒",)):
            steps.append({"step_id": "s3", "tool": "reminder.create",
                          "args": {"content": context.get("reminder_content", ""),
                                   "time": context.get("reminder_time", ""),
                                   "session_id": context.get("session_id", "")},
                          "description": "创建提醒"})
        return steps

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        results = state_summary.get("tool_results", [])
        parts = [f"{r.get('tool')}: {'成功' if r.get('ok') else '失败'}"
                 for r in results]
        return "已执行：" + "；".join(parts) if parts else "本次没有执行工具调用。"


class EnvLLMClient(LLMClient):
    """真实模型客户端（手动触发专用）。

    从环境变量读取：TRIPPILOT_LLM_BASE_URL / TRIPPILOT_LLM_API_KEY /
    TRIPPILOT_LLM_MODEL。key 仅存于进程环境。
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("TRIPPILOT_LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("TRIPPILOT_LLM_API_KEY", "")
        self.model = os.environ.get("TRIPPILOT_LLM_MODEL", "")
        if not (self.base_url and self.api_key and self.model):
            raise RuntimeError("真实模型需要手动设置 TRIPPILOT_LLM_BASE_URL/_API_KEY/_MODEL")

    def _chat(self, system: str, user: str) -> str:
        import httpx
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "temperature": 0.2},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        raw = self._chat(
            "你是座舱任务规划器。只输出 JSON 数组，每个元素含 step_id/tool/args/description。"
            f"可用工具：{available_tools}。不要编造工具名。",
            f"意图：{intent}\n上下文：{json.dumps(context, ensure_ascii=False)}")
        return json.loads(raw)

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        return self._chat("你是车载语音助手，根据工具执行结果给用户一句话总结，简短口语化。",
                          json.dumps(state_summary, ensure_ascii=False))


def make_llm() -> LLMClient:
    """有手动 key 就用真实模型，否则用确定性 stub。"""
    try:
        return EnvLLMClient()
    except RuntimeError:
        return DeterministicStub()


__all__ = ["LLMClient", "DeterministicStub", "ScriptedLLM", "EnvLLMClient", "make_llm"]
