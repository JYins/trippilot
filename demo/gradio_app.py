from __future__ import annotations

import html
import logging
import sys
from functools import partial
from pathlib import Path
from typing import Any

import gradio as gr
from fastapi import HTTPException

# 本地按文件执行时 demo/ 会成为导入根；补仓库根才能找到相邻的 trippilot 包。
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
if (REPOSITORY_ROOT / "trippilot").is_dir():
    sys.path.insert(0, str(REPOSITORY_ROOT))

from trippilot.api import TurnRequest, TurnResponse, turn


log = logging.getLogger("trippilot.demo")

NODE_LABELS = {
    "intent": "意图识别",
    "clarify": "信息澄清",
    "planner": "任务规划",
    "policy_gate": "安全策略",
    "human_confirm": "用户确认",
    "tool_executor": "工具执行",
    "verifier": "结果验证",
    "memory_recall": "偏好召回",
    "memory_capture": "偏好记录",
    "recovery": "失败恢复",
}
MAIN_NODES = [
    "intent", "clarify", "planner", "policy_gate", "tool_executor", "verifier",
]
AUXILIARY_NODES = [
    "memory_recall", "human_confirm", "memory_capture", "recovery",
]
SCENARIO_GROUPS = {
    "导航": [
        "带我去798",
        "去三里屯",
        "去亦庄，避开拥堵",
        "还有多远",
        "去学院路，几点能到",
        "去机场",
        "下周想开车去乌兰察布，帮我规划一下",
        "去亦庄",
    ],
    "天气": ["一会儿去潮白河，天气怎么样"],
    "限号": [
        "今天限号吗，我要去菜市口",
        "去三里屯，顺便帮我看看今天限号吗",
    ],
    "媒体": ["换首歌"],
    "车控": ["有点热，空调开大点，顺便把天窗打开，音量调小点"],
    "知识": ["798为什么叫798"],
    "澄清": ["去那个河边"],
}

BADGES = """
<div class="honesty-badges" aria-label="数据与环境说明">
  <span>座舱状态·模拟</span>
  <span>地图天气·录制数据</span>
  <span>无需真实车辆</span>
</div>
"""

CSS = """
.gradio-container { max-width: 1180px !important; margin: 0 auto !important; }
.honesty-badges { position: sticky; top: 0; z-index: 10; display: flex; flex-wrap: wrap;
  gap: 8px; padding: 12px 0; background: var(--background-fill-primary); }
.honesty-badges span { border: 1px solid #b8cec2; border-radius: 999px; padding: 6px 11px;
  color: #285441; background: #f7fbf8; font-size: 12px; font-weight: 650; }
.scenario-label { margin: 8px 0 2px; color: #607169; font-size: 12px; font-weight: 700; }
.scenario-button { min-width: 0 !important; text-align: left !important; }
.timeline { color: #24332c; }
.timeline-empty { padding: 48px 18px; color: #718078; text-align: center; }
.timeline-group + .timeline-group { margin-top: 18px; padding-top: 16px;
  border-top: 1px solid #e1e8e4; }
.timeline-group h3 { margin: 0 0 10px; color: #68776f; font-size: 12px; }
.timeline-nodes { display: flex; flex-wrap: wrap; gap: 8px; }
.timeline details { min-width: 210px; flex: 1 1 220px; border: 1px solid #cfd8d3;
  border-radius: 12px; background: #fff; overflow: hidden; }
.timeline details.visited { border-color: #287457; background: #f5fbf7; }
.timeline details.blocked { border-color: #b05235; background: #fff8f5; }
.timeline summary { cursor: pointer; padding: 10px 12px; color: #7a8580; font-size: 12px; }
.timeline details.visited summary { color: #246348; font-weight: 700; }
.timeline details.blocked summary { color: #9e442a; font-weight: 700; }
.timeline code { color: inherit; font-size: 10px; opacity: .75; }
.timeline ul { margin: 0; padding: 0 14px 13px 30px; color: #4e5f57;
  font-size: 12px; line-height: 1.65; }
"""


def _decision_name(decision: str) -> str:
    return {
        "allow": "允许",
        "deny": "拦截",
        "confirm": "需确认",
        "degrade": "降级",
    }.get(decision, decision)


def _describe_policy(decisions: list[dict[str, Any]]) -> str:
    if not decisions:
        return "输出：没有工具调用需要策略判断。"
    parts = []
    for item in decisions:
        reason = item.get("detail") or f"规则 {item.get('reason_code', '未知')}"
        parts.append(f"{_decision_name(str(item.get('decision', '未知')))}：{reason}")
    return f"输出：{'；'.join(parts)}。"


def _describe_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "输出：未执行工具调用。"
    parts = []
    for item in results:
        tool = item.get("tool", "未知工具")
        if item.get("ok"):
            result = "成功"
        else:
            result = f"失败（{item.get('error') or '未返回原因'}）"
        parts.append(f"{tool} {result}")
    return f"输出：{'；'.join(parts)}。"


