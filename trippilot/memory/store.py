"""用户偏好记忆：qdrant 本地文件模式 + 向量召回。

为什么是 qdrant 本地模式：见 docs/decisions/20251005-qdrant-preference-memory.md。
写入入口是 remember() / update()，内部先过 Memory Gate（policy_gate.py）：
is_transient 直接拒绝、sensitive 走确认，保证门禁不会被绕过。
"""

from __future__ import annotations

import hashlib
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.models import (Distance, FieldCondition, Filter, MatchValue,
                                  PayloadSchemaType, PointStruct, VectorParams)

from ..policy_gate import evaluate_memory_candidate

EmbedFn = Callable[[list[str]], list[list[float]]]
PreferenceKind = Literal["place", "label", "auth", "other"]
Sensitivity = Literal["normal", "sensitive"]

COLLECTION = "preferences"
DEFAULT_PATH = Path.home() / ".trippilot" / "memory"


class Preference(BaseModel):
    id: str
    user_id: str
    kind: PreferenceKind            # place 常去地点 / label 称呼 / auth 授权偏好 / other
    content: str
    sensitivity: Sensitivity = "normal"
    source_type: str = "chat"
    created_at: str = ""


class MemoryRejected(Exception):
    """Memory Gate 判了 deny：调用方按 reason_code 转成用户话术。"""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(detail or reason_code)
        self.reason_code = reason_code
        self.detail = detail


def hash_embedder(dim: int = 64) -> EmbedFn:
    """确定性 fake 向量：测试/离线用，不下载模型。

    中文短文本按字二元切分做哈希词袋，口语化短文本
    （"送我去公司" vs "公司地址：望京 SOHO"）能共享字二元。
    生产召回质量以 BGE 为准，不拿这个充数。
    """

    def embed(texts: list[str]) -> list[list[float]]:
        vecs = []
        for text in texts:
            v = [0.0] * dim
            t = text.lower().strip()
            grams = [t[i:i + 2] for i in range(len(t) - 1)] or [t]
            for g in grams:
                h = int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16)
                v[h % dim] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            vecs.append([x / norm for x in v])
        return vecs

    return embed


# bge-small-en-v1.5 的旧库是 384 维，新模型是 512 维，两者不兼容；单用户
# 本地原型直接删库重建，不为这批本地数据增加自动迁移逻辑。
def _bge_default() -> EmbedFn:
    try:
        from sentence_transformers import SentenceTransformer
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "缺少 sentence-transformers，无法加载默认 BGE 模型；"
            "请运行 `pip install sentence-transformers>=3.0` 安装。"
        ) from exc

    model = SentenceTransformer("BAAI/bge-small-zh-v1.5")

    def embed(texts: list[str]) -> list[list[float]]:
        vectors = model.encode(texts, normalize_embeddings=True)
        return [list(vector) for vector in vectors]

    return embed


