"""FastAPI Session API。

- 会话管理 / trace_id / 请求校验 / 脱敏日志
- 日志中不记录精确家庭住址、提醒正文等敏感字段（见 _redact）
"""

from __future__ import annotations

import logging
import re
import secrets
from copy import deepcopy
from hashlib import sha1
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .graph import build_graph, new_state, run_graph
from .llm import make_llm
from .memory.store import DEFAULT_PATH, PreferenceStore
from .state import ASRResult

log = logging.getLogger("trippilot.api")

app = FastAPI(title="TripPilot 途行智驾", version="0.1.0")

_graph = None
_pending_memory_confirms: dict[str, dict[str, dict[str, Any]]] = {}
_pending_memory_lock = Lock()


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
    confirm_nonce: str | None = None
    clarify_answer: str = ""            # 用户澄清回答回填


class PendingMemoryConfirm(BaseModel):
    nonce: str
    content_sha1: str
    content: str


class TurnResponse(BaseModel):
    session_id: str
    trace_id: str
    final_response: str
    confirmation_state: str
    needs_user_input: bool
    visited_nodes: list[str]
    policy_decisions: list[dict[str, Any]]
    verification: dict[str, Any]
    pending_memory_confirms: list[PendingMemoryConfirm]


def _take_pending_memory(session_id: str | None,
                         nonce: str) -> dict[str, Any]:
    with _pending_memory_lock:
        session_pending = _pending_memory_confirms.get(session_id or "")
        candidate = session_pending.pop(nonce, None) if session_pending else None
        if session_pending == {}:
            _pending_memory_confirms.pop(session_id or "", None)
    if candidate is None:
        raise HTTPException(status_code=400,
                            detail="确认凭证无效或已使用")
    return candidate


def _save_pending_memory(session_id: str,
                         candidates: list[dict[str, Any]]) -> list[PendingMemoryConfirm]:
    saved: dict[str, dict[str, Any]] = {}
    response_items: list[PendingMemoryConfirm] = []
    for candidate in candidates:
        content = str(candidate.get("content", ""))
        nonce = secrets.token_urlsafe(24)
        saved[nonce] = deepcopy(candidate)
        response_items.append(PendingMemoryConfirm(
            nonce=nonce,
            content_sha1=sha1(content.encode("utf-8")).hexdigest(),
            content=content,
        ))
    with _pending_memory_lock:
        _pending_memory_confirms[session_id] = saved
    return response_items


@app.post("/turn", response_model=TurnResponse)
def turn(req: TurnRequest) -> TurnResponse:
    log.info("turn request: %s", _redact(req.model_dump()))
    restored_candidates: list[dict[str, Any]] = []
    if req.confirm:
        if not req.confirm_nonce:
            raise HTTPException(status_code=400,
                                detail="确认请求缺少 confirm_nonce")
        restored_candidates.append(
            _take_pending_memory(req.session_id, req.confirm_nonce))
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
                      pending_memory_confirms=restored_candidates,
                      user_attributes={"user_id": "owner", "authenticated": True,
                                       "role": "owner"})
    out = run_graph(get_graph(), state)
    needs_input = out.confirmation_state == "pending" or out.stop_after_clarify
    pending_memory = []
    if out.pending_memory_confirms:
        pending_memory = _save_pending_memory(
            out.session_id, out.pending_memory_confirms)
    resp = TurnResponse(
        session_id=out.session_id, trace_id=out.trace_id,
        final_response=out.final_response,
        confirmation_state=out.confirmation_state,
        needs_user_input=needs_input,
        visited_nodes=out.visited_nodes,
        policy_decisions=[d.model_dump() for d in out.policy_decisions],
        verification=out.verification_result,
        pending_memory_confirms=pending_memory)
    log.info("turn response: %s", _redact(resp.model_dump()))
    return resp


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0"}
