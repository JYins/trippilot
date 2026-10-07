from typing import Any

from trippilot.graph_nodes import planner_node
from trippilot.llm import DeterministicStub, LLMClient
from trippilot.state import ASRResult, TripPilotState


def _plan(text: str, **context: Any) -> list[dict[str, Any]]:
    return DeterministicStub().plan(
        intent=text,
        context=context,
        available_tools=[],
    )


def test_planner_passes_recognized_places_and_vehicle_state():
    class RecordingLLM(LLMClient):
        context: dict[str, Any] = {}

        def plan(self, *, intent: str, context: dict[str, Any],
                 available_tools: list[str]) -> list[dict[str, Any]]:
            self.context = context
            return []

        def final_answer(self, *, state_summary: dict[str, Any]) -> str:
            return ""

    llm = RecordingLLM()
    state = TripPilotState(
        trace_id="trace",
        session_id="session",
        intent="还有多久",
        vehicle_state="driving_simulated",
        asr_result=ASRResult(
            text="还有多久",
            confidence=0.9,
            place_entities=[{"name": "目的地甲"}, {"kind": "unknown"}],
        ),
    )

    planner_node(state, llm)

    assert llm.context["place_names"] == ["目的地甲"]
    assert llm.context["vehicle_state"] == "driving_simulated"


def test_knowledge_question_is_exclusive():
    steps = _plan(
        "那个园区为什么叫这个名字",
        place_names=["那个园区"],
        knowledge_fixture="place_name",
    )

    assert steps == [{
        "step_id": "s1",
        "tool": "knowledge.qa",
        "args": {"question": "那个园区为什么叫这个名字",
                 "fixture": "place_name"},
        "description": "回答知识问题",
    }]


def test_weather_question_does_not_force_navigation():
    steps = _plan(
        "一会儿去河滨公园，天气怎么样",
        destination="河滨公园",
        place_names=["河滨公园"],
        weather_fixture="riverside",
    )

    assert [step["tool"] for step in steps] == ["weather.forecast"]
    assert steps[0]["args"] == {
        "area": "河滨公园",
        "fixture": "riverside",
    }


def test_navigation_uses_entities_without_guessing_raw_text():
    assert _plan("去那个河边") == []

    steps = _plan(
        "去文化园南门",
        origin="住处",
        place_names=["文化园"],
        clarify_resolved="南门",
        route_pref="avoid_congestion",
    )

    assert steps[0]["args"] == {
        "origin": "住处",
        "destination": "文化园",
        "fixture": "default",
        "option_count": 1,
        "route_pref": "avoid_congestion",
    }


def test_enroute_query_uses_current_location():
    steps = _plan(
        "还有多远",
        origin="旧起点",
        destination="目的地乙",
        vehicle_state="driving_simulated",
    )

    assert steps[0]["tool"] == "map.route"
    assert steps[0]["args"]["origin"] == "当前位置"


def test_long_trip_adds_forecast_and_numbers_steps_in_order():
    steps = _plan(
        "自驾去山谷，帮我规划一下，也提醒我",
        destination="山谷",
        reminder_content="准备出发",
        reminder_time="下周",
        session_id="session",
    )

    assert [step["tool"] for step in steps] == [
        "map.route", "weather.forecast", "reminder.create",
    ]
    assert [step["step_id"] for step in steps] == ["s1", "s2", "s3"]


def test_restriction_does_not_invent_plate_or_date():
    steps = _plan(
        "今天尾号限行吗",
        city="天津",
        restriction_fixture="tianjin",
    )

    assert steps[0]["args"] == {"city": "天津", "fixture": "tianjin"}


def test_auxiliary_weather_mention_stays_now():
    # "结合天气"只是辅助信息，不触发预报；预报只给明确的未来天气问句和长途规划。
    steps = _plan(
        "明天下午去中关村面试，结合天气帮我看看几点出发",
        destination="中关村",
        area="海淀区",
    )

    tools = [step["tool"] for step in steps]
    assert "weather.now" in tools
    assert "weather.forecast" not in tools


def test_media_and_vehicle_controls_follow_requested_order():
    steps = _plan("换首歌，声音调低，空调调低，关天窗")

    assert [(step["tool"], step["args"]) for step in steps] == [
        ("media.next", {}),
        ("media.volume", {"action": "decrease"}),
        ("vehicle.climate", {"action": "decrease_ac"}),
        ("vehicle.sunroof", {"action": "close"}),
    ]
