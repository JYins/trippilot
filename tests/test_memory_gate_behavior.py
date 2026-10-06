"""Memory Gate 写入行为：deny 抛异常且不写盘，confirm 挂起不写盘，allow 直接写。"""

import pytest

from trippilot.memory.store import (MemoryRejected, PreferenceStore,
                                    hash_embedder)

UID = "u_gate"


@pytest.fixture()
def store(tmp_path):
    s = PreferenceStore(tmp_path / "qdrant", embed_fn=hash_embedder())
    try:
        yield s
    finally:
        s.close()


def test_transient_rejected_and_not_written(store):
    with pytest.raises(MemoryRejected) as exc:
        store.remember(UID, "临时去一趟打印店", kind="other",
                       is_transient=True)
    assert exc.value.reason_code == "transient_memory_rejected"
    assert store.recall(UID, "打印店") == []


def test_sensitive_needs_confirm_and_not_written(store):
    status, mid = store.remember(UID, "家庭住址：望京XX小区1号楼",
                                 kind="place", sensitivity="sensitive")
    assert (status, mid) == ("needs_confirm", None)
    assert store.recall(UID, "家庭住址") == []


def test_normal_written_immediately(store):
    status, mid = store.remember(UID, "偏好地铁出行", kind="other")
    assert status == "written" and mid
    got = store.get(mid)
    assert got is not None and got.content == "偏好地铁出行"
