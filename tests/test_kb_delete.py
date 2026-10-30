"""删除知识库：契约 + 级联清理 + 保护条件。

用户视角的三条要求，这里逐条钉住：

1. 删库要连带删掉文档、块、向量与源文件（MySQL 靠外键级联，Milvus 靠主键批量删）；
2. 解析中的库拒绝删除（否则后台任务会把状态写回已删的行）；
3. 删除后 `GET /api/kbs` 立刻看不到它——前端列表/store 靠这个刷新。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.fakes_db import FakeDB, override_deps

KB = "kb_labor"


class FakeMilvus:
    """记录向量删除调用，用于断言"按主键批量删"这件事真的发生了。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def delete(self, *, collection_name: str, filter: str):  # noqa: A002 - 与 pymilvus 同名
        assert collection_name, "必须指定 collection"
        head, _, tail = filter.partition("in [")
        assert head.strip() == "chunk_id", f"期望按主键删，实际 filter={filter}"
        ids = [s.strip().strip('"') for s in tail.rstrip("]").split(",") if s.strip()]
        self.calls.append(ids)

    def flat(self) -> list[str]:
        return [cid for call in self.calls for cid in call]


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def milvus() -> FakeMilvus:
    return FakeMilvus()


@pytest.fixture
def client(db, milvus, monkeypatch):
    monkeypatch.setattr("app.db.milvus_client.get_milvus_db_client", lambda *a, **k: milvus)
    monkeypatch.setattr("app.api.knowledge.purge_kb_files", lambda kb_id: 0)
    restore = override_deps(app, db)
    yield TestClient(app)
    restore()


class TestDeleteKbApi:
    def test_delete_removes_kb_docs_chunks_vectors(self, client, db, milvus):
        resp = client.delete(f"/api/kbs/{KB}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == KB
        assert data["name"] == "劳动法知识库"
        # 统计量回传给前端做 toast，字段名与 frontend/src/api/kb.ts 一致
        assert data["doc_count"] == 2
        assert data["chunk_count"] == 320
        assert data["chunk_vectors"] == 1
        assert isinstance(data["file_count"], int)

        assert db.kbs == []
        assert db.docs == []          # 外键级联的模拟
        assert db.chunks == []
        assert db.qa_detached == [KB]  # qa_pairs 没有外键，靠手动置空
        assert db.commits == 1
        assert milvus.flat() == ["c1"]

    def test_list_kbs_no_longer_contains_deleted(self, client):
        assert client.delete(f"/api/kbs/{KB}").status_code == 200
        assert client.get("/api/kbs").json() == []

    def test_delete_unknown_kb_returns_404(self, client, db):
        resp = client.delete("/api/kbs/kb_not_exist")
        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        assert [k["id"] for k in db.kbs] == [KB]

    def test_delete_busy_kb_returns_409(self, client, db, milvus):
        db.docs[0]["status"] = "parsing"
        resp = client.delete(f"/api/kbs/{KB}")
        assert resp.status_code == 409
        assert resp.json()["code"] == "conflict"
        assert "解析" in resp.json()["message"]
        assert [k["id"] for k in db.kbs] == [KB]  # 没删掉
        assert milvus.calls == []                 # 也没动向量

    def test_delete_empty_kb_skips_vector_delete(self, client, db, milvus):
        db.docs = []
        db.chunks = []
        resp = client.delete(f"/api/kbs/{KB}")
        assert resp.status_code == 200
        assert resp.json()["chunk_vectors"] == 0
        assert milvus.calls == []

    def test_milvus_failure_keeps_kb(self, db, monkeypatch):
        """向量库挂了要报明确错误，且 MySQL 一行都不能动（否则库变成"半死"）。"""
        class _Boom:
            def delete(self, **_kw):
                raise RuntimeError("connection lost")

        monkeypatch.setattr("app.db.milvus_client.get_milvus_db_client", lambda *a, **k: _Boom())
        monkeypatch.setattr("app.api.knowledge.purge_kb_files", lambda kb_id: 0)
        restore = override_deps(app, db)
        try:
            resp = TestClient(app).delete(f"/api/kbs/{KB}")
        finally:
            restore()
        assert resp.status_code == 502
        assert resp.json()["code"] == "upstream_error"
        assert [k["id"] for k in db.kbs] == [KB]
        assert db.docs and db.chunks
        assert db.commits == 0


class TestPurgeKbFiles:
    def test_removes_only_that_kb_dir(self, tmp_path, monkeypatch):
        from app.ingest.service import purge_kb_files

        root = tmp_path / "uploads"
        (root / KB).mkdir(parents=True)
        (root / KB / "a.md").write_text("x", encoding="utf-8")
        (root / KB / "sub").mkdir()
        (root / KB / "sub" / "b.md").write_text("y", encoding="utf-8")
        (root / "kb_other").mkdir()

        class _S:
            upload_path = root

        monkeypatch.setattr("app.ingest.service.get_settings", lambda: _S())

        assert purge_kb_files(KB) == 2
        assert not (root / KB).exists()
        assert (root / "kb_other").is_dir()

    def test_rejects_path_traversal(self, tmp_path, monkeypatch):
        from app.ingest.service import purge_kb_files

        root = tmp_path / "uploads"
        root.mkdir()
        outside = tmp_path / "keep"
        outside.mkdir()
        (outside / "keep.md").write_text("别删我", encoding="utf-8")

        class _S:
            upload_path = root

        monkeypatch.setattr("app.ingest.service.get_settings", lambda: _S())

        assert purge_kb_files("../keep") == 0
        assert (outside / "keep.md").exists()
