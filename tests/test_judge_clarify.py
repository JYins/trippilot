from eval.judge import DIM_CLARIFY_QUALITY, DeterministicJudge


def test_no_clarification_needed_gets_full_score_from_event_semantics():
    score = DeterministicJudge().score(
        {"case_id": "ordinary-route", "context": {}},
        {
            "visited_nodes": ["intent", "clarify", "planner", "verifier"],
            "unexpected_tools": [],
            "final_response": "路线已经规划好了。",
            "clarify_checked": {"need_clarify": False, "reasons": []},
        },
    )

    assert score.dimensions[DIM_CLARIFY_QUALITY] == 1.0
    assert "need_clarify=False" in score.notes
    assert "无需澄清" in score.notes
