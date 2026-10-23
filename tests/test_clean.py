"""清洗测试：页眉页脚、页码、网页导航、目录点线、表格噪音。"""

from __future__ import annotations

from pathlib import Path

from app.ingest.clean import (
    clean_lines,
    is_noise,
    is_page_number,
    is_table_separator,
    is_toc_line,
    table_row_to_text,
)
from config import BASE_DIR

CONTRACT_MD = BASE_DIR / "data" / "raw" / "中华人民共和国劳动合同法.md"
CIVIL_MD = BASE_DIR / "data" / "raw" / "中华人民共和国民法典.md"


def _texts(lines: list[str]) -> list[str]:
    return [c.text for c in clean_lines(lines)]


class TestPageNumber:
    def test_plain_number(self):
        assert is_page_number("1")
        assert is_page_number(" 23 ")

    def test_slash_form(self):
        assert is_page_number("12/34")

    def test_chinese_form(self):
        assert is_page_number("第 3 页")
        assert is_page_number("第3页 共5页")

    def test_article_not_page(self):
        assert not is_page_number("第一条")


class TestNoise:
    def test_icp_and_site_links(self):
        assert is_noise("京ICP备12345678号")
        assert is_noise("加入收藏")

    def test_navigation_row(self):
        assert is_noise("首页 宪法 人大机构 栗战书委员长 代表大会会议")

    def test_date_lines(self):
        assert is_noise("8/23/2020")
        assert is_noise("2020年06月02日 08:28:13")

    def test_url(self):
        assert is_noise("https://www.gov.cn/xinwen")

    def test_header_with_department(self):
        assert is_noise("中华人民共和国劳动法 中华人民共和国人力资源和社会保障部")

    def test_article_content_not_noise(self):
        assert not is_noise("用人单位应当依法建立和完善规章制度，保障劳动者享有劳动权利和履行劳动义务。")


class TestToc:
    def test_dot_leader_line(self):
        assert is_toc_line("第一章 总则 ......... 1")
        assert is_toc_line("第五条 合同履行……12")

    def test_normal_line(self):
        assert not is_toc_line("第一条 为了完善劳动合同制度。")


class TestTable:
    def test_separator_detected(self):
        assert is_table_separator("| ----- | ------- |")
        assert is_table_separator("|---|---|")

    def test_row_to_text(self):
        assert table_row_to_text("|  第一章  |  总则  |") == "第一章 总则"

    def test_separator_dropped_in_clean(self):
        texts = _texts(["| ----- | ------- |", "第一条 为了完善劳动合同制度。"])
        assert "| ----- | ------- |" not in "".join(texts)


class TestCleanLines:
    def test_repeated_header_dropped(self):
        raw = [
            "中华人民共和国劳动法",
            "第一条 为了保护劳动者的合法权益。",
            "中华人民共和国劳动法",
            "第二条 在中华人民共和国境内的企业适用本法。",
            "中华人民共和国劳动法",
        ]
        texts = _texts(raw)
        assert "中华人民共和国劳动法" not in texts
        assert len(texts) == 2

    def test_repeated_content_kept(self):
        """正文句即使重复也不能被当成页眉删掉。"""
        sentence = "用人单位应当依法支付劳动报酬。"
        texts = _texts([sentence, sentence, sentence])
        assert len(texts) == 3

    def test_page_numbers_and_dates_removed(self):
        texts = _texts(["1", "8/23/2020", "第一条 为了完善劳动合同制度。"])
        assert texts == ["第一条 为了完善劳动合同制度。"]

    def test_line_numbers_preserved(self):
        """行号必须指向原始文件，便于溯源（"第二条 内容"在第 2 行）。"""
        cleaned = clean_lines(["页首", "第二条 内容。", "2"])
        target = next(c for c in cleaned if c.text.startswith("第二条"))
        assert target.line_no == 2

    def test_blank_lines_skipped(self):
        assert _texts(["", "   ", "第一条 内容。"]) == ["第一条 内容。"]


class TestRealCorpus:
    def test_contract_law_noise_removed(self):
        raw = CONTRACT_MD.read_text(encoding="utf-8").splitlines()
        texts = _texts(raw)
        joined = "\n".join(texts)
        # 表格分隔行与目录点线必须清掉；"目 录"本身在结构识别阶段被当作目录丢弃
        assert "| ----- |" not in joined
        assert "....." not in joined
        # 第一条必须在
        assert any(t.startswith("第一条") for t in texts)

    def test_civil_code_web_navigation_removed(self):
        raw = CIVIL_MD.read_text(encoding="utf-8").splitlines()
        joined = "\n".join(_texts(raw))
        for noise in ("加入收藏", "网站无障碍", "浏览字号", "图片报道"):
            assert noise not in joined

    def test_civil_code_page_date_removed(self):
        raw = CIVIL_MD.read_text(encoding="utf-8").splitlines()
        joined = "\n".join(_texts(raw))
        assert "8/23/2020" not in joined
