"""Eval Runner：轨迹级回归。

对 dataset_v0.jsonl 每条用例：
- 用 ScriptedLLM（有 scripted_plan）或 DeterministicStub 跑完整图；
- 工具全部走 recorded；
- 检查：Required-node Coverage、Forbidden-action Rate、Confirmation Compliance、
  期望的 deny/degrade reason_code；
- 输出固定结构的版本报告（见计划 §④）。

硬安全规则由程序断言，不交给 LLM Judge。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trippilot.graph import build_graph, new_state, run_graph
from trippilot.llm import DeterministicStub, ScriptedLLM
from trippilot.memory.store import PreferenceStore, hash_embedder
from trippilot.state import ASRResult
from trippilot.tools.tools import ReminderTool, TripLogTool


def load_dataset(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _memory_events_ok(trace, mem_store) -> bool:
    """含记忆用例的轨迹断言：必须有 memory_recall 事件；
    敏感写入（payload written）出现前，trace 里必须出现过 human_confirm。
    顺序断言逻辑复用 tests/test_graph_memory.py。
    """
    if "memory_recall" not in [e.node for e in trace]:
        return False
    hc_idx = next((i for i, e in enumerate(trace)
                   if e.node == "human_confirm"), None)
    for i, e in enumerate(trace):
        if e.node == "memory_capture" and e.payload.get("written"):
            sensitive = any(
                (p := mem_store.get(mid)) is not None
                and p.sensitivity == "sensitive"
                for mid in e.payload["written"])
            if sensitive and (hc_idx is None or hc_idx >= i):
                return False
    return True


def run_case(case: dict) -> dict:
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
    deny_ok = True
    if exp.get("expected_deny_reason"):
        deny_ok = (exp["expected_deny_reason"] in reasons
                   and not executed_tools)
    degrade_ok = True
    if exp.get("expected_degrade_reason"):
        degrade_ok = exp["expected_degrade_reason"] in reasons

    allowed = set(exp.get("allowed_tools", []))
    unexpected_tools = sorted(executed_tools - allowed)

    memory_events_ok = True
    if exp.get("expects_memory") or case.get("memory_candidates"):
        memory_events_ok = _memory_events_ok(out.trace, mem_store)

    passed = (node_coverage == 1.0 and not forbidden_hit and confirm_ok
              and deny_ok and degrade_ok and not unexpected_tools
              and memory_events_ok
              and out.verification_result.get("ok", False))

    mem_store.close()
    mem_dir.cleanup()

    return {
        "case_id": case["case_id"],
        "passed": passed,
        "node_coverage": round(node_coverage, 3),
        "missing_nodes": [n for n in required if n not in visited],
        "forbidden_hit": forbidden_hit,
        "unexpected_tools": unexpected_tools,
        "confirm_ok": confirm_ok,
        "deny_ok": deny_ok,
        "degrade_ok": degrade_ok,
        "memory_events_ok": memory_events_ok,
        "policy_reasons": reasons,
        "verification": out.verification_result,
        "visited_nodes": visited,
        "final_response": out.final_response,
    }


def main() -> int:
    dataset = ROOT / "fixtures" / "dataset_v0.jsonl"
    cases = load_dataset(dataset)
    results = [run_case(c) for c in cases]

    passed = sum(1 for r in results if r["passed"])
    print("=" * 64)
    print("TripPilot eval report  ·  v0.1  ·  数据集: dataset_v0 "
          f"({len(cases)} 条)")
    print("=" * 64)
    print(f"通过: {passed}/{len(cases)}")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['case_id']}  "
              f"node_cov={r['node_coverage']} "
              f"forbidden={r['forbidden_hit'] or '-'} "
              f"unexpected={r['unexpected_tools'] or '-'}")
        if not r["passed"]:
            print(f"       missing={r['missing_nodes']} "
                  f"confirm_ok={r['confirm_ok']} deny_ok={r['deny_ok']} "
                  f"degrade_ok={r['degrade_ok']} "
                  f"memory_events_ok={r['memory_events_ok']}")
            print(f"       visited={r['visited_nodes']}")
            print(f"       verification={r['verification']}")
    print("=" * 64)
    print("说明：全部用例使用 recorded 工具响应 + stub/脚本 LLM；")
    print("      真实模型与真实录音的实测指标待接入后单独报告。")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
