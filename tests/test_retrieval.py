"""检索层单测：过滤器、双路请求构造、RRF 结果映射、去重、动态 topK、Recall/MRR。

全部用假 Milvus 客户端 + DummyEmbedder/DummyReranker，不连真实服务。
"""

from __future__ import annotations

import pytest

from app.ingest.embedder import DummyEmbedder, Embedding
from app.retrieval import hybrid
from app.retrieval.hybrid import Hit, build_filter, build_kb_filter, dedupe_by_parent, to_hit
from app.retrieval.rerank import DummyReranker
from app.retrieval.service import mrr, recall_at_k, retrieve


def _entity(chunk_id: str, parent: str, content: str, kb: str = "kb_labor",
            law: str = "中华人民共和国劳动合同法", article: str = "第一条") -> dict:
    return {
        "chunk_id": chunk_id,
        "parent_id": parent,
        "kb_id": kb,
        "doc_id": "DOC1",
        "content": content,
        "title": f"{law}·第一章·{article}",
        "article_no": article,
        "chapter_path": "第一章",
        "law_name": law,
        "source_file": "a.md",
    }


class FakeMilvus:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    def hybrid_search(self, **kwargs):
        self.calls.append({"type": "hybrid", **kwargs})
        return [[{"entity": r, "distance": 0.5 - i * 0.01} for i, r in enumerate(self.rows)]]

    def search(self, **kwargs):
        self.calls.append({"type": "dense", **kwargs})
        return [[{"entity": r, "distance": 0.9 - i * 0.01} for i, r in enumerate(self.rows)]]


class TestFilter:
    def test_empty_means_all(self):
        assert build_kb_filter(None) == ""
        assert build_kb_filter([]) == ""

    def test_single_and_multi(self):
        assert build_kb_filter(["kb_labor"]) == 'kb_id in ["kb_labor"]'
        assert build_kb_filter(["kb_labor", "kb_civil"]) == 'kb_id in ["kb_labor", "kb_civil"]'

    def test_law_hints_combined_with_kb(self):
        """法域预过滤：kb 条件与 law 条件用 and 拼接。"""
        expr = build_filter(["kb_labor"], ["中华人民共和国劳动合同法"])
        assert expr == 'kb_id in ["kb_labor"] and law_name in ["中华人民共和国劳动合同法"]'

    def test_law_hints_alone(self):
        assert build_filter(None, ["中华人民共和国劳动法"]) == (
            'law_name in ["中华人民共和国劳动法"]'
        )

    def test_no_law_hints_keeps_old_behaviour(self):
        """hints 为空（general/other 意图）时不能加过滤，否则误伤召回。"""
        assert build_filter(["kb_labor"], None) == build_kb_filter(["kb_labor"])
        assert build_filter(["kb_labor"], []) == build_kb_filter(["kb_labor"])


class TestRequests:
    def test_hybrid_builds_two_requests(self):
        emb = Embedding(dense=[0.1] * 1024, sparse={1: 0.5, 2: 0.3})
        reqs = hybrid.build_requests(emb, pool=20, expr='kb_id in ["kb_labor"]', mode="hybrid")
        assert len(reqs) == 2
        assert reqs[0].anns_field == "dense_vector"
        assert reqs[1].anns_field == "sparse_vector"
        assert reqs[0].limit == 20

    def test_dense_mode_single_request(self):
        emb = Embedding(dense=[0.1] * 1024, sparse={1: 0.5})
        assert len(hybrid.build_requests(emb, pool=12, expr="", mode="dense")) == 1

    def test_empty_sparse_degrades_to_dense(self):
        emb = Embedding(dense=[0.1] * 1024, sparse={})
        assert len(hybrid.build_requests(emb, pool=12, expr="", mode="hybrid")) == 1


class TestToHit:
    def test_maps_all_traceability_fields(self):
        hit = to_hit({"entity": _entity("D1:p1:c0", "D1:p1", "条文"), "distance": 0.42})
        assert hit.chunk_id == "D1:p1:c0"
        assert hit.parent_id == "D1:p1"
        assert hit.score == pytest.approx(0.42)
        assert hit.law_article == "中华人民共和国劳动合同法#第一条"
        assert hit.embed_text.startswith("中华人民共和国劳动合同法")


class TestDedupe:
    def test_same_parent_keeps_best(self):
        hits = [
            Hit(chunk_id="D1:p1:c0", parent_id="D1:p1", kb_id="k", doc_id="D1",
                content="a", title="t", article_no="第一条", chapter_path="", law_name="L",
                source_file="f", score=0.2),
            Hit(chunk_id="D1:p1:c1", parent_id="D1:p1", kb_id="k", doc_id="D1",
                content="b", title="t", article_no="第一条", chapter_path="", law_name="L",
                source_file="f", score=0.9),
        ]
        result = dedupe_by_parent(hits, topk=5)
        assert len(result) == 1
        assert result[0].chunk_id == "D1:p1:c1"

    def test_respects_topk(self):
        hits = [
            Hit(chunk_id=f"D1:p{i}:c0", parent_id=f"D1:p{i}", kb_id="k", doc_id="D1",
                content="x", title="t", article_no=f"第{i}条", chapter_path="", law_name="L",
                source_file="f", score=1.0 / i)
            for i in range(1, 11)
        ]
        assert len(dedupe_by_parent(hits, topk=3)) == 3

    def test_rerank_score_preferred(self):
        hits = [
            Hit(chunk_id="a", parent_id="a", kb_id="", doc_id="", content="", title="",
                article_no="", chapter_path="", law_name="", source_file="", score=0.9,
                rerank_score=0.1),
            Hit(chunk_id="b", parent_id="b", kb_id="", doc_id="", content="", title="",
                article_no="", chapter_path="", law_name="", source_file="", score=0.1,
                rerank_score=0.9),
        ]
        assert dedupe_by_parent(hits, topk=1)[0].chunk_id == "b"