def _describe_verification(verification: dict[str, Any]) -> str:
    if not verification:
        return "输出：没有验证结果。"
    failures = verification.get("failures") or []
    suffix = f"（{'；'.join(str(item) for item in failures)}）" if failures else ""
    if verification.get("ok") and verification.get("task_completed"):
        return "输出：安全检查通过，任务完成。"
    if verification.get("ok"):
        return f"输出：安全检查通过，但任务未完成{suffix}。"
    return f"输出：安全检查未通过{suffix}。"


def _describe_event(item: dict[str, Any]) -> str:
    node = item.get("node")
    payload = item.get("payload") or {}
    if node == "intent":
        text = payload.get("text") or "空输入"
        return f"输入：用户请求“{text}”；输出：已提取为本轮意图。"
    if node == "memory_recall":
        failed = "，召回失败并已留痕" if payload.get("error") else ""
        return f"输出：召回 {payload.get('count') or 0} 条偏好{failed}。"
    if node == "clarify":
        if payload.get("need_clarify"):
            return "输出：当前信息不足，需要用户补充后继续。"
        return "输出：信息足够，可以进入任务规划。"
    if node == "planner":
        names = [step.get("tool") for step in payload.get("steps", []) if step.get("tool")]
        if names:
            return f"输出：计划调用 {'、'.join(names)}。"
        return "输出：规划完成，未生成工具调用。"
    return _describe_later_event(item, payload)


def _describe_later_event(item: dict[str, Any], payload: dict[str, Any]) -> str:
    node = item.get("node")
    if node == "policy_gate" and item.get("event") == "degraded_params":
        return "输出：为满足安全策略，已缩减工具参数。"
    if node == "policy_gate":
        return _describe_policy(payload.get("decisions") or [])
    if node == "human_confirm":
        if payload.get("confirmed"):
            return "输入：用户确认；输出：确认凭证通过。"
        return "输出：等待用户确认，尚未执行后续操作。"
    if node == "tool_executor":
        return _describe_results(payload.get("results") or [])
    if node == "memory_capture":
        return _describe_memory_capture(payload)
    if node == "recovery":
        return f"输出：进入第 {payload.get('recovery_count') or 1} 次确定性恢复并准备重试。"
    return "该节点已执行，但本次事件没有可展示的摘要。"


def _describe_memory_capture(payload: dict[str, Any]) -> str:
    written = payload.get("written") or []
    if written:
        return f"输出：写入 {len(written)} 条已确认偏好。"
    if payload.get("pending"):
        return f"输出：有 {payload['pending']} 条偏好等待用户确认。"
    return "输出：没有写入新的偏好。"


def _summaries_for_node(response: TurnResponse, node: str, visited: bool) -> list[str]:
    if not visited:
        return ["本轮未经过该节点，因此没有输入输出事件。"]
    # trace 的 verifier 事件没有 ok 字段，只能用顶层 verification，避免把成功误报成失败。
    if node == "verifier":
        return [_describe_verification(response.verification)]
    summaries = [
        _describe_event(item) for item in (response.trace or []) if item.get("node") == node
    ]
    if node == "policy_gate" and not summaries:
        summaries.append(_describe_policy(response.policy_decisions))
    return summaries or ["节点已经过，但本轮 trace 没有记录可展示的事件。"]


def _node_status(response: TurnResponse, node: str) -> str:
    denied = node == "policy_gate" and any(
        item.get("decision") == "deny" for item in response.policy_decisions
    )
    if denied:
        return "blocked"
    return "visited" if node in response.visited_nodes else "skipped"


def _ordered_nodes(response: TurnResponse, nodes: list[str]) -> list[str]:
    visited = [node for node in response.visited_nodes if node in nodes]
    ordered_visited = list(dict.fromkeys(visited))
    skipped = [node for node in nodes if node not in ordered_visited]
    return ordered_visited + skipped


def _node_details(response: TurnResponse, node: str, first_node: str | None) -> str:
    status = _node_status(response, node)
    visited = status != "skipped"
    sequence = response.visited_nodes.index(node) + 1 if node in response.visited_nodes else None
    status_text = {"visited": "已走过", "skipped": "已跳过", "blocked": "已拦截"}[status]
    sequence_text = f" · 第 {sequence} 个" if sequence else ""
    summaries = _summaries_for_node(response, node, visited)
    items = "".join(f"<li>{html.escape(summary)}</li>" for summary in summaries)
    open_attribute = " open" if node == first_node else ""
    return (
        f'<details class="{status}"{open_attribute}>'
        f"<summary>{html.escape(NODE_LABELS[node])} · {status_text}{sequence_text} "
        f"<code>{node}</code></summary><ul>{items}</ul></details>"
    )


