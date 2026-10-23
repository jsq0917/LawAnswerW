"""Prompt 模板与上下文装配测试（阶段 5，全离线）。"""

from __future__ import annotations

import pytest

from app.generation.prompts import (
    GREETING_REPLY,
    NO_DATA_REPLY,
    SYSTEM_PROMPT,
    ContextSource,
    build_context_block,
    build_messages,
    estimate_tokens,
)


class TestSystemPrompt:
    def test_six_segments_present(self):
        """六段结构（§6.5）：角色/忠实性/引用格式/结构/计算/语气。"""
        for keyword in ("LawAnswer", "忠实性", "引用格式", "回答结构", "计算", "语气"):
            assert keyword in SYSTEM_PROMPT

    def test_no_fabrication_constraint(self):
        assert "严禁编造" in SYSTEM_PROMPT

    def test_citation_requires_law_name(self):
        """引用格式必须带法律名（防两部法律同条号张冠李戴）。"""
        assert "《中华人民共和国劳动合同法》第三十九条" in SYSTEM_PROMPT

    def test_fixed_replies_non_empty(self):
        assert GREETING_REPLY and "劳动" in GREETING_REPLY
        assert NO_DATA_REPLY and "不建议" not in NO_DATA_REPLY
        assert "劳动监察" in NO_DATA_REPLY  # 无资料必须给下一步路径


class TestContextSourceTitle:
    def test_title_includes_law_name(self):
        src = ContextSource(law="中华人民共和国劳动合同法", chapter_path="第四章", article_no="第三十九条", content="x")
        assert src.title == "《中华人民共和国劳动合同法》·第四章·第三十九条"

    def test_title_omits_empty_parts(self):
        src = ContextSource(law="高频问答库", content="x")
        assert src.title == "《高频问答库》"


class TestEstimateTokens:
    def test_monotonic(self):
        assert estimate_tokens("") == 0
        assert estimate_tokens("试用期") < estimate_tokens("试用期最长可以约定多久")

    def test_chinese_ratio(self):
        # 中文约 1.5 字符/token：12 字 ≈ 8 token
        assert estimate_tokens("试用期最长可以约定多久呢") == 8


class TestBuildContextBlock:
    def _src(self, no: str, score: float, law: str = "中华人民共和国劳动合同法") -> ContextSource:
        return ContextSource(law=law, chapter_path="第四章", article_no=no, content=f"{no}正文", score=score)

    def test_format_and_numbering(self):
        block, used = build_context_block([self._src("第三十九条", 0.9), self._src("第四十条", 0.8)])
        assert block.startswith("【资料1】《中华人民共和国劳动合同法》·第四章·第三十九条")
        assert "【资料2】" in block
        assert len(used) == 2

    def test_high_score_first(self):
        """按分数降序装配：高分占【资料1】。"""
        block, used = build_context_block([self._src("第四十条", 0.5), self._src("第三十九条", 0.9)])
        assert used[0].article_no == "第三十九条"
        assert "【资料1】" in block and "第三十九条" in block.split("【资料2】")[0]

    def test_over_budget_drops_lowest(self):
        """超预算丢低分资料，保留高分。"""
        big = "法" * 400
        sources = [
            ContextSource(law="中华人民共和国劳动合同法", article_no="第三十九条", content=big, score=0.9),
            ContextSource(law="中华人民共和国劳动合同法", article_no="第四十条", content=big, score=0.8),
        ]
        block, used = build_context_block(sources, max_chars=450)
        assert len(used) == 1
        assert used[0].article_no == "第三十九条"

    def test_empty_content_skipped(self):
        sources = [self._src("第三十九条", 0.9), ContextSource(law="x", article_no="y", content="  ", score=0.5)]
        _, used = build_context_block(sources)
        assert len(used) == 1

    def test_long_content_truncated(self):
        src = ContextSource(law="中华人民共和国劳动合同法", article_no="第三十九条", content="法" * 900, score=0.9)
        block, _ = build_context_block([src], max_chars=6000)
        assert "……（已截断）" in block


class TestBuildMessages:
    def test_structure_system_history_user(self):
        messages = build_messages(
            "试用期多久",
            context_block="【资料1】《中华人民共和国劳动合同法》·第十九条\nx",
            history=[
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，请讲"},
                {"role": "user", "content": "试用期"},
            ],
        )
        assert messages[0]["role"] == "system"
        assert [m["role"] for m in messages] == ["system", "user", "assistant", "user", "user"]
        assert "【参考资料】" in messages[-1]["content"]
        assert "【用户问题】" in messages[-1]["content"]

    def test_no_history_no_context(self):
        messages = build_messages("你好")
        assert len(messages) == 2
        assert messages[-1]["content"] == "【用户问题】\n你好"

    def test_empty_history_entries_dropped(self):
        messages = build_messages("q", history=[{"role": "user", "content": "  "}, {"role": "assistant", "content": "a"}])
        assert [m["content"] for m in messages[1:-1]] == ["a"]
