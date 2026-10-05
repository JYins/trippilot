"""用户偏好记忆：qdrant 本地文件模式 + 向量召回 + 对话自动抽取。"""

from .extract import extract_candidates
from .store import (DEFAULT_PATH, MemoryRejected, Preference,
                    PreferenceStore, hash_embedder)

__all__ = ["DEFAULT_PATH", "MemoryRejected", "Preference",
           "PreferenceStore", "extract_candidates", "hash_embedder"]
