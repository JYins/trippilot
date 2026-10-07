"""LLM 客户端接口：可注入 stub（测试/无 key 运行），真实调用只走手动配置。

- 默认无 key 时使用 DeterministicStub：按规则生成 plan，不调用任何外部模型。
  保证「语音交互主循环先跑通」不依赖任何 key。
- 真实模型只在 TRIPPILOT_LLM_* 环境变量手动提供时启用；
  key 只存在于本次进程环境，不写入文件、不进日志。
"""

from __future__ import annotations

import json
import os
from typing import Any


class LLMClient:
    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        """返回 plan steps：[{step_id, tool, args, description}]。"""
        raise NotImplementedError

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        raise NotImplementedError


class ScriptedLLM(LLMClient):
    """按用例脚本返回固定 plan：eval 专用，构造特定测试场景。"""

    def __init__(self, plan: list[dict[str, Any]]) -> None:
        self._plan = plan

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        return self._plan

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        return "脚本执行完成。"


def _contains_any(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def _is_knowledge_question(text: str) -> bool:
    # 解释性疑问把地点当话题；例如“798为什么叫798”不能被地点实体带去导航。
    return _contains_any(
        text, ("为什么", "是什么", "介绍一下", "讲讲", "怎么来的", "什么意思"))


def _has_weather_words(text: str) -> bool:
    return _contains_any(text, ("天气", "气温", "下雨", "下雪", "几度"))


def _is_weather_question(text: str) -> bool:
    # 天气疑问是在收集出行决策信息；例如“一会儿去河边，天气怎么样”不是导航命令。
    question_words = ("怎么样", "如何", "好不好", "会不会")
    return _has_weather_words(text) and _contains_any(text, question_words)


def _is_navigation_request(text: str, has_destination: bool) -> bool:
    # 明确路线问法本身足以表达导航；例如“还有多久”可沿用上下文目的地。
    explicit_words = (
        "导航", "路线", "怎么去", "怎么走", "几点出发", "几点到",
        "几点能到", "还有多远", "还有多久",
    )
    if _contains_any(text, explicit_words):
        return True
    # 单独的“去”容易出现在闲聊里；例如“去那个河边”无实体时不能猜目的地。
    return "去" in text and has_destination


def _has_future_time(text: str) -> bool:
    # 明确未来时间才查预报；例如只问“天气几度”仍查当前天气。
    return _contains_any(
        text, ("未来", "一会儿", "等会儿", "待会儿", "明天", "后天", "下周", "小时"))


def _is_restriction_request(text: str) -> bool:
    # 限行咨询不代表已知车牌；例如“今天限号吗”只能查询城市规则。
    return _contains_any(text, ("限号", "限行", "尾号"))


def _is_reminder_request(text: str) -> bool:
    # 只有明确说“提醒”才创建副作用；例如提到明天本身不创建提醒。
    return "提醒" in text


def _is_media_next_request(text: str) -> bool:
    # 换曲只响应常见播放指令；例如“这首歌叫什么”不能切歌。
    return _contains_any(text, ("换歌", "换首歌", "切歌", "下一首"))


def _is_volume_request(text: str) -> bool:
    # 音量控制需要明确指向声音；例如“空调调小”不能连带降低音量。
    return _contains_any(text, ("音量", "声音"))


def _is_climate_request(text: str) -> bool:
    # 空调动作只由空调词触发；例如单说“有点热”不替用户操作车辆。
    return "空调" in text


def _is_sunroof_request(text: str) -> bool:
    # 天窗动作只由天窗词触发；例如普通的“打开”没有足够控制对象。
    return "天窗" in text


def _place_names(context: dict[str, Any]) -> list[str]:
    names = context.get("place_names", [])
    if not isinstance(names, list):
        return []
    return [name for name in names if isinstance(name, str) and name]


def _destination(context: dict[str, Any], place_names: list[str]) -> str:
    destination = context.get("destination")
    if isinstance(destination, str) and destination:
        return destination
    # 局部澄清不能脱离原地点使用；例如实体“文化园”比单独的“南门”更可落地。
    if place_names:
        return place_names[0]
    clarified = context.get("clarify_resolved")
    if isinstance(clarified, str) and clarified:
        return clarified
    return ""


def _weather_area(context: dict[str, Any], place_names: list[str]) -> str:
    # 调用方声明的区域与录制数据绑定；例如已有 area 时不能被目的地覆盖。
    for candidate in (context.get("area"), context.get("destination")):
        if isinstance(candidate, str) and candidate:
            return candidate
    return place_names[0] if place_names else "海淀区"


def _route_args(text: str, context: dict[str, Any],
                destination: str) -> dict[str, Any]:
    enroute = _contains_any(text, ("还有多远", "还有多久"))
    driving = str(context.get("vehicle_state", "")).startswith("driving")
    # 在途问题从车辆当前模拟位置计算；例如不能继续把“家里”当起点。
    origin = "当前位置" if enroute or driving else context.get("origin", "")
    args = {
        "origin": origin,
        "destination": destination,
        "fixture": context.get("map_fixture", "default"),
        "option_count": context.get("option_count", 1),
    }
    # 偏好来自用户上下文；例如不能因“去市区”自行补成避开拥堵。
    if "route_pref" in context:
        args["route_pref"] = context["route_pref"]
    return args


def _weather_item(text: str, context: dict[str, Any],
                  place_names: list[str], *,
                  force_forecast: bool = False,
                  future_ok: bool = True) -> tuple[str, dict[str, Any], str]:
    # 辅助性天气提及（"结合天气"）只查当前天气：老用例契约如此，
    # 预报语义只给"明确问未来天气"和"长途规划"两种强信号。
    forecast = force_forecast or (future_ok and _has_future_time(text))
    tool = "weather.forecast" if forecast else "weather.now"
    description = "查询天气预报" if forecast else "查询当前天气"
    args = {
        "area": _weather_area(context, place_names),
        "fixture": context.get("weather_fixture", "default"),
    }
    return tool, args, description


def _number_steps(
        items: list[tuple[str, dict[str, Any], str]]) -> list[dict[str, Any]]:
    return [
        {"step_id": f"s{index}", "tool": tool, "args": args,
         "description": description}
        for index, (tool, args, description) in enumerate(items, start=1)
    ]


def _travel_items(text: str, context: dict[str, Any],
                  place_names: list[str]) -> list[tuple[str, dict[str, Any], str]]:
    items: list[tuple[str, dict[str, Any], str]] = []
    destination = _destination(context, place_names)
    if _is_navigation_request(text, bool(destination)):
        items.append(("map.route", _route_args(text, context, destination),
                      "查询路线"))
        # 长途规划需要天气辅助决策；例如“下周自驾去外地”不能只给路线。
        if _contains_any(text, ("规划", "自驾")):
            items.append(_weather_item(
                text, context, place_names, force_forecast=True))

    weather_added = any(tool.startswith("weather.") for tool, _, _ in items)
    if _has_weather_words(text) and not weather_added:
        items.append(_weather_item(text, context, place_names,
                                   future_ok=False))
    return items


def _query_and_reminder_items(
        text: str, context: dict[str, Any]
) -> list[tuple[str, dict[str, Any], str]]:
    items: list[tuple[str, dict[str, Any], str]] = []
    if _is_restriction_request(text):
        items.append((
            "restriction.query",
            {"city": context.get("city", "北京"),
             "fixture": context.get("restriction_fixture", "default")},
            "查询限行",
        ))
    if _is_reminder_request(text):
        items.append((
            "reminder.create",
            {"content": context.get("reminder_content", ""),
             "time": context.get("reminder_time", ""),
             "session_id": context.get("session_id", "")},
            "创建提醒",
        ))
    return items


def _media_items(text: str) -> list[tuple[str, dict[str, Any], str]]:
    items: list[tuple[str, dict[str, Any], str]] = []
    if _is_media_next_request(text):
        items.append(("media.next", {}, "切换到下一首"))
    if _is_volume_request(text):
        quieter = _contains_any(text, ("小", "低", "轻", "降", "调小", "静音"))
        action = "decrease" if quieter else "increase"
        items.append(("media.volume", {"action": action}, "调整音量"))
    return items


def _vehicle_items(text: str) -> list[tuple[str, dict[str, Any], str]]:
    items: list[tuple[str, dict[str, Any], str]] = []
    if _is_climate_request(text):
        quieter = _contains_any(text, ("小", "低", "轻", "调小", "关小", "调低"))
        # 未出现调小词时默认调大；例如“热，空调开大”应保持 increase_ac。
        action = "decrease_ac" if quieter else "increase_ac"
        items.append(("vehicle.climate", {"action": action}, "调整空调"))
    if _is_sunroof_request(text):
        action = "close" if "关" in text else "open"
        items.append(("vehicle.sunroof", {"action": action}, "控制天窗"))
    return items


class DeterministicStub(LLMClient):
    """确定性 stub：按关键词规则生成 plan，用于无 key 跑通主循环与回归。

    注意：这是测试脚手架，不是产品能力；eval 报告中 LLM 相关能力
    以真实模型实测为准，不拿 stub 结果充数。
    """

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        text = intent
        place_names = _place_names(context)

        if _is_knowledge_question(text):
            return _number_steps([(
                "knowledge.qa",
                {"question": text,
                 "fixture": context.get("knowledge_fixture", "default")},
                "回答知识问题",
            )])
        if _is_weather_question(text):
            return _number_steps([_weather_item(text, context, place_names)])

        items = _travel_items(text, context, place_names)
        items.extend(_query_and_reminder_items(text, context))
        items.extend(_media_items(text))
        items.extend(_vehicle_items(text))
        return _number_steps(items)

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        results = state_summary.get("tool_results", [])
        parts = [f"{r.get('tool')}: {'成功' if r.get('ok') else '失败'}"
                 for r in results]
        return "已执行：" + "；".join(parts) if parts else "本次没有执行工具调用。"


class EnvLLMClient(LLMClient):
    """真实模型客户端（手动触发专用）。

    从环境变量读取：TRIPPILOT_LLM_BASE_URL / TRIPPILOT_LLM_API_KEY /
    TRIPPILOT_LLM_MODEL。key 仅存于进程环境。
    """

    def __init__(self) -> None:
        self.base_url = os.environ.get("TRIPPILOT_LLM_BASE_URL", "").rstrip("/")
        self.api_key = os.environ.get("TRIPPILOT_LLM_API_KEY", "")
        self.model = os.environ.get("TRIPPILOT_LLM_MODEL", "")
        if not (self.base_url and self.api_key and self.model):
            raise RuntimeError("真实模型需要手动设置 TRIPPILOT_LLM_BASE_URL/_API_KEY/_MODEL")

    def _chat(self, system: str, user: str) -> str:
        import httpx
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model,
                  "messages": [{"role": "system", "content": system},
                               {"role": "user", "content": user}],
                  "temperature": 0.2},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    def plan(self, *, intent: str, context: dict[str, Any],
             available_tools: list[str]) -> list[dict[str, Any]]:
        raw = self._chat(
            "你是座舱任务规划器。只输出 JSON 数组，每个元素含 step_id/tool/args/description。"
            f"可用工具：{available_tools}。不要编造工具名。",
            f"意图：{intent}\n上下文：{json.dumps(context, ensure_ascii=False)}")
        return json.loads(raw)

    def final_answer(self, *, state_summary: dict[str, Any]) -> str:
        return self._chat("你是车载语音助手，根据工具执行结果给用户一句话总结，简短口语化。",
                          json.dumps(state_summary, ensure_ascii=False))


def make_llm() -> LLMClient:
    """有手动 key 就用真实模型，否则用确定性 stub。"""
    try:
        return EnvLLMClient()
    except RuntimeError:
        return DeterministicStub()


__all__ = ["LLMClient", "DeterministicStub", "ScriptedLLM", "EnvLLMClient", "make_llm"]
