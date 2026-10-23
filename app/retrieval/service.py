"""检索编排：编码 → 双路召回 → (重排) → 去重 → 父块回填。

一次调用的数据流::

    question
      → bge-m3 编码（dense 1024 + sparse）
      → Milvus hybrid_search(dense, sparse) + RRFRanker(k=60)   [候选池 = max(4×topK, 12)]
      → bge-reranker-v2-m3 重排（可选，消融时关掉）
      → 按 parent_id 去重（同条多子块只留最高分）
      → 截断到 topK（simple 3 / medium 5 / complex 8）
      → MySQL 批量回表取整条法条正文（保证进 prompt 的是完整条文）

mode=dense 用于消融实验（L1 组：仅稠密）。
"""

from __future__ import annotations

import time

from sqlalchemy import bindparam, text

from app.core.logger import get_logger
from app.ingest.embedder import Embedder, get_embedder
from app.retrieval import hybrid
from app.retrieval.hybrid import Hit, RetrievalResult
from app.retrieval.rerank import Reranker, get_reranker
from config import Settings, get_settings

logger = get_logger("lawanswer.retrieval")


def retrieve(
    question: str,
    *,
    kb_ids: list[str] | None = None,
    complexity: str = "medium",
    topk: int | None = None,
    mode: str = "hybrid",
    use_rerank: bool = True,
    expand_parent: bool = True,
    law_hints: list[str] | None = None,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
    client=None,
    collection: str | None = None,
    settings: Settings | None = None,
) -> RetrievalResult:
    s = settings or get_settings()
    started = time.perf_counter()
    resolved_topk = topk or s.retrieval.topk_for(complexity)
    pool = s.retrieval.pool_for(resolved_topk)
    collection = collection or s.milvus_collection
    # 意图层给出的法域（1-3 部法律）；为空则全库检索
    hints = list(law_hints or []) if s.retrieval_law_filter else []

    if not question.strip():
        return RetrievalResult([], question, resolved_topk, pool, complexity, mode, False, 0)

    embedder = embedder or get_embedder()
    client = client or _default_client()

    embedding = embedder.encode([question])[0]
    candidates = hybrid.search(
        client,
        collection,
        embedding,
        pool=pool,
        kb_ids=kb_ids,
        mode=mode,
        rrf_k=s.retrieval.rrf_k,
        ranker=s.retrieval_ranker,
        dense_weight=s.retrieval.dense_weight,
        sparse_weight=s.retrieval.sparse_weight,
        law_hints=hints,
    )
    dense_hits = len(candidates)

    reranked = False
    if use_rerank and candidates:
        reranker = reranker or get_reranker(s)
        scores = reranker.score(question, [c.embed_text for c in candidates])
        for hit, score in zip(candidates, scores):
            hit.rerank_score = score
        candidates.sort(key=lambda h: h.rerank_score if h.rerank_score is not None else h.score, reverse=True)
        reranked = True

    hits = hybrid.dedupe_by_parent(candidates, topk=resolved_topk)
    if expand_parent and hits:
        _attach_parents(hits)

    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "检索完成 q=%.40s kb=%s law_hints=%s complexity=%s topK=%d pool=%d 候选=%d 重排=%s 命中=%d 耗时=%dms",
        question,
        kb_ids or "all",
        hints or "all",
        complexity,
        resolved_topk,
        pool,
        dense_hits,
        reranked,
        len(hits),
        elapsed,
    )
    return RetrievalResult(
        hits=hits,
        question=question,
        topk=resolved_topk,
        pool=pool,
        complexity=complexity,
        mode=mode,
        reranked=reranked,
        elapsed_ms=elapsed,
        debug={
            "candidates": dense_hits,
            "pool": pool,
            "kb_ids": kb_ids or [],
            "law_hints": hints,
            "rrf_k": s.retrieval.rrf_k,
            "ranker": s.retrieval_ranker,
            "mode": mode,
        },
    )


def _attach_parents(hits: list[Hit]) -> None:
    """按 parent_id 批量回表取整条法条，避免 N 次查询。"""
    parent_ids = [h.parent_id or h.chunk_id for h in hits]
    from app.db.mysql import get_engine

    stmt = text(
        "SELECT id, content FROM document_chunks "
        "WHERE id IN :ids AND chunk_type = 'parent' AND is_enabled = 1"
    ).bindparams(bindparam("ids", expanding=True))
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(stmt, {"ids": parent_ids}).fetchall()
    except Exception as exc:  # noqa: BLE001 - 回表失败不应让整次检索失败
        logger.warning("父块回表失败，降级使用子块正文：%s", exc)
        return
    contents = {row[0]: row[1] for row in rows}
    for hit in hits:
        hit.parent_content = contents.get(hit.parent_id or hit.chunk_id, "")


def _default_client():
    from app.db.milvus_client import get_milvus_db_client

    return get_milvus_db_client()


def recall_at_k(hits: list[Hit], expected: set[str], k: int) -> float:
    """条级 Recall@K：命中任一子块即该条命中。

    ``expected`` 用 ``法名#条号`` 标识，避免两部法律同条号混淆。
    """
    if not expected:
        return 0.0
    got = {h.law_article for h in hits[:k]}
    return len(got & expected) / len(expected)


def mrr(hits: list[Hit], expected: set[str]) -> float:
    for rank, hit in enumerate(hits, start=1):
        if hit.law_article in expected:
            return 1.0 / rank
    return 0.0
