"""实验 B 脚本测试：全部离线。

fixture 上的 P/R=1.0 是已知断言（见 test_memory_extract.py），这里只测
extraction_pr.py 的统计逻辑本身；探针的 7 个误判是"宁可误抽不漏记"
的设计取舍（见 decisions/20251005-memory-extraction.md），
测的是"误抽确实存在且可复现"，不是"误抽不该发生"。
"""

from trippilot.memory.extract import extract_candidates
from eval.extraction_pr import EXTRA_PROBES, run_fixture, run_probes


def test_fixture_pr_is_perfect():
    fx = run_fixture()
    assert fx["tp"] == 10 and fx["fp"] == 0 and fx["fn"] == 0
    for rule, st in fx["per_rule"].items():
        assert st["fp"] == 0 and st["fn"] == 0, rule


def test_documented_false_positives_are_reproducible():
    # 设计时已知的误抽模式，探针必须能稳定复现（否则"已知风险"是空话）
    assert [c["content"] for c in
            extract_candidates("别走这条路")] == ["别走这条路"]
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


def test_bie_bu_yao_is_the_worst_rule_on_probes():
    # 别/不要型探针 precision 最低：这是"最拉胯规则"结论的数据依据
    probes = run_probes()
    bie = [p for p in probes if p["text"] in
           ("别走这条路", "今天堵车，以后别走这条路", "别急，慢慢来",
            "不要急", "以后别再给我放广告了")]
    tp = sum(1 for p in bie if p["should_extract"] and p["got"])
    fp = sum(1 for p in bie if not p["should_extract"] and p["got"])
    assert tp == 1 and fp == 4
    assert tp / (tp + fp) == 0.2