def _timeline_group(
    response: TurnResponse,
    title: str,
    nodes: list[str],
    first_node: str | None,
) -> str:
    details = "".join(
        _node_details(response, node, first_node) for node in _ordered_nodes(response, nodes)
    )
    return (
        '<section class="timeline-group">'
        f"<h3>{html.escape(title)}</h3><div class=\"timeline-nodes\">{details}</div></section>"
    )


def render_timeline(response: TurnResponse) -> str:
    first_node = next(
        (node for node in response.visited_nodes if node in NODE_LABELS),
        None,
    )
    main = _timeline_group(response, "主链路", MAIN_NODES, first_node)
    auxiliary = _timeline_group(response, "辅助节点", AUXILIARY_NODES, first_node)
    trace_id = html.escape(response.trace_id)
    return (
        '<div class="timeline">'
        f"<p><small>trace_id: {trace_id}</small></p>{main}{auxiliary}</div>"
    )


def _empty_timeline() -> str:
    return (
        '<div class="timeline-empty">'
        "发送一条消息后，这里会显示本轮真实执行轨迹。</div>"
    )


def send_message(
    message: str,
    history: list[dict[str, str]] | None,
    vehicle_state: str,
    session_id: str | None,
    current_timeline: str,
) -> tuple[list[dict[str, str]], str | None, str, str]:
    clean_message = (message or "").strip()
    next_history = list(history or [])
    if not clean_message:
        return next_history, session_id, current_timeline, ""
    next_history.append({"role": "user", "content": clean_message})
    try:
        response = turn(TurnRequest(
            text=clean_message,
            vehicle_state=vehicle_state,
            session_id=session_id,
        ))
    except HTTPException as exc:
        answer = f"请求被后端拒绝：{exc.detail}，换句话试试。"
        next_history.append({"role": "assistant", "content": answer})
        return next_history, session_id, current_timeline, ""
    except Exception:
        log.exception("Gradio demo 处理请求时发生未知异常")
        answer = (
            "请求处理失败，后端遇到了未预期的问题。"
            "请稍后重试或换句话试试。"
        )
        next_history.append({"role": "assistant", "content": answer})
        return next_history, session_id, current_timeline, ""
    answer = response.final_response or "本轮没有返回回答。"
    if response.needs_user_input:
        answer += "\n\n本轮需要你补充信息：直接在输入框继续说就行。"
    next_history.append({"role": "assistant", "content": answer})
    return next_history, response.session_id, render_timeline(response), ""


def _add_scenario_buttons() -> list[tuple[gr.Button, str]]:
    buttons = []
    for group_name, scenarios in SCENARIO_GROUPS.items():
        gr.Markdown(group_name, elem_classes="scenario-label")
        with gr.Row():
            for scenario in scenarios:
                button = gr.Button(scenario, size="sm", elem_classes="scenario-button")
                buttons.append((button, scenario))
    return buttons


def _bind_send_events(
    buttons: list[tuple[gr.Button, str]],
    message: gr.Textbox,
    send: gr.Button,
    chatbot: gr.Chatbot,
    vehicle_state: gr.Radio,
    session_id: gr.State,
    timeline: gr.HTML,
) -> None:
    inputs = [message, chatbot, vehicle_state, session_id, timeline]
    outputs = [chatbot, session_id, timeline, message]
    send.click(send_message, inputs=inputs, outputs=outputs)
    message.submit(send_message, inputs=inputs, outputs=outputs)
    scenario_inputs = [chatbot, vehicle_state, session_id, timeline]
    for button, scenario in buttons:
        button.click(partial(send_message, scenario), inputs=scenario_inputs, outputs=outputs)


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="TripPilot 途行智驾 · Gradio 体验版") as demo:
        session_id = gr.State(value=None)
        gr.Markdown("# TripPilot 途行智驾 · Gradio 体验版")
        gr.HTML(BADGES)
        vehicle_state = gr.Radio(
            choices=[("驻车模拟", "parked_simulated"), ("行驶模拟", "driving_simulated")],
            value="parked_simulated",
            label="座舱状态（仅影响后续请求）",
        )
        gr.Markdown("## 15 个体验场景")
        scenario_buttons = _add_scenario_buttons()
        with gr.Row():
            with gr.Column(scale=5):
                chatbot = gr.Chatbot(label="对话", height=520)
                with gr.Row():
                    message = gr.Textbox(
                        label="输入",
                        placeholder="例如：带我去798",
                        scale=5,
                    )
                    send = gr.Button("发送", variant="primary", scale=1)
            with gr.Column(scale=5):
                gr.Markdown("## Trajectory 时间线")
                timeline = gr.HTML(_empty_timeline())
        _bind_send_events(
            scenario_buttons, message, send, chatbot, vehicle_state, session_id, timeline,
        )
    return demo


if __name__ == "__main__":
    demo = build_demo()
    demo.launch(css=CSS)
