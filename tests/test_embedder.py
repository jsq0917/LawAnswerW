"""向量化测试：Dummy 确定性、稀疏过滤、Milvus 实体转换、设备回退。"""

from __future__ import annotations

import math

import pytest

from app.ingest.embedder import (
    SPARSE_MIN_WEIGHT,
    BgeM3Embedder,
    DummyEmbedder,
    Embedding,
    embeddings_to_milvus,
)


class TestDummy:
    def test_dim_and_norm(self):
        emb = DummyEmbedder(dim=1024).encode(["试用期不得超过六个月。"])[0]
        assert len(emb.dense) == 1024
        norm = math.sqrt(sum(v * v for v in emb.dense))
        assert abs(norm - 1.0) < 1e-6

    def test_deterministic(self):
        embedder = DummyEmbedder()
        a = embedder.encode(["同一段文本"])[0]
        b = embedder.encode(["同一段文本"])[0]
        assert a.dense == b.dense
        assert a.sparse == b.sparse

    def test_different_text_different_vector(self):
        embedder = DummyEmbedder()
        a = embedder.encode(["文本一"])[0]
        b = embedder.encode(["文本二"])[0]
        assert a.dense != b.dense

    def test_sparse_keys_are_ints_in_range(self):
        emb = DummyEmbedder().encode(["竞业限制期限不得超过二年。"])[0]
        assert emb.sparse
        assert all(isinstance(k, int) and 0 <= k < 250_000 for k in emb.sparse)
        assert all(v >= SPARSE_MIN_WEIGHT for v in emb.sparse.values())

    def test_empty_input(self):
        assert DummyEmbedder().encode([]) == []

    def test_batch_matches_single(self):
        embedder = DummyEmbedder()
        batched = embedder.encode(["甲", "乙"])
        assert [e.dense for e in batched] == [embedder.encode([t])[0].dense for t in ("甲", "乙")]


class TestMilvusConvert:
    def test_dense_and_sparse_present(self):
        emb = Embedding(dense=[0.1] * 1024, sparse={5: 0.3, 9: 0.7})
        entity = embeddings_to_milvus(emb)
        assert len(entity["dense_vector"]) == 1024
        assert entity["sparse_vector"] == {5: 0.3, 9: 0.7}

    def test_empty_sparse_gets_placeholder(self):
        """Milvus 拒绝空稀疏向量，必须给占位值。"""
        entity = embeddings_to_milvus(Embedding(dense=[0.0] * 1024, sparse={}))
        assert entity["sparse_vector"] == {0: 0.0}

    def test_sparse_arrays_sorted(self):
        emb = Embedding(dense=[], sparse={9: 0.9, 2: 0.2})
        indices, values = emb.sparse_arrays()
        assert indices == [2, 9]
        assert values == [0.2, 0.9]


class TestDeviceResolution:
    def test_falls_back_to_cpu_without_cuda(self):
        embedder = BgeM3Embedder("dummy", device="cuda")
        torch = pytest.importorskip("torch")
        expected = "cuda" if torch.cuda.is_available() else "cpu"
        assert embedder._resolve_device() == expected

    def test_explicit_cpu(self):
        assert BgeM3Embedder("dummy", device="cpu")._resolve_device() == "cpu"
