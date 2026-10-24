"""问答缓存链路单测：三态判定（精确命中/相似不命中/未命中）、缓存读写、降级。

假 engine / redis / embedder，不连真实服务；真实链路由
test_qa_cache_integration.py（integration 标记）覆盖。
"""

from __future__ import annotations

import json

import pytest

from app.ingest.embedder import Embedding
from app.orchestration.qa_cache import (
    QAPair,
    QARetriever,
    _NullRedis,
    cache_key,
    normalize_question,
)

QA_ROW_PROBATION = (
    7, "试用期", "试用期最长可以约定多久？", json.dumps(["试用期能设多长时间", "试用期一般多长"]),
    "三个月以上不满一年，试用期不得超过一个月……",
    json.dumps([{"law": "中华人民共和国劳动合同法", "article": "第十九条"}], ensure_ascii=False),
    "kb_labor", "labor_law", "试用期,时长", 5,
)
QA_ROW_OVERTIME = (
    12, "加班费", "加班费怎么计算？", json.dumps(["加班工资怎么算"]),
    "支付不低于工资的百分之一百五十……",
    json.dumps([{"law": "中华人民共和国劳动法", "article": "第四十四条"}], ensure_ascii=False),
    "kb_labor", "labor_law", "加班,加班费", 3,
)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows, executed):
        self._rows = rows
        self._executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        self._executed.append((str(stmt), params))
        return FakeResult(self._rows)


class FakeConn:
    def __init__(self, engine, for_write=False):
        self._engine = engine
        self._for_write = for_write

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self._engine.executed.append((sql, params))
        if self._for_write:
            return FakeResult([])
        return FakeResult(self._engine._rows_for(sql))


class FakeEngine:
    """按 SQL 类型返回不同行：BM25 查询 → bm25_rows，话题参考 → topic_rows。"""

    def __init__(self, bm25_rows=None, topic_rows=None):
        self.bm25_rows = bm25_rows or []
        self.topic_rows = topic_rows if topic_rows is not None else []
        self.executed: list[tuple[str, dict]] = []

    def _rows_for(self, sql: str):
        if "category = :topic" in sql:
            return self.topic_rows
        return self.bm25_rows

    def connect(self):
        return FakeConn(self)

    def begin(self):
        return FakeConn(self, for_write=True)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.zset: dict[str, float] = {}
        self.set_calls: list[tuple] = []

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.set_calls.append((key, ex))
        self.store[key] = value

    def zincrby(self, key, amount, member):
        self.zset[member] = self.zset.get(member, 0) + amount


class FakeEmbedder:
    """与真实 Embedder 同构：encode 返回 Embedding 对象。

    same=True 时所有文本同一向量 → 余弦 1.0；否则各文本不同向量 → 余弦 0。
    """

    def __init__(self, same=True):
        self.same = same
        self.encode_calls: list[list[str]] = []

    def encode(self, texts, batch_size=16):
        self.encode_calls.append(list(texts))
        out = []
        for i, t in enumerate(texts):
            if self.same:
                dense = [1.0, 0.0]
            else:
                dense = [float(i % 2), float((i + 1) % 2)]
            out.append(Embedding(dense=dense, sparse={}))
        return out


def _retriever(bm25_rows=None, redis=None, embedder=None, topic_rows=None, engine=None) -> QARetriever:
    return QARetriever(
        engine=engine or FakeEngine(bm25_rows, topic_rows),
        redis=redis or FakeRedis(),
        embedder=embedder or FakeEmbedder(),
    )


# ---------------------------------------------------------------------------
# 归一化与缓存 key
# ---------------------------------------------------------------------------
class TestNormalize:
    def test_punctuation_and_stopwords(self):
        assert normalize_question("请问，我想问一下：试用期最长可以约定多久？") == "试用期最长可以约定多久"

    def test_case_insensitive(self):
        assert normalize_question("HR 试用期") == "hr试用期"

    def test_stable_hash_key(self):
        # 归一化抹平标点差异 → 同一问题缓存 key 相同
        assert cache_key(normalize_question("试用期最长多久？")) == cache_key(
            normalize_question("试用期最长多久")
        )


# ---------------------------------------------------------------------------
# 三态判定
# ---------------------------------------------------------------------------
class TestLookupThreeStates:
    def test_exact_cache_hit(self):
        redis = FakeRedis()
        qa = QAPair(id=7, category="试用期", question_std="试用期最长可以约定多久？", answer="标准答案")
        norm = normalize_question("试用期最长可以约定多久？")
        redis.store[cache_key(norm)] = json.dumps(qa.to_dict(), ensure_ascii=False)
        result = _retriever(redis=redis).lookup("试用期最长可以约定多久？")
        assert result.hit is not None
        assert result.hit.id == 7
        assert result.source == "redis_exact"
        assert result.similarity == 1.0

    def test_bm25_cosine_hit_writes_cache_and_bumps(self):
        redis = FakeRedis()
        engine = FakeEngine(bm25_rows=[QA_ROW_PROBATION])
        result = QARetriever(engine=engine, redis=redis, embedder=FakeEmbedder(same=True)).lookup(
            "试用期最长可以约定多久"
        )
        assert result.hit is not None
        assert result.source == "mysql_bm25_cosine"
        assert result.similarity == pytest.approx(1.0)
        # 只写精确缓存一个 key（qa:ans/qa:hot 两个只写不读的 key 已移除）
        assert len(redis.set_calls) == 1
        assert redis.set_calls[0][1] == 604800
        # MySQL hit_count 自增
        assert any("UPDATE" in sql for sql, _ in engine.executed)

    def test_cosine_below_threshold_miss(self):
        result = _retriever(
            bm25_rows=[QA_ROW_OVERTIME],
            topic_rows=[QA_ROW_OVERTIME],
            embedder=FakeEmbedder(same=False),
        ).lookup("试用期最长可以约定多久", structured_topic="试用期")
        assert result.hit is None
        # 未命中但给同类参考
        assert result.source == "topic_reference"
        assert [r.id for r in result.references] == [12]

    def test_no_bm25_candidate_topic_reference(self):
        result = _retriever(
            bm25_rows=[],
            topic_rows=[QA_ROW_PROBATION],
            embedder=FakeEmbedder(),
        ).lookup("试用期被辞退有补偿吗", structured_topic="试用期")
        assert result.hit is None
        assert result.source == "topic_reference"
        assert [r.id for r in result.references] == [7]

    def test_no_candidate_no_topic_is_none(self):
        result = _retriever().lookup("试用期被辞退有补偿吗")
        assert result.hit is None
        assert result.source == "none"
        assert result.references == []

    def test_empty_query(self):
        result = _retriever().lookup("   ")
        assert result.hit is None
        assert result.source == "none"


