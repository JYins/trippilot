"""LangGraph 编排：组装节点、声明边和路由。"""

from __future__ import annotations

import uuid
from functools import partial
from typing import Any, Callable

from langgraph.graph import END, StateGraph

from .graph_nodes import (clarify_node, human_confirm_node, intent_node,
                          memory_capture_node, memory_confirm_id,
                          memory_recall_node, planner_node, policy_gate_node,
                          recovery_node, tool_executor_node, verifier_node)
from .llm import DeterministicStub, LLMClient
from .memory.store import PreferenceStore
from .state import TripPilotState
from .tools import get_tool


def _route_after_clarify(state: TripPilotState) -> str:
    if state.stop_after_clarify:
        return "stop"
    return "go"


def _route_after_confirm(state: TripPilotState) -> str:
    if state.stop_after_confirm:
        return "stop"
    return "go"


def _route_after_policy(state: TripPilotState) -> str:
    return state.route_after_policy


def _route_after_capture(state: TripPilotState) -> str:
    if (state.pending_memory_confirms
            and state.confirmation_state != "confirmed"):
        return "confirm"
    verification = state.verification_result or {}
    has_failed_tool = any(not result.ok for result in state.tool_results)
    if (not verification.get("task_completed", False)
            and state.recovery_count < 1 and has_failed_tool):
        return "recover"
    return "end"


def build_graph(llm: LLMClient | None = None,
                memory_store: PreferenceStore | None = None,
                tool_lookup: Callable[[str], Any] = get_tool) -> Any:
    llm = llm or DeterministicStub()
    graph = StateGraph(TripPilotState)

    graph.add_node("intent", intent_node)
    graph.add_node("memory_recall", partial(
        memory_recall_node, memory_store=memory_store))
    graph.add_node("clarify", clarify_node)
    graph.add_node("planner", partial(planner_node, llm=llm))
    graph.add_node("policy_gate", policy_gate_node)
    graph.add_node("human_confirm", human_confirm_node)
    graph.add_node("tool_executor", partial(
        tool_executor_node, tool_lookup=tool_lookup))
    graph.add_node("verifier", partial(verifier_node, llm=llm))
    graph.add_node("memory_capture", partial(
        memory_capture_node, memory_store=memory_store))
    graph.add_node("recovery", recovery_node)

    graph.set_entry_point("intent")
    graph.add_edge("intent", "memory_recall")
    graph.add_edge("memory_recall", "clarify")
    graph.add_conditional_edges(
        "clarify", _route_after_clarify,
        {"stop": END, "go": "planner"})
    graph.add_edge("planner", "policy_gate")
    graph.add_conditional_edges(
        "policy_gate", _route_after_policy,
        {"deny": "verifier", "confirm": "human_confirm",
         "allow": "tool_executor"})
    graph.add_conditional_edges(
        "human_confirm", _route_after_confirm,
        {"stop": END, "go": "tool_executor"})
    graph.add_edge("tool_executor", "verifier")
    graph.add_edge("verifier", "memory_capture")
    graph.add_conditional_edges(
        "memory_capture", _route_after_capture,
        {"confirm": "human_confirm", "recover": "recovery", "end": END})
    graph.add_edge("recovery", "tool_executor")
    return graph.compile()


def new_state(session_id: str | None = None,
              **kwargs: Any) -> TripPilotState:
    return TripPilotState(
        trace_id=f"tr_{uuid.uuid4().hex[:12]}",
        session_id=session_id or f"sess_{uuid.uuid4().hex[:8]}",
        **kwargs,
    )


def run_graph(graph: Any, state: TripPilotState) -> TripPilotState:
    """执行图并把不同 LangGraph 版本的返回值归一化。"""
    output = graph.invoke(state)
    if isinstance(output, TripPilotState):
        return output
    return TripPilotState(**output)


__all__ = ["build_graph", "memory_confirm_id", "new_state", "run_graph"]
