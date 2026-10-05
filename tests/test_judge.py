"""judge 模块测试：全部离线，不许调真实 API。

覆盖：协议可插拔、judge 分数不进 verdict 门、
DeterministicJudge 纯规则、DeepSeekJudge 无 key 必抛错。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from eval.judge import (
    DIM_CLARIFY_QUALITY,
    DIM_PLAN_EFFICIENCY,
    DeepSeekJudge,
    DeterministicJudge,
    JudgeBackend,
    JudgeScore,
)

ROOT = Path(__file__).resolve().parent.parent


class FakeJudge:
    """测试替身：故意打 0 分，用来证明 judge 不影响 verdict。"""

    def score(self, case: dict, trace_result: dict) -> JudgeScore:
        return JudgeScore(
            dimensions={DIM_PLAN_EFFICIENCY: 0.0, DIM_CLARIFY_QUALITY: 0.0},
            notes="fake",
        )


def test_backend_protocol_is_pluggable():
    assert isinstance(FakeJudge(), JudgeBackend)
    assert isinstance(DeterministicJudge(), JudgeBackend)
    s = FakeJudge().score({}, {})
    assert set(s.dimensions) == {DIM_PLAN_EFFICIENCY, DIM_CLARIFY_QUALITY}
    assert s.notes == "fake"


def test_judge_score_does_not_change_verdict():
    # fake 打 0 分，verdict 必须和没 judge 时完全一致——铁律回归
    from eval.eval_runner import load_dataset, run_case
    cases = load_dataset(ROOT / "fixtures" / "dataset_v0.jsonl")
    case = next(c for c in cases if c["case_id"] == "TP-ROUTE-001")
    plain = run_case(case)
    judged = run_case(case, judge=FakeJudge())
    assert judged["passed"] == plain["passed"]
    assert judged["judge"]["dimensions"][DIM_PLAN_EFFICIENCY] == 0.0
    assert judged["judge"]["notes"] == "fake"


def test_deterministic_judge_offline_clean_trace():
    j = DeterministicJudge()
    case = {"case_id": "x", "context": {}}
    trace = {"visited_nodes": ["intent", "planner", "policy_gate",
                               "tool_executor", "verifier"],
             "unexpected_tools": [], "final_response": "ok"}
    s = j.score(case, trace)
    assert s.dimensions[DIM_PLAN_EFFICIENCY] == 1.0
    assert s.dimensions[DIM_CLARIFY_QUALITY] == 1.0  # 无需澄清不扣分
    assert s.notes


def test_deterministic_judge_penalizes_loops_and_out_of_scope_tools():
    j = DeterministicJudge()
    case = {"case_id": "x", "context": {}}
    trace = {"visited_nodes": ["intent", "planner", "planner",
                               "tool_executor", "verifier"],
             "unexpected_tools": ["reminder.create"],
             "final_response": "ok"}
    s = j.score(case, trace)
    assert s.dimensions[DIM_PLAN_EFFICIENCY] < 1.0
    assert 0.0 <= s.dimensions[DIM_PLAN_EFFICIENCY] <= 1.0


def test_deterministic_judge_clarify_heuristics():
    j = DeterministicJudge()
    trace_base = {"unexpected_tools": [], "final_response": "想确认一下：西站，你指的是哪一个？"}
    # 歧义被解决
    s = j.score({"context": {"clarify_answer": "北京西站"}},
                {**trace_base, "visited_nodes": ["intent", "clarify"]})
    assert s.dimensions[DIM_CLARIFY_QUALITY] == 1.0
    # 走了澄清但没话可说
    s = j.score({"context": {}},
                {**trace_base, "visited_nodes": ["intent", "clarify"],
                 "final_response": ""})
    assert s.dimensions[DIM_CLARIFY_QUALITY] < 0.6


def _no_cli(monkeypatch, tmp_path):
    # 把类默认的 CLI 路径指到不存在的位置，模拟"机器上没装 skill"
    monkeypatch.setattr(DeepSeekJudge, "CLI", tmp_path / "no-such-cli")


def test_deepseek_judge_requires_key_or_cli(tmp_path, monkeypatch):
    _no_cli(monkeypatch, tmp_path)
    monkeypatch.delenv("TRIPPILOT_JUDGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="pending"):
        DeepSeekJudge()


def test_deepseek_judge_error_message_guides_user(tmp_path, monkeypatch):
    _no_cli(monkeypatch, tmp_path)
    monkeypatch.delenv("TRIPPILOT_JUDGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        DeepSeekJudge()
    assert "TRIPPILOT_JUDGE_API_KEY" in str(exc.value)
    assert "DeepSeek API" in str(exc.value)


def test_deepseek_judge_targets_deepseek_api():
    assert DeepSeekJudge.BASE_URL == "https://api.deepseek.com"
    assert DeepSeekJudge.MODEL == "deepseek-chat"


def _fake_cli(tmp_path: Path, script: str) -> Path:
    p = tmp_path / "deepseek-chat"
    p.write_text(script)
    p.chmod(0o755)
    return p


_FAKE_OK = (
    "#!/usr/bin/env python3\n"
    "import sys, json\n"
    "sys.stdin.read()\n"
    'print(json.dumps({"plan_efficiency": 0.8, "clarify_quality": 0.9,'
    ' "notes": "fake-cli"}))\n'
)


def test_deepseek_judge_prefers_cli_over_env(tmp_path, monkeypatch):
    # 有 CLI 时不需要 env key：key 根本不该进进程
    monkeypatch.delenv("TRIPPILOT_JUDGE_API_KEY", raising=False)
    j = DeepSeekJudge(cli_path=_fake_cli(tmp_path, _FAKE_OK))
    assert j.via == "cli"
    s = j.score({"case_id": "x", "context": {}},
                {"visited_nodes": ["intent"], "unexpected_tools": [],
                 "final_response": "ok"})
    assert s.dimensions[DIM_PLAN_EFFICIENCY] == 0.8
    assert s.dimensions[DIM_CLARIFY_QUALITY] == 0.9
    assert s.notes == "fake-cli"


def test_deepseek_judge_falls_back_to_env_when_cli_missing(tmp_path,
                                                           monkeypatch):
    _no_cli(monkeypatch, tmp_path)
    monkeypatch.setenv("TRIPPILOT_JUDGE_API_KEY", "sk-fake")
    j = DeepSeekJudge()
    assert j.via == "env"
    assert j._key == "sk-fake"


def test_deepseek_judge_cli_failure_falls_back_to_http(tmp_path, monkeypatch):
    # CLI 挂了 + 有 key → 降级直调（_chat_http 被替掉，不真发请求）
    bad = _fake_cli(tmp_path, "#!/bin/sh\nexit 1\n")
    monkeypatch.setenv("TRIPPILOT_JUDGE_API_KEY", "sk-fake")
    j = DeepSeekJudge(cli_path=bad)
    canned = json.dumps({"plan_efficiency": 0.7, "clarify_quality": 0.7,
                         "notes": "fallback"})
    monkeypatch.setattr(j, "_chat_http", lambda s, u: canned)
    s = j.score({"case_id": "x"}, {"visited_nodes": []})
    assert s.notes == "fallback"


def test_deepseek_judge_cli_failure_without_key_raises(tmp_path, monkeypatch):
    # CLI 挂了 + 没 key → 如实抛错，不 fake 分数
    bad = _fake_cli(tmp_path, "#!/bin/sh\nexit 1\n")
    monkeypatch.delenv("TRIPPILOT_JUDGE_API_KEY", raising=False)
    j = DeepSeekJudge(cli_path=bad)
    with pytest.raises(subprocess.CalledProcessError):
        j._chat("sys", "user")
