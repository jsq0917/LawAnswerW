"""Milvus schema 与初始化逻辑测试（用 FakeClient，不连真实 Milvus）。"""

from __future__ import annotations

import pytest
from pymilvus import DataType

from app.db.milvus_schema import (
    DENSE_DIM,
    DENSE_FIELD,
    FIELD_SPECS,
    INDEX_SPECS,
    PARTITION_KEY_FIELD,
    PRIMARY_FIELD,
    SPARSE_FIELD,
    build_schema,
    describe_specs,
    ensure_collection,
    ensure_database,
)


class FakeIndexParams:
    def __init__(self, sink: list):
        self.sink = sink
        self.items: list[dict] = []

    def add_index(self, **kwargs):
        self.items.append(kwargs)


class FakeMilvusClient:
    """记录调用的最小替身，用于验证初始化逻辑而不是连通性。"""

    def __init__(self):
        self.databases = ["default"]
        self.collections: list[str] = []
        self.calls: list[tuple] = []
        self.current_db = "default"
        self.loaded: list[str] = []
        self.created_schema = None
        self.created_indexes: list[dict] = []

    def list_databases(self):
        return list(self.databases)

    def create_database(self, name):
        self.databases.append(name)
        self.calls.append(("create_database", name))

    def using_database(self, name):
        self.current_db = name
        self.calls.append(("using_database", name))

    def has_collection(self, name):
        return name in self.collections

    def prepare_index_params(self):
        return FakeIndexParams(self.calls)

    def create_collection(self, collection_name, schema, index_params, consistency_level):
        self.collections.append(collection_name)
        self.created_schema = schema
        self.created_indexes = index_params.items
        self.calls.append(("create_collection", collection_name, consistency_level))

    def load_collection(self, name):
        self.loaded.append(name)

    def drop_collection(self, name):
        self.collections.remove(name)
        self.calls.append(("drop_collection", name))


class TestSpec:
    def test_dense_dim_is_bge_m3(self):
        assert DENSE_DIM == 1024

    def test_primary_key_is_chunk_id(self):
        spec = describe_specs()
        primary = [f for f in spec["fields"] if f["is_primary"]]
        assert len(primary) == 1
        assert primary[0]["name"] == PRIMARY_FIELD == "chunk_id"
        assert primary[0]["max_length"] == 64

    def test_kb_id_is_partition_key(self):
        spec = describe_specs()
        pk = [f for f in spec["fields"] if f["is_partition_key"]]
        assert len(pk) == 1
        assert pk[0]["name"] == PARTITION_KEY_FIELD == "kb_id"

    def test_partition_key_has_no_index(self):
        """Milvus 不允许给 partition key 字段建索引，必须在设计里就不出现。"""
        assert PARTITION_KEY_FIELD not in {i.field_name for i in INDEX_SPECS}

    def test_dense_index_is_hnsw_cosine(self):
        dense = next(i for i in INDEX_SPECS if i.field_name == DENSE_FIELD)
        assert dense.index_type == "HNSW"
        assert dense.metric_type == "COSINE"
        assert dense.params["M"] == 16
        assert dense.params["efConstruction"] == 200

    def test_sparse_index_is_inverted_ip(self):
        sparse = next(i for i in INDEX_SPECS if i.field_name == SPARSE_FIELD)
        assert sparse.index_type == "SPARSE_INVERTED_INDEX"
        assert sparse.metric_type == "IP"
        assert 0 <= sparse.params["drop_ratio_search"] <= 1

    def test_traceability_fields_present(self):
        """溯源字段一个都不能少：文件名、条号、章节路径、序号。"""
        names = {f.name for f in FIELD_SPECS}
        assert {"source_file", "article_no", "chapter_path", "chunk_index", "doc_sha1"} <= names

    def test_no_dynamic_field(self):
        schema = build_schema()
        assert schema.enable_dynamic_field is False


class TestBuildSchema:
    def test_field_count_and_types(self):
        schema = build_schema()
        fields = {f.name: f for f in schema.fields}
        assert len(fields) == len(FIELD_SPECS)
        assert fields[DENSE_FIELD].dtype == DataType.FLOAT_VECTOR
        assert fields[DENSE_FIELD].params["dim"] == DENSE_DIM
        assert fields[SPARSE_FIELD].dtype == DataType.SPARSE_FLOAT_VECTOR
        assert fields["chunk_index"].dtype == DataType.INT32
        assert fields["updated_at"].dtype == DataType.INT64

    def test_dim_override(self):
        schema = build_schema(dim=768)
        dense = next(f for f in schema.fields if f.name == DENSE_FIELD)
        assert dense.params["dim"] == 768


class TestEnsureCollection:
    def test_creates_database_and_collection(self):
        client = FakeMilvusClient()
        result = ensure_collection(client, "lawanswer", "law_chunks")
        assert result["created"] is True
        assert "lawanswer" in client.databases
        assert "law_chunks" in client.collections
        assert client.current_db == "lawanswer"
        assert client.loaded == ["law_chunks"]

    def test_indexes_all_created(self):
        client = FakeMilvusClient()
        ensure_collection(client, "lawanswer", "law_chunks")
        created = {item["field_name"] for item in client.created_indexes}
        assert created == {spec.field_name for spec in INDEX_SPECS}
        consistency = next(c for c in client.calls if c[0] == "create_collection")
        assert consistency[2] == "Strong"

    def test_idempotent_second_call_skips(self):
        client = FakeMilvusClient()
        ensure_collection(client, "lawanswer", "law_chunks")
        result = ensure_collection(client, "lawanswer", "law_chunks")
        assert result["created"] is False
        assert client.collections == ["law_chunks"]

    def test_drop_recreates(self):
        client = FakeMilvusClient()
        ensure_collection(client, "lawanswer", "law_chunks")
        result = ensure_collection(client, "lawanswer", "law_chunks", drop=True)
        assert result["created"] is True
        assert any(c[0] == "drop_collection" for c in client.calls)
        assert client.collections == ["law_chunks"]

    def test_existing_database_not_recreated(self):
        client = FakeMilvusClient()
        client.databases.append("lawanswer")
        assert ensure_database(client, "lawanswer") is False
        assert not any(c[0] == "create_database" for c in client.calls)

    def test_default_database_is_noop(self):
        client = FakeMilvusClient()
        assert ensure_database(client, "default") is False
        assert client.calls == []

    @pytest.mark.parametrize("db_name", ["lawanswer", "kb_test"])
    def test_returns_requested_names(self, db_name):
        client = FakeMilvusClient()
        result = ensure_collection(client, db_name, "law_chunks")
        assert result["database"] == db_name
        assert result["collection"] == "law_chunks"
