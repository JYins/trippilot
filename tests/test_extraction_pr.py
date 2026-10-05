"""实验 B 脚本测试：全部离线。

fixture 上的 P/R=1.0 是已知断言（见 test_memory_extract.py），这里只测
extraction_pr.py 的统计逻辑本身；探针里剩下的 3 个误判（"我喜欢今天的
歌单"当下情绪、"叫我一声哥听听"玩笑称呼、"他让我记住明天开会"复述）
是"宁可误抽不漏记"的设计取舍（见 decisions/20251005-memory-extraction.md），
测的是"误抽确实存在且可复现"，不是"误抽不该发生"。
"别走这条路"曾在此列，20251006 起裸"别/不要"不再触发抽取（见
decisions/20251006-memory-negation-fix.md），已移入回归测试。
"""

from trippilot.memory.extract import extract_candidates
from eval.extraction_pr import EXTRA_PROBES, run_fixture, run_probes


def test_fixture_pr_is_perfect():
    fx = run_fixture()
    # 20251006 起 13 条正例（10 旧 + 3 持久型否定回归），0 FP/FN
    assert fx["tp"] == 13 and fx["fp"] == 0 and fx["fn"] == 0
    for rule, st in fx["per_rule"].items():
        assert st["fp"] == 0 and st["fn"] == 0, rule


def test_documented_false_positives_are_reproducible():
    # 设计时已知的误抽模式，探针必须能稳定复现（否则"已知风险"是空话）。
    # "别走这条路"曾在此列，20251006 修复（裸"别/不要"不再触发抽取），
    # 回归见 test_memory_extract.py::test_bare_negation_suppressed。
    assert [c["content"] for c in
            extract_candidates("我喜欢今天的歌单")] == ["我喜欢今天的歌单"]
    assert [c["content"] for c in
            extract_candidates("叫我一声哥听听")] == ["一声哥听听"]
    assert [c["content"] for c in
            extract_candidates("他让我记住明天开会")] == ["明天开会"]


def test_probes_cover_the_two_focus_areas():
    # 实验 B 的两个重点：别/不要（0.65 最低置信）、我喜欢（情绪 vs 偏好）
    texts = [t for t, _, _ in EXTRA_PROBES]
    assert any(t.startswith("别") or "别" in t for t in texts)
    assert any("我喜欢" in t for t in texts)


def test_negation_rule_only_fires_on_persistent_markers():
    # 回归 20251006：否定规则只认"以后/再"持久标记。
    # 5 条否定探针现在应该全对：3 条一次性指令不抽，
    # 2 条持久型（"以后别再…""今天堵车，以后别…"改判）抽到。
    probes = run_probes()
    neg = [p for p in probes if p["text"] in
           ("别走这条路", "今天堵车，以后别走这条路", "别急，慢慢来",
            "不要急", "以后别再给我放广告了")]
    assert all(p["verdict"] == "OK" for p in neg)
    tp = sum(1 for p in neg if p["should_extract"] and p["got"])
    fp = sum(1 for p in neg if not p["should_extract"] and p["got"])
    assert tp == 2 and fp == 0
