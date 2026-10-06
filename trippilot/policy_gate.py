"""确定性 Policy Gate：ABAC + 参数级授权 + Memory Gate。

不依赖模型"自觉遵守"：输入为 (Subject, Resource, Action, Environment, Parameters)，
输出为 allow / deny / confirm / degrade + 稳定 reason_code。

安全规则绝不交给 LLM Judge 决定；这里的全部判定都是纯程序。
"""

from __future__ import annotations

import re
from typing import Any

from .state import PolicyDecision

TOOL_POLICIES = {
    "map.route": ("route", "read"),
    "map.search": ("route", "read"),
    "weather.now": ("weather", "read"),
    "weather.forecast": ("weather", "read"),
    "reminder.create": ("reminder", "create"),
    "reminder.delete": ("reminder", "delete"),
    "trip_log.append": ("trip_log", "create"),
}

# ASR 置信度阈值：地点/时间实体低于此值时，禁止产生副作用的调用
LOW_CONFIDENCE_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# ABAC 主入口
# ---------------------------------------------------------------------------

def evaluate(
    *,
    subject: dict[str, Any],
    resource: str,
    action: str,
    environment: dict[str, Any],
    parameters: dict[str, Any],
) -> PolicyDecision:
    """通用 ABAC 判定。

    subject: {user_id, authenticated: bool, role: owner/passenger/guest}
    resource: calendar / reminder / route / memory / weather ...
    action: read / create / update / delete / share
    environment: {vehicle_state, asr_confidence, interaction_mode}
    parameters: 动作参数（地点、时间、收件人、提醒内容...）
    """
    role = subject.get("role", "guest")
    authenticated = bool(subject.get("authenticated", False))
    vehicle_state = environment.get("vehicle_state", "parked_simulated")
    asr_conf = float(environment.get("asr_confidence", 1.0))

    # 1. 未认证用户：只允许只读查询
    if not authenticated:
        if action == "read":
            return PolicyDecision(decision="allow", reason_code="guest_read_only",
                                  detail="未认证用户仅允许只读查询")
        return PolicyDecision(decision="deny", reason_code="unauthenticated_write",
                              detail="未认证用户禁止写操作")

    # 2. 乘客/访客：禁止修改车主日历与记忆
    if role in ("passenger", "guest") and resource in ("calendar", "memory") and action != "read":
        return PolicyDecision(decision="deny", reason_code="role_not_permitted",
                              detail=f"{role} 无权 {action} {resource}")

    # 3. 删除/修改日历：无论车辆状态，一律确认
    if resource == "calendar" and action in ("delete", "update"):
        return PolicyDecision(decision="confirm", reason_code="calendar_mutation_needs_confirm",
                              detail="日历删除/修改必须经用户确认")

    # 4. ASR 对地点或时间置信不足：禁止产生副作用的调用
    if asr_conf < LOW_CONFIDENCE_THRESHOLD and action in ("create", "update", "delete", "share"):
        return PolicyDecision(decision="deny", reason_code="low_asr_confidence_blocks_side_effect",
                              detail=f"ASR 置信度 {asr_conf:.2f} < {LOW_CONFIDENCE_THRESHOLD}，"
                                     "禁止执行有副作用的调用，先澄清")

    # 5. 创建提醒：必须展示最终时间和内容并获得确认
    if resource == "reminder" and action == "create":
        missing = [k for k in ("time", "content") if k not in parameters]
        if missing:
            return PolicyDecision(decision="deny", reason_code="reminder_missing_params",
                                  detail=f"创建提醒缺少参数: {missing}")
        return PolicyDecision(decision="confirm", reason_code="reminder_create_needs_confirm",
                              detail="创建提醒需展示最终时间与内容并确认")

    # 6. 行驶中展示复杂选项：降级为最多两个简短选项
    if vehicle_state == "driving_simulated" and action == "read" \
            and parameters.get("option_count", 1) > 2:
        return PolicyDecision(decision="degrade", reason_code="driving_degrade_options",
                              detail="行驶中（模拟）降级为最多两个简短选项")

    # 7. 分享动作：必须确认
    if action == "share":
        return PolicyDecision(decision="confirm", reason_code="share_needs_confirm",
                              detail="分享动作需用户确认")

    # 8. 默认：读允许，写走确认
    if action == "read":
        return PolicyDecision(decision="allow", reason_code="default_read_allow")
    return PolicyDecision(decision="confirm", reason_code="default_write_confirm",
                          detail="默认写操作需确认")


