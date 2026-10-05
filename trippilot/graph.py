"""LangGraph Orchestrator。

节点流：intent → clarify → planner → policy_gate → human_confirm
        → tool_executor → verifier → (recovery | 结束)

- Policy Gate 的 deny / confirm 直接决定路由，不经过模型"商量"。
- 轨迹为 append-only：每个节点追加 TraceEvent 与 visited_nodes。
- 失败时诚实降级：verifier 明确标记失败，不声称已完成。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from langgraph.graph import END, StateGraph

from .llm import LLMClient, DeterministicStub
from .policy_gate import check_tool_call, scan_tool_text
from .state import (ASRResult, PlanStep, PolicyDecision, ToolCall, ToolResult,
                    TraceEvent, TripPilotState)
from .tools import ToolError, get_tool


def _trace(state: TripPilotState, node: str, event: str,
           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ev = TraceEvent(node=node, event=event, payload=payload or {})
    return {"trace": [*state.trace, ev],
            "visited_nodes": [*state.visited_nodes, node]}


def build_graph(llm: LLMClient | None = None) -> Any:
    llm = llm or DeterministicStub()
    g = StateGraph(TripPilotState)

    # -- intent ----------------------------------------------------------
    def intent_node(state: TripPilotState) -> dict[str, Any]:
        text = state.asr_result.text if state.asr_result else state.user_request
        upd = _trace(state, "intent", "intent_extracted", {"text": text})
        upd["intent"] = text
        upd["user_request"] = text
        return upd

    # -- clarify ---------------------------------------------------------
    def clarify_node(state: TripPilotState) -> dict[str, Any]:
        asr = state.asr_result
        need_clarify = False
        reasons: list[str] = []
        if asr:
            if asr.confidence < 0.6:
                need_clarify = True
                reasons.append(f"asr_low_confidence({asr.confidence:.2f})")
            if len(asr.place_entities) > 1:
                need_clarify = True
                names = [p.get("name", "?") for p in asr.place_entities]
                reasons.append(f"place_ambiguity({','.join(names)})")
        # 测试/非交互模式：trip_context 可预置 clarify_answer 直接通过
        answer = state.trip_context.get("clarify_answer")
        upd = _trace(state, "clarify", "clarify_checked",
                     {"need_clarify": need_clarify, "reasons": reasons})
        if need_clarify and not answer:
            upd["confirmation_state"] = "pending"
            upd["final_response"] = llm.clarify_question(
                ambiguity="；".join(reasons))
            upd["stop_after_clarify"] = True  # 内部标记：等待用户回答
        elif answer:
            upd["trip_context"] = {**state.trip_context,
                                   "clarify_resolved": answer}
        return upd

    # -- planner ---------------------------------------------------------
    def planner_node(state: TripPilotState) -> dict[str, Any]:
        t0 = time.time()
        steps = llm.plan(intent=state.intent,
                         context={"session_id": state.session_id,
                                  **state.trip_context},
                         available_tools=["map.route", "weather.now",
                                          "reminder.create", "trip_log.append"])
        plan = [PlanStep(step_id=s.get("step_id", f"s{i}"),
                         tool=s.get("tool"), args=s.get("args", {}),
                         description=s.get("description", ""))
                for i, s in enumerate(steps)]
        calls = [ToolCall(tool=p.tool, args=p.args, source="recorded")
                 for p in plan if p.tool]
        upd = _trace(state, "planner", "plan_created",
                     {"steps": [p.model_dump() for p in plan]})
        upd["plan"] = plan
        upd["pending_tool_calls"] = calls
        upd["latency_breakdown"] = {**state.latency_breakdown,
                                    "planner": round(time.time() - t0, 3)}
        return upd

    # -- policy_gate ------------------------------------------------------
    def policy_gate_node(state: TripPilotState) -> dict[str, Any]:
        decisions: list[PolicyDecision] = []
        for call in state.pending_tool_calls:
            decisions.append(check_tool_call(call, state))
        upd = _trace(state, "policy_gate", "decisions_made",
                     {"decisions": [d.model_dump() for d in decisions]})
        upd["policy_decisions"] = decisions
        kinds = {d.decision for d in decisions}
        if "deny" in kinds:
            upd["route_after_policy"] = "deny"
            upd["denied_tools"] = [c.tool for c, d in zip(state.pending_tool_calls, decisions) if d.decision == "deny"]
        elif "confirm" in kinds:
            upd["route_after_policy"] = "confirm"
        else:
            upd["route_after_policy"] = "allow"
        return upd

    # -- human_confirm ----------------------------------------------------
    def human_confirm_node(state: TripPilotState) -> dict[str, Any]:
        # 非交互模式：trip_context.confirm=true 视为用户已确认
        confirmed = state.trip_context.get("confirm", False)
        upd = _trace(state, "human_confirm",
                     "confirmed" if confirmed else "awaiting_user",
                     {"confirmed": confirmed})
        if confirmed:
            upd["confirmation_state"] = "confirmed"
        else:
            upd["confirmation_state"] = "pending"
            needs = [d.reason_code for d in state.policy_decisions
                     if d.decision == "confirm"]
            upd["final_response"] = "需要你确认：" + "；".join(
                d.detail for d in state.policy_decisions
                if d.decision == "confirm") or "；".join(needs)
            upd["stop_after_confirm"] = True
        return upd

    # -- tool_executor ----------------------------------------------------
    def tool_executor_node(state: TripPilotState) -> dict[str, Any]:
        results: list[ToolResult] = []
        calls: list[ToolCall] = []
        for call in state.pending_tool_calls:
            t0 = time.time()
            try:
                tool = get_tool(call.tool)
                res = tool.run(call)
                # 工具返回文本进入系统前先做注入检查
                blob = str(res.data)
                if scan_tool_text(blob):
                    res = ToolResult(tool=res.tool, ok=False,
                                     source=res.source,
                                     error="tool_text_injection_suspected: "
                                           "工具返回含可疑指令，视为不可信数据，已拦截")
            except ToolError as e:
                res = ToolResult(tool=call.tool, ok=False,
                                 source=call.source, error=str(e))
            results.append(res)
            calls.append(call)
            state.latency_breakdown[f"tool:{call.tool}"] = round(
                time.time() - t0, 3)
        upd = _trace(state, "tool_executor", "tools_executed",
                     {"results": [r.model_dump() for r in results]})
        upd["tool_results"] = [*state.tool_results, *results]
        upd["tool_calls"] = [*state.tool_calls, *calls]
        upd["pending_tool_calls"] = []
        upd["latency_breakdown"] = dict(state.latency_breakdown)
        return upd

    # -- verifier ----------------------------------------------------------
    def verifier_node(state: TripPilotState) -> dict[str, Any]:
        expected = state.trip_context.get("success_criteria", {})
        failures: list[str] = []
        # 1. 禁止动作：被 deny 的工具绝不能出现在 tool_calls
        executed = {c.tool for c in state.tool_calls}
        for t in state.denied_tools:
            if t in executed:
                failures.append(f"forbidden_action_executed:{t}")
        # 2. 工具全部成功？
        for r in state.tool_results:
            if not r.ok and not expected.get("allow_tool_failure"):
                failures.append(f"tool_failed:{r.tool}:{r.error}")
        # 3. 应确认的是否确认
        if any(d.decision == "confirm" for d in state.policy_decisions):
            if state.confirmation_state != "confirmed":
                failures.append("confirmation_missing")
        ok = not failures
        upd = _trace(state, "verifier",
                     "verified_ok" if ok else "verified_fail",
                     {"failures": failures})
        upd["verification_result"] = {"ok": ok, "failures": failures}
        if ok:
            upd["final_response"] = llm.final_answer(state_summary={
                "tool_results": [r.model_dump() for r in state.tool_results]})
        else:
            upd["final_response"] = ("这次没能安全完成：" +
                                     "；".join(failures))
        return upd

    # -- recovery ----------------------------------------------------------
    def recovery_node(state: TripPilotState) -> dict[str, Any]:
        upd = _trace(state, "recovery", "retry_once",
                     {"recovery_count": state.recovery_count + 1})
        # 仅重试失败的工具调用；reminder 幂等键防止重复副作用
        failed = [c.tool for c, r in
                  zip(state.tool_calls[-len(state.tool_results):],
                      state.tool_results) if not r.ok]
        upd["pending_tool_calls"] = [
            ToolCall(tool=t,
                     args={"fixture": "default",
                           "session_id": state.session_id},
                     source="recorded") for t in failed]
        upd["recovery_count"] = state.recovery_count + 1
        upd["tool_results"] = []
        return upd

    # -- 组装 --------------------------------------------------------------
    g.add_node("intent", intent_node)
    g.add_node("clarify", clarify_node)
    g.add_node("planner", planner_node)
    g.add_node("policy_gate", policy_gate_node)
    g.add_node("human_confirm", human_confirm_node)
    g.add_node("tool_executor", tool_executor_node)
    g.add_node("verifier", verifier_node)
    g.add_node("recovery", recovery_node)

    g.set_entry_point("intent")
    g.add_edge("intent", "clarify")
    g.add_conditional_edges(
        "clarify",
        lambda s: "stop" if s.stop_after_clarify else "go",
        {"stop": END, "go": "planner"})
    g.add_edge("planner", "policy_gate")
    g.add_conditional_edges(
        "policy_gate",
        lambda s: s.route_after_policy,
        {"deny": "verifier", "confirm": "human_confirm", "allow": "tool_executor"})
    g.add_conditional_edges(
        "human_confirm",
        lambda s: "stop" if s.stop_after_confirm else "go",
        {"stop": END, "go": "tool_executor"})
    g.add_edge("tool_executor", "verifier")
    g.add_conditional_edges(
        "verifier",
        lambda s: ("recover"
                   if (not s.verification_result.get("ok")
                       and s.recovery_count < 1
                       and any(not r.ok for r in s.tool_results))
                   else "end"),
        {"recover": "recovery", "end": END})
    g.add_edge("recovery", "tool_executor")

    return g.compile()


def new_state(session_id: str | None = None,
              **kwargs: Any) -> TripPilotState:
    return TripPilotState(
        trace_id=f"tr_{uuid.uuid4().hex[:12]}",
        session_id=session_id or f"sess_{uuid.uuid4().hex[:8]}",
        **kwargs)


def run_graph(graph: Any, state: TripPilotState) -> TripPilotState:
    """执行图并把结果归一化为 TripPilotState（invoke 在不同版本返回 dict）。"""
    out = graph.invoke(state)
    if isinstance(out, TripPilotState):
        return out
    return TripPilotState(**out)


__all__ = ["build_graph", "new_state", "run_graph"]
