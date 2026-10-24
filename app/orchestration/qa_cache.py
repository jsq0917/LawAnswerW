"""高频问答检索（结构化链路，设计方案 §6.4）。

三态判定 + Redis 热缓存::

    query → 归一化（去标点/停用词）
          → Redis qa:exact:{sha1} 精确缓存命中？          —— 是 → 返回（毫秒级）
          → MySQL ngram 全文 BM25 召回 top3
          → bge-m3 余弦(query, top1.question_std) ≥ 0.90？ —— 是 → 精确命中
          → 未命中但命中话题 → 取同类 2 条作参考（与法条合并，不写缓存）

Redis key 设计（TTL 见 :mod:`config`）：

===============  ==================================  ======
key              内容                                  TTL
===============  ==================================  ======
qa:exact:{sha1}   完整问答对 JSON                       7 天
qa:ans:{id}       单条标准答案                          1 天
qa:hot            ZSET 热问题 id（按 hit_count）        持久
===============  ==================================  ======
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from app.core.logger import get_logger
from app.db.mysql import get_engine
from app.db.redis_client import get_redis
from app.ingest.embedder import Embedder, get_embedder
from config import Settings, get_settings

logger = get_logger("lawanswer.qa_cache")

#: 精确缓存 key 前缀
REDIS_EXACT_PREFIX = "qa:exact:"
#: 单条答案 key 前缀
REDIS_ANS_PREFIX = "qa:ans:"
#: 热问题 ZSET
REDIS_HOT_KEY = "qa:hot"

#: 轻量停用词（BM25 归一化用；保留"怎么/什么"等内容词）
STOP_WORDS: tuple[str, ...] = (
    "请问", "我想问", "想问一下", "问一下", "一下", "我想", "我们", "咱们",
    "的", "了", "吗", "呢", "啊", "呀", "吧", "诶", "哦",
)

#: 去标点/空白（保留中文、字母、数字）
#: 三引号避免字符串内 ASCII 单双引号截断
_PUNCT_RE = re.compile(
    r'''[\s，。！？、；：“”‘’（）()\[\]【】{}…~·!?.,;:"'`\-_/\\|@#$%^&*+=<>]+'''
)


def normalize_question(query: str) -> str:
    """归一化问法：去标点空白 + 去停用词。BM25 查询与缓存 key 共用。"""
    text_ = _PUNCT_RE.sub("", (query or "").strip().lower())
    for sw in STOP_WORDS:
        text_ = text_.replace(sw, "")
    return text_.strip()


def cache_key(norm_query: str) -> str:
    return REDIS_EXACT_PREFIX + hashlib.sha1(norm_query.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class QAPair:
    """qa_pairs 表一行的应用层视图。"""

    id: int
    category: str
    question_std: str
    question_variants: list[str] = field(default_factory=list)
    answer: str = ""
    law_refs: list[dict] = field(default_factory=list)
    kb_id: str | None = None
    intent: str = "labor_law"
    keywords: str = ""
    hit_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QAPair":
        return cls(
            id=int(data["id"]),
            category=data.get("category", ""),
            question_std=data.get("question_std", ""),
            question_variants=list(data.get("question_variants") or []),
            answer=data.get("answer", ""),
            law_refs=list(data.get("law_refs") or []),
            kb_id=data.get("kb_id"),
            intent=data.get("intent", "labor_law"),
            keywords=data.get("keywords", "") or "",
            hit_count=int(data.get("hit_count", 0)),
        )


@dataclass(frozen=True)
class QALookupResult:
    """一次问答检索的三态结果。"""

    hit: QAPair | None = None  # 精确命中
    references: list[QAPair] = field(default_factory=list)  # 同类参考（混合链路的结论段素材）
    source: str = "none"  # redis_exact | mysql_bm25_cosine | topic_reference | none
    similarity: float | None = None  # 余弦门得分（命中时非 None）
    elapsed_ms: int = 0
    error: str | None = None  # 非致命错误（如 Redis 不可用），降级不影响结果


def _decode_json(value: Any, default: Any) -> Any:
    """MySQL JSON 列可能返回 str（PyMySQL）或已解析对象。"""
    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return value


def _row_to_qa(row: Any) -> QAPair:
    return QAPair(
        id=int(row[0]),
        category=row[1] or "",
        question_std=row[2] or "",
        question_variants=list(_decode_json(row[3], [])),
        answer=row[4] or "",
        law_refs=list(_decode_json(row[5], [])),
        kb_id=row[6],
        intent=row[7] or "labor_law",
        keywords=row[8] or "",
        hit_count=int(row[9] or 0),
    )


class QARetriever:
    """问答库检索器。依赖均可注入，测试用假实现替换。"""

    def __init__(
        self,
        *,
        engine: Engine | None = None,
        redis: Any = None,
        embedder: Embedder | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._engine = engine
        self._redis = redis
        self._embedder = embedder

    # ------------------------------------------------------------------
    # 懒加载依赖：Redis / embedder 故障不应让整条链路崩掉
    # ------------------------------------------------------------------
    @property
    def redis(self) -> Any:
        if self._redis is None:
            try:
                self._redis = get_redis(self._settings)
            except Exception as exc:  # noqa: BLE001 - Redis 不可用时降级
                logger.warning("Redis 连接失败，问答缓存降级为直查 MySQL: %s", exc)
                self._redis = _NullRedis()
        return self._redis

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = get_embedder(self._settings)
        return self._embedder

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            self._engine = get_engine(self._settings)
        return self._engine

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def lookup_exact(self, query: str) -> QAPair | None:
        """只查 Redis 精确缓存（0.1ms 级），不做 BM25 / 余弦门。

        用于编排层"缓存前置"：高频问题命中精确缓存就直接回放，
        跳过 LLM 意图识别，把 TTFT 从 ~2s 砍到 ~0.1s。
        未命中返回 None，调用方再走完整的 lookup()。
        """
        query = (query or "").strip()
        if not query:
            return None
        norm = normalize_question(query)
        hit = self._lookup_exact_cache(norm)
        if hit is not None:
            logger.info("精确缓存前置命中 q=%.40s id=%s", query, hit.id)
        return hit

    def lookup(
        self,
        query: str,
        *,
        structured_topic: str | None = None,
        kb_ids: list[str] | None = None,
    ) -> QALookupResult:
        started = time.perf_counter()
        query = (query or "").strip()
        if not query:
            return QALookupResult(elapsed_ms=_elapsed(started))

        norm = normalize_question(query)
        result = self._lookup_exact_cache(norm)
        if result is not None:
            result = QALookupResult(
                hit=result,
                source="redis_exact",
                similarity=1.0,
                elapsed_ms=_elapsed(started),
            )
            logger.info("问答精确缓存命中 q=%.40s id=%s 耗时=%dms", query, result.hit.id, result.elapsed_ms)
            return result

        error = None
        try:
            candidates = self._bm25_topn(norm, kb_ids=kb_ids, topn=self._settings.qa_bm25_topn)
        except Exception as exc:  # noqa: BLE001 - MySQL 故障 → 无结构化数据，继续向量链路
            logger.warning("问答 BM25 查询失败，跳过结构化链路: %s", exc)
            error = f"{type(exc).__name__}: {str(exc)[:120]}"
            candidates = []

        if candidates:
            top1 = candidates[0]
            similarity = self._cosine(query, top1.question_std)
            if similarity is not None and similarity >= self._settings.qa_similarity_threshold:
                self._on_hit(norm, top1)
                logger.info(
                    "问答精确命中(BM25+余弦) q=%.40s id=%s sim=%.4f 耗时=%dms",
                    query,
                    top1.id,
                    similarity,
                    _elapsed(started),
                )
                return QALookupResult(
                    hit=top1,
                    source="mysql_bm25_cosine",
                    similarity=similarity,
                    elapsed_ms=_elapsed(started),
                    error=error,
                )
            logger.info(
                "问答未命中 q=%.40s top1_id=%s sim=%s(<%.2f)",
                query,
                top1.id,
                f"{similarity:.4f}" if similarity is not None else "None",
                self._settings.qa_similarity_threshold,
            )

        references: list[QAPair] = []
        if structured_topic:
            try:
                references = self._topic_references(structured_topic, kb_ids=kb_ids, limit=2)
            except Exception as exc:  # noqa: BLE001
                logger.warning("同类参考查询失败: %s", exc)
                error = error or f"{type(exc).__name__}: {str(exc)[:120]}"

        return QALookupResult(
            references=references,
            source="topic_reference" if references else "none",
            elapsed_ms=_elapsed(started),
            error=error,
        )

    # ------------------------------------------------------------------
    # 缓存
    # ------------------------------------------------------------------
    def _lookup_exact_cache(self, norm: str) -> QAPair | None:
        try:
            raw = self.redis.get(cache_key(norm))
        except Exception as exc:  # noqa: BLE001 - 缓存故障直穿 MySQL
            logger.warning("Redis 读取失败，直查 MySQL: %s", exc)
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return QAPair.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("问答缓存内容损坏，忽略: %s", exc)
            return None

    def _on_hit(self, norm: str, qa: QAPair) -> None:
        """命中后写缓存 + 计数 + 热榜。缓存写入失败不影响返回。"""
        try:
            self.redis.set(cache_key(norm), json.dumps(qa.to_dict(), ensure_ascii=False), ex=self._settings.cache_qa_ttl)
            self.redis.set(REDIS_ANS_PREFIX + str(qa.id), qa.answer, ex=86400)
            self.redis.zincrby(REDIS_HOT_KEY, 1, str(qa.id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("问答缓存写入失败: %s", exc)
        try:
            with self.engine.begin() as conn:
                # updated_at 由 ON UPDATE CURRENT_TIMESTAMP(3) 自动维护
                conn.execute(
                    text("UPDATE qa_pairs SET hit_count = hit_count + 1 WHERE id = :id"),
                    {"id": qa.id},
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("hit_count 自增失败 id=%s: %s", qa.id, exc)

    # ------------------------------------------------------------------
    # MySQL
    # ------------------------------------------------------------------
    def _kb_filter(self, kb_ids: list[str] | None) -> tuple[str, dict]:
        """问答对允许 kb_id 为空（通用），故用 IS NULL OR IN。"""
        if kb_ids:
            return " AND (kb_id IS NULL OR kb_id IN :kb_ids)", {"kb_ids": list(kb_ids)}
        return "", {}

    def _bm25_topn(self, norm: str, *, kb_ids: list[str] | None, topn: int) -> list[QAPair]:
        # 注意：FULLTEXT 索引建在 search_text（标准问法+变体拼接）上——
        # MySQL 全文索引不能直接建在 JSON 列上（见 01_schema.sql 注释）。
        kb_sql, kb_params = self._kb_filter(kb_ids)
        sql = (
            "SELECT id, category, question_std, question_variants, answer, law_refs, "
            "       kb_id, intent, keywords, hit_count, "
            "       MATCH(search_text) AGAINST (:q IN NATURAL LANGUAGE MODE) AS bm25 "
            "FROM qa_pairs "
            "WHERE is_active = 1 "
            "  AND MATCH(search_text) AGAINST (:q IN NATURAL LANGUAGE MODE) > 0"
            + kb_sql
            + " ORDER BY bm25 DESC LIMIT :topn"
        )
        stmt = text(sql)
        if kb_ids:
            stmt = stmt.bindparams(bindparam("kb_ids", expanding=True))
        params = {"q": norm, "topn": topn, **kb_params}
        with self.engine.connect() as conn:
            rows = conn.execute(stmt, params).fetchall()
        return [_row_to_qa(r) for r in rows]

    def _topic_references(self, topic: str, *, kb_ids: list[str] | None, limit: int = 2) -> list[QAPair]:
        kb_sql, kb_params = self._kb_filter(kb_ids)
        sql = (
            "SELECT id, category, question_std, question_variants, answer, law_refs, "
            "       kb_id, intent, keywords, hit_count "
            "FROM qa_pairs "
            "WHERE is_active = 1 AND category = :topic" + kb_sql
            + " ORDER BY hit_count DESC, id ASC LIMIT :limit"
        )
        stmt = text(sql)
        if kb_ids:
            stmt = stmt.bindparams(bindparam("kb_ids", expanding=True))
        params = {"topic": topic, "limit": limit, **kb_params}
        with self.engine.connect() as conn:
            rows = conn.execute(stmt, params).fetchall()
        return [_row_to_qa(r) for r in rows]

    # ------------------------------------------------------------------
    # 余弦门
    # ------------------------------------------------------------------
    def _cosine(self, query: str, question_std: str) -> float | None:
        """bge-m3 稠密向量余弦相似度；embedder 故障返回 None（视为不命中）。"""
        try:
            vecs = self.embedder.encode([query, question_std])
        except Exception as exc:  # noqa: BLE001
            logger.warning("余弦门编码失败，按不命中处理: %s", exc)
            return None
        a = np.asarray(vecs[0].dense, dtype=np.float32)
        b = np.asarray(vecs[1].dense, dtype=np.float32)
        denom = float(np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            return None
        return float(np.dot(a, b) / denom)


class _NullRedis:
    """Redis 不可用时的占位：读写全部静默失败。"""

    def get(self, *_a: Any, **_k: Any) -> None:
        return None

    def set(self, *_a: Any, **_k: Any) -> None:
        return None

    def zincrby(self, *_a: Any, **_k: Any) -> None:
        return None


def _elapsed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


@lru_cache(maxsize=4)
def _build_retriever(engine: Engine | None, redis: Any, embedder: Embedder | None) -> QARetriever:
    return QARetriever(engine=engine, redis=redis, embedder=embedder)


def get_qa_retriever(
    *,
    engine: Engine | None = None,
    redis: Any = None,
    embedder: Embedder | None = None,
) -> QARetriever:
    """进程级单例（依赖内部懒加载，首次 lookup 时才连库/加载模型）。

    传入任一依赖（测试注入）时返回非共享实例。
    """
    if engine is not None or redis is not None or embedder is not None:
        return QARetriever(engine=engine, redis=redis, embedder=embedder)
    return _build_retriever(None, None, None)


def reset_qa_retriever() -> None:
    _build_retriever.cache_clear()
