"""实验脚本测试：全部离线。

judge_agreement.py 跑全量 12 条图比较重（约 4s），这里只测
不跑图的纯逻辑部分：合成退化探针一定被 judge 打低分、
analyze() 的分类逻辑。全量 12 条的集成行为由脚本本身的
stdout + data/judge_agreement.json 落盘保证。
"""

from eval.judge import DIM_CLARIFY_QUALITY, DIM_PLAN_EFFICIENCY
from eval.judge_agreement import analyze, analyze_live, run_synthetic_probes


def test_synthetic_bad_traces_all_flagged():
    # 规则 verdict 永远 PASS 的烂轨迹，judge 必须全部打低分——
    # 这是 DeterministicJudge 存在的理由（advisory 维度有信息增量）
    rows = run_synthetic_probes()
    assert len(rows) == 3
    by_name = {r["name"]: r for r in rows}
    assert by_name["planner 兜圈 3 次（规划绕路）"][DIM_PLAN_EFFICIENCY] < 0.7
    assert by_name["调了用例允许范围外的工具"][
        DIM_PLAN_EFFICIENCY] <= 0.5
    assert by_name["走了 clarify 但一句追问都没说出来"][
        DIM_CLARIFY_QUALITY] < 0.6


def _row(case_id, passed, plan_eff, clarify_q):
    return {"case_id": case_id, "passed": passed,
            "judge": {"dimensions": {DIM_PLAN_EFFICIENCY: plan_eff,
                                     DIM_CLARIFY_QUALITY: clarify_q}}}


def test_analyze_finds_rule_pass_but_judge_bad():
    rows = [_row("A", True, 1.0, 1.0),
            _row("B", True, 0.4, 0.6),   # 规则过但 judge 烂
            _row("C", True, 1.0, 0.6)]   # 澄清默认桶：按阈值也算"烂"
    a = analyze(rows)
    assert a["n"] == 3
    assert a["rule_pass"] == 3
    assert a["judge_bad_but_rule_pass"] == ["B", "C"]
    assert a["judge_fully_uninformative_ids"] == ["A"]


def test_analyze_rule_fail_not_counted_as_judge_bad():
    rows = [_row("D", False, 0.2, 0.2)]
    a = analyze(rows)
    # 规则 FAIL 的 case 不进"规则过了但 judge 烂"统计
    assert a["judge_bad_but_rule_pass"] == []


def _live_row(case_id, det_plan, det_clar, live_plan, live_clar):
    return {"case_id": case_id, "passed": True,
            "det": {DIM_PLAN_EFFICIENCY: det_plan,
                    DIM_CLARIFY_QUALITY: det_clar},
            "live": {DIM_PLAN_EFFICIENCY: live_plan,
                     DIM_CLARIFY_QUALITY: live_clar},
            "delta_plan": round(abs(live_plan - det_plan), 3),
            "delta_clarify": round(abs(live_clar - det_clar), 3)}


def test_analyze_live_classifies_stricter_side():
    rows = [_live_row("A", 1.0, 1.0, 0.5, 1.0),   # live 更严
            _live_row("B", 0.5, 1.0, 0.9, 1.0),   # det 误伤
            {"case_id": "C", "live_error": "boom"}]
    a = analyze_live(rows)
    assert a["n"] == 3
    assert a["live_ok"] == 2
    assert a["live_errors"] == ["C"]
    assert a["mean_abs_delta_plan"] == 0.45
    assert a["live_stricter"] == ["A"]
    assert a["det_stricter"] == ["B"]


def test_analyze_live_ignores_rule_failed_cases():
    # 规则 verdict 已经 FAIL 的 case，live 再严也不算"发现新东西"
    row = _live_row("F", 1.0, 1.0, 0.5, 1.0)
    row["passed"] = False
    a = analyze_live([row])
    assert a["live_stricter"] == []
    assert a["det_stricter"] == []
