from eval.eval_runner import check_node_trace


def test_sequence_check_rejects_out_of_order_nodes():
    result = check_node_trace(
        ["intent", "planner", "clarify", "tool_executor"],
        {"node_sequence": ["clarify", "planner", "tool_executor"]},
    )

    assert result["sequence_check"] is False


def test_sequence_check_rejects_missing_node():
    result = check_node_trace(
        ["intent", "clarify", "planner", "verifier"],
        {"node_sequence": ["clarify", "planner", "tool_executor"]},
    )

    assert result["sequence_check"] is False


def test_no_loop_rejects_third_visit():
    result = check_node_trace(
        ["planner", "tool_executor", "planner", "planner"],
        {},
    )

    assert result["no_loop"] is False
    assert result["excessive_revisits"] == {"planner": 3}


def test_allow_revisit_exempts_expected_recovery_loop():
    result = check_node_trace(
        ["tool_executor", "recovery", "tool_executor", "tool_executor"],
        {"allow_revisit": ["tool_executor"]},
    )

    assert result["no_loop"] is True
    assert result["excessive_revisits"] == {}
