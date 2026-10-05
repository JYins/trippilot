"""实验 A：DeterministicJudge vs 规则 verdict 一致性。

做法：
1. 对 dataset_v0 全部 12 条用例跑 run_case(..., judge=DeterministicJudge())，
   记录每条的 pass/fail + plan_efficiency / clarify_quality；
2. 问三个问题：
   a. 是否有"规则 PASS 但 judge 觉得轨迹烂"（plan_efficiency<0.7 或
      clarify_quality<0.7）的 case？
   b. 是否有 judge 分数完全没区分度的 case（全是 1.0，什么也看不出来）？
   c. 规则 FAIL 但 judge 高分——说明 judge 看不见的问题维度？
3. 造 3 条合成退化轨迹（planner 兜圈、越界工具、澄清死胡同），
   检验 DeterministicJudge 对"规则写不出的烂轨迹"有没有分辨力。

实验 A 全程离线。实验 B 需要 live judge（deepseek skill 或
TRIPPILOT_JUDGE_API_KEY），无后端时如实 pending、不 fake 分数。

实验 B（`--live`）：DeterministicJudge vs live DeepSeekJudge。
每条用例跑两遍图（stub/recorded，确定性），两个 judge 各打一次分，
只比 advisory 维度、不碰 verdict。回答"什么时候规则够用、
什么时候值得上 LLM judge"。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from eval.eval_runner import load_dataset, run_case
from eval.judge import (
    DIM_CLARIFY_QUALITY,
    DIM_PLAN_EFFICIENCY,
    DeepSeekJudge,
    DeterministicJudge,
)

BAD_PLAN = 0.7
BAD_CLARIFY = 0.7

# 合成退化轨迹：规则 verdict 永远 PASS（visited/nodes 全走齐了），
# 检验 judge 能不能指出"走得对但走得蠢"。
SYNTHETIC_BAD_TRACES = [
    {
        "name": "planner 兜圈 3 次（规划绕路）",
        "case": {"case_id": "SYN-LOOP", "context": {}},
        "trace": {
            "visited_nodes": ["intent", "planner", "planner", "planner",
                              "policy_gate", "tool_executor", "verifier"],
            "unexpected_tools": [],
            "final_response": "路线已规划好",
        },
    },
    {
        "name": "调了用例允许范围外的工具",
        "case": {"case_id": "SYN-SCOPE", "context": {}},
        "trace": {
            "visited_nodes": ["intent", "planner", "policy_gate",
                              "tool_executor", "verifier"],
            "unexpected_tools": ["reminder.create"],
            "final_response": "已处理",
        },
    },
    {
        "name": "走了 clarify 但一句追问都没说出来",
        "case": {"case_id": "SYN-CLARIFY-DEADEND", "context": {}},
        "trace": {
            "visited_nodes": ["intent", "clarify", "planner",
                              "policy_gate", "verifier"],
            "unexpected_tools": [],
            "final_response": "",
        },
    },
]


def run_real_cases() -> list[dict]:
    cases = load_dataset(ROOT / "fixtures" / "dataset_v0.jsonl")
    judge = DeterministicJudge()
    return [run_case(c, judge=judge) for c in cases]


def run_synthetic_probes() -> list[dict]:
    judge = DeterministicJudge()
    rows = []
    for p in SYNTHETIC_BAD_TRACES:
        s = judge.score(p["case"], p["trace"])
        rows.append({"name": p["name"], **s.dimensions, "notes": s.notes})
    return rows


def check_live_judge_status() -> dict:
    # 无 key 时必须 pending，不能伪造分数（铁律）
    try:
        DeepSeekJudge()
        return {"available": True}
    except RuntimeError as e:
        return {"available": False, "reason": str(e)}


def analyze(rows: list[dict]) -> dict:
    judge_bad_but_rule_pass = [
        r for r in rows
        if r["passed"]
        and (r["judge"]["dimensions"][DIM_PLAN_EFFICIENCY] < BAD_PLAN
             or r["judge"]["dimensions"][DIM_CLARIFY_QUALITY] < BAD_CLARIFY)
    ]
    uninformative = [
        r["case_id"] for r in rows
        if (r["judge"]["dimensions"][DIM_PLAN_EFFICIENCY] == 1.0
            and r["judge"]["dimensions"][DIM_CLARIFY_QUALITY] == 1.0)
    ]
    return {
        "n": len(rows),
        "rule_pass": sum(1 for r in rows if r["passed"]),
        "judge_bad_but_rule_pass": [r["case_id"]
                                    for r in judge_bad_but_rule_pass],
        "judge_fully_uninformative_ids": uninformative,
    }


def run_live_comparison() -> list[dict]:
    """实验 B：每条用例两个 judge 各打一次分，只比 advisory 维度。"""
    cases = load_dataset(ROOT / "fixtures" / "dataset_v0.jsonl")
    det, live = DeterministicJudge(), DeepSeekJudge()
    rows = []
    for c in cases:
        rd = run_case(c, judge=det)
        rl = run_case(c, judge=live)
        dd = rd["judge"]["dimensions"]
        lj = rl["judge"]
        row: dict = {"case_id": c["case_id"], "passed": rd["passed"],
                     "via": live.via, "det": dd}
        if "dimensions" in lj:
            ld = lj["dimensions"]
            row["live"] = ld
            row["live_notes"] = lj.get("notes", "")
            row["delta_plan"] = round(
                abs(ld[DIM_PLAN_EFFICIENCY] - dd[DIM_PLAN_EFFICIENCY]), 3)
            row["delta_clarify"] = round(
                abs(ld[DIM_CLARIFY_QUALITY] - dd[DIM_CLARIFY_QUALITY]), 3)
        else:
            row["live_error"] = lj.get("error", "?")
        rows.append(row)
    return rows


def analyze_live(rows: list[dict]) -> dict:
    ok = [r for r in rows if "live" in r]

    def mean(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 3) if xs else None

    # det 满分判 1.0 - eps：浮点 artifact 不该让分类静默变空
    def det_full(x: float) -> bool:
        return x >= 1.0 - 1e-9

    return {
        "n": len(rows),
        "live_ok": len(ok),
        "live_errors": [r["case_id"] for r in rows if "live" not in r],
        "mean_abs_delta_plan": mean([r["delta_plan"] for r in ok]),
        "mean_abs_delta_clarify": mean([r["delta_clarify"] for r in ok]),
        # live 更严：规则过了、det 觉得全满分，live 挑出毛病
        "live_stricter": [
            r["case_id"] for r in ok
            if r["passed"]
            and det_full(r["det"][DIM_PLAN_EFFICIENCY])
            and det_full(r["det"][DIM_CLARIFY_QUALITY])
            and (r["live"][DIM_PLAN_EFFICIENCY] < BAD_PLAN
                 or r["live"][DIM_CLARIFY_QUALITY] < BAD_CLARIFY)],
        # det 更严：规则过了、det 扣分但 live 觉得没问题（规则代理的误伤）
        "det_stricter": [
            r["case_id"] for r in ok
            if r["passed"]
            and (r["det"][DIM_PLAN_EFFICIENCY] < BAD_PLAN
                 or r["det"][DIM_CLARIFY_QUALITY] < BAD_CLARIFY)
            and r["live"][DIM_PLAN_EFFICIENCY] >= BAD_PLAN
            and r["live"][DIM_CLARIFY_QUALITY] >= BAD_CLARIFY],
    }


def main_live() -> None:
    try:
        rows = run_live_comparison()
    except RuntimeError as e:
        print(f"live judge pending：{e}")
        return
    print("=" * 64)
    print("实验 B：DeterministicJudge vs live DeepSeekJudge（advisory 维度）")
    print("=" * 64)
    for r in rows:
        if "live" in r:
            print(f"{r['case_id']:<16} "
                  f"det_plan={r['det'][DIM_PLAN_EFFICIENCY]:.2f} "
                  f"live_plan={r['live'][DIM_PLAN_EFFICIENCY]:.2f}  "
                  f"det_clar={r['det'][DIM_CLARIFY_QUALITY]:.2f} "
                  f"live_clar={r['live'][DIM_CLARIFY_QUALITY]:.2f}")
            if r["live_notes"]:
                print(f"       live_notes: {r['live_notes']}")
        else:
            print(f"{r['case_id']:<16} live_error={r['live_error'][:80]}")
    print("-" * 64)
    a = analyze_live(rows)
    print(f"分析: {json.dumps(a, ensure_ascii=False, indent=2)}")
    out = ROOT / "docs" / "experiments" / "data" / "judge_live_vs_det.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rows": rows, "analysis": a},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"原始数据已写到 {out.relative_to(ROOT)}")


def main() -> None:
    if "--live" in sys.argv:
        main_live()
        return
    real = run_real_cases()
    synth = run_synthetic_probes()
    live = check_live_judge_status()

    print("=" * 64)
    print("实验 A：judge 一致性（DeterministicJudge vs 规则 verdict）")
    print("=" * 64)
    for r in real:
        d = r["judge"]["dimensions"]
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['case_id']:<16} "
              f"plan_eff={d[DIM_PLAN_EFFICIENCY]:.2f} "
              f"clarify_q={d[DIM_CLARIFY_QUALITY]:.2f} "
              f"visited={r['visited_nodes']}")
        print(f"       notes: {r['judge']['notes']}")
    print("-" * 64)
    print("合成退化轨迹探针（规则 verdict 无法捕获的烂轨迹）：")
    for s in synth:
        print(f"  {s['name']:<24} plan_eff={s[DIM_PLAN_EFFICIENCY]:.2f} "
              f"clarify_q={s[DIM_CLARIFY_QUALITY]:.2f}")
    print("-" * 64)
    a = analyze(real)
    print(f"分析: {json.dumps(a, ensure_ascii=False, indent=2)}")
    print(f"live judge 状态: {json.dumps(live, ensure_ascii=False)}")

    out = ROOT / "docs" / "experiments" / "data" / "judge_agreement.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "real_cases": [{"case_id": r["case_id"], "passed": r["passed"],
                        "judge": r["judge"],
                        "visited_nodes": r["visited_nodes"]}
                       for r in real],
        "synthetic_probes": synth,
        "live_judge": live,
        "analysis": a,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"原始数据已写到 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
