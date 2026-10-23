"""结构识别测试：编/章/节/条、目录丢弃、交叉引用不误判、三部法律条数硬校验。"""

from __future__ import annotations

import pytest

from app.ingest.clean import clean_lines
from app.ingest.structure import (
    law_name_from_filename,
    parse_law,
    validate_article_sequence,
)
from config import BASE_DIR

RAW_DIR = BASE_DIR / "data" / "raw"


def _parse(lines: list[str], law: str = "测试法"):
    return parse_law("test.md", law, clean_lines(lines))


class TestHeading:
    def test_chapter_and_article(self):
        doc = _parse(
            [
                "第一章 总则",
                "第一条 为了规范测试。",
                "第二条 本法适用于测试。",
            ]
        )
        assert doc.article_count == 2
        assert doc.articles[0].chapter_path == "第一章 总则"
        assert doc.articles[0].content == "为了规范测试。"

    def test_section_path(self):
        doc = _parse(
            [
                "第五章 特别规定",
                "第二节 劳务派遣",
                "第五十七条 经营劳务派遣业务应当具备下列条件。",
            ]
        )
        assert doc.articles[0].chapter_path == "第五章 特别规定·第二节 劳务派遣"

    def test_part_level_for_civil_code(self):
        doc = _parse(
            [
                "第一编 总则",
                "第一章 基本规定",
                "第一条 为了保护民事主体的合法权益。",
            ]
        )
        assert doc.articles[0].chapter_path == "第一编 总则·第一章 基本规定"

    def test_heading_with_joined_spaces(self):
        """PDF 里"总     则"归一后是"总 则"，标题要拼回"总则"。"""
        doc = _parse(["第一章 总     则", "第一条 内容。"])
        assert doc.articles[0].chapter_path == "第一章 总则"


class TestAntiMisjudge:
    def test_cross_reference_without_space_is_content(self):
        """正文折行处的"第三十九条和第四十条…"条号后无空白，不能当新条。"""
        doc = _parse(
            [
                "第二章 订立",
                "第一条 依照第三十九条和第四十条规定处理。",
                "第二条 另一条。",
            ]
        )
        assert doc.article_count == 2
        assert "第三十九条和第四十条" in doc.articles[0].content

    def test_article_number_regression_is_content(self):
        """整行引用且带空格，但条号回退 → 按正文处理。"""
        doc = _parse(
            [
                "第一章 总则",
                "第五条 依照 第三条 的规定执行。",
                "第六条 内容六。",
            ]
        )
        assert doc.article_count == 2
        assert doc.articles[0].article_no == "第五条"
        assert "第三条" in doc.articles[0].content

    def test_toc_before_first_article_discarded(self):
        doc = _parse(
            [
                "第一编 总则",
                "第一章 基本规定",
                "第二章 自然人",
                "第一编 总则",
                "第一章 基本规定",
                "第一条 真正的正文。",
            ]
        )
        assert doc.article_count == 1
        assert doc.articles[0].content == "真正的正文。"
        # 目录区的章不该污染正文路径（正文重新出现过一次）
        assert doc.articles[0].chapter_path == "第一编 总则·第一章 基本规定"

    def test_content_before_first_article_ignored(self):
        doc = _parse(["中华人民共和国民法典", "（2020年5月28日通过）", "第一条 内容。"])
        assert doc.article_count == 1


class TestFlush:
    def test_multiline_article_joined(self):
        doc = _parse(
            [
                "第一条 为了完善劳动合同制度，明确劳动合同双方当事人的",
                "权利和义务，保护劳动者的合法权益。",
            ]
        )
        assert doc.articles[0].content == "为了完善劳动合同制度，明确劳动合同双方当事人的权利和义务，保护劳动者的合法权益。"

    def test_article_without_content_skipped(self):
        doc = _parse(["第一条", "第二条 有内容。"])
        assert doc.article_count == 1

    def test_no_articles_returns_empty(self):
        doc = _parse(["随便一段文字。", "又一段。"])
        assert doc.article_count == 0


class TestRealCorpus:
    @pytest.mark.parametrize(
        "filename,expected,law",
        [
            ("中华人民共和国劳动合同法.md", 98, "中华人民共和国劳动合同法"),
            ("中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md", 107, "中华人民共和国劳动法"),
            ("中华人民共和国民法典.md", 1260, "中华人民共和国民法典"),
        ],
    )
    def test_expected_article_counts(self, filename, expected, law):
        """三条断言是入库硬校验：数量不对就说明清洗/结构识别退化了。"""
        path = RAW_DIR / filename
        doc = parse_law(filename, law_name_from_filename(filename), clean_lines(path.read_text(encoding="utf-8").splitlines()))
        assert doc.article_count == expected
        assert validate_article_sequence(doc.articles) == []

    def test_civil_code_last_article(self):
        path = RAW_DIR / "中华人民共和国民法典.md"
        doc = parse_law(path.name, "中华人民共和国民法典", clean_lines(path.read_text(encoding="utf-8").splitlines()))
        assert doc.articles[-1].article_no == "第一千二百六十条"
        assert doc.articles[-1].article_num == 1260

    def test_civil_code_has_seven_parts(self):
        path = RAW_DIR / "中华人民共和国民法典.md"
        doc = parse_law(path.name, "中华人民共和国民法典", clean_lines(path.read_text(encoding="utf-8").splitlines()))
        parts = {a.chapter_path.split("·")[0] for a in doc.articles}
        assert len(parts) == 7


class TestLawName:
    def test_strip_department_suffix(self):
        assert (
            law_name_from_filename("中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md")
            == "中华人民共和国劳动法"
        )

    def test_plain(self):
        assert law_name_from_filename("中华人民共和国民法典.md") == "中华人民共和国民法典"

    def test_full_text_suffix(self):
        assert law_name_from_filename("中华人民共和国劳动合同法（全文）.md") == "中华人民共和国劳动合同法"
