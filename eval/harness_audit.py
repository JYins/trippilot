"""Harness 审计：把 DeepSeek Harness 的两个可操作思想做成回归。

1. 事件溯源不变式（model-visible ⟺ logged）：
   planner/LLM 每一次被调用时看到的输入——intent 文本、tool 参数、
   tool 结果摘要——必须全部能在 append-only trace 里找到。
   模型看见的 ⟺ 日志里可审计的。

2. 工具注册表 seam（capability seam 的轻量实现）：
   tools/ 下的工具必须经 get_tool(TOOLS) 注册表获取；
   业务代码直调具体工具类会被揪出来。consumer 不知道具体 provider。

纯程序断言，不调 LLM 做判断；只读 fixtures + recorded，不联网。

跑法：python eval/harness_audit.py（repo 根目录下）
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import pkgutil
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import trippilot.tools  # noqa: E402  路径插完才能 import
from trippilot.graph import build_graph, new_state, run_graph  # noqa: E402
from trippilot.llm import DeterministicStub  # noqa: E402
from trippilot.memory.store import PreferenceStore, hash_embedder  # noqa: E402
from trippilot.state import ASRResult, TraceEvent, TripPilotState  # noqa: E402
from trippilot.tools import TOOLS, ToolError, get_tool  # noqa: E402
from trippilot.tools.base import BaseTool  # noqa: E402
from trippilot.tools.tools import ReminderTool, TripLogTool  # noqa: E402


class RecordingLLM(DeterministicStub):
    """记下 LLM 每次调用看到的输入：审计的"模型可见"侧。

    有 scripted plan 的用例（eval 的 ScriptedLLM 场景）同样记录——
    不变式查的是"模型看到了什么"，不是"模型怎么算出来的"。

    context 照样记录（留作将来审计扩围），但当前审计只查命名范畴：
    intent、工具参数、工具结果摘要、偏好召回。context 里的环境配置
    （timezone/vehicle_state 等）是已知盲区，见决策记录。
    """

    def __init__(self, scripted_plan: list[dict] | None = None) -> None:
        self._scripted = scripted_plan
        self.seen: list[dict[str, Any]] = []

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        # session_id 是挥发性关联标识，不进审计比对（见决策记录盲区说明）
        auditable = {k: v for k, v in context.items()
                     if k != "session_id"}
        self.seen.append({"call": "plan", "intent": intent,
                          "context": auditable})
        if self._scripted is not None:
            return self._scripted
        return super().plan(intent=intent, context=context,
                            available_tools=available_tools)

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        self.seen.append({"call": "final_answer",
                          "summary": state_summary})
        return super().final_answer(state_summary=state_summary)


# ---------------------------------------------------------------------------
# 1. 事件溯源不变式
# ---------------------------------------------------------------------------

def _leaves(value: Any) -> list[str]:
    """展开成叶子标量字符串：工具结果摘要就长这样。"""
    out: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == "session_id":  # 挥发性标识，不审计
                continue
            out.extend(_leaves(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_leaves(v))
    elif isinstance(value, bool):
        # trace 里是 JSON 序列化：true/false 全小写，不能用 str()
        out.append("true" if value else "false")
    elif value is not None and value != "":
        out.append(str(value))
    return out


def _trace_blob(trace: list[TraceEvent]) -> str:
    return "\n".join(
        json.dumps(e.model_dump(), ensure_ascii=False) for e in trace)


def audit_event_sourcing(out: TripPilotState,
                         seen: list[dict[str, Any]]) -> dict[str, Any]:
    """断言：模型看到的输入 ⟺ trace 里都有。返回缺失项列表。"""
    blob = _trace_blob(out.trace)
    missing: list[str] = []

    def present(label: str, text: str) -> None:
        if text and text not in blob:
            missing.append(f"{label}: 日志里找不到「{text[:60]}」")

    for entry in seen:
        if entry["call"] == "plan":
            present("plan.intent", entry["intent"])
        elif entry["call"] == "final_answer":
            for r in entry["summary"].get("tool_results", []):
                present("final_answer.tool", r.get("tool", ""))
                for v in _leaves(r.get("data", {})):
                    present("final_answer.tool_result", v)
                if r.get("error"):
                    present("final_answer.tool_error", r["error"])

    # planner 的工具参数决策：plan 输出也要进日志才算可审计
    # （planner context 里的环境配置如 timezone/vehicle_state 目前不进
    # trace——已知盲区，见决策记录；这里只查语义性决策）
    for step in out.plan:
        for v in _leaves(step.args):
            present("plan.tool_args", v)

    # preferences：召回结果要和 recall 事件对得上（内容注入 prompt
    # 目前还没发生，见决策记录——审计只查事件存在 + 数量一致）
    if out.preferences:
        ev = next((e for e in out.trace if e.node == "memory_recall"), None)
        if ev is None:
            missing.append("preferences: 召回了偏好但没有 memory_recall 事件")
        elif ev.payload.get("count") != len(out.preferences):
            missing.append(
                f"preferences: recall 事件 count={ev.payload.get('count')}"
                f" 与实际 {len(out.preferences)} 对不上")

    return {"passed": not missing, "missing": missing}


# ---------------------------------------------------------------------------
# 2. 工具注册表 seam
# ---------------------------------------------------------------------------

def _declared_tool_classes() -> list[type]:
    """tools/ 包里声明的所有具体工具类。"""
    pkg = Path(trippilot.tools.__file__).parent
    found = []
    for mod in pkgutil.iter_modules([str(pkg)]):
        module = importlib.import_module(f"trippilot.tools.{mod.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (issubclass(obj, BaseTool) and obj is not BaseTool
                    and obj.__module__ == module.__name__):
                found.append(obj)
    return found


def _direct_constructions(path: Path) -> list[str]:
    """业务代码里直调具体工具类的构造：XxxTool(...)。"""
    hits = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else \
            func.attr if isinstance(func, ast.Attribute) else ""
        if name.endswith("Tool") and name != "BaseTool":
            hits.append(f"{path.name}:{node.lineno} 直调 {name}()，应走 get_tool")
    return hits


def audit_tool_registry() -> dict[str, Any]:
    """断言：工具走注册表，不许直调。返回问题列表。"""
    issues: list[str] = []

    # 声明的每个工具类都必须在注册表里找得到
    for cls in _declared_tool_classes():
        base = cls.name.split(".")[0]
        registered = TOOLS.get(base)
        if not isinstance(registered, cls):
            issues.append(f"{cls.__name__}: 未在 TOOLS 注册表注册")

    # 未注册的名字必须 loud 失败，不能静默返回什么
    try:
        get_tool("not_a_registered_tool")
        issues.append("get_tool('not_a_registered_tool') 没有抛错")
    except ToolError:
        pass

    # 业务代码（trippilot/，tools/ 自身除外）不许直调具体工具类
    pkg = Path(trippilot.tools.__file__).parent.parent
    for path in sorted(pkg.rglob("*.py")):
        if "tools" in path.relative_to(pkg).parts:
            continue
        issues.extend(_direct_constructions(path))

    return {"passed": not issues, "issues": issues}


# ---------------------------------------------------------------------------
# 跑一条轨迹 + 汇总报告
# ---------------------------------------------------------------------------

def run_audit_case(case: dict) -> tuple[TripPilotState, list[dict]]:
    # 工具是有状态单例（reminder 幂等存储）：每条用例前清零，互不污染
    ReminderTool.reset()
    TripLogTool.reset()
    llm = RecordingLLM(case.get("scripted_plan"))
    mem_dir = tempfile.TemporaryDirectory()
    mem_store = PreferenceStore(Path(mem_dir.name) / "qdrant",
                               embed_fn=hash_embedder())
    graph = build_graph(llm, memory_store=mem_store)

    asr_cfg = case.get("asr")
    asr = ASRResult(text=asr_cfg["text"], confidence=asr_cfg["confidence"],
                    place_entities=asr_cfg.get("place_entities", []),
                    time_entities=asr_cfg.get("time_entities", []),
                    measured=False) if asr_cfg else None

    state = new_state(
        user_request=case["user_request"], asr_result=asr,
        vehicle_state=case["context"].get("vehicle_state", "parked_simulated"),
        trip_context=case["context"],
        memory_candidates=case.get("memory_candidates", []),
        user_attributes={"user_id": "owner", "authenticated": True,
                         "role": "owner"})
    out = run_graph(graph, state)
    mem_store.close()
    mem_dir.cleanup()
    return out, llm.seen


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def run_harness_audit(dataset: Path) -> dict[str, Any]:
    cases = load_cases(dataset)
    per_case = []
    for case in cases:
        out, seen = run_audit_case(case)
        inv = audit_event_sourcing(out, seen)
        per_case.append({"case_id": case["case_id"], **inv})
    return {"cases": per_case, "seam": audit_tool_registry()}


def main() -> int:
    report = run_harness_audit(ROOT / "fixtures" / "dataset_v0.jsonl")
    print("=" * 64)
    print("TripPilot harness audit  ·  借鉴 dsh：model-visible ⟺ logged")
    print("=" * 64)

    print("[事件溯源不变式]")
    all_ok = True
    for r in report["cases"]:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  [{mark}] {r['case_id']}")
        if not r["passed"]:
            all_ok = False
            for m in r["missing"]:
                print(f"         缺失: {m}")

    print("[工具注册表 seam]")
    seam = report["seam"]
    print(f"  [{'PASS' if seam['passed'] else 'FAIL'}]")
    if not seam["passed"]:
        all_ok = False
        for i in seam["issues"]:
            print(f"         问题: {i}")

    print("=" * 64)
    print(f"审计结论：{'通过' if all_ok else '未通过'}")
    print("说明：纯程序断言 + recorded 工具响应 + stub LLM；不联网。")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
