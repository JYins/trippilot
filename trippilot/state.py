"""TripPilot state schema.

显式 Pydantic 状态对象：每个节点只能修改声明过的字段，
关键状态变化写入 append-only trace，避免只保存最终回答。

注意：vehicle_state 全部为纯软件模拟（simulated），
不宣称来源于真实车辆硬件。报告中统一标注 simulated。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 车辆状态：纯软件模拟，由测试面板 / 场景 fixture 注入
VehicleState = Literal["parked_simulated", "slow_simulated", "driving_simulated"]

class ASRResult(BaseModel):
    text: str
    confidence: float = Field(ge=0.0, le=1.0)
    # 地点 / 时间实体：由地点实体检查程序真实输出
    place_entities: list[dict[str, Any]] = Field(default_factory=list)
    time_entities: list[dict[str, Any]] = Field(default_factory=list)
    # 是否真实测得（真实录音）还是模拟输入
    measured: bool = False


class PlanStep(BaseModel):
    step_id: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    description: str = ""


class ToolCall(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    # 双轨标记：recorded / live_manual
    source: Literal["recorded", "live_manual"] = "recorded"


class ToolResult(BaseModel):
    tool: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    source: Literal["recorded", "live_manual"] = "recorded"


class PolicyDecision(BaseModel):
    decision: Literal["allow", "deny", "confirm", "degrade"]
    reason_code: str
    detail: str = ""


class TraceEvent(BaseModel):
    node: str
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TripPilotState(BaseModel):
    trace_id: str
    session_id: str
    user_request: str = ""
    asr_result: ASRResult | None = None
    intent: str = ""
    trip_context: dict[str, Any] = Field(default_factory=dict)
    # 纯软件模拟，见文件头注释
    vehicle_state: VehicleState = "parked_simulated"
    user_attributes: dict[str, Any] = Field(default_factory=dict)
    plan: list[PlanStep] = Field(default_factory=list)
    pending_tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)
    confirmation_state: Literal["not_required", "pending", "confirmed", "rejected"] = "not_required"
    # 工具类确认已授予，只覆盖工具调用，不覆盖记忆写入
    tool_call_confirmed: bool = False
    tool_results: list[ToolResult] = Field(default_factory=list)
    verification_result: dict[str, Any] = Field(default_factory=dict)
    recovery_count: int = 0
    memory_candidates: list[dict[str, Any]] = Field(default_factory=list)
    # memory_capture 挂起的敏感偏好：走现有 human_confirm 流程，确认后才写
    pending_memory_confirms: list[dict[str, Any]] = Field(default_factory=list)
    # 已授予的记忆写入确认 ID；memory_confirm_id 粒度，只对当时挂起的候选有效
    confirmed_memory_ids: list[str] = Field(default_factory=list)
    # memory_recall 召回的偏好（Preference.model_dump()），供 planner/回答参考
    preferences: list[dict[str, Any]] = Field(default_factory=list)
    final_response: str = ""
    errors: list[str] = Field(default_factory=list)
    latency_breakdown: dict[str, float] = Field(default_factory=dict)
    # append-only 轨迹：节点事件按发生顺序追加，不修改历史
    trace: list[TraceEvent] = Field(default_factory=list)
    # 实际经过的节点名（eval 用 required_nodes 校验）
    visited_nodes: list[str] = Field(default_factory=list)
    # 内部路由标记（图执行用）
    stop_after_clarify: bool = False
    stop_after_confirm: bool = False
    route_after_policy: str = "allow"  # allow / confirm / deny
    denied_tools: list[str] = Field(default_factory=list)