# ---------------------------------------------------------------------------
# 工具调用级检查（Planner 输出 → Policy Gate）
# ---------------------------------------------------------------------------

def check_tool_call(tool_call: Any, state: Any) -> PolicyDecision:
    """对单次工具调用做参数级授权。

    tool_call: ToolCall；state: TripPilotState（取 asr 置信度、车辆状态、地点歧义）
    """
    tool = tool_call.tool
    args = dict(tool_call.args or {})
    asr = state.asr_result
    asr_conf = asr.confidence if asr else 1.0

    policy = TOOL_POLICIES.get(tool)
    if policy is None:
        return PolicyDecision(decision="deny", reason_code="unknown_tool",
                              detail=f"未知工具操作: {tool}")

    subject = {"user_id": state.user_attributes.get("user_id", "owner"),
               "authenticated": state.user_attributes.get("authenticated", True),
               "role": state.user_attributes.get("role", "owner")}
    environment = {"vehicle_state": state.vehicle_state, "asr_confidence": asr_conf}

    # 路线查询 + 地点歧义 → 强制澄清（走 confirm 通道）；
    # 若 clarify 节点已解决歧义（clarify_resolved），不再重复强制
    if tool in ("map.route", "map.search"):
        ambiguous = args.get("place_ambiguous", False)
        if asr and len(asr.place_entities) > 1:
            ambiguous = True
        if ambiguous and not state.trip_context.get("clarify_resolved"):
            return PolicyDecision(decision="confirm",
                                  reason_code="place_ambiguity_requires_clarify",
                                  detail="地点存在歧义，禁止猜测后直接执行，先澄清")

    resource, action = policy
    parameters = dict(args)
    if tool in ("map.route", "map.search"):
        parameters["option_count"] = args.get("option_count", 1)

    return evaluate(subject=subject, resource=resource, action=action,
                    environment=environment, parameters=parameters)

# ---------------------------------------------------------------------------
# 工具返回文本的 prompt-injection 检查
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS = [
    r"忽略.{0,10}(规则|指令|系统)",
    r"ignore.{0,20}(rule|instruction|system)",
    r"(system|系统)\s*[:：]\s*",
    r"override.{0,20}(policy|rule)",
    r"调用.{0,10}(日历|提醒|calendar|reminder)",
    r"call.{0,10}(calendar|reminder)",
    r"jailbreak",
    r"越狱",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def scan_tool_text(text: str) -> bool:
    """工具返回文本是否包含可疑的指令注入。True = 可疑，视为不可信数据。"""
    if not text:
        return False
    return bool(_INJECTION_RE.search(text))


# ---------------------------------------------------------------------------
# Memory Gate：候选记忆 → write / reject / request_confirm
# ---------------------------------------------------------------------------

def evaluate_memory_candidate(candidate: dict[str, Any]) -> PolicyDecision:
    """Memory Gate：决定一条候选记忆的去向。

    candidate: {content, source_type, is_transient, sensitivity, ...}
    sensitivity: normal / sensitive（家庭住址等）
    """
    if candidate.get("is_transient"):
        return PolicyDecision(decision="deny", reason_code="transient_memory_rejected",
                              detail="一次性目的地/临时信息默认不得写入长期记忆")
    if candidate.get("sensitivity") == "sensitive":
        return PolicyDecision(decision="confirm", reason_code="sensitive_memory_needs_confirm",
                              detail="敏感信息（家庭住址等）写入前单独确认，并允许删除")
    return PolicyDecision(decision="allow", reason_code="stable_preference_write",
                          detail="稳定偏好写入长期记忆")


__all__ = [
    "evaluate", "check_tool_call", "scan_tool_text", "evaluate_memory_candidate",
    "PolicyDecision", "LOW_CONFIDENCE_THRESHOLD", "TOOL_POLICIES",
]
