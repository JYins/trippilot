"""LLM-as-judge：只评"规则写不出的维度"，分数只进报告、不进 pass/fail 门。

硬安全规则（deny/degrade/confirm/required 节点覆盖）全部由程序断言，
见 AGENTS.md §2「确定性优先」——judge 永远不参与 verdict，
避免 LLM 幻觉污染安全判定。
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
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

        clarify_checked = trace_result.get("clarify_checked")
        need_clarify = (clarify_checked.get("need_clarify")
                        if isinstance(clarify_checked, dict) else None)
        if need_clarify is False:
            clarify_quality = 1.0
            why = "clarify_checked.need_clarify=False，无需澄清"
        elif need_clarify is True:
            if case.get("context", {}).get("clarify_answer"):
                clarify_quality = 1.0
                why = "clarify_checked.need_clarify=True，歧义已解决"
            elif trace_result.get("final_response"):
                clarify_quality = 0.6
                why = ("clarify_checked.need_clarify=True，已追问；"
                       "问法质量需人工/LLM 复核")
            else:
                clarify_quality = 0.3
                why = ("clarify_checked.need_clarify=True，"
                       "但没有有效追问")
        elif "clarify" not in visited:
            clarify_quality, why = 1.0, "无 clarify_checked 事件，且未访问澄清节点"
        elif case.get("context", {}).get("clarify_answer"):
            clarify_quality, why = 1.0, "无 clarify_checked 事件；按兼容分支判定歧义已解决"
        elif trace_result.get("final_response"):
            clarify_quality, why = 0.6, "无 clarify_checked 事件；按兼容分支判定已追问"
        else:
            clarify_quality, why = 0.3, "无 clarify_checked 事件；按兼容分支未发现有效追问"
        notes = (f"plan_efficiency={efficiency:.2f}（重复节点{repeats}次，"
                 f"越界工具{trace_result.get('unexpected_tools') or '无'}）；"
                 f"clarify_quality={clarify_quality:.2f}（{why}）")
        return JudgeScore(
            dimensions={DIM_PLAN_EFFICIENCY: efficiency,
                        DIM_CLARIFY_QUALITY: clarify_quality},
            notes=notes,
        )


class DeepSeekJudge:
    """live judge：优先走 deepseek skill CLI 子进程。

    不把 key 写进配置：skill CLI 走 authd surrogate，进程里永远见不到
    明文 key，日志和报错都带不出来。TRIPPILOT_JUDGE_API_KEY 只是备用，
    给没有 skill 的机器用。两者都没有时构造直接抛错——幻觉出来的分数
    比没分数更伤评测可信度。
    """

    BASE_URL = "https://api.deepseek.com"
    MODEL = "deepseek-chat"
    CLI = Path.home() / "workspace/skills/deepseek/bin/deepseek-chat"

    def __init__(self, cli_path: Path | None = None) -> None:
        self._cli = Path(cli_path) if cli_path is not None else self.CLI
        self._key = os.environ.get("TRIPPILOT_JUDGE_API_KEY", "").strip()
        if self._cli.is_file() and os.access(self._cli, os.X_OK):
            self.via = "cli"
        elif self._key:
            self.via = "env"
        else:
            raise RuntimeError(
                "live 测试 pending：deepseek skill 不可用，"
                "也没检测到 TRIPPILOT_JUDGE_API_KEY。"
                "用户开通 DeepSeek API 后再跑。")

    def _chat(self, system: str, user: str) -> str:
        if self.via == "cli":
            try:
                return self._chat_cli(system, user)
            except (subprocess.SubprocessError, OSError):
                # CLI 挂了：有备用 key 就降级直调，没有就如实抛错
                if not self._key:
                    raise
        return self._chat_http(system, user)

    def _chat_cli(self, system: str, user: str) -> str:
        # wrapper 超时要大于 CLI 自己的 120s，让 CLI 先按自己的语义超时
        proc = subprocess.run(
            [str(self._cli), "--json", "--temperature", "0.2",
             "--system", system],
            input=user, capture_output=True, text=True, timeout=130,
        )
        proc.check_returncode()
        return proc.stdout

    def _chat_http(self, system: str, user: str) -> str:
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
            "只输出 json 对象（JSON）：{\"plan_efficiency\": 分数, "
            "\"clarify_quality\": 分数,"
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
