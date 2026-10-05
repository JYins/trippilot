"""实验脚本测试：全部离线。

judge_agreement.py 跑全量 12 条图比较重（约 4s），这里只测
不跑图的纯逻辑部分：合成退化探针一定被 judge 打低分、
analyze() 的分类逻辑。全量 12 条的集成行为由脚本本身的
stdout + data/judge_agreement.json 落盘保证。
"""

from eval.judge import DIM_CLARIFY_QUALITY, DIM_PLAN_EFFICIENCY
from eval.judge_agreement import analyze, run_synthetic_probes


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
