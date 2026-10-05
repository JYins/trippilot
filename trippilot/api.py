"""FastAPI Session API。

- 会话管理 / trace_id / 请求校验 / 脱敏日志
- 日志中不记录精确家庭住址、提醒正文等敏感字段（见 _redact）
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from .graph import build_graph, new_state, run_graph
from .llm import make_llm
from .memory.store import DEFAULT_PATH, PreferenceStore
from .state import ASRResult

log = logging.getLogger("trippilot.api")

app = FastAPI(title="TripPilot 途行智驾", version="0.1.0")

_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph(make_llm(),
                             memory_store=PreferenceStore(DEFAULT_PATH))
    return _graph


# ---------------------------------------------------------------------------
# 脱敏
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS = {"home_address", "exact_address", "contact", "phone",
                   "reminder_content", "calendar_body",
                   "content"}  # 记忆偏好正文（可能含家庭住址），日志里脱敏


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if k in _SENSITIVE_KEYS else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, str) and re.search(r"[\u4e00-\u9fff]{2,}.*\d+号", obj):
        return "***address***"
    return obj


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------

class TurnRequest(BaseModel):
    session_id: str | None = None
    text: str = ""                      # 文本输入
    asr_text: str = ""                  # 或语音转写文本
    asr_confidence: float = 1.0
    asr_measured: bool = False          # 是否真实录音测得
    vehicle_state: str = "parked_simulated"
    trip_context: dict[str, Any] = {}
    confirm: bool = False               # 用户确认（human_confirm 回填）
    clarify_answer: str = ""            # 用户澄清回答回填
    pending_memory_confirms: list[dict[str, Any]] = []  # 上一轮挂起的敏感偏好，客户端原样回传


class TurnResponse(BaseModel):
    session_id: str
    trace_id: str
    final_response: str
    confirmation_state: str
    needs_user_input: bool
    visited_nodes: list[str]
    policy_decisions: list[dict[str, Any]]
    verification: dict[str, Any]
    pending_memory_confirms: list[dict[str, Any]]  # 本轮挂起的敏感偏好，客户端下轮回传


@app.post("/turn", response_model=TurnResponse)
def turn(req: TurnRequest) -> TurnResponse:
    log.info("turn request: %s", _redact(req.model_dump()))
    asr = None
    if req.asr_text or req.text:
        asr = ASRResult(text=req.asr_text or req.text,
                        confidence=req.asr_confidence,
                        measured=req.asr_measured)
    ctx = dict(req.trip_context)
    if req.confirm:
        ctx["confirm"] = True
    if req.clarify_answer:
        ctx["clarify_answer"] = req.clarify_answer

    state = new_state(session_id=req.session_id,
                      user_request=req.text or req.asr_text,
                      asr_result=asr,
                      vehicle_state=req.vehicle_state,  # type: ignore
                      trip_context=ctx,
                      pending_memory_confirms=req.pending_memory_confirms,
                      user_attributes={"user_id": "owner", "authenticated": True,
                                       "role": "owner"})
    out = run_graph(get_graph(), state)
    needs_input = out.confirmation_state == "pending" or out.stop_after_clarify
    resp = TurnResponse(
        session_id=out.session_id, trace_id=out.trace_id,
        final_response=out.final_response,
        confirmation_state=out.confirmation_state,
        needs_user_input=needs_input,
        visited_nodes=out.visited_nodes,
        policy_decisions=[d.model_dump() for d in out.policy_decisions],
        verification=out.verification_result,
        pending_memory_confirms=out.pending_memory_confirms)
    log.info("turn response: %s", _redact(resp.model_dump()))
    return resp


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}
