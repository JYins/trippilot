"""LangGraph Orchestrator。

节点流：intent → memory_recall → clarify → planner → policy_gate
        → human_confirm → tool_executor → verifier → memory_capture
        → (recovery | human_confirm | 结束)

- Policy Gate 的 deny / confirm 直接决定路由，不经过模型"商量"。
- 轨迹为 append-only：每个节点追加 TraceEvent 与 visited_nodes。
- 失败时诚实降级：verifier 明确标记失败，不声称已完成。
- 记忆写入走 Memory Gate：sensitive 偏好挂起后经现有 human_confirm
  确认才写，不另起确认机制；human_confirm 事件先于实际写入（eval 回归）。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from langgraph.graph import END, StateGraph

from .llm import LLMClient, DeterministicStub
from .memory.extract import extract_candidates
from .memory.store import MemoryRejected, PreferenceStore
from .policy_gate import check_tool_call, scan_tool_text
from .state import (ASRResult, PlanStep, PolicyDecision, ToolCall, ToolResult,
                    TraceEvent, TripPilotState)
from .tools import ToolError, get_tool


def _trace(state: TripPilotState, node: str, event: str,
           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ev = TraceEvent(node=node, event=event, payload=payload or {})
    return {"trace": [*state.trace, ev],
            "visited_nodes": [*state.visited_nodes, node]}


def _render_clarify_question(asr: ASRResult, low_conf: bool) -> str:
    """追问话术：确定性模板，用户听到的必须是人话且点名选项。

    reason code（如 place_ambiguity(...)）只进 trace，不进 final_response——
    之前直接把 code 拼进话术，用户听到的是机器码，live judge 在
    TP-AMBIG-001 等 3 条上给了低分（见 decisions/20251006-clarify-wording.md）。
    low_conf 由调用方（clarify_node）判定后传入，阈值只留一处。
    """
    names = [p.get("name", "") for p in asr.place_entities if p.get("name")]
    if len(names) > 1:
        options = "、".join(f"「{n}」" for n in names)
        question = f"你指的是{options}中的哪一个？"
        return f"刚才没太听清，{question}" if low_conf else question
    if len(names) == 1 and low_conf:
        # 单个地点 + 没听清：按"猜测确认"问，不把猜测当事实；
        # time entity 不点名——它从没产生过 reason code，没有"选项"可点，
        # judge 想要也只是 advisory 层面的建议，不跟。
        return f"刚才没太听清，你是说「{names[0]}」吗？"
    if low_conf:
        return "刚才没太听清，能再说一遍吗？"
    # 兜底：need_clarify 为真时理论上走不到（必有其一），但模板不假设
    # 调用方——以后加新歧义类型没配模板时，宁可问得笼统也不泄露 code。
    return "没太确定你的意思，能再具体说一下吗？"


def _insert_candidate(store: PreferenceStore, uid: str,
                      cand: dict[str, Any]) -> str:
    """Gate 已通过（或上一轮已判 confirm 且用户已确认）后的直接写盘。"""
    return store._insert(
        uid, cand["content"], cand.get("kind", "other"),
        cand.get("sensitivity", "normal"), cand.get("source_type", "chat"))


def _capture_candidate(store: PreferenceStore, uid: str,
                       cand: dict[str, Any],
                       confirmed: bool) -> tuple[str, str | None]:
    """处理单条记忆候选：返回 ("written", id) / ("pending", None)。

    Gate 判 deny 时抛 MemoryRejected。Gate 判 confirm 且本轮已确认
    （human_confirm 事件已在轨迹里）时直接 _insert：remember() 里
    Gate 刚跑过一次，不重复跑。
    """
    status, mid = store.remember(
        user_id=uid,
        content=cand["content"],
        kind=cand.get("kind", "other"),
        sensitivity=cand.get("sensitivity", "normal"),
        source_type=cand.get("source_type", "chat"),
        is_transient=cand.get("is_transient", False),
    )
    if status == "written":
        return "written", mid
    if confirmed:
        return "written", _insert_candidate(store, uid, cand)
    return "pending", None


def _mark_pending(pending: list[dict[str, Any]],
                   decisions: list[PolicyDecision],
                   cand: dict[str, Any]) -> None:
    """敏感候选挂起：记入 pending 并追加 confirm 决策（话术带内容）。"""
    pending.append(cand)
    decisions.append(PolicyDecision(
        decision="confirm",
        reason_code="sensitive_memory_needs_confirm",
        detail=f"记住这条偏好吗？「{cand['content']}」（可随时删除）"))


def build_graph(llm: LLMClient | None = None,
              memory_store: PreferenceStore | None = None) -> Any:
    llm = llm or DeterministicStub()
    g = StateGraph(TripPilotState)

    # -- intent ----------------------------------------------------------
    def intent_node(state: TripPilotState) -> dict[str, Any]:
        text = state.asr_result.text if state.asr_result else state.user_request
        upd = _trace(state, "intent", "intent_extracted", {"text": text})
        upd["intent"] = text
        upd["user_request"] = text
        return upd

    # -- memory_recall ---------------------------------------------------
    def memory_recall_node(state: TripPilotState) -> dict[str, Any]:
        prefs: list[dict[str, Any]] = []
        error = ""
        if memory_store is not None:
            try:
                uid = state.user_attributes.get("user_id", "owner")
                query = state.intent or state.user_request
                prefs = [p.model_dump()
                         for p in memory_store.recall(uid, query, top_k=3)]
            except Exception as e:  # 记忆失败不拦主流程，记进 trace 诚实暴露
                error = f"{type(e).__name__}: {e}"
        upd = _trace(state, "memory_recall", "preferences_recalled",
                     {"count": len(prefs), "error": error})
        upd["preferences"] = prefs
        return upd

    # -- clarify ---------------------------------------------------------
    def clarify_node(state: TripPilotState) -> dict[str, Any]:
        asr = state.asr_result
        need_clarify = False
        reasons: list[str] = []
        low_conf = bool(asr and asr.confidence < 0.6)
        if asr:
            if low_conf:
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
            # 追问走确定性模板：reason code 只进 trace，用户听到人话
            upd["final_response"] = _render_clarify_question(asr, low_conf)
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

        calls: list[ToolCall] = []
        degraded: list[dict[str, Any]] = []
        for call, decision in zip(state.pending_tool_calls, decisions):
            if decision.decision != "degrade":
                calls.append(call)
                continue
            args = dict(call.args)
            before = args.get("option_count")
            args["option_count"] = 2
            calls.append(ToolCall(tool=call.tool, args=args,
                                  source=call.source))
            degraded.append({"tool": call.tool,
                             "parameter": "option_count",
                             "from": before, "to": 2})

        upd = _trace(state, "policy_gate", "decisions_made",
                     {"decisions": [d.model_dump() for d in decisions]})
        if degraded:
            upd["trace"].append(TraceEvent(
                node="policy_gate", event="degraded_params",
                payload={"changes": degraded}))
        upd["policy_decisions"] = decisions
        upd["pending_tool_calls"] = calls
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
            details = "；".join(d.detail for d in state.policy_decisions
                                if d.decision == "confirm")
            upd["final_response"] = "需要你确认：" + (details or "；".join(needs))
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

    # -- memory_capture --------------------------------------------------
    def memory_capture_node(state: TripPilotState) -> dict[str, Any]:
        # 逐条过 Memory Gate 写偏好；sensitive 挂起，走现有 human_confirm。
        # 已确认（confirmation_state == "confirmed"）时把挂起的一并写入，
        # 保证 human_confirm 事件先于实际写入。
        # 外部没给 memory_candidates 时从本轮 user_request 自动抽取一次：
        # 抽到的候选照样逐条走下面的 Gate 流程，不绕过。human_confirm
        # 回绕进来时 user_request 没变，不重复抽（否则确认后会重复写盘）。
        written: list[str] = []
        rejected: list[str] = []
        pending: list[dict[str, Any]] = []
        decisions = list(state.policy_decisions)
        confirmed = state.confirmation_state == "confirmed"
        candidates = list(state.memory_candidates)
        extracted = 0
        trip_context = dict(state.trip_context)
        if memory_store is not None:
            uid = state.user_attributes.get("user_id", "owner")
            if not candidates and \
                    trip_context.get("extracted_from") != state.user_request:
                candidates = extract_candidates(
                    state.user_request,
                    {"user_id": uid, "session_id": state.session_id})
                extracted = len(candidates)
                trip_context["extracted_from"] = state.user_request
            if confirmed:
                # 上一轮 Gate 已判 confirm（能进 pending 的前提），现已确认：
                # 直接 _insert，不重复跑 Gate；deny 的候选进不了 pending
                for cand in state.pending_memory_confirms:
                    written.append(_insert_candidate(memory_store, uid, cand))
            else:
                # 未确认：上一轮挂起的继续保留，重新走 human_confirm，不能静默丢
                for cand in state.pending_memory_confirms:
                    _mark_pending(pending, decisions, cand)
            for cand in candidates:
                try:
                    status, mid = _capture_candidate(
                        memory_store, uid, cand, confirmed)
                except MemoryRejected as e:
                    rejected.append(e.reason_code)
                    continue
                if status == "written":
                    written.append(mid or "")
                else:
                    _mark_pending(pending, decisions, cand)
        event = ("memories_written" if written else
                 "confirm_needed" if pending else "nothing_to_capture")
        upd = _trace(state, "memory_capture", event,
                     {"written": written, "rejected": rejected,
                      "pending": len(pending), "extracted": extracted})
        upd["policy_decisions"] = decisions
        upd["trip_context"] = trip_context
        upd["memory_candidates"] = []
        upd["pending_memory_confirms"] = pending
        return upd

    def _route_after_capture(s: TripPilotState) -> str:
        # 挂起的敏感偏好 → 现有 human_confirm；否则沿用 verifier 的恢复逻辑
        if s.pending_memory_confirms and s.confirmation_state != "confirmed":
            return "confirm"
        vr = s.verification_result or {}
        if (not vr.get("ok") and s.recovery_count < 1
                and any(not r.ok for r in s.tool_results)):
            return "recover"
        return "end"

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
    g.add_node("memory_recall", memory_recall_node)
    g.add_node("clarify", clarify_node)
    g.add_node("planner", planner_node)
    g.add_node("policy_gate", policy_gate_node)
    g.add_node("human_confirm", human_confirm_node)
    g.add_node("tool_executor", tool_executor_node)
    g.add_node("verifier", verifier_node)
    g.add_node("memory_capture", memory_capture_node)
    g.add_node("recovery", recovery_node)

    g.set_entry_point("intent")
    g.add_edge("intent", "memory_recall")
    g.add_edge("memory_recall", "clarify")
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
    g.add_edge("verifier", "memory_capture")
    g.add_conditional_edges(
        "memory_capture",
        _route_after_capture,
        {"confirm": "human_confirm", "recover": "recovery", "end": END})
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
