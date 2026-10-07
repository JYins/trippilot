"""记忆审计与 Qdrant 数据同机存放，二者处于同一信任边界。

将来迁移到服务端 PostgreSQL 时，这张表必须补上按用户隔离的访问控制。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class MemoryAudit:
    def __init__(self, db_path: str | Path) -> None:
        self._connection = sqlite3.connect(db_path)
        self._connection.row_factory = sqlite3.Row
        self._create_schema()

    def _create_schema(self) -> None:
        self._connection.execute("""
            CREATE TABLE IF NOT EXISTS memory_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                user_id TEXT NOT NULL,
                event TEXT NOT NULL,
                memory_id TEXT,
                content TEXT,
                reason TEXT
            )
        """)
        self._connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_memory_events_user_ts
            ON memory_events(user_id, ts)
        """)
        self._connection.commit()

    def log_event(self, user_id: str, event: str,
                  memory_id: str | None = None,
                  content: str | None = None,
                  reason: str | None = None) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        cursor = self._connection.execute(
            """
            INSERT INTO memory_events (
                ts, user_id, event, memory_id, content, reason
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (ts, user_id, event, memory_id, content, reason),
        )
        self._connection.commit()
        return cursor.lastrowid

    def list_events(self, user_id: str, limit: int = 50) -> list[dict]:
        rows = self._connection.execute(
            """
            SELECT id, ts, user_id, event, memory_id, content, reason
            FROM memory_events
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._connection.close()
