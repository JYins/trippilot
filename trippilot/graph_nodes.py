"""TripPilot 图节点实现。编排关系留在 graph.py。"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Callable

from .llm import LLMClient
from .memory.extract import extract_candidates
from .memory.store import MemoryRejected, PreferenceStore
from .policy_gate import check_tool_call, scan_tool_text
from .state import (ASRResult, PlanStep, PolicyDecision, ToolCall, ToolResult,
                    TraceEvent, TripPilotState)
from .tools import ToolError, fixture_key_for, get_tool


def _trace(state: TripPilotState, node: str, event: str,
           payload: dict[str, Any] | None = None) -> dict[str, Any]:
    trace_event = TraceEvent(node=node, event=event, payload=payload or {})
    return {
        "trace": [*state.trace, trace_event],
        "visited_nodes": [*state.visited_nodes, node],
    }


def intent_node(state: TripPilotState) -> dict[str, Any]:
    text = state.asr_result.text if state.asr_result else state.user_request
    update = _trace(state, "intent", "intent_extracted", {"text": text})
    update["intent"] = text
    update["user_request"] = text
    return update


def memory_recall_node(state: TripPilotState,
                       memory_store: PreferenceStore | None) -> dict[str, Any]:
    preferences: list[dict[str, Any]] = []
    error = ""
    if memory_store is not None:
        try:
            user_id = state.user_attributes.get("user_id", "owner")
            query = state.intent or state.user_request
            preferences = [
                item.model_dump()
                for item in memory_store.recall(user_id, query, top_k=3)
            ]
        except Exception as exc:  # 记忆失败不应拦住导航主流程，但必须留痕。
            error = f"{type(exc).__name__}: {exc}"
    update = _trace(
        state,
        "memory_recall",
        "preferences_recalled",
        {"count": len(preferences), "error": error},
    )
    update["preferences"] = preferences
    return update


def _render_clarify_question(asr: ASRResult, low_confidence: bool) -> str:
    """生成只含人类可读选项、不泄露内部 reason code 的追问。"""
    names = [place.get("name", "") for place in asr.place_entities
             if place.get("name")]
    if len(names) > 1:
        options = "、".join(f"「{name}」" for name in names)
        question = f"你指的是{options}中的哪一个？"
        if low_confidence:
            return f"刚才没太听清，{question}"
        return question
    if len(names) == 1 and low_confidence:
        return f"刚才没太听清，你是说「{names[0]}」吗？"
    if low_confidence:
        return "刚才没太听清，能再说一遍吗？"
    return "没太确定你的意思，能再具体说一下吗？"


def _match_place_answer(asr: ASRResult, answer: object) -> str | None:
    """返回答案唯一对应的标准地点名；无匹配或仍有歧义时返回 None。"""
    text = str(answer).strip()
    if not text:
        return None
    names = [str(place.get("name", "")).strip()
             for place in asr.place_entities if place.get("name")]
    exact_matches = [name for name in names if text == name]
    if len(exact_matches) == 1:
        return exact_matches[0]
    partial_matches = [name for name in names
                       if text in name or name in text]
    if len(partial_matches) == 1:
        return partial_matches[0]
    return None


def clarify_node(state: TripPilotState) -> dict[str, Any]:
    asr = state.asr_result
    reasons: list[str] = []
    low_confidence = bool(asr and asr.confidence < 0.6)
    if asr and low_confidence:
        reasons.append(f"asr_low_confidence({asr.confidence:.2f})")
    if asr and len(asr.place_entities) > 1:
        names = [place.get("name", "?") for place in asr.place_entities]
        reasons.append(f"place_ambiguity({','.join(names)})")

    need_clarify = bool(reasons)
    update = _trace(
        state,
        "clarify",
        "clarify_checked",
        {"need_clarify": need_clarify, "reasons": reasons},
    )
    if not need_clarify:
        return update

    answer = state.trip_context.get("clarify_answer")
    place_ambiguous = bool(asr and len(asr.place_entities) > 1)
    matched_place = None
    if asr and place_ambiguous:
        matched_place = _match_place_answer(asr, answer)
        resolved = matched_place
    elif isinstance(answer, str) and answer.strip():
        resolved = answer.strip()
    else:
        resolved = None

    if not resolved:
        update["confirmation_state"] = "pending"
        update["final_response"] = _render_clarify_question(asr, low_confidence)
        update["stop_after_clarify"] = True
        return update

    trip_context = {**state.trip_context, "clarify_resolved": resolved}
    if matched_place:
        trip_context["destination"] = matched_place
    update["trip_context"] = trip_context
    update["stop_after_clarify"] = False
    return update


def planner_node(state: TripPilotState, llm: LLMClient) -> dict[str, Any]:
    started_at = time.time()
    preferences = [
        {"kind": item.get("kind", "other"),
         "content": item.get("content", "")}
        for item in state.preferences[:3]
    ]
    steps = llm.plan(
        intent=state.intent,
        context={"session_id": state.session_id,
                 **state.trip_context,
                 "preferences": preferences},
        available_tools=["map.route", "weather.now", "reminder.create",
                         "trip_log.append"],
    )
    plan = [
        PlanStep(
            step_id=step.get("step_id", f"s{index}"),
            tool=step.get("tool"),
            args=step.get("args", {}),
            description=step.get("description", ""),
        )
        for index, step in enumerate(steps)
    ]
    calls = [ToolCall(tool=step.tool, args=step.args, source="recorded")
             for step in plan if step.tool]
    update = _trace(state, "planner", "plan_created",
                    {"steps": [step.model_dump() for step in plan]})
    update["plan"] = plan
    update["pending_tool_calls"] = calls
    update["latency_breakdown"] = {
        **state.latency_breakdown,
        "planner": round(time.time() - started_at, 3),
    }
    return update


def policy_gate_node(state: TripPilotState) -> dict[str, Any]:
    decisions = [check_tool_call(call, state)
                 for call in state.pending_tool_calls]
    calls: list[ToolCall] = []
    degraded: list[dict[str, Any]] = []
    for call, decision in zip(state.pending_tool_calls, decisions):
        if decision.decision != "degrade":
            calls.append(call)
            continue
        args = dict(call.args)
        previous_count = args.get("option_count")
        args["option_count"] = 2
        calls.append(ToolCall(tool=call.tool, args=args, source=call.source))
        degraded.append({
            "tool": call.tool,
            "parameter": "option_count",
            "from": previous_count,
            "to": 2,
        })

    update = _trace(state, "policy_gate", "decisions_made",
                    {"decisions": [item.model_dump() for item in decisions]})
    if degraded:
        update["trace"].append(TraceEvent(
            node="policy_gate",
            event="degraded_params",
            payload={"changes": degraded},
        ))
    update["policy_decisions"] = decisions
    update["pending_tool_calls"] = calls
    decision_kinds = {item.decision for item in decisions}
    if "deny" in decision_kinds:
        update["route_after_policy"] = "deny"
        update["denied_tools"] = [
            call.tool
            for call, decision in zip(state.pending_tool_calls, decisions)
            if decision.decision == "deny"
        ]
    elif "confirm" in decision_kinds:
        update["route_after_policy"] = "confirm"
    else:
        update["route_after_policy"] = "allow"
    return update


def memory_confirm_id(candidate: dict[str, Any]) -> str:
    raw = f"{candidate.get('kind', 'other')}{candidate['content']}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def human_confirm_node(state: TripPilotState) -> dict[str, Any]:
    confirmed = state.trip_context.get("confirm", False)
    confirmed_ids: list[str] = []
    if confirmed:
        confirmed_ids = [memory_confirm_id(item)
                         for item in state.pending_memory_confirms]
    update = _trace(
        state,
        "human_confirm",
        "confirmed" if confirmed else "awaiting_user",
        {"confirmed": confirmed, "confirmed_ids": confirmed_ids},
    )
    if confirmed:
        update["confirmation_state"] = "confirmed"
        return update

    update["confirmation_state"] = "pending"
    reasons = [item.reason_code for item in state.policy_decisions
               if item.decision == "confirm"]
    details = "；".join(item.detail for item in state.policy_decisions
                         if item.decision == "confirm")
    update["final_response"] = "需要你确认：" + (details or "；".join(reasons))
    update["stop_after_confirm"] = True
    return update


ToolLookup = Callable[[str], Any]


def tool_executor_node(state: TripPilotState,
                       tool_lookup: ToolLookup = get_tool) -> dict[str, Any]:
    results: list[ToolResult] = []
    calls: list[ToolCall] = []
    latency = dict(state.latency_breakdown)
    for call in state.pending_tool_calls:
        started_at = time.time()
        try:
            result = tool_lookup(call.tool).run(call)
            if scan_tool_text(str(result.data)):
                result = ToolResult(
                    tool=result.tool,
                    ok=False,
                    source=result.source,
                    error=("tool_text_injection_suspected: "
                           "工具返回含可疑指令，视为不可信数据，已拦截"),
                )
        except ToolError as exc:
            result = ToolResult(tool=call.tool, ok=False,
                                source=call.source, error=str(exc))
        results.append(result)
        calls.append(call)
        latency[f"tool:{call.tool}"] = round(time.time() - started_at, 3)
    update = _trace(state, "tool_executor", "tools_executed",
                    {"results": [item.model_dump() for item in results]})
    update["tool_results"] = [*state.tool_results, *results]
    update["tool_calls"] = [*state.tool_calls, *calls]
    update["pending_tool_calls"] = []
    update["latency_breakdown"] = latency
    return update


def _verification_failures(state: TripPilotState) -> tuple[list[str],
                                                               list[str],
                                                               PolicyDecision | None]:
    safety_failures: list[str] = []
    task_failures: list[str] = []
    executed_tools = {call.tool for call in state.tool_calls}
    for tool_name in state.denied_tools:
        if tool_name in executed_tools:
            safety_failures.append(f"forbidden_action_executed:{tool_name}")

    denied = next((item for item in state.policy_decisions
                   if item.decision == "deny"), None)
    if denied is not None:
        task_failures.append(f"policy_denied:{denied.reason_code}")

    criteria = state.trip_context.get("success_criteria", {})
    for result in state.tool_results:
        if not result.ok and not criteria.get("allow_tool_failure"):
            task_failures.append(
                f"tool_failed:{result.tool}:{result.error}")
    needs_confirmation = any(item.decision == "confirm"
                             for item in state.policy_decisions)
    if needs_confirmation and state.confirmation_state != "confirmed":
        safety_failures.append("confirmation_missing")
        task_failures.append("confirmation_missing")
    return safety_failures, task_failures, denied


def verifier_node(state: TripPilotState, llm: LLMClient) -> dict[str, Any]:
    safety_failures, task_failures, denied = _verification_failures(state)
    failures = [*safety_failures, *task_failures]
    safe = not safety_failures
    task_completed = not task_failures
    if safe and task_completed:
        event = "verified_ok"
    elif safe:
        event = "verified_safe"
    else:
        event = "verified_fail"

    update = _trace(state, "verifier", event, {
        "failures": failures,
        "safety_failures": safety_failures,
        "task_failures": task_failures,
        "task_completed": task_completed,
    })
    update["verification_result"] = {
        "ok": safe,
        "task_completed": task_completed,
        "failures": failures,
        "safety_failures": safety_failures,
        "task_failures": task_failures,
    }
    if denied is not None:
        detail = denied.detail or "该操作不符合当前安全策略"
        update["final_response"] = (
            f"这个操作被安全策略拦下了：{detail}"
            f"（原因代码：{denied.reason_code}）"
        )
    elif safe and task_completed:
        update["final_response"] = llm.final_answer(state_summary={
            "tool_results": [item.model_dump() for item in state.tool_results],
        })
    else:
        update["final_response"] = "这次没能安全完成：" + "；".join(failures)
    return update


def _insert_candidate(store: PreferenceStore, user_id: str,
                      candidate: dict[str, Any]) -> str:
    return store._insert(
        user_id,
        candidate["content"],
        candidate.get("kind", "other"),
        candidate.get("sensitivity", "normal"),
        candidate.get("source_type", "chat"),
    )


def _capture_candidate(store: PreferenceStore, user_id: str,
                       candidate: dict[str, Any],
                       confirmed: bool) -> tuple[str, str | None]:
    status, memory_id = store.remember(
        user_id=user_id,
        content=candidate["content"],
        kind=candidate.get("kind", "other"),
        sensitivity=candidate.get("sensitivity", "normal"),
        source_type=candidate.get("source_type", "chat"),
        is_transient=candidate.get("is_transient", False),
    )
    if status == "written":
        return "written", memory_id
    if confirmed:
        return "written", _insert_candidate(store, user_id, candidate)
    return "pending", None


def _mark_pending(pending: list[dict[str, Any]],
                  decisions: list[PolicyDecision],
                  candidate: dict[str, Any]) -> None:
    pending.append(candidate)
    decisions.append(PolicyDecision(
        decision="confirm",
        reason_code="sensitive_memory_needs_confirm",
        detail=f"记住这条偏好吗？「{candidate['content']}」（可随时删除）",
    ))


def _prepare_memory_candidates(
        state: TripPilotState,
        user_id: str) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    candidates = list(state.memory_candidates)
    trip_context = dict(state.trip_context)
    if candidates or trip_context.get("extracted_from") == state.user_request:
        return candidates, trip_context, 0
    candidates = extract_candidates(
        state.user_request,
        {"user_id": user_id, "session_id": state.session_id},
    )
    trip_context["extracted_from"] = state.user_request
    return candidates, trip_context, len(candidates)


def _write_memory_candidates(
        state: TripPilotState,
        store: PreferenceStore,
        user_id: str,
        candidates: list[dict[str, Any]]) -> tuple[list[str], list[str],
                                                    list[str], list[dict[str, Any]],
                                                    list[PolicyDecision]]:
    written: list[str] = []
    written_confirm_ids: list[str] = []
    rejected: list[str] = []
    pending: list[dict[str, Any]] = []
    decisions = list(state.policy_decisions)
    confirmed = state.confirmation_state == "confirmed"

    if confirmed:
        for candidate in state.pending_memory_confirms:
            written.append(_insert_candidate(store, user_id, candidate))
            written_confirm_ids.append(memory_confirm_id(candidate))
    else:
        for candidate in state.pending_memory_confirms:
            _mark_pending(pending, decisions, candidate)

    for candidate in candidates:
        try:
            status, memory_id = _capture_candidate(
                store, user_id, candidate, confirmed)
        except MemoryRejected as exc:
            rejected.append(exc.reason_code)
            continue
        if status == "written":
            written.append(memory_id or "")
        else:
            _mark_pending(pending, decisions, candidate)
    return written, written_confirm_ids, rejected, pending, decisions


def memory_capture_node(state: TripPilotState,
                        memory_store: PreferenceStore | None) -> dict[str, Any]:
    written: list[str] = []
    written_confirm_ids: list[str] = []
    rejected: list[str] = []
    pending: list[dict[str, Any]] = []
    decisions = list(state.policy_decisions)
    trip_context = dict(state.trip_context)
    extracted = 0
    if memory_store is not None:
        user_id = state.user_attributes.get("user_id", "owner")
        candidates, trip_context, extracted = _prepare_memory_candidates(
            state, user_id)
        result = _write_memory_candidates(
            state, memory_store, user_id, candidates)
        written, written_confirm_ids, rejected, pending, decisions = result

    if written:
        event = "memories_written"
    elif pending:
        event = "confirm_needed"
    else:
        event = "nothing_to_capture"
    update = _trace(state, "memory_capture", event, {
        "written": written,
        "rejected": rejected,
        "pending": len(pending),
        "pending_confirm_ids": [memory_confirm_id(item) for item in pending],
        "written_confirm_ids": written_confirm_ids,
        "extracted": extracted,
    })
    update["policy_decisions"] = decisions
    update["trip_context"] = trip_context
    update["memory_candidates"] = []
    update["pending_memory_confirms"] = pending
    return update


def recovery_node(state: TripPilotState) -> dict[str, Any]:
    result_count = len(state.tool_results)
    recent_calls = state.tool_calls[-result_count:] if result_count else []
    retries: list[ToolCall] = []
    changes: list[dict[str, Any]] = []
    fixture_fallback_tools = {
        "map.route", "map.search", "weather.now", "weather.forecast",
    }
    for call, result in zip(recent_calls, state.tool_results):
        if result.ok:
            continue
        args = dict(call.args)
        if call.tool in fixture_fallback_tools and "fixture" in args:
            previous_fixture = args["fixture"]
            fallback = fixture_key_for(call.tool.split(".")[0], args)
            if fallback != previous_fixture:
                args["fixture"] = fallback
                changes.append({
                    "tool": call.tool,
                    "parameter": "fixture",
                    "from": previous_fixture,
                    "to": fallback,
                })
        retries.append(ToolCall(tool=call.tool, args=args, source=call.source))
    update = _trace(state, "recovery", "retry_once", {
        "recovery_count": state.recovery_count + 1,
        "changes": changes,
    })
    update["pending_tool_calls"] = retries
    update["recovery_count"] = state.recovery_count + 1
    update["tool_results"] = []
    return update
