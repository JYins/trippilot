"""FastAPI Session API（单用户本地模式，无认证）。

- 会话管理 / trace_id / 请求校验 / 脱敏日志
- 所有请求固定使用 local 记忆命名空间，不提供多用户身份保证
- 请求原文不落日志，其他日志文本统一遮盖手机号、生日和地址
"""

from __future__ import annotations

import logging
import re
import secrets
from contextlib import asynccontextmanager
from copy import deepcopy
from hashlib import sha1
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .graph import build_graph, new_state, run_graph
from .llm import make_llm
from .memory.store import DEFAULT_PATH, PreferenceStore
from .state import ASRResult, VehicleState

_graph = None
_memory_store: PreferenceStore | None = None
_pending_memory_confirms: dict[str, dict[str, dict[str, Any]]] = {}
_pending_memory_lock = Lock()


def close_graph_resources() -> None:
    global _graph, _memory_store
    if _memory_store is not None:
        _memory_store.close()
    _memory_store = None
    _graph = None


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    try:
        yield
    finally:
        close_graph_resources()


log = logging.getLogger("trippilot.api")

app = FastAPI(title="TripPilot 途行智驾", version="0.1.0",
              lifespan=_app_lifespan)

LOCAL_USER_ATTRIBUTES = {
    "user_id": "local",
    "authenticated": False,
    "role": "local",
}


def get_graph():
    global _graph, _memory_store
    if _graph is None:
        _memory_store = PreferenceStore(DEFAULT_PATH)
        _graph = build_graph(make_llm(), memory_store=_memory_store)
    return _graph


_SENSITIVE_KEYS = {"home_address", "exact_address", "contact", "phone",
                   "reminder_content", "calendar_body", "content"}
_PII_PATTERNS = (
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "***phone***"),
    (re.compile(
        r"(?:生日(?:是|为|[:：])?|出生日期(?:是|为|[:：])?)\s*"
        r"(?:19|20)\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?"
    ), "***birthday***"),
    (re.compile(
        r"(?:我家住在|住址(?:是|为|[:：])?|地址(?:是|为|[:：])?)"
        r"[^，。；;\n]{2,}"
    ), "***address***"),
    (re.compile(
        r"[\u4e00-\u9fff]{2,}(?:省|市|区|县|街道|路|街|巷|小区|大厦)"
        r"[^，。；;\n]*"
    ), "***address***"),
)


def _redact_text(value: str) -> str:
    redacted = value
    for pattern, replacement in _PII_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if k in _SENSITIVE_KEYS else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    if isinstance(obj, str):
        return _redact_text(obj)
    return obj


def _request_log(req: "TurnRequest") -> dict[str, Any]:
    payload = req.model_dump()
    for field_name in ("text", "asr_text"):
        value = payload[field_name]
        payload[field_name] = {
            "length": len(value),
            "sha1": sha1(value.encode("utf-8")).hexdigest()[:12],
        }
    return _redact(payload)


class TurnRequest(BaseModel):
    session_id: str | None = None
    text: str = ""                      # 文本输入
    asr_text: str = ""                  # 或语音转写文本
    asr_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    asr_measured: bool = False          # 是否真实录音测得
    vehicle_state: VehicleState = "parked_simulated"
    trip_context: dict[str, Any] = Field(default_factory=dict)
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
    trace: list[dict[str, Any]] | None = None


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
        # 合并而非覆盖：同一 session 可能有多轮未确认的候选，
        # 覆盖会让旧 nonce 的快照丢失、用户永远确认不了
        existing = _pending_memory_confirms.get(session_id, {})
        existing.update(saved)
        _pending_memory_confirms[session_id] = existing
    return response_items


@app.post("/turn", response_model=TurnResponse)
def turn(req: TurnRequest) -> TurnResponse:
    log.info("turn request: %s", _request_log(req))
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
                      vehicle_state=req.vehicle_state,
                      trip_context=ctx,
                      pending_memory_confirms=restored_candidates,
                      user_attributes=dict(LOCAL_USER_ATTRIBUTES))
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
        pending_memory_confirms=pending_memory,
        trace=[event.model_dump() for event in getattr(out, "trace", None) or []])
    log.info("turn response: %s", _redact(
        resp.model_dump(exclude={"trace"})))
    return resp


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "version": "0.1.0",
        "mode": "single_user_local",
        "authentication": "none",
    }


WEB_DIR = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
