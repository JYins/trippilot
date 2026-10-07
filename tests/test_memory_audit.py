from trippilot.memory import MemoryAudit
from trippilot.memory.store import PreferenceStore, hash_embedder


USER_ID = "audit-user"


def _events(db_path):
    audit = MemoryAudit(db_path)
    try:
        return audit.list_events(USER_ID)
    finally:
        audit.close()


def test_audit_written_logged(tmp_path):
    audit_path = tmp_path / "audit.db"
    store = PreferenceStore(
        tmp_path / "qdrant",
        embed_fn=hash_embedder(),
        audit_path=audit_path,
    )
    try:
        status, memory_id = store.remember(
            USER_ID, "常去地点：首都机场 T3", kind="place")
    finally:
        store.close()

    events = _events(audit_path)
    assert status == "written"
    assert len(events) == 1
    assert events[0]["event"] == "written"
    assert events[0]["memory_id"] == memory_id


def test_audit_updated_logged(tmp_path):
    audit_path = tmp_path / "audit.db"
    store = PreferenceStore(
        tmp_path / "qdrant",
        embed_fn=hash_embedder(),
        audit_path=audit_path,
    )
    try:
        _, memory_id = store.remember(
            USER_ID, "常去地点：首都机场 T3", kind="place")
        store.update(memory_id, content="常去地点：大兴机场")
    finally:
        store.close()

    events = _events(audit_path)
    assert events[0]["event"] == "updated"
    assert events[0]["content"] == "常去地点：大兴机场"


def test_audit_forgotten_logged(tmp_path):
    audit_path = tmp_path / "audit.db"
    store = PreferenceStore(
        tmp_path / "qdrant",
        embed_fn=hash_embedder(),
        audit_path=audit_path,
    )
    try:
        _, memory_id = store.remember(
            USER_ID, "常去地点：首都机场 T3", kind="place")
        store.forget(memory_id)
    finally:
        store.close()

    events = _events(audit_path)
    assert events[0]["event"] == "forgotten"
    assert events[0]["memory_id"] == memory_id


def test_audit_list_order_limit(tmp_path):
    audit_path = tmp_path / "audit.db"
    store = PreferenceStore(
        tmp_path / "qdrant",
        embed_fn=hash_embedder(),
        audit_path=audit_path,
    )
    try:
        store.remember(USER_ID, "常去地点：机场", kind="place")
        store.remember(USER_ID, "称呼：小王", kind="label")
        store.remember(USER_ID, "授权偏好：每次询问", kind="auth")
    finally:
        store.close()

    audit = MemoryAudit(audit_path)
    try:
        events = audit.list_events(USER_ID, limit=2)
    finally:
        audit.close()

    assert len(events) == 2
    assert events[0]["id"] > events[1]["id"]
