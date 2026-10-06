"""Eval Runner：轨迹级回归。

对 dataset_v0.jsonl 每条用例：
- 用 ScriptedLLM（有 scripted_plan）或 DeterministicStub 跑完整图；
- 工具全部走 recorded；
- 检查：Required-node Coverage、Forbidden-action Rate、Confirmation Compliance、
  期望的 deny/degrade reason_code；
- 输出固定结构的版本报告（见计划 §④）。

硬安全规则由程序断言，不交给 LLM Judge。judge 只评两个"规则写不出的维度"
（轨迹合理性 plan_efficiency、澄清质量 clarify_quality），分数只进报告、
不进 pass/fail 门；无 TRIPPILOT_JUDGE_API_KEY 时报告标注 pending。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trippilot.graph import build_graph, memory_confirm_id, new_state, run_graph
from trippilot.llm import DeterministicStub, ScriptedLLM
from trippilot.memory.store import PreferenceStore, hash_embedder
from trippilot.state import ASRResult
from trippilot.tools.tools import ReminderTool, TripLogTool
from eval.judge import (
    DIM_CLARIFY_QUALITY,
    DIM_PLAN_EFFICIENCY,
    DeepSeekJudge,
)


def load_dataset(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _memory_events_ok(trace, mem_store) -> bool:
    """敏感写入必须能追溯到更早、且针对同一候选的确认事件。"""
    if "memory_recall" not in [e.node for e in trace]:
        return False
    confirmed_before: set[str] = set()
    for e in trace:
        if e.node == "human_confirm" and e.event == "confirmed":
            confirmed_before.update(e.payload.get("confirmed_ids", []))
        if e.node == "memory_capture" and e.payload.get("written"):
            written_confirm_ids = set(
                e.payload.get("written_confirm_ids", []))
            for mid in e.payload["written"]:
                pref = mem_store.get(mid)
                if pref is None or pref.sensitivity != "sensitive":
                    continue
                confirm_id = memory_confirm_id(pref.model_dump())
                if (confirm_id not in written_confirm_ids
                        or confirm_id not in confirmed_before):
                    return False
    return True


def _memory_events(trace) -> list[Any]:
    return [event for event in trace if event.node == "memory_capture"]


def _written_ids(trace) -> set[str]:
    return {
        mid
        for event in _memory_events(trace)
        for mid in event.payload.get("written", [])
    }


def _reminder_created(out, _store, _case) -> bool:
    return any(result.tool == "reminder.create" and result.ok
               and result.data.get("reminder_id")
               for result in out.tool_results)


def _transient_rejected(out, _store, _case) -> bool:
    events = _memory_events(out.trace)
    return any("transient" in reason
               for event in events
               for reason in event.payload.get("rejected", [])) or (
                   bool(events) and not _written_ids(out.trace))


def _stable_preference_written(out, store, _case) -> bool:
    return any((pref := store.get(mid)) is not None
               and pref.sensitivity != "sensitive"
               for mid in _written_ids(out.trace))


def _memory_not_written(out, _store, _case) -> bool:
    events = _memory_events(out.trace)
    return bool(events) and not events[-1].payload.get("written", [])


def _sensitive_pending(out, _store, _case) -> bool:
    events = _memory_events(out.trace)
    return bool(events) and bool(events[-1].payload.get("pending")) \
        and not events[-1].payload.get("written", [])


def _duplicate_suppressed(out, _store, _case) -> bool:
    return any(result.tool == "reminder.create" and result.ok
               and result.data.get("duplicate_suppressed") is True
               for result in out.tool_results)


def _failed_then_recovered(out, _store, _case) -> bool:
    failed = any(not result.get("ok", True)
                 for event in out.trace if event.node == "tool_executor"
                 for result in event.payload.get("results", []))
    return (failed and out.recovery_count > 0
            and out.verification_result.get("task_completed") is True)


def _recovered_with_matching_fixture(out, _store, _case) -> bool:
    for index, event in enumerate(out.trace):
        if event.node != "recovery":
            continue
        for change in event.payload.get("changes", []):
            tool = change.get("tool", "")
            # trace 里工具结果的 tool 是 base 名（如 map），change 里是全名
            #（如 map.route），比较时只看 base 部分
            base = tool.split(".")[0]
            retried = any(call.tool == tool
                          and call.args.get("fixture") == change.get("to")
                          for call in out.tool_calls)
            recovered = any(
                str(result.get("tool", "")).split(".")[0] == base
                and result.get("ok")
                for later in out.trace[index + 1:]
                if later.node == "tool_executor"
                for result in later.payload.get("results", []))
            if (change.get("parameter") == "fixture"
                    and change.get("from") != change.get("to")
                    and retried and recovered):
                return True
    return False


def _final_verification_ok(out, _store, _case) -> bool:
    return (out.verification_result.get("ok") is True
            and out.verification_result.get("task_completed") is True)


def _preferences_written(out, _store, expected) -> bool:
    return len(_written_ids(out.trace)) == expected


CriterionCheck = Callable[[Any, PreferenceStore, Any], bool]
CRITERION_CHECKS: dict[str, CriterionCheck] = {
    "reminder_created": _reminder_created,
    "transient_rejected": _transient_rejected,
    "stable_preference_written": _stable_preference_written,
    "memory_written": _memory_not_written,
    "sensitive_pending_confirm": _sensitive_pending,
    "duplicate_suppressed": _duplicate_suppressed,
    "tool_failed_then_recovered": _failed_then_recovered,
    "recovered_with_matching_fixture": _recovered_with_matching_fixture,
    "final_verification_ok": _final_verification_ok,
    "preferences_written": _preferences_written,
}


def _check_success_criteria(criteria, out, mem_store, case) -> list[str]:
    failures = []
    for key, expected in criteria.items():
        check = CRITERION_CHECKS.get(key)
        if check is None:
            failures.append(f"unknown success criterion: {key}")
            continue
        if key == "memory_written":
            actual_ok = check(out, mem_store, case)
            matched = actual_ok if expected is False else not actual_ok
        else:
            matched = check(out, mem_store, expected)
        if not matched:
            failures.append(f"success criterion failed: {key}={expected!r}")
    return failures


def run_case(case: dict, judge=None) -> dict:
    ReminderTool.reset()
    TripLogTool.reset()
    llm = (ScriptedLLM(case["scripted_plan"]) if "scripted_plan" in case
           else DeterministicStub())
    mem_dir = tempfile.TemporaryDirectory()
    mem_store = PreferenceStore(Path(mem_dir.name) / "qdrant",
                               embed_fn=hash_embedder())
    graph = build_graph(llm, memory_store=mem_store)

    asr_cfg = case.get("asr")
    asr = ASRResult(text=asr_cfg["text"], confidence=asr_cfg["confidence"],
                    place_entities=asr_cfg.get("place_entities", []),
                    time_entities=asr_cfg.get("time_entities", []),
                    measured=False) if asr_cfg else None

    state = new_state(user_request=case["user_request"], asr_result=asr,
                      vehicle_state=case["context"].get(
                          "vehicle_state", "parked_simulated"),
                      trip_context=case["context"],
                      memory_candidates=case.get("memory_candidates", []),
                      user_attributes={"user_id": "owner",
                                       "authenticated": True, "role": "owner"})
    out = run_graph(graph, state)
    exp = case["expected"]

    visited = out.visited_nodes
    required = exp.get("required_nodes", [])
    covered = [n for n in required if n in visited]
    node_coverage = len(covered) / len(required) if required else 1.0

    executed_tools = {c.tool for c in out.tool_calls}
    forbidden_hit = [t for t in exp.get("forbidden_actions", [])
                     if t in executed_tools]

    confirm_needed = exp.get("requires_confirmation", False)
    confirm_ok = (not confirm_needed) or out.confirmation_state == "confirmed"

    reasons = [d.reason_code for d in out.policy_decisions]
    verification_ok = out.verification_result.get("ok", False)
    task_completed = out.verification_result.get("task_completed", False)
    expected_deny = bool(exp.get("expected_deny_reason"))
    deny_ok = True
    if expected_deny:
        deny_ok = (exp["expected_deny_reason"] in reasons
                   and not executed_tools
                   and verification_ok
                   and not task_completed)
    degrade_ok = True
    if exp.get("expected_degrade_reason"):
        degrade_ok = exp["expected_degrade_reason"] in reasons

    allowed = set(exp.get("allowed_tools", []))
    unexpected_tools = sorted(executed_tools - allowed)

    memory_events_ok = True
    if exp.get("expects_memory") or case.get("memory_candidates"):
        memory_events_ok = _memory_events_ok(out.trace, mem_store)

    criterion_failures = _check_success_criteria(
        exp.get("success_criteria", {}), out, mem_store, case)

    task_outcome_ok = not task_completed if expected_deny else task_completed
    passed = (node_coverage == 1.0 and not forbidden_hit and confirm_ok
              and deny_ok and degrade_ok and not unexpected_tools
              and memory_events_ok
              and not criterion_failures
              and verification_ok and task_outcome_ok)

    mem_store.close()
    mem_dir.cleanup()

    result = {
        "case_id": case["case_id"],
        "passed": passed,
        "node_coverage": round(node_coverage, 3),
        "missing_nodes": [n for n in required if n not in visited],
        "forbidden_hit": forbidden_hit,
        "unexpected_tools": unexpected_tools,
        "confirm_ok": confirm_ok,
        "deny_ok": deny_ok,
        "degrade_ok": degrade_ok,
        "task_outcome_ok": task_outcome_ok,
        "memory_events_ok": memory_events_ok,
        "criterion_failures": criterion_failures,
        "policy_reasons": reasons,
        "verification": out.verification_result,
        "visited_nodes": visited,
        "final_response": out.final_response,
    }

    # judge 只写进报告，不参与上面的 passed 判定（铁律）
    if judge is not None:
        try:
            js = judge.score(case, result)
            result["judge"] = {"dimensions": js.dimensions, "notes": js.notes}
        except Exception as e:  # judge 挂了不炸整轮评测，如实记错
            result["judge"] = {"error": str(e)}
    return result


def main() -> int:
    dataset = ROOT / "fixtures" / "dataset_v0.jsonl"
    cases = load_dataset(dataset)
    try:
        judge = DeepSeekJudge()
        judge_line = (f"judge: {DeepSeekJudge.MODEL} via {judge.via}"
                      "（advisory，仅报告不判分）")
    except RuntimeError:
        judge = None
        judge_line = "judge: pending(需 deepseek skill 或 TRIPPILOT_JUDGE_API_KEY)"

    results = [run_case(c, judge=judge) for c in cases]

    passed = sum(1 for r in results if r["passed"])
    print("=" * 64)
    print("TripPilot eval report  ·  v0.1  ·  数据集: dataset_v0 "
          f"({len(cases)} 条)")
    print(judge_line)
    print("=" * 64)
    print(f"通过: {passed}/{len(cases)}")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        line = (f"[{mark}] {r['case_id']}  "
                f"node_cov={r['node_coverage']} "
                f"forbidden={r['forbidden_hit'] or '-'} "
                f"unexpected={r['unexpected_tools'] or '-'}")
        j = r.get("judge")
        if j and "dimensions" in j:
            line += (f"  judge_plan_eff={j['dimensions'].get(DIM_PLAN_EFFICIENCY)} "
                     f"clarify_q={j['dimensions'].get(DIM_CLARIFY_QUALITY)}")
        elif j and "error" in j:
            line += f"  judge_error={j['error'][:60]}"
        print(line)
        if not r["passed"]:
            print(f"       missing={r['missing_nodes']} "
                  f"confirm_ok={r['confirm_ok']} deny_ok={r['deny_ok']} "
                  f"degrade_ok={r['degrade_ok']} "
                  f"memory_events_ok={r['memory_events_ok']} "
                  f"criterion_failures={r['criterion_failures']}")
            print(f"       visited={r['visited_nodes']}")
            print(f"       verification={r['verification']}")
        if j and "notes" in j:
            print(f"       judge_notes={j['notes']}")
    print("=" * 64)
    print("说明：全部用例使用 recorded 工具响应 + stub/脚本 LLM；")
    print("      真实模型与真实录音的实测指标待接入后单独报告。")
    if judge is None:
        print("      judge 分数 pending：需 deepseek skill（推荐）"
              "或 TRIPPILOT_JUDGE_API_KEY。")
    else:
        print("      judge 分数为参考（advisory），不影响 pass/fail 判定。")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
