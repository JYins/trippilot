"""LLM-as-judge：只评"规则写不出的维度"，分数只进报告、不进 pass/fail 门。

硬安全规则（deny/degrade/confirm/required 节点覆盖）全部由程序断言，
见 AGENTS.md §2「确定性优先」——judge 永远不参与 verdict，
避免 LLM 幻觉污染安全判定。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# advisory 维度：只进报告，不进 verdict
DIM_PLAN_EFFICIENCY = "plan_efficiency"  # 轨迹合理性：plan 步骤是否冗余/绕路
DIM_CLARIFY_QUALITY = "clarify_quality"  # 澄清质量：clarify 问得是否到位


@dataclass
class JudgeScore:
    dimensions: dict[str, float] = field(default_factory=dict)
    notes: str = ""


@runtime_checkable
class JudgeBackend(Protocol):
    def score(self, case: dict, trace_result: dict) -> JudgeScore:
        """case 是 fixture 用例，trace_result 是 eval_runner.run_case 的返回。"""
        ...


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


class DeterministicJudge:
    """纯规则打分，离线可用。

    只是 LLM judge 上线前的粗糙代理：能捕捉兜圈/工具越界这类结构信号，
    澄清"问得好不好"这种语义判断它做不到——那是 DeepSeekJudge 的活。
    """

    def score(self, case: dict, trace_result: dict) -> JudgeScore:
        visited = trace_result.get("visited_nodes", []) or []

        # 兜圈：同一个节点反复出现，说明 plan 在绕路
        repeats = len(visited) - len(set(visited))
        efficiency = 1.0 - 0.2 * repeats
        if trace_result.get("unexpected_tools"):
            efficiency = min(efficiency, 0.5)
        efficiency = _clamp01(efficiency)

        # 澄清：没走 clarify 节点 = 无需澄清，不扣分；
        # 走了且 context 里预置了答案 = 歧义被解决；
        # 走了但没答案、final_response 非空 = 问了一句就停住；
        # 走了但连话都没说出来 = 最差
        clarify_visited = "clarify" in visited
        if not clarify_visited:
            clarify_quality, why = 1.0, "无需澄清"
        elif case.get("context", {}).get("clarify_answer"):
            clarify_quality, why = 1.0, "歧义已解决"
        elif trace_result.get("final_response"):
            clarify_quality, why = 0.6, "触发了澄清但未确认问法质量（需人工/LLM 复核）"
        else:
            clarify_quality, why = 0.3, "澄清节点执行但无有效追问"
        notes = (f"plan_efficiency={efficiency:.2f}（重复节点{repeats}次，"
                 f"越界工具{trace_result.get('unexpected_tools') or '无'}）；"
                 f"clarify_quality={clarify_quality:.2f}（{why}）")
        return JudgeScore(
            dimensions={DIM_PLAN_EFFICIENCY: efficiency,
                        DIM_CLARIFY_QUALITY: clarify_quality},
            notes=notes,
        )


class DeepSeekJudge:
    """live judge：OpenAI-compatible chat API。

    无 key 时构造直接抛错——幻觉出来的分数比没有分数更伤评测可信度，
    所以这里绝不 fallback 到 fake 分数。
    """

    BASE_URL = "https://api.deepseek.com"
    MODEL = "deepseek-chat"

    def __init__(self) -> None:
        key = os.environ.get("TRIPPILOT_JUDGE_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "live 测试 pending，需用户开通 DeepSeek API key："
                "设置环境变量 TRIPPILOT_JUDGE_API_KEY 后再跑。")
        self._key = key

    def _chat(self, system: str, user: str) -> str:
        import httpx
        resp = httpx.post(
            f"{self.BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {self._key}"},
            json={"model": self.MODEL,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "temperature": 0.2,
                  "response_format": {"type": "json_object"}},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def score(self, case: dict, trace_result: dict) -> JudgeScore:
        system = (
            "你是座舱 Agent 轨迹评审员。只评两个 advisory 维度，"
            "每个 0~1 打分：\n"
            f"1. {DIM_PLAN_EFFICIENCY}（轨迹合理性）：plan 步骤有没有冗余、"
            "绕路、重复调同一个工具、做了和用户意图无关的事。\n"
            f"2. {DIM_CLARIFY_QUALITY}（澄清质量）：当轨迹里出现澄清时，"
            "追问是否点名了歧义选项、是否简短口语；没有歧义则给满分。\n"
            "只输出 JSON：{\"plan_efficiency\": 分数, \"clarify_quality\": 分数,"
            " \"notes\": \"一句话中文点评\"}。不要输出其他内容。"
        )
        asr = case.get("asr") or {}
        payload = {
            "user_request": case.get("user_request"),
            "asr_confidence": asr.get("confidence"),
            "place_entities": [e.get("name") for e in asr.get("place_entities", [])],
            "visited_nodes": trace_result.get("visited_nodes"),
            "policy_reasons": trace_result.get("policy_reasons"),
            "final_response": (trace_result.get("final_response") or "")[:500],
        }
        raw = self._chat(system, json.dumps(payload, ensure_ascii=False))
        data = json.loads(raw)
        dims = {DIM_PLAN_EFFICIENCY: _clamp01(data[DIM_PLAN_EFFICIENCY]),
                DIM_CLARIFY_QUALITY: _clamp01(data[DIM_CLARIFY_QUALITY])}
        return JudgeScore(dimensions=dims, notes=str(data.get("notes", "")))


__all__ = [
    "DIM_PLAN_EFFICIENCY",
    "DIM_CLARIFY_QUALITY",
    "JudgeScore",
    "JudgeBackend",
    "DeterministicJudge",
    "DeepSeekJudge",
]
