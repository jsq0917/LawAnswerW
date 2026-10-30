"""追问预测（问题推荐）单测：全离线，不调真实 LLM。

覆盖三层：
1. ``build_messages`` 的装配与回答截断（纯函数）；
2. ``coerce_suggestions`` 的脏数据校正（模型自由发挥是常态）；
3. ``generate_suggestions`` 的容错契约——**任何失败都返回空列表**；
   以及 ``POST /api/chat/suggestions`` 的接口契约。
"""

from __future__ import annotations

import pytest

from app.generation.llm_client import LLMError, LLMResponse
from app.generation.suggestions import (
    ANSWER_EXCERPT_CHARS,
    build_messages,
    coerce_suggestions,
    generate_suggestions,
)

QUERY = "劳动合同到期不续签要赔钱吗"
ANSWER = (
    "根据《中华人民共和国劳动合同法》第四十六条，除用人单位维持或者提高劳动合同约定"
    "条件续订而劳动者不同意续订的情形外，期满终止固定期限劳动合同应当支付经济补偿。"
)


class FakeClient:
    """最小 LLM 假件：返回预置 content，或抛出预置异常，并记录调用参数。"""

    def __init__(self, content: str = "", exc: Exception | None = None) -> None:
        self.content = content
        self.exc = exc
        self.calls: list[dict] = []

    def chat(self, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append({"messages": list(messages), **kwargs})
        if self.exc is not None:
            raise self.exc
        return LLMResponse(content=self.content, model="fake", latency_ms=1)


class TestBuildMessages:
    def test_contains_query_and_answer(self):
        msgs = build_messages(QUERY, ANSWER, ["中华人民共和国劳动合同法"])
        assert msgs[0]["role"] == "system"
        assert "追问" in msgs[0]["content"]
        user = msgs[1]["content"]
        assert QUERY in user
        assert "第四十六条" in user
        assert "中华人民共和国劳动合同法" in user

    def test_answer_is_truncated(self):
        msgs = build_messages(QUERY, "法" * (ANSWER_EXCERPT_CHARS + 500))
        assert "法" * ANSWER_EXCERPT_CHARS in msgs[1]["content"]
        assert "法" * (ANSWER_EXCERPT_CHARS + 1) not in msgs[1]["content"]

    def test_law_names_optional(self):
        msgs = build_messages(QUERY, ANSWER)
        assert "【回答涉及的法律】" not in msgs[1]["content"]


class TestCoerce:
    def test_normal_case(self):
        data = {"questions": ["经济补偿怎么算？", "需要准备哪些证据？", "可以申请仲裁吗？"]}
        assert coerce_suggestions(data, QUERY) == [
            "经济补偿怎么算？",
            "需要准备哪些证据？",
            "可以申请仲裁吗？",
        ]

    def test_limits_to_three(self):
        data = {"questions": ["问题一是什么", "问题二是什么", "问题三是什么", "问题四是什么"]}
        assert len(coerce_suggestions(data, QUERY)) == 3

    def test_dedup_and_drop_original(self):
        data = {"questions": [QUERY, QUERY + "？", "经济补偿怎么算", "经济补偿怎么算？"]}
        assert coerce_suggestions(data, QUERY) == ["经济补偿怎么算"]

    def test_accepts_alternative_keys_and_bare_list(self):
        assert coerce_suggestions({"suggestions": ["A 问题内容"]}, QUERY) == ["A 问题内容"]
        assert coerce_suggestions(["B 问题内容"], QUERY) == ["B 问题内容"]

    def test_drops_invalid_entries(self):
        data = {"questions": ["", "   ", None, 123, "超" * 80, "有效问题内容"]}
        assert coerce_suggestions(data, QUERY) == ["有效问题内容"]

    def test_non_dict_returns_empty(self):
        assert coerce_suggestions("not a dict", QUERY) == []
        assert coerce_suggestions(None, QUERY) == []

    def test_strips_bullet_prefix(self):
        assert coerce_suggestions({"questions": ["1. 经济补偿怎么算"]}, QUERY) == ["经济补偿怎么算"]


class TestGenerate:
    def test_success(self):
        client = FakeClient('{"questions": ["经济补偿怎么算？", "需要哪些证据？", "能申请仲裁吗？"]}')
        out = generate_suggestions(QUERY, ANSWER, client=client)
        assert len(out) == 3
        assert client.calls[0]["json_mode"] is True
        assert client.calls[0]["temperature"] == pytest.approx(0.7)

    def test_tolerates_markdown_fence(self):
        client = FakeClient('```json\n{"questions": ["经济补偿怎么算？"]}\n```')
        assert generate_suggestions(QUERY, ANSWER, client=client) == ["经济补偿怎么算？"]

    def test_llm_error_returns_empty(self):
        client = FakeClient(exc=LLMError("boom"))
        assert generate_suggestions(QUERY, ANSWER, client=client) == []

    def test_bad_json_returns_empty(self):
        assert generate_suggestions(QUERY, ANSWER, client=FakeClient("这不是 JSON")) == []

    def test_missing_answer_skips_call(self):
        client = FakeClient('{"questions": ["A 问题"]}')
        assert generate_suggestions(QUERY, "   ", client=client) == []
        assert client.calls == []

    def test_disabled_by_settings(self, settings_factory):
        settings = settings_factory(suggest_enabled=False)
        client = FakeClient('{"questions": ["A 问题"]}')
        assert generate_suggestions(QUERY, ANSWER, client=client, settings=settings) == []
        assert client.calls == []


class TestSuggestionsAPI:
    def test_returns_list(self, client, monkeypatch):
        from app.api import chat as chat_api

        monkeypatch.setattr(
            chat_api,
            "generate_suggestions",
            lambda *a, **kw: ["经济补偿怎么算？", "需要哪些证据？"],
        )
        resp = client.post(
            "/api/chat/suggestions",
            json={"query": QUERY, "answer": ANSWER, "law_names": ["中华人民共和国劳动合同法"]},
        )
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == ["经济补偿怎么算？", "需要哪些证据？"]

    def test_empty_when_prediction_fails(self, client, monkeypatch):
        from app.api import chat as chat_api

        monkeypatch.setattr(chat_api, "generate_suggestions", lambda *a, **kw: [])
        resp = client.post("/api/chat/suggestions", json={"query": QUERY, "answer": ANSWER})
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []

    def test_rejects_empty_answer(self, client):
        resp = client.post("/api/chat/suggestions", json={"query": QUERY, "answer": ""})
        assert resp.status_code == 422
