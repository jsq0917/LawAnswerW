"""加载器测试：类型识别、Markdown/PDF 读取、缺依赖与缺密钥的失败路径。"""

from __future__ import annotations

import pytest

from app.ingest.loaders import (
    UnsupportedDocumentError,
    detect_file_type,
    load_document,
    load_image,
    load_markdown,
    load_pdf,
)
from config import BASE_DIR

SRC = BASE_DIR / "data" / "src"
RAW = BASE_DIR / "data" / "raw"


class TestDetect:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("a.md", "md"),
            ("a.markdown", "md"),
            ("a.txt", "md"),
            ("a.PDF", "pdf"),
            ("a.pptx", "pptx"),
            ("a.png", "image"),
            ("a.jpg", "image"),
        ],
    )
    def test_supported(self, name, expected):
        assert detect_file_type(BASE_DIR / name) == expected

    def test_unsupported(self):
        with pytest.raises(UnsupportedDocumentError):
            detect_file_type(BASE_DIR / "a.docx")

    def test_no_suffix(self):
        with pytest.raises(UnsupportedDocumentError):
            detect_file_type(BASE_DIR / "README")


class TestMarkdown:
    def test_real_file(self):
        doc = load_markdown(RAW / "中华人民共和国劳动合同法.md")
        assert doc.file_type == "md"
        assert len(doc.text) > 10_000
        assert "第一条" in doc.text

    def test_gbk_fallback(self, tmp_path):
        path = tmp_path / "gbk.md"
        path.write_bytes("第一条 中文内容。".encode("gb18030"))
        assert "第一条" in load_markdown(path).text


class TestPdf:
    def test_real_pdf_text_layer(self):
        doc = load_pdf(SRC / "中华人民共和国劳动合同法.pdf")
        assert doc.file_type == "pdf"
        assert doc.page_count > 10
        assert doc.empty_pages == 0
        assert len(doc.text) > 5_000

    def test_scanned_pdf_raises(self, tmp_path):
        """没有文本层的 PDF 必须明确失败，而不是把空文本塞进向量库。"""
        pymupdf = pytest.importorskip("pymupdf")
        path = tmp_path / "blank.pdf"
        pdf = pymupdf.open()
        pdf.new_page()
        pdf.save(path)
        pdf.close()
        with pytest.raises(UnsupportedDocumentError) as exc:
            load_pdf(path)
        assert "OCR" in str(exc.value)


class TestImage:
    def test_requires_api_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        path = tmp_path / "a.png"
        path.write_bytes(b"fake")
        with pytest.raises(UnsupportedDocumentError) as exc:
            load_image(path)
        assert "DASHSCOPE_API_KEY" in str(exc.value)


class TestDispatch:
    def test_missing_file(self, tmp_path):
        with pytest.raises(UnsupportedDocumentError):
            load_document(tmp_path / "nope.md")

    def test_dispatch_markdown(self):
        doc = load_document(RAW / "中华人民共和国民法典.md")
        assert doc.file_type == "md"
        assert doc.source_file.endswith(".md")
