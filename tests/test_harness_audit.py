"""harness_audit 的回归：事件溯源不变式 + 工具注册表 seam。

全部离线：stub LLM + recorded 工具响应，不联网。
"""

import json
from pathlib import Path

import pytest

from eval.harness_audit import (RecordingLLM, _direct_constructions,
                                audit_event_sourcing, audit_tool_registry,
                                run_audit_case, run_harness_audit)
from trippilot.graph import build_graph, new_state, run_graph
from trippilot.memory.store import PreferenceStore, hash_embedder
from trippilot.tools import ToolError, get_tool

ROOT = Path(__file__).resolve().parent.parent
ATTRS = {"user_id": "owner", "authenticated": True, "role": "owner"}


def _case(case_id: str) -> dict:
    for line in (ROOT / "fixtures" / "dataset_v0.jsonl").read_text(
            encoding="utf-8").splitlines():
        if line.strip():
            case = json.loads(line)
            if case["case_id"] == case_id:
                return case
    raise AssertionError(f"fixture 里没有 {case_id}")


# -- 事件溯源不变式 ----------------------------------------------------------

def test_event_sourcing_invariant_passes():
    # TP-ROUTE-001：多工具全链路，intent/工具参数/工具结果都要进日志
    out, seen = run_audit_case(_case("TP-ROUTE-001"))
    assert seen, "这条用例应该至少调一次 LLM"
    result = audit_event_sourcing(out, seen)
    assert result["passed"], f"缺失项: {result['missing']}"


def test_event_sourcing_catches_dropped_event():
    # 反例：把 tool_executor 事件从 trace 里删掉，审计必须揪出来
    out, seen = run_audit_case(_case("TP-ROUTE-001"))
    out.trace = [e for e in out.trace if e.node != "tool_executor"]
    result = audit_event_sourcing(out, seen)
    assert not result["passed"]
    assert any("final_answer.tool_result" in m for m in result["missing"])


def test_event_sourcing_catches_missing_intent():
    # 反例：intent 事件丢了，plan.intent 断言必须 fail
    out, seen = run_audit_case(_case("TP-ROUTE-001"))
    out.trace = [e for e in out.trace if e.node != "intent"]
    result = audit_event_sourcing(out, seen)
    assert not result["passed"]
    assert any("plan.intent" in m for m in result["missing"])


def test_preferences_recall_matches_logged_count(tmp_path):
    # 召回的偏好要和 memory_recall 事件对得上
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    store.remember("owner", "公司地址：望京 SOHO", kind="place")
    llm = RecordingLLM()
    graph = build_graph(llm, memory_store=store)
    out = run_graph(graph, new_state(user_request="送我去公司",
                                     user_attributes=ATTRS))
    assert out.preferences, "应该召回至少一条偏好"
    result = audit_event_sourcing(out, llm.seen)
    assert result["passed"], f"缺失项: {result['missing']}"
    store.close()


# -- 工具注册表 seam --------------------------------------------------------

def test_tool_registry_seam_passes_on_current_code():
    result = audit_tool_registry()
    assert result["passed"], f"问题: {result['issues']}"


def test_get_tool_rejects_unknown_name():
    with pytest.raises(ToolError):
        get_tool("not_a_registered_tool")


def test_get_tool_resolves_dotted_names():
    # planner 传的是 "map.route" 这种点分名，注册表要能解析
    assert get_tool("map.route").name == "map"
    assert get_tool("reminder.create").name == "reminder"


def test_direct_construction_is_flagged(tmp_path):
    # AST 门禁：直调具体工具类必须被揪出来
    bad = tmp_path / "bad.py"
    bad.write_text("from trippilot.tools.tools import MapTool\n"
                   "tool = MapTool()\n", encoding="utf-8")
    hits = _direct_constructions(bad)
    assert len(hits) == 1
    assert "MapTool" in hits[0]

    good = tmp_path / "good.py"
    good.write_text("from trippilot.tools import get_tool\n"
                    "tool = get_tool('map.route')\n", encoding="utf-8")
    assert _direct_constructions(good) == []


# -- 全量报告 --------------------------------------------------------------

def test_full_audit_report_passes():
    report = run_harness_audit(ROOT / "fixtures" / "dataset_v0.jsonl")
    assert len(report["cases"]) == 12
    failed = [r for r in report["cases"] if not r["passed"]]
    assert not failed, f"未通过的用例: {failed}"
    assert report["seam"]["passed"], f"seam 问题: {report['seam']['issues']}"