class PreferenceStore:
    """偏好存储：QdrantClient(path=...) 本地文件模式，无 server 无端口。

    单进程假设（本地文件模式多进程并发写有文件锁限制），
    见 decisions/20251005-qdrant-preference-memory.md。
    """

    def __init__(self, path: str | Path = DEFAULT_PATH,
                 embed_fn: EmbedFn | None = None) -> None:
        self._client = QdrantClient(path=str(path))
        self._embed_fn = embed_fn

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if self._embed_fn is None:
            # 生产默认 BGE 首次加载较慢且可能下载，避免未使用记忆时承担开销。
            self._embed_fn = _bge_default()
        return self._embed_fn(texts)

    def _ready(self, dim: int | None = None) -> bool:
        """collection 可用返回 True；不存在且不知道向量维度时返回 False。"""
        if not self._client.collection_exists(COLLECTION):
            if dim is None:  # 还没写过任何东西，没什么可查的
                return False
            self._client.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )
            # 本地模式下 payload 索引不起作用（但 filter 照常工作）；
            # 建索引是为将来切 server 模式留的，这个 warning 是预期内的
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message=".*Payload indexes have no effect.*")
                self._client.create_payload_index(
                    COLLECTION, field_name="user_id",
                    field_schema=PayloadSchemaType.KEYWORD)
        return True

    @staticmethod
    def _to_preference(point_id: object, payload: dict) -> "Preference":
        return Preference(**{**payload, "id": str(point_id)})

    def remember(self, user_id: str, content: str, kind: PreferenceKind,
                 sensitivity: Sensitivity = "normal",
                 source_type: str = "chat",
                 is_transient: bool = False) -> tuple[str, str | None]:
        """先过 Memory Gate 再写。

        返回 ("written", id) / ("needs_confirm", None)；
        deny 直接抛 MemoryRejected。needs_confirm 的不写入，
        由调用方（graph 的 human_confirm 流程）确认后再处理。
        """
        decision = evaluate_memory_candidate({
            "content": content, "source_type": source_type,
            "is_transient": is_transient, "sensitivity": sensitivity,
        })
        if decision.decision == "deny":
            raise MemoryRejected(decision.reason_code, decision.detail)
        if decision.decision == "confirm":
            return "needs_confirm", None
        return "written", self._insert(user_id, content, kind,
                                       sensitivity, source_type)

    def _insert(self, user_id: str, content: str, kind: PreferenceKind,
                sensitivity: Sensitivity, source_type: str) -> str:
        """实际写盘（不含 Gate）。

        调用前必须已过 evaluate_memory_candidate：要么判 allow，
        要么判 confirm 且用户已在 human_confirm 确认。
        graph 的 memory_capture 是除 remember 外唯一的调用方。
        """
        vec = self._embed([content])[0]
        self._ready(len(vec))
        pref = Preference(
            id=uuid.uuid4().hex, user_id=user_id, kind=kind, content=content,
            sensitivity=sensitivity, source_type=source_type,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._client.upsert(COLLECTION, [PointStruct(
            id=pref.id, vector=vec, payload=pref.model_dump())])
        return pref.id

    def recall(self, user_id: str, query: str, top_k: int = 5,
               kind: PreferenceKind | None = None) -> list[Preference]:
        """向量检索 + user_id 过滤；还没记过任何东西时返回 []。"""
        if not self._ready():
            return []
        vec = self._embed([query])[0]
        must = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        if kind is not None:
            must.append(FieldCondition(key="kind",
                                       match=MatchValue(value=kind)))
        hits = self._client.query_points(
            COLLECTION, query=vec, query_filter=Filter(must=must),
            limit=top_k).points
        # 取舍：cosine 可为负；fake 向量（哈希词袋）恒非负所以分数 >= 0；
        # > 0 会静默丢掉弱相关（正交或负相关）的命中，换召回率保精确率
        return [self._to_preference(h.id, h.payload)
                for h in hits if h.score > 0]

    def get(self, memory_id: str) -> Preference | None:
        if not self._ready():
            return None
        records = self._client.retrieve(COLLECTION, ids=[memory_id])
        if not records:
            return None
        return self._to_preference(records[0].id, records[0].payload)

    def update(self, memory_id: str, content: str | None = None,
               kind: PreferenceKind | None = None,
               sensitivity: Sensitivity | None = None,
               source_type: str | None = None,
               is_transient: bool = False) -> tuple[str, Preference | None]:
        """改 content / sensitivity 先过 Memory Gate，语义和 remember 一致。

        返回 ("written", pref) / ("needs_confirm", None)；
        deny 抛 MemoryRejected，id 不存在抛 KeyError。
        只改 kind / source_type 这类非敏感字段时直接写；
        content 没变时复用已存向量，不重新向量化。
        """
        old = self.get(memory_id)
        if old is None:
            raise KeyError(f"preference {memory_id} 不存在")
        content_changed = content is not None and content != old.content
        sens_changed = (sensitivity is not None
                        and sensitivity != old.sensitivity)
        data = old.model_dump()
        if content is not None:
            data["content"] = content
        if kind is not None:
            data["kind"] = kind
        if sensitivity is not None:
            data["sensitivity"] = sensitivity
        if source_type is not None:
            data["source_type"] = source_type

        if content_changed or sens_changed:
            decision = evaluate_memory_candidate({
                "content": data["content"],
                "source_type": data["source_type"],
                "is_transient": is_transient,
                "sensitivity": data["sensitivity"],
            })
            if decision.decision == "deny":
                raise MemoryRejected(decision.reason_code, decision.detail)
            if decision.decision == "confirm":
                return "needs_confirm", None
            vec = self._embed([data["content"]])[0]
            self._ready(len(vec))
        else:
            vec = self._stored_vector(memory_id)
        pref = Preference(**data)
        self._client.upsert(COLLECTION, [PointStruct(
            id=pref.id, vector=vec, payload=pref.model_dump())])
        return "written", pref

    def _stored_vector(self, memory_id: str) -> list[float]:
        """取已存点的向量：content 没变时复用，省一次向量化。"""
        records = self._client.retrieve(COLLECTION, ids=[memory_id],
                                        with_vectors=True)
        return list(records[0].vector)

    def forget(self, memory_id: str) -> None:
        if self._ready():
            self._client.delete(COLLECTION, points_selector=[memory_id])

    def forget_all(self, user_id: str) -> None:
        if self._ready():
            self._client.delete(COLLECTION, points_selector=Filter(must=[
                FieldCondition(key="user_id",
                               match=MatchValue(value=user_id))]))

    def close(self) -> None:
        self._client.close()
