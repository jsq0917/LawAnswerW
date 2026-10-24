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
from concurrent.futures import ThreadPoolExecutor
from typing import Sequence

from sqlalchemy import bindparam, text

from app.core.logger import get_logger
from app.ingest.embedder import Embedder, get_embedder
from app.retrieval import hybrid
from app.retrieval.hybrid import Hit, RetrievalResult
from app.retrieval.rerank import Reranker, get_reranker
from config import Settings, get_settings

logger = get_logger("lawanswer.retrieval")

#: 多子查询检索时每个子查询的 topK 放大系数（合并去重后截断回最终 topK）
MULTI_POOL_FACTOR = 1.5


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
        # 重排前按法条（parent）预去重：同条多子块重复送 cross-encoder 既浪费算力，
        # 半句子块也容易误导打分。每条只保留融合分最高的子块。
        if len(candidates) > 1:
            candidates = hybrid.dedupe_by_parent(candidates, topk=len(candidates))
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


def retrieve_multi(
    sub_queries: Sequence[str],
    *,
    kb_ids: list[str] | None = None,
    complexity: str = "medium",
    topk: int | None = None,
    law_hints: list[str] | None = None,
    settings: Settings | None = None,
    retrieve_fn=None,
    max_workers: int = 3,
) -> RetrievalResult:
    """多子查询检索：**并行**分检 → 按法条（parent_id）合并去重 → 全局截断。

    生产链路（``AnswerEngine._retrieve``）与 ``recall_eval`` 共用本函数，
    保证"评测口径 = 线上链路"（REPORT §4.2 假设 C：多跳问题必须拆子查询分检）。

    * 每个子查询取 ``MULTI_POOL_FACTOR × 最终topK`` 条，给全局合并留余量；
    * 合并按 ``parent_id``（同一法条的不同子块只留综合分最高的一条），
      不能按 chunk_id——那会让同一法条重复挤占最终 topK 和上下文；
    * 跨子查询的 rerank_score 不可直接比较（各自对不同 query 打分），
      取每条法条在所有子查询下的**最高分**作全局序（max 聚合）；
    * 个别子查询失败不致命：只要有任一成功就继续（部分降级），全失败才抛异常。
    """
    s = settings or get_settings()
    fn = retrieve_fn or retrieve
    final_topk = topk or s.retrieval.topk_for(complexity)
    per_topk = max(int(final_topk * MULTI_POOL_FACTOR), final_topk)
    subs = [q.strip() for q in sub_queries if q and q.strip()]
    if not subs:
        raise ValueError("sub_queries 为空")
    started = time.perf_counter()

    def _one(q: str):
        return fn(
            q,
            kb_ids=kb_ids,
            complexity=complexity,
            topk=per_topk,
            law_hints=law_hints,
            settings=s,
        )

    errors: list[Exception] = []
    results: list = []
    if len(subs) == 1:
        results.append(_one(subs[0]))
    else:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(subs))) as ex:
            futures = [(q, ex.submit(_one, q)) for q in subs]
            for q, fut in futures:
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001 - 部分子查询失败不拖垮整体
                    errors.append(exc)
                    logger.warning("子查询检索失败（部分降级）q=%.40s：%s", q, str(exc)[:120])
        if not results:
            raise errors[0]

    merged: dict[str, Hit] = {}
    reranked = False
    for r in results:
        reranked = reranked or r.reranked
        for hit in r.hits:
            key = hit.parent_id or hit.chunk_id
            prev = merged.get(key)
            if prev is None or hybrid._rank_value(hit) > hybrid._rank_value(prev):
                merged[key] = hit
    hits = sorted(merged.values(), key=hybrid._rank_value, reverse=True)[:final_topk]
    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "多子查询检索完成：子查询 %d 条 × topK=%d → 法条去重 %d 条 → 截断 %d 条"
        "（重排=%s，失败子查询 %d 条）耗时=%dms",
        len(subs), per_topk, len(merged), len(hits), reranked, len(errors), elapsed,
    )
    return RetrievalResult(
        hits=hits,
        question="｜".join(subs),
        topk=final_topk,
        pool=per_topk,
        complexity=complexity,
        mode="multi",
        reranked=reranked,
        elapsed_ms=elapsed,
        debug={
            "sub_queries": subs,
            "per_subquery_topk": per_topk,
            "failed_subqueries": len(errors),
        },
    )
