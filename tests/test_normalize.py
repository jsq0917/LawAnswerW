"""归一化测试：康熙部首、全角空格、零宽字符、中文数字、折行拼接。"""

from __future__ import annotations

import pytest

from app.ingest.normalize import (
    cn_to_int,
    count_kangxi,
    has_kangxi,
    normalize_line,
    normalize_text,
    remove_cjk_gap,
    smart_join,
    to_standard_chars,
)


class TestKangxi:
    def test_kangxi_radical_one_mapped(self):
        """真实样本：劳动法 PDF 的"第⼀条"里 ⼀ 是 U+2F00。"""
        raw = "第" + chr(0x2F00) + "条　为了保护劳动者的合法权益"
        assert has_kangxi(raw)
        assert to_standard_chars(raw).startswith("第一条")

    def test_manual_supplement_chars(self):
        assert to_standard_chars("⺠⻅⻓⻔") == "民见长门"

    def test_count_kangxi_on_real_corpus(self):
        from pathlib import Path

        from config import BASE_DIR

        text = (BASE_DIR / "data" / "raw" / "中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md").read_text(
            encoding="utf-8"
        )
        # 实测 1122 个；留一点余量避免不同换行处理带来的微小差异
        assert count_kangxi(text) > 1000

    def test_no_kangxi_after_normalize(self):
        from pathlib import Path

        from config import BASE_DIR

        text = (BASE_DIR / "data" / "raw" / "中华人民共和国劳动法_中华人民共和国人力资源和社会保障部.md").read_text(
            encoding="utf-8"
        )
        assert count_kangxi(normalize_text(text)) == 0


class TestWhitespace:
    def test_fullwidth_space_collapsed(self):
        assert normalize_line("第一章\u3000总\u3000则") == "第一章总则"

    def test_keep_cjk_gap_preserves_structural_space(self):
        """结构识别需要这个空格区分标题与正文交叉引用。"""
        assert normalize_line("第一章 总 则", keep_cjk_gap=True) == "第一章 总 则"
        assert normalize_line("第一章 总 则") == "第一章总则"

    def test_ascii_spaces_kept_between_latin(self):
        assert normalize_line("Model  BGE  M3") == "Model BGE M3"

    def test_zero_width_removed(self):
        assert normalize_line("劳动\u200b法\ufeff") == "劳动法"

    def test_remove_cjk_gap(self):
        assert remove_cjk_gap("总 则") == "总则"
        assert remove_cjk_gap("a b") == "a b"

    def test_fullwidth_digits_to_halfwidth(self):
        assert normalize_line("２０２４年") == "2024年"


class TestSmartJoin:
    def test_chinese_to_chinese_no_space(self):
        assert smart_join("劳动者的", "权利") == "劳动者的权利"

    def test_chinese_punctuation_no_space(self):
        assert smart_join("劳动合同的；", "（二）与本单位") == "劳动合同的；（二）与本单位"

    def test_latin_needs_space(self):
        assert smart_join("2008", "年") == "2008 年"

    def test_empty_sides(self):
        assert smart_join("", "条文") == "条文"
        assert smart_join("条文", "") == "条文"

    def test_trailing_punct_start(self):
        assert smart_join("终止", "，并办理") == "终止，并办理"


class TestCnNumber:
    @pytest.mark.parametrize(
        "cn,expected",
        [
            ("一", 1),
            ("十", 10),
            ("十九", 19),
            ("二十", 20),
            ("三十九", 39),
            ("一百", 100),
            ("一百零三", 103),
            ("一百零七", 107),
            ("九百九十九", 999),
            ("一千", 1000),
            ("一千二百六十", 1260),
        ],
    )
    def test_values(self, cn, expected):
        assert cn_to_int(cn) == expected

    def test_zero(self):
        assert cn_to_int("零") == 0
