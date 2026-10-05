"""实验 B：记忆抽取 precision/recall 实测。

1. 16 条 fixtures/memory_extract_cases.jsonl：逐条跑 extract_candidates，
   按 content 精确匹配算 TP/FP/FN → 总体 P/R；再按触发规则拆分
   （每条正例命中了哪条规则，哪条规则在拉胯）。
2. extra probes：刻意挑规则的灰色地带——
   "别/不要"（0.65 最低置信）的一次性指令 vs 稳定禁忌；
   "我喜欢"的当下一时情绪 vs 稳定偏好；
   玩笑称呼、安慰语等噪音；
   看误抽长什么样（这些不在 fixture 里，FP 全记 extra 列）。
3. 挑 3 个最有代表性的 bad case 写进报告。

全程离线，无 LLM。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trippilot.memory.extract import extract_candidates  # noqa: E402
import trippilot.memory.extract as extract_mod  # noqa: E402  _which_rule 反查规则表

# 灰色地带探针：(原文, 期望抽到？, 备注)
EXTRA_PROBES = [
    ("别走这条路", False, "一次性导航指令：最典型的'别'误抽场景"),
    ("今天堵车，以后别走这条路", False, "分句里的一次性指令"),
    ("别急，慢慢来", False, "安慰语，不是偏好"),
    ("不要急", False, "安慰语，'不要'触发"),
    ("以后别再给我放广告了", True, "真·稳定禁忌：'别再'句式"),
    ("我喜欢今天的歌单", False, "当下一时情绪 vs 稳定偏好"),
    ("我喜欢坐地铁", True, "稳定偏好：应该抽到"),
    ("我讨厌堵车", True, "稳定厌恶：应该抽到"),
    ("叫我一声哥听听", False, "玩笑称呼：'叫我'误抽"),
    ("他让我记住明天开会", False, "复述别人的指令：'记住'误抽"),
    ("记住我家地址是望京XX小区", True, "PII：必须抽到且标 sensitive"),
    ("以后都走这条路", True, "持久标记：路线偏好"),
]

# 规则身份：按 extract.py _RULES 的顺序
RULE_NAMES = ["记住型", "电话型", "裸号码", "住址型", "称呼型", "生日型",
              "以后都型", "我喜欢型", "别/不要型"]


def _which_rule(text: str) -> str:
    # 反查：看本轮命中的候选置信度对应哪条规则
    hits = [i for i, rule in enumerate(extract_mod._RULES)
            if rule["re"].search(text)]
    return "+".join(RULE_NAMES[i] for i in hits) or "无"


def run_fixture() -> dict:
    cases = [json.loads(l) for l in
             (ROOT / "fixtures" / "memory_extract_cases.jsonl")
             .read_text(encoding="utf-8").splitlines() if l.strip()]
    tp = fp = fn = 0
    per_rule: dict[str, dict] = {}
    rows = []
    for c in cases:
        text, expect = c["text"], c["expect"]
        got = extract_candidates(text)
        exp_contents = [e["content"] for e in expect]
        got_contents = [g["content"] for g in got]
        tp_c = len(set(exp_contents) & set(got_contents))
        fp_c = len([g for g in got_contents if g not in exp_contents])
        fn_c = len([e for e in exp_contents if e not in got_contents])
        tp, fp, fn = tp + tp_c, fp + fp_c, fn + fn_c
        rule = _which_rule(text) if expect or got else "无"
        if expect:
            st = per_rule.setdefault(rule, {"tp": 0, "fp": 0, "fn": 0,
                                            "n": 0})
            st["tp"] += tp_c
            st["fp"] += fp_c
            st["fn"] += fn_c
            st["n"] += 1
        rows.append({"text": text, "rule": rule,
                     "expect_n": len(expect), "got_n": len(got),
                     "tp": tp_c, "fp": fp_c, "fn": fn_c,
                     "got": got_contents})
    return {"tp": tp, "fp": fp, "fn": fn, "rows": rows, "per_rule": per_rule}


def run_probes() -> list[dict]:
    out = []
    for text, should_extract, note in EXTRA_PROBES:
        got = extract_candidates(text)
        got_contents = [g["content"] for g in got]
        verdict = ("OK" if bool(got) == should_extract else "误判")
        out.append({"text": text, "should_extract": should_extract,
                    "note": note, "got": got_contents,
                    "confidences": [g["confidence"] for g in got],
                    "verdict": verdict})
    return out


def main() -> None:
    fx = run_fixture()
    pr = run_probes()

    tp, fp, fn = fx["tp"], fx["fp"], fx["fn"]
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0

    print("=" * 64)
    print("实验 B：记忆抽取 P/R 实测（16 条 fixture + 12 条灰色探针）")
    print("=" * 64)
    print(f"fixture 总体：TP={tp} FP={fp} FN={fn} "
          f"precision={precision:.3f} recall={recall:.3f}")
    print("-" * 64)
    print("按规则拆分（只统计有期望抽取的正例）：")
    for rule, st in fx["per_rule"].items():
        p = st["tp"] / (st["tp"] + st["fp"]) if st["tp"] + st["fp"] else 1.0
        r = st["tp"] / (st["tp"] + st["fn"]) if st["tp"] + st["fn"] else 1.0
        print(f"  {rule:<8} n={st['n']} tp={st['tp']} fp={st['fp']} "
              f"fn={st['fn']} P={p:.2f} R={r:.2f}")
    print("-" * 64)
    print("灰色地带探针：")
    for p in pr:
        mark = "✓" if p["verdict"] == "OK" else "✗"
        print(f"  [{mark}] {p['text']!r} 期望抽取={p['should_extract']} "
              f"实际={p['got']} conf={p['confidences']}")
        print(f"       备注: {p['note']}")
    print("=" * 64)

    out = ROOT / "docs" / "experiments" / "data" / "extraction_pr.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "fixture": {"tp": tp, "fp": fp, "fn": fn,
                    "precision": precision, "recall": recall,
                    "per_rule": fx["per_rule"], "rows": fx["rows"]},
        "probes": pr,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"原始数据已写到 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
