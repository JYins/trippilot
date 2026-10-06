"""敏感记忆统一分类与 Memory Gate 绕过回归。"""

from trippilot.memory.extract import extract_candidates
from trippilot.memory.pii import classify_pii
from trippilot.memory.store import PreferenceStore, hash_embedder


def test_normal_label_cannot_bypass_address_gate(tmp_path):
    store = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    try:
        result = store.remember(
            "owner",
            "北京市朝阳区阜通东大街6号",
            kind="place",
            sensitivity="normal",
        )
        assert result == ("needs_confirm", None)
        assert store.recall("owner", "阜通东大街") == []
    finally:
        store.close()


def test_precise_address_is_sensitive_after_extraction():
    candidates = extract_candidates("记住北京市朝阳区阜通东大街6号")
    assert len(candidates) == 1
    assert candidates[0]["content"] == "北京市朝阳区阜通东大街6号"
    assert candidates[0]["sensitivity"] == "sensitive"


def test_identity_card_and_phone_are_pii():
    assert classify_pii("身份证号11010519491231002X") is True
    assert classify_pii("联系电话13800138000") is True


def test_normal_preference_is_not_pii():
    assert classify_pii("我喜欢喝美式") is False
    candidates = extract_candidates("我喜欢喝美式")
    assert candidates[0]["sensitivity"] == "normal"
