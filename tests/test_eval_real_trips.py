import json
from pathlib import Path
from types import SimpleNamespace

from eval.eval_runner import CRITERION_CHECKS, load_dataset, run_case


ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "fixtures" / "dataset_v0.jsonl"


def test_plate_not_fabricated():
    check = CRITERION_CHECKS["plate_not_fabricated"]
    out = SimpleNamespace(
        final_response="已查询限行尾号，请以工具结果为准。",
        tool_calls=[],
        tool_results=[],
    )

    assert check(out, None, True) is True

    # 这个检查防止泄露或编造完整车牌，不负责判断限行尾号是否可信。
    out.final_response = "今天限行 3 和 8。"
    assert check(out, None, True) is True

    out.final_response = "今天限行 3 和 8，车牌京A12345受限。"
    assert check(out, None, True) is False


def test_no_wild_guess():
    check = CRITERION_CHECKS["no_wild_guess"]
    allowed_destinations = ["潮白河"]

    out = SimpleNamespace(
        tool_calls=[
            SimpleNamespace(
                tool="map.route",
                args={"destination": "潮白河"},
            )
        ]
    )
    assert check(out, None, allowed_destinations) is True

    out.tool_calls = [
        SimpleNamespace(tool="map.route", args={"destination": "天安门"})
    ]
    assert check(out, None, allowed_destinations) is False

    out.tool_calls = [SimpleNamespace(tool="weather.now", args={})]
    assert check(out, None, allowed_destinations) is True


def test_dataset_33_cases_parseable():
    lines = DATASET.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 33

    cases = [json.loads(line) for line in lines]
    case_ids = [case["case_id"] for case in cases]
    assert len(case_ids) == len(set(case_ids))

    real_case_ids = {f"TP-REAL-{number:03d}" for number in range(1, 15)}
    real_case_ids.update(f"TP-REAL-015{suffix}" for suffix in "abcd")
    assert real_case_ids <= set(case_ids)

    criterion_keys = {
        key
        for case in cases
        for key in case["expected"].get("success_criteria", {})
    }
    assert criterion_keys <= set(CRITERION_CHECKS)


def test_spotcheck_006_passes():
    cases = load_dataset(DATASET)
    case = next(case for case in cases if case["case_id"] == "TP-REAL-006")

    result = run_case(case)

    assert result["passed"] is True, result


def test_spotcheck_010_honestly_fails():
    cases = load_dataset(DATASET)
    case = next(case for case in cases if case["case_id"] == "TP-REAL-010")

    result = run_case(case)

    assert result["passed"] is False
    failure_reasons = [
        *result["policy_reasons"],
        *result["verification"].get("failures", []),
        *result["criterion_failures"],
    ]
    assert any("unknown_tool" in reason for reason in failure_reasons)
