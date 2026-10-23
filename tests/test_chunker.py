"""父子分块测试：父块不被截断、短条文 1:1、长条文按句切、重叠生效。"""

from __future__ import annotations

import pytest

from app.ingest.chunker import (
    build_chunks,
    pack_sentences,
    split_content,
    split_sentences,
    truncation_rate,
)
from app.ingest.models import Article, ParsedDocument


def _doc(articles: list[Article], law: str = "测试法") -> ParsedDocument:
    return ParsedDocument(
        source_file="test.md",
        law_name=law,
        text="".join(a.content for a in articles),
        articles=articles,
    )


def _article(no: str, num: int, content: str, path: str = "第一章 总则") -> Article:
    return Article(
        article_no=no,
        article_num=num,
        chapter_path=path,
        content=content,
        line_start=1,
        line_end=1,
    )


class TestSplitSentences:
    def test_split_on_chinese_period(self):
        assert split_sentences("第一句。第二句。") == ["第一句。", "第二句。"]

    def test_keeps_trailing_text(self):
        assert split_sentences("有句号。没句号") == ["有句号。", "没句号"]

    def test_semicolon(self):
        assert split_sentences("甲；乙；") == ["甲；", "乙；"]


class TestPack:
    def test_short_text_single_piece(self):
        assert pack_sentences(["短句。"], 300, 50) == ["短句。"]

    def test_respects_max_size(self):
        sentences = ["这是第%d句，用来测试装箱是否会超长。" % i for i in range(20)]
        pieces = pack_sentences(sentences, 100, 20)
        assert all(len(p) <= 100 for p in pieces)
        assert len(pieces) > 1

    def test_overlap_carried(self):
        """重叠按整句回带：上一块末尾的整句会出现在下一块开头。"""
        sentences = ["甲" * 60 + "。", "乙" * 60 + "。", "丙" * 60 + "。"]
        pieces = pack_sentences(sentences, 130, 62)
        assert len(pieces) >= 2
        assert pieces[1].startswith("乙")
        assert pieces[0].endswith("乙" * 60 + "。")

    def test_long_sentence_hard_split(self):
        pieces = pack_sentences(["无标点" * 100], 50, 10)   # 300 字 → 6 块
        assert all(len(p) <= 50 for p in pieces)
        assert len(pieces) == 6

    def test_invalid_chunk_size(self):
        with pytest.raises(ValueError):
            pack_sentences(["a"], 0, 0)


class TestSplitContent:
    def test_short_article_not_split(self):
        content = "试用期不得超过六个月。"
        assert split_content(content, 300, 50) == [content]

    def test_long_article_split(self):
        content = "。".join(["这是一段很长的法条内容" * 3] * 6) + "。"
        pieces = split_content(content, 100, 20)
        assert len(pieces) > 1
        assert all(len(p) <= 100 for p in pieces)


class TestBuildChunks:
    def test_parent_always_created(self):
        doc = _doc([_article("第一条", 1, "内容一。")])
        chunks = build_chunks(doc, doc_id="D1", kb_id="kb_test")
        parents = [c for c in chunks if c.chunk_type == "parent"]
        assert len(parents) == 1
        assert parents[0].chunk_id == "D1:p1"
        assert parents[0].content == "内容一。"

    def test_short_article_single_child(self):
        doc = _doc([_article("第一条", 1, "内容一。")])
        children = [c for c in build_chunks(doc, doc_id="D1", kb_id="kb_test") if c.chunk_type == "child"]
        assert len(children) == 1
        assert children[0].content == parents_content(doc)
        assert children[0].parent_id == "D1:p1"

    def test_long_article_multiple_children(self):
        long_content = "。".join(["长条文内容" * 8] * 5) + "。"
        doc = _doc([_article("第一条", 1, long_content)])
        chunks = build_chunks(doc, doc_id="D1", kb_id="kb_test", chunk_size=100, overlap=20)
        children = [c for c in chunks if c.chunk_type == "child"]
        assert len(children) > 1
        assert all(c.parent_id == "D1:p1" for c in children)
        assert [c.chunk_id for c in children] == [f"D1:p1:c{i}" for i in range(len(children))]

    def test_child_never_truncates_article(self):
        """子块拼起来必须覆盖整条，法条不允许丢字。"""
        content = "。".join([f"第{i}句内容比较长一些用来触发切分" for i in range(10)]) + "。"
        doc = _doc([_article("第五条", 5, content)])
        chunks = build_chunks(doc, doc_id="D1", kb_id="kb_test", chunk_size=80, overlap=0)
        assert truncation_rate(chunks) == 0.0

    def test_title_contains_law_and_path(self):
        doc = _doc([_article("第十九条", 19, "试用期不得超过六个月。", "第二章 劳动合同的订立")], law="中华人民共和国劳动合同法")
        parent = next(c for c in build_chunks(doc, doc_id="D1", kb_id="kb_test") if c.chunk_type == "parent")
        assert parent.title == "中华人民共和国劳动合同法·第二章 劳动合同的订立·第十九条"

    def test_embed_text_prepends_title(self):
        doc = _doc([_article("第一条", 1, "内容。")], law="测试法")
        child = next(c for c in build_chunks(doc, doc_id="D1", kb_id="kb_test") if c.chunk_type == "child")
        assert child.embed_text.startswith("测试法·第一章 总则·第一条")

    def test_overlap_must_be_smaller(self):
        doc = _doc([_article("第一条", 1, "内容。")])
        with pytest.raises(ValueError):
            build_chunks(doc, doc_id="D1", kb_id="kb_test", chunk_size=50, overlap=50)

    def test_plain_document_fallback(self):
        """非法律文书（无条号）也要能分块，落成"段N"伪条文。"""
        doc = ParsedDocument(
            source_file="a.pptx",
            law_name="培训材料",
            text="\n".join(f"第{i}段内容" for i in range(50)),
            articles=[],
        )
        chunks = build_chunks(doc, doc_id="D1", kb_id="kb_test", chunk_size=50, overlap=10)
        parents = [c for c in chunks if c.chunk_type == "parent"]
        assert parents and all(c.article_no.startswith("段") for c in parents)

    def test_chunk_index_is_sequential(self):
        doc = _doc([_article("第一条", 1, "内容一。"), _article("第二条", 2, "内容二。")])
        chunks = build_chunks(doc, doc_id="D1", kb_id="kb_test")
        assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


class TestRealCorpusMetrics:
    def test_total_chunks_and_truncation(self):
        from config import BASE_DIR
        from app.ingest.clean import clean_lines
        from app.ingest.pipeline import prepare

        total_parents = total_children = 0
        for name in (
            "中华人民共和国劳动合同法.md",
            "中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md",
            "中华人民共和国民法典.md",
        ):
            prepared = prepare(BASE_DIR / "data" / "raw" / name, chunk_size=300, overlap=50)
            assert truncation_rate(prepared.chunks) == 0.0
            total_parents += len(prepared.parents)
            total_children += len(prepared.children)

        assert total_parents == 1465
        # 平均条文 97 字，300 字切分下子块只比父块略多
        assert 1465 <= total_children <= 1700


def parents_content(doc: ParsedDocument) -> str:
    return doc.articles[0].content