# ---------------------------------------------------------------------------
# 降级
# ---------------------------------------------------------------------------
class TestDegradation:
    def test_redis_down_falls_through_to_mysql(self):
        class BrokenRedis:
            def get(self, *a, **k):
                raise ConnectionError("redis down")

            def set(self, *a, **k):
                raise ConnectionError("redis down")

            def zincrby(self, *a, **k):
                raise ConnectionError("redis down")

        result = QARetriever(
            engine=FakeEngine(bm25_rows=[QA_ROW_PROBATION]),
            redis=BrokenRedis(),
            embedder=FakeEmbedder(),
        ).lookup("试用期最长可以约定多久")
        assert result.hit is not None
        assert result.source == "mysql_bm25_cosine"

    def test_mysql_down_records_error_no_raise(self):
        class BrokenEngine:
            def connect(self):
                raise ConnectionError("mysql down")

            def begin(self):
                raise ConnectionError("mysql down")

        result = QARetriever(engine=BrokenEngine(), redis=FakeRedis(), embedder=FakeEmbedder()).lookup(
            "试用期最长", structured_topic="试用期"
        )
        assert result.hit is None
        assert result.error is not None
        assert result.source == "none"

    def test_corrupt_cache_entry_ignored(self):
        redis = FakeRedis()
        redis.store[cache_key(normalize_question("试用期最长可以约定多久"))] = "{broken json"
        result = QARetriever(
            engine=FakeEngine(bm25_rows=[QA_ROW_PROBATION]), redis=redis, embedder=FakeEmbedder()
        ).lookup("试用期最长可以约定多久")
        assert result.hit is not None
        assert result.source == "mysql_bm25_cosine"

    def test_null_redis_silent(self):
        r = _NullRedis()
        assert r.get("x") is None
        assert r.set("x", "y") is None
        assert r.zincrby("z", 1, "m") is None


# ---------------------------------------------------------------------------
# JSON 列解码与行映射
# ---------------------------------------------------------------------------
class TestQAPairMapping:
    def test_json_columns_as_string(self):
        from app.orchestration.qa_cache import _row_to_qa

        qa = _row_to_qa(QA_ROW_PROBATION)
        assert qa.id == 7
        assert qa.question_variants == ["试用期能设多长时间", "试用期一般多长"]
        assert qa.law_refs[0]["article"] == "第十九条"
        assert qa.hit_count == 5

    def test_json_columns_already_parsed(self):
        from app.orchestration.qa_cache import _row_to_qa

        row = (7, "试用期", "q", ["v1"], "a", [{"law": "L", "article": "A"}], "kb_labor", "labor_law", "k", 1)
        qa = _row_to_qa(row)
        assert qa.question_variants == ["v1"]
        assert qa.law_refs == [{"law": "L", "article": "A"}]

    def test_from_dict_roundtrip(self):
        qa = QAPair(id=1, category="c", question_std="q", answer="a", law_refs=[{"law": "L", "article": "A"}])
        assert QAPair.from_dict(qa.to_dict()).law_refs == qa.law_refs

    def test_from_dict_tolerates_missing(self):
        qa = QAPair.from_dict({"id": "9", "question_std": "q"})
        assert qa.id == 9
        assert qa.answer == ""
        assert qa.question_variants == []


# ---------------------------------------------------------------------------
# SQL 正确性
# ---------------------------------------------------------------------------
class TestSqlStatements:
    def test_bm25_uses_search_text_column(self):
        engine = FakeEngine()
        _retriever(engine=engine).lookup("试用期最长", structured_topic="试用期")
        sqls = [s for s, _ in engine.executed]
        assert any("MATCH(search_text)" in s for s in sqls)
        assert not any("MATCH(question_std" in s for s in sqls)

    def test_kb_filter_expanding(self):
        engine = FakeEngine(bm25_rows=[QA_ROW_PROBATION])
        result = _retriever(engine=engine).lookup("试用期最长", kb_ids=["kb_labor"])
        # bindparams 会把 :kb_ids 改写为 POSTCOMPILE_kb_ids 占位符；
        # 能查到结果即证明 expanding 参数端到端可用（无 ArgumentError）
        assert any("POSTCOMPILE_kb_ids" in s or ":kb_ids" in s for s, _ in engine.executed)
        assert result.hit is not None

    def test_hit_count_update(self):
        engine = FakeEngine(bm25_rows=[QA_ROW_PROBATION])
        _retriever(engine=engine).lookup("试用期最长可以约定多久")
        updates = [s for s, _ in engine.executed if "UPDATE" in s]
        assert updates
        assert "hit_count = hit_count + 1" in updates[0]