class TestRetrieve:
    @pytest.fixture
    def fake(self):
        rows = [
            _entity("D1:p19:c0", "D1:p19", "试用期不得超过六个月。", article="第十九条"),
            _entity("D1:p39:c0", "D1:p39", "劳动者有下列情形之一的，用人单位可以解除劳动合同。", article="第三十九条"),
            _entity("D1:p46:c0", "D1:p46", "用人单位应当向劳动者支付经济补偿。", article="第四十六条"),
        ]
        return FakeMilvus(rows)

    def test_dynamic_topk_by_complexity(self, fake):
        for complexity, expected in (("simple", 3), ("medium", 5), ("complex", 8)):
            result = retrieve(
                "试用期多久", complexity=complexity, client=fake,
                embedder=DummyEmbedder(), reranker=DummyReranker(), expand_parent=False,
            )
            assert result.topk == expected
            assert result.pool == max(4 * expected, 12)
            assert len(result.hits) <= expected

    def test_rerank_reorders(self, fake):
        result = retrieve(
            "试用期 经济补偿", client=fake, embedder=DummyEmbedder(),
            reranker=DummyReranker(), expand_parent=False, topk=3,
        )
        assert result.reranked is True
        scores = [h.rerank_score for h in result.hits]
        assert scores == sorted(scores, reverse=True)

    def test_dense_mode_uses_client_search(self, fake):
        retrieve("x", client=fake, embedder=DummyEmbedder(), mode="dense",
                 use_rerank=False, expand_parent=False)
        assert fake.calls[0]["type"] == "dense"

    def test_hybrid_mode_uses_hybrid_search(self, fake):
        retrieve("试用期多久", client=fake, embedder=DummyEmbedder(), reranker=DummyReranker())
        assert fake.calls[0]["type"] == "hybrid"

    def test_default_ranker_is_weighted_dense_first(self, fake):
        """默认稠密主导（0.75/0.25）：等权 RRF 在跨法近义条上会稀释稠密信号。"""
        retrieve("试用期多久", client=fake, embedder=DummyEmbedder(), reranker=DummyReranker())
        spec = fake.calls[0]["ranker"].dict()
        assert spec["strategy"] == "weighted"
        assert spec["params"]["weights"] == [0.75, 0.25]

    def test_law_hints_filter_reaches_milvus(self, fake):
        retrieve("试用期多久", kb_ids=["kb_labor"],
                 law_hints=["中华人民共和国劳动合同法"],
                 client=fake, embedder=DummyEmbedder(), use_rerank=False, expand_parent=False)
        expr = fake.calls[0]["reqs"][0].expr
        assert 'law_name in ["中华人民共和国劳动合同法"]' in expr
        assert 'kb_id in ["kb_labor"]' in expr

    def test_kb_filter_passed(self, fake):
        retrieve("试用期多久", kb_ids=["kb_civil"], client=fake, embedder=DummyEmbedder(),
                 use_rerank=False, expand_parent=False)
        call = fake.calls[0]
        assert call["reqs"][0].expr == 'kb_id in ["kb_civil"]'

    def test_empty_question_short_circuits(self, fake):
        result = retrieve("   ", client=fake, embedder=DummyEmbedder())
        assert result.hits == []
        assert fake.calls == []

    def test_elapsed_and_debug(self, fake):
        result = retrieve("试用期多久", client=fake, embedder=DummyEmbedder(),
                          reranker=DummyReranker(), expand_parent=False)
        assert result.elapsed_ms >= 0
        assert result.debug["candidates"] == 3


class TestMetrics:
    def _hits(self, articles: list[str]) -> list[Hit]:
        return [
            Hit(chunk_id=f"c{i}", parent_id=f"p{i}", kb_id="", doc_id="", content="",
                title="", article_no=a, chapter_path="", law_name="L", source_file="")
            for i, a in enumerate(articles)
        ]

    def test_recall_at_k(self):
        hits = self._hits(["第一条", "第二条", "第三条"])
        assert recall_at_k(hits, {"L#第二条"}, 5) == 1.0
        assert recall_at_k(hits, {"L#第三条"}, 2) == 0.0
        assert recall_at_k(hits, {"L#第二条", "L#第九条"}, 5) == 0.5

    def test_recall_empty_expected(self):
        assert recall_at_k(self._hits(["第一条"]), set(), 3) == 0.0

    def test_mrr(self):
        assert mrr(self._hits(["第一条", "第二条"]), {"L#第二条"}) == 0.5
        assert mrr(self._hits(["第九条"]), {"L#第二条"}) == 0.0
