"""上传校验与增量判定测试（不连数据库）。"""

from __future__ import annotations

import pytest

from app.ingest.service import MAX_UPLOAD_BYTES, UploadRejected, validate_upload


class TestValidateUpload:
    def test_accepts_supported_types(self):
        for name in ("a.md", "a.pdf", "a.pptx", "a.png"):
            validate_upload(name, 1024, 300, 50)

    def test_rejects_unsupported(self):
        with pytest.raises(UploadRejected) as exc:
            validate_upload("a.docx", 1024, 300, 50)
        assert "不支持的文件类型" in str(exc.value)

    def test_rejects_empty(self):
        with pytest.raises(UploadRejected):
            validate_upload("a.md", 0, 300, 50)

    def test_rejects_oversize(self):
        with pytest.raises(UploadRejected) as exc:
            validate_upload("a.pdf", MAX_UPLOAD_BYTES + 1, 300, 50)
        assert "上限" in str(exc.value)

    @pytest.mark.parametrize("size", [10, 49, 2001])
    def test_chunk_size_range(self, size):
        with pytest.raises(UploadRejected):
            validate_upload("a.md", 1024, size, 50)

    def test_overlap_must_be_smaller_than_chunk(self):
        with pytest.raises(UploadRejected):
            validate_upload("a.md", 1024, 100, 100)

    def test_negative_overlap(self):
        with pytest.raises(UploadRejected):
            validate_upload("a.md", 1024, 100, -1)


class TestIncrementalSha:
    def test_same_content_same_sha1(self, tmp_path):
        from app.ingest.pipeline import prepare

        path = tmp_path / "a.md"
        path.write_text("第一章 总则\n第一条 内容一。\n第二条 内容二。", encoding="utf-8")
        assert prepare(path).content_sha1 == prepare(path).content_sha1

    def test_changed_content_changes_sha1(self, tmp_path):
        from app.ingest.pipeline import prepare

        path = tmp_path / "a.md"
        path.write_text("第一条 内容一。", encoding="utf-8")
        first = prepare(path).content_sha1
        path.write_text("第一条 内容一（修订）。", encoding="utf-8")
        assert prepare(path).content_sha1 != first

    def test_noise_change_does_not_change_sha1(self, tmp_path):
        """只改了页码/页眉，正文没变 → 不该触发重建。"""
        from app.ingest.pipeline import prepare

        path = tmp_path / "a.md"
        path.write_text("第一条 内容一。\n2", encoding="utf-8")
        first = prepare(path).content_sha1
        path.write_text("第一条 内容一。\n3", encoding="utf-8")
        assert prepare(path).content_sha1 == first


class TestRebind:
    def test_ids_rewritten(self, tmp_path):
        from app.ingest.pipeline import prepare, rebind_ids

        path = tmp_path / "a.md"
        path.write_text("第一条 内容一。", encoding="utf-8")
        prepared = prepare(path)
        bound = rebind_ids(prepared, doc_id="DOC1", kb_id="kb_labor")
        assert all(c.doc_id == "DOC1" for c in bound.chunks)
        assert all(c.kb_id == "kb_labor" for c in bound.chunks)
        assert any(c.chunk_id.startswith("DOC1:p1") for c in bound.chunks)
        assert all("PENDING" not in c.chunk_id for c in bound.chunks)
