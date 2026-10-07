from trippilot.graph import new_state
from trippilot.policy_gate import check_tool_call
from trippilot.state import ToolCall
from trippilot.tools.knowledge import KnowledgeTool


def test_recorded_knowledge_answers_matching_question():
    question = "798为什么叫798"
    result = KnowledgeTool().run(ToolCall(
        tool="knowledge.qa", args={"question": question}))

    assert result.ok is True
    assert result.data["question"] == question
    assert result.data["matched_keywords"] == ["798"]
    assert result.data["source"] == "recorded"


def test_unknown_question_honestly_returns_failure():
    result = KnowledgeTool().run(ToolCall(
        tool="knowledge.qa", args={"question": "这座桥什么时候建成"}))

    assert result.ok is False
    assert "不知道" in result.error


def test_unknown_knowledge_operation_is_denied_and_not_run():
    call = ToolCall(tool="knowledge.ask", args={})

    decision = check_tool_call(call, new_state())
    result = KnowledgeTool().run(call)

    assert decision.decision == "deny"
    assert decision.reason_code == "unknown_tool"
    assert result.ok is False

